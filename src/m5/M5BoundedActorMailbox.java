// M5 experimental actor-worker runtime, kept separate from static Graal codegen.
// Off-heap fixed ring (no per-message envelopes/boxing), one worker per actor.
// Real independently scheduled threads, not a proof of production tenant security.
import java.lang.foreign.Arena;
import java.lang.foreign.MemorySegment;
import java.lang.foreign.ValueLayout;
import java.util.concurrent.atomic.AtomicLong;

public final class M5BoundedActorMailbox implements AutoCloseable {
    private final Object gate = new Object();
    private final Arena nativeArena = Arena.ofShared();
    private final MemorySegment ring;
    private final MemorySegment state;
    private final Thread worker;
    private final int capacity;
    private int head;
    private int tail;
    private int queued;
    private int maximumDepth;
    private boolean paused;
    private boolean stopping;
    private boolean closed;
    private boolean arenaReleased;
    // Captured before nativeArena.close(): snapshot remains legal after disposal.
    private long terminalState;
    private long accepted;
    private long rejectedFull;
    private long rejectedClosed;
    private long processed;
    private long abandoned;
    private long failedTurns;
    private long controlNotices;
    private long workerThreadId;
    private Throwable fault;

    public M5BoundedActorMailbox(int capacity, long initial, boolean initiallyPaused) {
        if (capacity < 1 || capacity > 4096)
            throw new IllegalArgumentException("actor mailbox capacity must be 1..4096");
        this.capacity = capacity;
        this.paused = initiallyPaused;
        this.ring = nativeArena.allocate((long) capacity * Long.BYTES, Long.BYTES);
        this.state = nativeArena.allocate(ValueLayout.JAVA_LONG);
        state.set(ValueLayout.JAVA_LONG, 0, initial);
        this.worker = new Thread(this::runWorker, "atto-m5-actor-worker");
        this.worker.setDaemon(true);
        this.worker.start();
    }

    // Admission and the first retirement transition are linearizable under gate.
    // False means no message was admitted. A full mailbox cannot allocate memory.
    public boolean try_send(long value) {
        synchronized (gate) {
            if (stopping || closed) { rejectedClosed++; return false; }
            if (queued == capacity) { rejectedFull++; return false; }
            ring.set(ValueLayout.JAVA_LONG, (long) tail * Long.BYTES, value);
            tail = (tail + 1) % capacity;
            queued++;
            accepted++;
            if (queued > maximumDepth) maximumDepth = queued;
            gate.notifyAll();
            return true;
        }
    }

    public void release_worker() {
        synchronized (gate) {
            paused = false;
            gate.notifyAll();
        }
    }

    private void runWorker() {
        // An interruption while idle is a worker failure, not a failed admitted turn.
        boolean turnInFlight = false;
        synchronized (gate) { workerThreadId = Thread.currentThread().threadId(); }
        try {
            while (true) {
                long value;
                synchronized (gate) {
                    while ((queued == 0 || paused) && !stopping) gate.wait();
                    // close() releases pause, but worker may still have admitted mail.
                    if (paused && stopping) paused = false;
                    while (queued == 0 && !stopping) gate.wait();
                    if (queued == 0 && stopping) break;
                    if (paused) continue;
                    value = ring.get(ValueLayout.JAVA_LONG, (long) head * Long.BYTES);
                    head = (head + 1) % capacity;
                    queued--;
                    turnInFlight = true;
                    gate.notifyAll();
                }
                // State is written by precisely this worker (actor-owner), never
                // a sending thread. Math.addExact rejects overflow before commit.
                long next = Math.addExact(state.get(ValueLayout.JAVA_LONG, 0), value);
                state.set(ValueLayout.JAVA_LONG, 0, next);
                synchronized (gate) {
                    processed++;
                    turnInFlight = false;
                    gate.notifyAll();
                }
            }
        } catch (Throwable error) {
            synchronized (gate) {
                fault = error;
                if (turnInFlight) failedTurns++;
                // Diagnostic counter only. No real supervisor CONTROL queue exists yet.
                controlNotices++;
                abandoned += queued;
                queued = 0;
                stopping = true;
                gate.notifyAll();
            }
        } finally {
            synchronized (gate) {
                // The worker is now quiescent; capture off-heap state before disposal.
                terminalState = state.get(ValueLayout.JAVA_LONG, 0);
                closed = true;
                gate.notifyAll();
            }
        }
    }

    public void request_stop() {
        synchronized (gate) {
            stopping = true;
            paused = false;
            gate.notifyAll();
        }
    }

    public void await_done() throws InterruptedException {
        request_stop();
        worker.join(30_000L);
        if (worker.isAlive()) throw new IllegalStateException("actor worker failed to retire");
    }

    public Snapshot snapshot() {
        synchronized (gate) {
            // Immutable terminal copy; never dereference the released off-heap arena.
            long v = closed ? terminalState : Long.MIN_VALUE;
            return new Snapshot(accepted, processed, rejectedFull, rejectedClosed,
                abandoned, failedTurns, controlNotices, maximumDepth, capacity, queued,
                closed, v, workerThreadId, fault != null);
        }
    }

    @Override public void close() throws InterruptedException {
        await_done();
        // Serializes competing close() calls and preserves legal post-close snapshots.
        synchronized (gate) {
            if (!arenaReleased) {
                nativeArena.close();
                arenaReleased = true;
            }
        }
    }

    public record Snapshot(
        long accepted, long processed, long rejectedFull, long rejectedClosed,
        long abandoned, long failedTurns, long supervisorControlNotices, int maxDepth, int capacity,
        int remaining, boolean closed, long finalState, long workerThreadId,
        boolean failed
    ) {}
}
