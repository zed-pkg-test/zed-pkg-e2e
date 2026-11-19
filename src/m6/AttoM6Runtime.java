// Experimental AttoLang host-side NIO / pooled serial-actor execution prototype.
// NOT the canonical .atto compiler, an OS sandbox, or hard-real-time certification.
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.ReadableByteChannel;
import java.nio.channels.CancelledKeyException;
import java.nio.channels.SelectableChannel;
import java.nio.channels.SelectionKey;
import java.nio.channels.Selector;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;

public final class AttoM6Runtime implements AutoCloseable {
    @FunctionalInterface public interface Receive { void receive(long value) throws Exception; }
    public enum Admission { ACCEPTED, FULL, CLOSED, STALE }
    public enum ControlKind { STOP, PANIC }
    // Object identity as well as generation fence: no forged/reused ActorRef authority.
    public record ActorRef(String name, long generation) {}
    public record Snapshot(long accepted, long completed, long failed, long abandoned,
                           int queued, boolean running, boolean accepting, boolean retired,
                           long generation, long controlsDelivered, ControlKind lastControl,
                           String failureType, int workerIdentities) {}
    public record ReactorSnapshot(long bytesAdmitted, long bytesHeld, long eof, long errors,
                                  long wakeRequests, long bytesRejected, int registrations) {}
    // Watchdog is diagnostic, not a preemptive guest scheduler or supervisor CONTROL.
    public record TurnBudgetSnapshot(long maxElapsedNanos, long overBudgetTurns,
                                     long watchdogNotices, boolean running,
                                     boolean overdueNow) {}
    public record SchedulerSnapshot(int readyDepth, int controlDepth, int registeredActors,
                                    int activeReceives, long watchdogNotices,
                                    long overBudgetTurns, long budgetNanos) {}

    private static final class Actor {
        final ActorRef ref;
        final Receive handler;
        final int capacity;
        final ArrayDeque<Long> inbox = new ArrayDeque<>();
        final Set<Long> workers = new HashSet<>();
        long accepted, completed, failed, abandoned, controlsDelivered;
        long turnStartedNanos, maxTurnNanos, overBudgetTurns, watchdogNotices;
        boolean currentTurnNotified;
        boolean accepting = true, fenced, running, scheduled, retired;
        boolean controlQueued;
        ControlKind pendingControl, lastControl;
        String failureType;
        Actor(ActorRef ref, Receive handler, int capacity) {
            this.ref = ref; this.handler = handler; this.capacity = capacity;
        }
    }
    private static final class Registration {
        final SelectableChannel channel;
        final ActorRef actor;
        final ByteBuffer buffer = ByteBuffer.allocateDirect(1);
        SelectionKey key;
        Integer heldByte;
        Registration(SelectableChannel channel, ActorRef actor) {
            this.channel = channel; this.actor = actor;
        }
    }

    private final Object gate = new Object();
    private final Map<String, Actor> actors = new HashMap<>();
    private final Map<String, Long> generations = new HashMap<>();
    private final ArrayDeque<Actor> ready = new ArrayDeque<>();
    private final ArrayDeque<Actor> controls = new ArrayDeque<>();
    private final List<Thread> carriers = new ArrayList<>();
    private final Thread supervisor;
    private final Thread reactor;
    private final Thread watchdog;
    private final long turnBudgetNanos;
    private long totalWatchdogNotices, totalOverBudgetTurns;
    private final Selector selector;
    private final ArrayBlockingQueue<Runnable> nioCommands = new ArrayBlockingQueue<>(128);
    private static final int MAX_REGISTRATIONS = 512;
    // Only the reactor thread accesses these registration objects / selection keys.
    private final Map<SelectableChannel, Registration> registrations = new HashMap<>();
    private final AtomicLong ioAdmitted = new AtomicLong();
    private final AtomicLong ioHeld = new AtomicLong();
    private final AtomicLong ioEof = new AtomicLong();
    private final AtomicLong ioErrors = new AtomicLong();
    private final AtomicLong wakes = new AtomicLong();
    private final AtomicLong ioRejected = new AtomicLong();
    private volatile boolean reactorStopping;
    private boolean stopping;
    private boolean closing;
    private volatile boolean fullyClosed;
    private final int maxActors;

    public AttoM6Runtime(int carrierCount, int maxActors) throws IOException {
        this(carrierCount, maxActors, 50);
    }

    // A 1..60000ms observation threshold; this does not interrupt or preempt handlers.
    public AttoM6Runtime(int carrierCount, int maxActors, long turnBudgetMillis)
            throws IOException {
        if (carrierCount < 1 || carrierCount > 32 || maxActors < 1 || maxActors > 256 ||
            turnBudgetMillis < 1 || turnBudgetMillis > 60_000)
            throw new IllegalArgumentException("invalid bounded carriers, actors or turn budget");
        this.maxActors = maxActors;
        this.turnBudgetNanos = TimeUnit.MILLISECONDS.toNanos(turnBudgetMillis);
        selector = Selector.open();
        supervisor = new Thread(this::supervise, "atto-m6-supervisor-CONTROL");
        reactor = new Thread(this::react, "atto-m6-nio-reactor");
        watchdog = new Thread(this::watch_over_budget, "atto-m6-budget-watchdog");
        supervisor.start(); reactor.start(); watchdog.start();
        for (int i = 0; i < carrierCount; i++) {
            Thread worker = new Thread(this::carry, "atto-m6-carrier-" + i);
            carriers.add(worker);
            worker.start();
        }
    }

    public ActorRef spawn(String name, int capacity, Receive handler) {
        if (name == null || name.isEmpty() || name.length() > 128 || handler == null ||
            capacity < 1 || capacity > 4096) throw new IllegalArgumentException("invalid actor");
        synchronized (gate) {
            if (stopping || closing) throw new IllegalStateException("runtime stopping");
            Actor prior = actors.get(name);
            if (prior != null && !prior.retired) throw new IllegalStateException("actor already live");
            if (prior == null && actors.size() >= maxActors)
                throw new IllegalStateException("actor registry full");
            long generation = Math.addExact(generations.getOrDefault(name, 0L), 1L);
            generations.put(name, generation);
            ActorRef ref = new ActorRef(name, generation);
            actors.put(name, new Actor(ref, handler, capacity));
            return ref;
        }
    }

    private Actor resolve(ActorRef ref) {
        if (ref == null) return null;
        Actor actor = actors.get(ref.name());
        // Reference identity matters: a record with matching fields is not a valid handle.
        return actor != null && actor.ref == ref && !actor.retired ? actor : null;
    }

    public Admission try_send(ActorRef ref, long value) {
        synchronized (gate) {
            Actor actor = resolve(ref);
            if (actor == null) return Admission.STALE;
            if (!actor.accepting || actor.fenced || stopping || closing) return Admission.CLOSED;
            if (actor.inbox.size() >= actor.capacity) return Admission.FULL;
            actor.inbox.addLast(value);
            actor.accepted++;
            schedule(actor);
            return Admission.ACCEPTED;
        }
    }
    private void schedule(Actor actor) {
        if (!actor.scheduled && !actor.running && !actor.fenced && !actor.inbox.isEmpty()) {
            actor.scheduled = true;
            ready.addLast(actor);
            gate.notifyAll();
        }
    }

    public boolean request_stop(ActorRef ref) {
        synchronized (gate) {
            Actor actor = resolve(ref);
            if (actor == null) return false;
            actor.accepting = false;
            control(actor, ControlKind.STOP);
            gate.notifyAll();
            wake_reactor();
            return true;
        }
    }

    private void control(Actor actor, ControlKind kind) {
        if (actor.controlQueued) {
            if (kind == ControlKind.PANIC) actor.pendingControl = kind;
            return;
        }
        // A repeated STOP after its delivery is not another terminal event.
        // PANIC still upgrades an already delivered STOP (different severity).
        if (actor.lastControl == ControlKind.PANIC ||
            (actor.lastControl == ControlKind.STOP && kind == ControlKind.STOP))
            return;
        // One logical CONTROL slot per incarnation; DATA capacity cannot consume it.
        actor.pendingControl = kind;
        actor.controlQueued = true;
        controls.addLast(actor);
        gate.notifyAll();
    }

    private void maybe_retire(Actor actor) {
        if (!actor.accepting && actor.inbox.isEmpty() && !actor.running &&
            !actor.controlQueued) {
            actor.retired = true;
            // A fenced actor's stale ready entry must not accumulate across restarts.
            if (actor.scheduled) ready.remove(actor);
            actor.scheduled = false;
            gate.notifyAll();
        }
    }
    private void supervise() {
        try {
            while (true) {
                synchronized (gate) {
                    while (controls.isEmpty() && !stopping) gate.wait();
                    if (controls.isEmpty() && stopping) return;
                    Actor actor = controls.removeFirst();
                    ControlKind kind = actor.pendingControl;
                    actor.pendingControl = null;
                    actor.controlQueued = false;
                    actor.controlsDelivered++;
                    actor.lastControl = kind;
                    if (kind == ControlKind.PANIC) {
                        actor.fenced = true;
                        actor.accepting = false;
                        actor.abandoned += actor.inbox.size();
                        actor.inbox.clear();
                    }
                    maybe_retire(actor);
                    gate.notifyAll();
                }
                wake_reactor();
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            synchronized (gate) { if (!stopping) throw new AssertionError("supervisor interrupted", e); }
        }
    }

    private void carry() {
        try {
            while (true) {
                Actor actor;
                long mail;
                synchronized (gate) {
                    while (true) {
                        if (stopping && ready.isEmpty() && controls.isEmpty()) return;
                        // Pending CONTROL must be dispatched before a new DATA turn.
                        if (!controls.isEmpty() || ready.isEmpty()) { gate.wait(); continue; }
                        actor = ready.removeFirst();
                        actor.scheduled = false;
                        if (actor.retired || actor.fenced || actor.running || actor.inbox.isEmpty())
                            continue;
                        mail = actor.inbox.removeFirst();
                        actor.running = true;
                        actor.turnStartedNanos = System.nanoTime();
                        actor.currentTurnNotified = false;
                        actor.workers.add(Thread.currentThread().threadId());
                        gate.notifyAll(); // Wake an idle watchdog for the active turn.
                        break;
                    }
                }
                wake_reactor(); // Capacity became available; rearm paused NIO.
                Throwable error = null;
                try { actor.handler.receive(mail); }
                catch (Throwable e) { error = e; }
                synchronized (gate) {
                    long elapsed = System.nanoTime() - actor.turnStartedNanos;
                    if (elapsed > actor.maxTurnNanos) actor.maxTurnNanos = elapsed;
                    if (elapsed >= turnBudgetNanos) {
                        actor.overBudgetTurns++;
                        totalOverBudgetTurns++;
                    }
                    actor.running = false;
                    if (error == null) actor.completed++;
                    else {
                        actor.failed++;
                        actor.failureType = error.getClass().getSimpleName();
                        // Fence immediately; supervisor notice delivery is separate.
                        actor.fenced = true;
                        actor.accepting = false;
                        actor.abandoned += actor.inbox.size();
                        actor.inbox.clear();
                        control(actor, ControlKind.PANIC);
                    }
                    if (!actor.fenced) schedule(actor);
                    maybe_retire(actor);
                    gate.notifyAll();
                }
                wake_reactor();
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            synchronized (gate) { if (!stopping) throw new AssertionError("carrier interrupted", e); }
        }
    }

    // Samples running callbacks without invoking guest code or changing dispatch order.
    private void watch_over_budget() {
        try {
            synchronized (gate) {
                while (!stopping) {
                    boolean active = false;
                    long now = System.nanoTime();
                    for (Actor actor : actors.values()) {
                        if (!actor.running) continue;
                        active = true;
                        if (!actor.currentTurnNotified &&
                            now - actor.turnStartedNanos >= turnBudgetNanos) {
                            actor.currentTurnNotified = true;
                            actor.watchdogNotices++;
                            totalWatchdogNotices++;
                            gate.notifyAll();
                        }
                    }
                    if (!stopping) {
                        // Park indefinitely without active turns; avoid idle polling.
                        if (active) gate.wait(Math.max(1L,
                            Math.min(10L, TimeUnit.NANOSECONDS.toMillis(turnBudgetNanos / 4L))));
                        else gate.wait();
                    }
                }
            }
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            synchronized (gate) {
                if (!stopping) throw new AssertionError("watchdog interrupted", interrupted);
            }
        }
    }

    public TurnBudgetSnapshot budget_snapshot(ActorRef ref) {
        synchronized (gate) {
            Actor actor = actors.get(ref.name());
            if (actor == null || actor.ref != ref)
                throw new IllegalArgumentException("stale actor ref");
            boolean overdue = actor.running &&
                System.nanoTime() - actor.turnStartedNanos >= turnBudgetNanos;
            return new TurnBudgetSnapshot(actor.maxTurnNanos, actor.overBudgetTurns,
                actor.watchdogNotices, actor.running, overdue);
        }
    }

    public SchedulerSnapshot scheduler_snapshot() {
        synchronized (gate) {
            int active = 0;
            for (Actor actor : actors.values()) if (actor.running) active++;
            return new SchedulerSnapshot(ready.size(), controls.size(), actors.size(),
                active, totalWatchdogNotices, totalOverBudgetTurns, turnBudgetNanos);
        }
    }

    public Snapshot snapshot(ActorRef ref) {
        synchronized (gate) {
            Actor actor = actors.get(ref.name());
            if (actor == null || actor.ref != ref) throw new IllegalArgumentException("stale actor ref");
            return new Snapshot(actor.accepted, actor.completed, actor.failed, actor.abandoned,
                actor.inbox.size(), actor.running, actor.accepting, actor.retired, ref.generation(),
                actor.controlsDelivered, actor.lastControl, actor.failureType, actor.workers.size());
        }
    }
    public boolean await_terminal(ActorRef ref, long timeoutMillis) throws InterruptedException {
        long deadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMillis);
        synchronized (gate) {
            for (;;) {
                Snapshot snap = snapshot(ref);
                if (snap.retired()) return true;
                long nanos = deadline - System.nanoTime();
                if (nanos <= 0) return false;
                TimeUnit.NANOSECONDS.timedWait(gate, nanos);
            }
        }
    }
    public static void check_conservation(Snapshot s) {
        if (s.accepted() != s.completed() + s.failed() + s.abandoned() +
                s.queued() + (s.running() ? 1 : 0))
            throw new AssertionError("accepted mail not conserved");
    }

    public void register_read(SelectableChannel channel, ActorRef actor) throws Exception {
        if (!(channel instanceof ReadableByteChannel))
            throw new IllegalArgumentException("channel not readable");
        synchronized (gate) {
            if (resolve(actor) == null || !resolve(actor).accepting || stopping || closing)
                throw new IllegalStateException("actor not accepting I/O");
        }
        CompletableFuture<Void> completion = new CompletableFuture<>();
        Runnable request = () -> {
            try {
                if (registrations.containsKey(channel)) throw new IllegalStateException("already registered");
                if (registrations.size() >= MAX_REGISTRATIONS)
                    throw new IllegalStateException("NIO registration limit reached");
                synchronized (gate) {
                    Actor state = resolve(actor);
                    if (state == null || !state.accepting) throw new IllegalStateException("actor stopping");
                }
                channel.configureBlocking(false);
                Registration registration = new Registration(channel, actor);
                registration.key = channel.register(selector, 0, registration);
                registrations.put(channel, registration);
                completion.complete(null);
            } catch (Throwable e) { completion.completeExceptionally(e); }
        };
        synchronized (gate) {
            if (closing || stopping || reactorStopping || !reactor.isAlive())
                throw new IllegalStateException("reactor stopping");
            if (!nioCommands.offer(request))
                throw new IllegalStateException("NIO command queue full");
            wake_reactor();
        }
        completion.get(5, TimeUnit.SECONDS);
    }

    public void cancel_read(SelectableChannel channel) throws Exception {
        CompletableFuture<Void> completion = new CompletableFuture<>();
        synchronized (gate) {
            if (closing || stopping || reactorStopping || !reactor.isAlive())
                throw new IllegalStateException("reactor stopping");
            if (!nioCommands.offer(() -> {
                Registration reg = registrations.remove(channel);
                if (reg != null) detach(reg);
                completion.complete(null);
            })) throw new IllegalStateException("NIO command queue full");
            wake_reactor();
        }
        completion.get(5, TimeUnit.SECONDS);
    }
    private void detach(Registration r) {
        if (r.heldByte != null) {
            r.heldByte = null;
            ioHeld.decrementAndGet();
            ioRejected.incrementAndGet();
        }
        r.key.cancel();
        try { r.channel.close(); } catch (IOException ignored) { ioErrors.incrementAndGet(); }
    }

    private void drain_commands() {
        Runnable task;
        while ((task = nioCommands.poll()) != null) task.run();
    }
    private boolean admitting(Registration r) {
        synchronized (gate) {
            Actor actor = resolve(r.actor);
            return actor != null && actor.accepting && !actor.fenced;
        }
    }
    private boolean has_space(Registration r) {
        synchronized (gate) {
            Actor actor = resolve(r.actor);
            return actor != null && actor.accepting && !actor.fenced &&
                actor.inbox.size() < actor.capacity;
        }
    }
    private void rearm_all() {
        Iterator<Registration> it = registrations.values().iterator();
        while (it.hasNext()) {
            Registration r = it.next();
            if (!admitting(r) || !r.key.isValid()) { detach(r); it.remove(); continue; }
            if (r.heldByte != null && has_space(r)) {
                if (try_send(r.actor, r.heldByte.longValue()) == Admission.ACCEPTED) {
                    r.heldByte = null;
                    ioAdmitted.incrementAndGet();
                    ioHeld.decrementAndGet();
                }
            }
            int interest = r.heldByte == null && has_space(r) ? SelectionKey.OP_READ : 0;
            // An application may close a channel concurrently with readiness rearming.
            // A cancelled key must retire only this registration, not kill the reactor.
            try {
                if (r.key.interestOps() != interest) r.key.interestOps(interest);
            } catch (CancelledKeyException cancelled) {
                detach(r);
                it.remove();
            }
        }
    }
    private void on_read(Registration r) {
        if (r.heldByte != null || !has_space(r)) { r.key.interestOps(0); return; }
        r.buffer.clear();
        int n;
        try { n = ((ReadableByteChannel) r.channel).read(r.buffer); }
        catch (IOException e) {
            ioErrors.incrementAndGet();
            registrations.remove(r.channel); detach(r); return;
        }
        if (n < 0) { ioEof.incrementAndGet(); registrations.remove(r.channel); detach(r); return; }
        if (n == 0) return;
        r.buffer.flip();
        long byteValue = Byte.toUnsignedInt(r.buffer.get());
        Admission outcome = try_send(r.actor, byteValue);
        if (outcome == Admission.ACCEPTED) ioAdmitted.incrementAndGet();
        else if (outcome == Admission.FULL) { // A producer raced readiness.
            r.heldByte = (int) byteValue;
            ioHeld.incrementAndGet();
            r.key.interestOps(0);
        } else {
            // A read racing STOP is explicitly rejected, never reported admitted.
            ioRejected.incrementAndGet();
            registrations.remove(r.channel);
            detach(r);
        }
    }
    private void react() {
        try {
            while (!reactorStopping) {
                drain_commands();
                rearm_all();
                // A previous budgeted pass may have left selected keys behind.
                // Consume those first rather than sleeping for another 250ms.
                if (selector.selectedKeys().isEmpty()) selector.select(250);
                // Selected keys remain in the set across select() calls. They must
                // be drained even when the subsequent select() returns zero;
                // otherwise a >64-ready-key batch can starve its remaining keys.
                Iterator<SelectionKey> it = selector.selectedKeys().iterator();
                int budget = 64;
                while (it.hasNext() && budget-- > 0) {
                    SelectionKey key = it.next();
                    it.remove();
                    try {
                        if (key.isValid() && key.isReadable())
                            on_read((Registration) key.attachment());
                    } catch (CancelledKeyException cancelled) {
                        Registration r = (Registration) key.attachment();
                        if (r != null && registrations.remove(r.channel) == r)
                            detach(r);
                    }
                }
                if (it.hasNext()) selector.wakeup();
            }
        } catch (IOException e) { throw new IllegalStateException("reactor I/O failure", e); }
        finally {
            drain_commands();
            for (Registration r : registrations.values()) detach(r);
            registrations.clear();
            try { selector.close(); } catch (IOException e) { ioErrors.incrementAndGet(); }
        }
    }
    public ReactorSnapshot reactor_snapshot() {
        // Registration count is inspected through a selector-owner command.
        CompletableFuture<Integer> number = new CompletableFuture<>();
        synchronized (gate) {
            if (closing || stopping || reactorStopping || !reactor.isAlive())
                throw new IllegalStateException("reactor stopping");
            if (!nioCommands.offer(() -> number.complete(registrations.size())))
                throw new IllegalStateException("NIO command queue full");
            wake_reactor();
        }
        try {
            return new ReactorSnapshot(ioAdmitted.get(), ioHeld.get(), ioEof.get(),
                ioErrors.get(), wakes.get(), ioRejected.get(),
                number.get(5, TimeUnit.SECONDS));
        } catch (Exception e) { throw new IllegalStateException("reactor unresponsive", e); }
    }

    private void wake_reactor() {
        wakes.incrementAndGet();
        selector.wakeup();
    }

    @Override public synchronized void close() throws Exception {
        if (fullyClosed) return;
        if (Thread.currentThread() == supervisor || Thread.currentThread() == reactor ||
            Thread.currentThread() == watchdog || carriers.contains(Thread.currentThread()))
            throw new IllegalStateException("cannot close runtime on internal thread");
        List<ActorRef> refs;
        synchronized (gate) {
            closing = true; // Fence spawn/send/register before the drain snapshot.
            refs = actors.values().stream().filter(a -> !a.retired).map(a -> a.ref).toList();
        }
        for (ActorRef ref : refs) request_stop(ref);
        for (ActorRef ref : refs) {
            if (!await_terminal(ref, 30_000))
                throw new IllegalStateException("refusing to reclaim live actor " + ref.name());
        }
        synchronized (gate) {
            stopping = true;
            reactorStopping = true;
            gate.notifyAll();
            wake_reactor();
        }
        reactor.join(10_000);
        supervisor.join(10_000);
        watchdog.join(10_000);
        for (Thread worker : carriers) worker.join(10_000);
        if (reactor.isAlive() || supervisor.isAlive() || watchdog.isAlive() ||
            carriers.stream().anyMatch(Thread::isAlive))
            throw new IllegalStateException("runtime threads failed to quiesce");
        fullyClosed = true;
    }
}
