// M7 host-side cooperative scheduler with a closed-world compiled-frame ABI.
// A frame yields ONLY at compiler-inserted safe points. Never claims forced preemption,
// arbitrary guest borrow safety, or hard-real-time scheduling guarantees.
import java.util.ArrayDeque;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.List;
import java.util.ArrayList;
import java.util.concurrent.TimeUnit;

public final class AttoM7CooperativeRuntime implements AutoCloseable {
    public enum Admission { ACCEPTED, FULL, CLOSED, STALE }
    public enum Step { YIELD, DONE }
    public enum ControlKind { STOP, PANIC }
    @FunctionalInterface public interface FrameFactory { Frame new_frame(long value); }
    public interface Frame {
        Step advance(Slice slice) throws Exception;
        long result();
        int resume_pc();
    }
    public static final class Slice {
        private final int maxReductions;
        private final long deadline;
        private int charged;
        Slice(int maxReductions, long maxNanos) {
            this.maxReductions = maxReductions;
            this.deadline = System.nanoTime() + maxNanos;
        }
        // Called at a compiler-verified safe point AFTER committing live locals.
        public boolean cooperate() {
            if (charged == maxReductions)
                throw new IllegalStateException("safe-point reductions exceeded");
            charged++;
            return charged >= maxReductions || System.nanoTime() - deadline >= 0;
        }
        public int charged_reductions() { return charged; }
    }
    // Record equality is NOT authority. Resolve by object identity plus generation.
    public record ActorRef(String name, long generation) {}
    public record Snapshot(long accepted, long completed, long failed, long abandoned,
                           long yielded, long slices, long resultSum, int queued,
                           boolean inFlight, boolean running, boolean accepting,
                           boolean retired, int controlsDelivered, ControlKind lastControl,
                           int maxConcurrentReceives, int carrierIdentities, int resumePc) {}
    public record Queues(int ready, int control, int actors, int running) {}
    // Host-level receipt: emitted only by the supervisor priority CONTROL path.
    // Fault code 1=checked arithmetic, 101=generic guest/host panic.
    public record SupervisorNotice(ControlKind kind, long actorId, String actorName,
          long generation, long supervisorId, long messageId, int faultCode,
          String reason) {
        // Canonical M9 notice wire: exact JSON key order matches the Rust/Wasm
        // host. Preserve the name for diagnostic use, not as wire identity.
        public String to_wire_json() {
            if (kind == null || actorId < 0 || actorId > MAX_WIRE_ID ||
                generation <= 0 || generation > MAX_WIRE_ID ||
                supervisorId < 0 || supervisorId > MAX_WIRE_ID ||
                messageId < 0 || messageId > MAX_WIRE_ID ||
                reason == null || reason.isEmpty() ||
                reason.getBytes(java.nio.charset.StandardCharsets.UTF_8).length > 128 ||
                (kind == ControlKind.STOP && (messageId != 0 || faultCode != 0)) ||
                (kind == ControlKind.PANIC &&
                    faultCode != 1 && faultCode != 2 && faultCode != 100 &&
                    faultCode != 101))
                throw new IllegalArgumentException("invalid canonical M9 supervisor notice");
            return "{\"kind\":\"" + kind.name() + "\",\"actor_id\":" + actorId +
                ",\"generation\":" + generation + ",\"supervisor_id\":" + supervisorId +
                ",\"message_id\":" + messageId + ",\"fault_code\":" + faultCode +
                ",\"reason\":" + quote_json(reason) + "}";
        }
        private static String quote_json(String value) {
            var b = new StringBuilder("\"");
            for (int i = 0; i < value.length(); i++) {
                char ch = value.charAt(i);
                switch (ch) {
                    case '"' -> b.append("\\\"");
                    case '\\' -> b.append("\\\\");
                    case '\n' -> b.append("\\n");
                    case '\r' -> b.append("\\r");
                    case '\t' -> b.append("\\t");
                    case '\b' -> b.append("\\b");
                    case '\f' -> b.append("\\f");
                    default -> {
                        if (ch < 0x20) {
                            b.append("\\u");
                            for (int shift = 12; shift >= 0; shift -= 4)
                                b.append(Character.forDigit((ch >> shift) & 0xf, 16));
                        } else b.append(ch);
                    }
                }
            }
            return b.append('"').toString();
        }
    }
    private record Mail(long messageId, long value) {}
    private static final long MAX_WIRE_ID = 9_007_199_254_740_991L;

    private static final class Actor {
        final ActorRef ref;
        final long actorId;
        final FrameFactory factory;
        final int capacity;
        final long supervisorId;
        final ArrayDeque<Mail> inbox = new ArrayDeque<>();
        long nextMessageId = 1L;
        long activeMessageId, lastFailedMessageId;
        int lastFaultCode;
        String lastFaultReason = "";
        final Set<Long> carriers = new HashSet<>();
        Frame active;
        // An accepted message remains in flight during frame construction and YIELD.
        // Never infer in-flight status solely from the nullable active frame.
        boolean inFlight, running, scheduled, accepting = true, retired, fenced;
        int resumePc = -1;
        boolean controlQueued;
        ControlKind pendingControl, lastControl;
        long accepted, completed, failed, abandoned, yielded, slices, resultSum;
        int concurrent, maxConcurrent, controlsDelivered;
        Actor(ActorRef ref, long actorId, int capacity, FrameFactory factory,
              long supervisorId) {
            this.ref = ref; this.actorId = actorId;
            this.capacity = capacity; this.factory = factory;
            this.supervisorId = supervisorId;
        }
    }

    private final Object gate = new Object();
    private long nextActorId; // Monotonic within runtime, never reused on reincarnation.
    private final Map<String, Actor> actors = new HashMap<>();
    private final Map<String, Long> generations = new HashMap<>();
    private final ArrayDeque<Actor> ready = new ArrayDeque<>();
    private final ArrayDeque<Actor> controls = new ArrayDeque<>();
    // Supervisor-facing, independent from DATA inboxes and scheduler ready queue.
    private final ArrayDeque<SupervisorNotice> supervisorNotices = new ArrayDeque<>();
    private final List<Thread> carriers = new ArrayList<>();
    private final Thread supervisor;
    private final int maxActors, reductionBudget;
    private final long nanoBudget;
    private boolean closing, stopping, fullyClosed;

    public AttoM7CooperativeRuntime(int carrierCount, int maxActors,
                                    int reductionBudget, long sliceMillis) {
        if (carrierCount < 1 || carrierCount > 32 || maxActors < 1 || maxActors > 256 ||
            reductionBudget < 1 || reductionBudget > 65536 ||
            sliceMillis < 1 || sliceMillis > 60_000)
            throw new IllegalArgumentException("bounded carrier, actor and slice budgets required");
        this.maxActors = maxActors;
        this.reductionBudget = reductionBudget;
        this.nanoBudget = TimeUnit.MILLISECONDS.toNanos(sliceMillis);
        this.supervisor = new Thread(this::supervise, "atto-m7-CONTROL");
        supervisor.start();
        for (int i = 0; i < carrierCount; i++) {
            Thread thread = new Thread(this::carry, "atto-m7-carrier-" + i);
            carriers.add(thread);
            thread.start();
        }
    }

    public ActorRef spawn(String name, int capacity, FrameFactory factory) {
        return spawn(name, capacity, factory, 0L);
    }
    public ActorRef spawn(String name, int capacity, FrameFactory factory,
                          long supervisorId) {
        if (name == null || name.isEmpty() || name.length() > 128 || factory == null ||
            capacity < 1 || capacity > 4096 || supervisorId < 0 ||
            supervisorId > MAX_WIRE_ID)
            throw new IllegalArgumentException("invalid actor");
        synchronized (gate) {
            if (closing || stopping) throw new IllegalStateException("runtime closed");
            Actor previous = actors.get(name);
            if (previous != null && !previous.retired)
                throw new IllegalStateException("actor name already active");
            if (previous == null && actors.size() >= maxActors)
                throw new IllegalStateException("actor registration limit");
            long generation = Math.addExact(generations.getOrDefault(name, 0L), 1L);
            if (generation > MAX_WIRE_ID || nextActorId > MAX_WIRE_ID)
                throw new IllegalStateException("M9 actor wire identity exhausted");
            long actorId = nextActorId++;
            generations.put(name, generation);
            var ref = new ActorRef(name, generation);
            actors.put(name, new Actor(ref, actorId, capacity, factory, supervisorId));
            return ref;
        }
    }
    private Actor resolve(ActorRef ref) {
        if (ref == null) return null;
        Actor actor = actors.get(ref.name());
        return actor != null && actor.ref == ref && !actor.retired ? actor : null;
    }
    public Admission try_send(ActorRef ref, long value) {
        synchronized (gate) {
            Actor actor = resolve(ref);
            if (actor == null) return Admission.STALE;
            if (closing || stopping || !actor.accepting || actor.fenced) return Admission.CLOSED;
            if (actor.inbox.size() >= actor.capacity) return Admission.FULL;
            long id = actor.nextMessageId;
            if (id > MAX_WIRE_ID) {
                actor.accepting = false;
                control(actor, ControlKind.STOP);
                return Admission.CLOSED;
            }
            actor.nextMessageId = id + 1L;
            actor.inbox.addLast(new Mail(id, value));
            actor.accepted++;
            schedule(actor);
            return Admission.ACCEPTED;
        }
    }
    private void schedule(Actor actor) {
        if (!actor.scheduled && !actor.running && !actor.fenced &&
            ((actor.inFlight && actor.active != null) ||
             (!actor.inFlight && !actor.inbox.isEmpty()))) {
            actor.scheduled = true;
            ready.addLast(actor);
            gate.notifyAll();
        }
    }
    private void control(Actor actor, ControlKind kind) {
        if (actor.controlQueued) {
            if (kind == ControlKind.PANIC) actor.pendingControl = kind;
            return;
        }
        if (actor.lastControl == ControlKind.PANIC ||
            (actor.lastControl == ControlKind.STOP && kind == ControlKind.STOP))
            return;
        actor.controlQueued = true;
        actor.pendingControl = kind;
        controls.addLast(actor);
        gate.notifyAll();
    }
    public boolean request_stop(ActorRef ref) {
        synchronized (gate) {
            Actor actor = resolve(ref);
            if (actor == null) return false;
            actor.accepting = false;
            control(actor, ControlKind.STOP);
            gate.notifyAll();
            return true;
        }
    }
    // Asynchronous fatal CONTROL: reject new sends immediately. Running guest
    // code is NEVER interrupted or reclaimed; a yielded frame is invalidated
    // by the supervisor after it has been physically released by its carrier.
    public boolean request_panic(ActorRef ref) {
        synchronized (gate) {
            Actor actor = resolve(ref);
            if (actor == null) return false;
            actor.accepting = false;
            actor.fenced = true;
            actor.lastFailedMessageId = actor.activeMessageId;
            actor.lastFaultCode = 101;
            actor.lastFaultReason = "host requested panic";
            control(actor, ControlKind.PANIC);
            gate.notifyAll();
            return true;
        }
    }
    private void retire_if_quiescent(Actor actor) {
        if (!actor.accepting && !actor.inFlight && !actor.running &&
            actor.inbox.isEmpty() && !actor.controlQueued) {
            actor.retired = true;
            if (actor.scheduled) ready.remove(actor);
            actor.scheduled = false;
            gate.notifyAll();
        }
    }
    private void supervise() {
        try {
            synchronized (gate) {
                for (;;) {
                    while (controls.isEmpty() && !stopping) gate.wait();
                    if (controls.isEmpty() && stopping) return;
                    Actor actor = controls.removeFirst();
                    ControlKind kind = actor.pendingControl;
                    actor.pendingControl = null;
                    actor.controlQueued = false;
                    actor.controlsDelivered++;
                    actor.lastControl = kind;
                    long noticeId = kind == ControlKind.PANIC
                        ? (actor.lastFailedMessageId != 0L
                            ? actor.lastFailedMessageId : actor.activeMessageId)
                        : 0L;
                    int noticeFault = kind == ControlKind.PANIC
                        ? (actor.lastFaultCode == 0 ? 101 : actor.lastFaultCode) : 0;
                    String noticeReason = kind == ControlKind.PANIC
                        ? (actor.lastFaultReason.isEmpty()
                            ? "actor failed" : actor.lastFaultReason)
                        : "graceful stop";
                    supervisorNotices.addLast(new SupervisorNotice(kind,
                        actor.actorId, actor.ref.name(), actor.ref.generation(),
                        actor.supervisorId, noticeId, noticeFault, noticeReason));
                    if (kind == ControlKind.PANIC) {
                        actor.fenced = true;
                        actor.accepting = false;
                        actor.abandoned += actor.inbox.size();
                        actor.inbox.clear();
                        // A currently running callback is allowed to physically quiesce.
                        // A yielded frame is safe to abandon here (no carrier owns it).
                        if (!actor.running && actor.inFlight) {
                            actor.abandoned++;
                            actor.inFlight = false;
                            actor.active = null;
                            actor.resumePc = -1;
                        }
                    }
                    if (!actor.fenced) schedule(actor);
                    retire_if_quiescent(actor);
                    gate.notifyAll();
                }
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            synchronized (gate) {
                if (!stopping) throw new AssertionError("M7 supervisor interrupted", e);
            }
        }
    }
    private void carry() {
        try {
            while (true) {
                Actor actor;
                Frame frame = null;
                boolean create = false;
                long message = 0L;
                synchronized (gate) {
                    for (;;) {
                        if (stopping && ready.isEmpty() && controls.isEmpty()) return;
                        // The CONTROL loop must finish before any DATA resume/start.
                        if (!controls.isEmpty() || ready.isEmpty()) {
                            gate.wait(); continue;
                        }
                        actor = ready.removeFirst();
                        actor.scheduled = false;
                        if (actor.fenced || actor.retired || actor.running) continue;
                        if (!actor.inFlight) {
                            if (actor.inbox.isEmpty()) continue;
                            Mail mail = actor.inbox.removeFirst();
                            message = mail.value();
                            actor.activeMessageId = mail.messageId();
                            actor.inFlight = true; // Reserve BEFORE calling guest factory.
                            create = true;
                        } else {
                            if (actor.active == null) continue;
                            frame = actor.active;
                        }
                        actor.running = true;
                        actor.concurrent++;
                        actor.maxConcurrent = Math.max(actor.maxConcurrent, actor.concurrent);
                        actor.carriers.add(Thread.currentThread().threadId());
                        break;
                    }
                }

                // Absolutely no guest callback executes under the global scheduler lock:
                // factory.new_frame(), frame.advance(), frame.result(), frame.resume_pc().
                Throwable failure = null;
                Step result = null;
                long value = 0L;
                int pc = -1;
                var slice = new Slice(reductionBudget, nanoBudget);
                if (create) {
                    try {
                        frame = actor.factory.new_frame(message);
                        if (frame == null)
                            throw new IllegalStateException("compiled frame factory returned null");
                    } catch (Throwable e) {
                        failure = e;
                    }
                }
                boolean alreadyFenced;
                synchronized (gate) {
                    alreadyFenced = actor.fenced;
                }
                if (failure == null && !alreadyFenced) {
                    try {
                        result = frame.advance(slice);
                        if (result == Step.YIELD) {
                            if (slice.charged_reductions() == 0)
                                throw new IllegalStateException("YIELD without safe-point charge");
                            pc = frame.resume_pc();
                            if (pc < 0 || pc > 4096)
                                throw new IllegalStateException("invalid committed continuation PC");
                        } else if (result == Step.DONE) {
                            value = frame.result();
                        } else {
                            throw new IllegalStateException("invalid continuation outcome");
                        }
                    } catch (Throwable e) {
                        failure = e;
                    }
                }

                synchronized (gate) {
                    actor.running = false;
                    actor.concurrent--;
                    actor.slices++;
                    if (failure != null) {
                        actor.lastFailedMessageId = actor.activeMessageId;
                        actor.lastFaultCode = failure instanceof ArithmeticException ? 1 : 101;
                        actor.lastFaultReason = actor.lastFaultCode == 1
                            ? "checked arithmetic" : "guest callback failure";
                        // Factory / guest / result / PC failure settles exactly the
                        // one reserved message, even if PANIC was already delivered.
                        actor.failed++;
                        actor.inFlight = false;
                        actor.activeMessageId = 0L;
                        actor.active = null;
                        actor.resumePc = -1;
                        actor.accepting = false;
                        actor.fenced = true;
                        actor.abandoned += actor.inbox.size();
                        actor.inbox.clear();
                        control(actor, ControlKind.PANIC);
                    } else if (actor.fenced) {
                        // PANIC raced a physically running callback. It has now
                        // quiesced; never resume a formerly yielded continuation.
                        actor.abandoned++;
                        actor.inFlight = false;
                        actor.activeMessageId = 0L;
                        actor.active = null;
                        actor.resumePc = -1;
                    } else if (result == Step.YIELD) {
                        // Same accepted message, no new mailbox admission/settlement.
                        actor.active = frame;
                        actor.resumePc = pc;
                        actor.yielded++;
                    } else {
                        try {
                            actor.resultSum = Math.addExact(actor.resultSum, value);
                            actor.completed++;
                        } catch (ArithmeticException error) {
                            actor.lastFailedMessageId = actor.activeMessageId;
                            actor.lastFaultCode = 1;
                            actor.lastFaultReason = "checked arithmetic";
                            actor.failed++;
                            actor.accepting = false;
                            actor.fenced = true;
                            actor.abandoned += actor.inbox.size();
                            actor.inbox.clear();
                            control(actor, ControlKind.PANIC);
                        }
                        actor.inFlight = false;
                        actor.activeMessageId = 0L;
                        actor.active = null;
                        actor.resumePc = -1;
                    }
                    if (!actor.fenced) schedule(actor);
                    retire_if_quiescent(actor);
                    gate.notifyAll();
                }
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            synchronized (gate) {
                if (!stopping) throw new AssertionError("M7 carrier interrupted", e);
            }
        }
    }
    // Numeric actor ID is for supervisor notice wire equality. The ActorRef
    // object, not its numeric fields, remains the only mailbox authority.
    public long actor_id(ActorRef ref) {
        synchronized (gate) {
            if (ref == null) throw new IllegalArgumentException("null actor ref");
            Actor actor = actors.get(ref.name());
            if (actor == null || actor.ref != ref)
                throw new IllegalArgumentException("stale actor identity");
            return actor.actorId;
        }
    }
    public SupervisorNotice poll_supervisor_notice() {
        synchronized (gate) {
            return supervisorNotices.pollFirst();
        }
    }
    public SupervisorNotice await_supervisor_notice(long timeoutMillis)
            throws InterruptedException {
        if (timeoutMillis < 1 || timeoutMillis > 60_000)
            throw new IllegalArgumentException("invalid notice deadline");
        long deadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMillis);
        synchronized (gate) {
            while (supervisorNotices.isEmpty()) {
                long remaining = deadline - System.nanoTime();
                if (remaining <= 0) return null;
                TimeUnit.NANOSECONDS.timedWait(gate, remaining);
            }
            return supervisorNotices.removeFirst();
        }
    }
    public Snapshot snapshot(ActorRef ref) {
        synchronized (gate) {
            if (ref == null) throw new IllegalArgumentException("null actor ref");
            Actor actor = actors.get(ref.name());
            if (actor == null || actor.ref != ref)
                throw new IllegalArgumentException("stale actor handle");
            return new Snapshot(actor.accepted, actor.completed, actor.failed,
                actor.abandoned, actor.yielded, actor.slices, actor.resultSum,
                actor.inbox.size(), actor.inFlight, actor.running,
                actor.accepting, actor.retired, actor.controlsDelivered,
                actor.lastControl, actor.maxConcurrent, actor.carriers.size(),
                actor.running ? -2 : actor.resumePc);
        }
    }
    public Queues queues() {
        synchronized (gate) {
            int active = 0;
            for (Actor actor : actors.values()) if (actor.running) active++;
            return new Queues(ready.size(), controls.size(), actors.size(), active);
        }
    }
    public static void check_conservation(Snapshot s) {
        if (s.accepted() != s.completed() + s.failed() + s.abandoned() +
            s.queued() + (s.inFlight() ? 1L : 0L))
            throw new AssertionError("M7 accepted DATA conservation failed: " + s);
    }
    public boolean await_terminal(ActorRef ref, long timeoutMillis)
            throws InterruptedException {
        long deadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMillis);
        synchronized (gate) {
            while (true) {
                if (snapshot(ref).retired()) return true;
                long remaining = deadline - System.nanoTime();
                if (remaining <= 0) return false;
                TimeUnit.NANOSECONDS.timedWait(gate, remaining);
            }
        }
    }
    @Override public synchronized void close() throws Exception {
        if (fullyClosed) return;
        if (Thread.currentThread() == supervisor || carriers.contains(Thread.currentThread()))
            throw new IllegalStateException("cannot synchronously close on internal thread");
        List<ActorRef> live = new ArrayList<>();
        synchronized (gate) {
            closing = true;
            for (Actor actor : actors.values()) if (!actor.retired) live.add(actor.ref);
            for (ActorRef ref : live) request_stop(ref);
        }
        for (ActorRef ref : live)
            if (!await_terminal(ref, 30_000))
                throw new IllegalStateException("refusing to reclaim live frame " + ref.name());
        synchronized (gate) { stopping = true; gate.notifyAll(); }
        supervisor.join(10_000);
        for (Thread carrier : carriers) carrier.join(10_000);
        if (supervisor.isAlive() || carriers.stream().anyMatch(Thread::isAlive))
            throw new IllegalStateException("M7 runtime threads not physically quiesced");
        fullyClosed = true;
    }
}
