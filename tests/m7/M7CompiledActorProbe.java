// Actual .atto -> Java generated continuation executable conformance, JVM and Native Image.
// The restricted compiler emits M7GeneratedActor from fixtures/m7/cooperative_sum.atto.
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.BooleanSupplier;

public final class M7CompiledActorProbe {
    private static void require(boolean ok, String message) {
        if (!ok) throw new AssertionError(message);
    }
    private static void until(BooleanSupplier predicate, String message) throws Exception {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(20);
        while (!predicate.getAsBoolean() && System.nanoTime() < deadline) Thread.sleep(1);
        require(predicate.getAsBoolean(), "deadline: " + message);
    }
    private static void send(AttoM7CooperativeRuntime rt,
                             AttoM7CooperativeRuntime.ActorRef ref, long value) throws Exception {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(20);
        for (;;) {
            var result = rt.try_send(ref, value);
            if (result == AttoM7CooperativeRuntime.Admission.ACCEPTED) return;
            require(result == AttoM7CooperativeRuntime.Admission.FULL, "unexpected: " + result);
            require(System.nanoTime() < deadline, "send blocked");
            Thread.onSpinWait();
        }
    }
    private static void compiled_sum_yield_resume_and_fairness() throws Exception {
        // One carrier proves a CPU-heavy compiled guest cannot monopolize it when
        // compiler-inserted safe points return a durable PC.
        try (var rt = new AttoM7CooperativeRuntime(1, 16, 4, 200)) {
            var actor = rt.spawn("compiled-sum", 4, M7GeneratedActor::new_frame);
            var neighbor = rt.spawn("compiled-neighbor", 4, M7GeneratedActor::new_frame);
            send(rt, actor, 100_000);
            until(() -> rt.snapshot(actor).yielded() > 5, "compiled frame did not yield");
            var mid = rt.snapshot(actor);
            AttoM7CooperativeRuntime.check_conservation(mid);
            require(mid.inFlight() && (mid.running() || mid.resumePc() >= 0),
                "safe point failed to retain in-flight message/committed PC: " + mid);
            send(rt, neighbor, 12);
            until(() -> rt.snapshot(neighbor).completed() == 1, "neighbor starved");
            var other = rt.snapshot(neighbor);
            require(other.resultSum() == 66 && other.completed() == 1,
                "compiled neighbor produced wrong sum: " + other);
            send(rt, actor, 11);
            require(rt.request_stop(actor), "STOP rejected a compiled actor");
            var afterStop = rt.try_send(actor, 99);
            require(afterStop == AttoM7CooperativeRuntime.Admission.CLOSED ||
                    afterStop == AttoM7CooperativeRuntime.Admission.STALE,
                "STOP failed to fence new DATA: " + afterStop);
            require(rt.await_terminal(actor, 20_000), "compiled actor STOP failed to drain");
            var s = rt.snapshot(actor);
            require(s.accepted() == 2 && s.completed() == 2 && s.failed() == 0 &&
                s.abandoned() == 0 && s.resultSum() == 4_999_950_055L &&
                s.yielded() >= 10_000 && s.maxConcurrentReceives() == 1 &&
                s.controlsDelivered() == 1 &&
                s.lastControl() == AttoM7CooperativeRuntime.ControlKind.STOP,
                "compiled PC resume repeated/skipped effects: " + s);
            require(s.slices() > s.completed(), "no continuation slices happened");
            AttoM7CooperativeRuntime.check_conservation(s);
            require(rt.request_stop(neighbor) && rt.await_terminal(neighbor, 10_000),
                "compiled neighbor did not stop");
            AttoM7CooperativeRuntime.check_conservation(rt.snapshot(neighbor));
            var newIncarnation = rt.spawn("compiled-sum", 2, M7GeneratedActor::new_frame);
            require(newIncarnation.generation() == actor.generation() + 1 &&
                rt.try_send(actor, 5) == AttoM7CooperativeRuntime.Admission.STALE,
                "old frame generation regained authority");
            require(rt.request_stop(newIncarnation) &&
                rt.await_terminal(newIncarnation, 10_000),
                "restarted generation failed to retire");
        }
        System.out.println("M7_COMPILED_ATTO_PC_YIELD_RESUME=PASS");
        System.out.println("M7_COMPILED_ATTO_ACCEPTED_SETTLED=2");
        System.out.println("M7_COMPILED_ACTOR_NEIGHBOR_PROGRESS=PASS");
        System.out.println("M7_COMPILED_CONTINUATION_REINCARNATION_FENCE=PASS");
    }

    private static void four_carrier_serial_fairness() throws Exception {
        try (var rt = new AttoM7CooperativeRuntime(4, 64, 6, 200)) {
            final int count = 32;
            List<AttoM7CooperativeRuntime.ActorRef> actors = new ArrayList<>();
            for (int i = 0; i < count; i++)
                actors.add(rt.spawn("pool-" + i, 4, M7GeneratedActor::new_frame));
            for (var actor : actors) {
                send(rt, actor, 500);
                send(rt, actor, 100);
                send(rt, actor, 10);
                require(rt.request_stop(actor), "pool STOP failed");
            }
            for (var actor : actors) {
                require(rt.await_terminal(actor, 15_000), "pool actor starved");
                var s = rt.snapshot(actor);
                require(s.accepted() == 3 && s.completed() == 3 &&
                    s.resultSum() == 129_745 && s.maxConcurrentReceives() == 1 &&
                    s.controlsDelivered() == 1 && s.yielded() > 0,
                    "pooled actor frame loss/overlap: " + s);
                AttoM7CooperativeRuntime.check_conservation(s);
            }
            var q = rt.queues();
            require(q.ready() == 0 && q.control() == 0 && q.running() == 0,
                "stale actor scheduler entries: " + q);
        }
        System.out.println("M7_SERIAL_32_ACTORS_4_CARRIERS=PASS");
    }

    private static void priority_control_and_panic_conservation() throws Exception {
        try (var rt = new AttoM7CooperativeRuntime(2, 8, 1, 100)) {
            var started = new CountDownLatch(1);
            var release = new CountDownLatch(1);
            var ref = rt.spawn("faulting-frame", 2, value -> new AttoM7CooperativeRuntime.Frame() {
                int pc;
                @Override public int resume_pc() { return pc; }
                @Override public long result() { return 0; }
                @Override public AttoM7CooperativeRuntime.Step advance(
                        AttoM7CooperativeRuntime.Slice slice) throws Exception {
                    if (pc == 0) {
                        started.countDown();
                        require(release.await(10, TimeUnit.SECONDS),
                            "faulting turn did not release");
                        pc = 1;
                        slice.cooperate();
                        return AttoM7CooperativeRuntime.Step.YIELD;
                    }
                    throw new ArithmeticException("guest-style checked fault");
                }
            });
            send(rt, ref, 1);
            require(started.await(5, TimeUnit.SECONDS), "faulting receive not active");
            send(rt, ref, 2);
            send(rt, ref, 3); // capacity=2, second and third queued
            require(rt.try_send(ref, 4) == AttoM7CooperativeRuntime.Admission.FULL,
                "bounded mailbox incorrectly admitted overflow");
            require(rt.request_stop(ref), "priority STOP rejected");
            until(() -> rt.snapshot(ref).controlsDelivered() == 1,
                "STOP not delivered while guest callback active");
            for (int i=0;i<1000;i++) require(rt.request_stop(ref),
                "repeated STOP refused active incarnation");
            release.countDown();
            require(rt.await_terminal(ref, 15_000), "faulting frame not quiescent");
            var s = rt.snapshot(ref);
            require(s.accepted() == 3 && s.completed() == 0 &&
                s.failed() == 1 && s.abandoned() == 2 &&
                s.controlsDelivered() == 2 &&
                s.lastControl() == AttoM7CooperativeRuntime.ControlKind.PANIC &&
                s.maxConcurrentReceives() == 1,
                "STOP->PANIC lost priority or admitted mail: " + s);
            AttoM7CooperativeRuntime.check_conservation(s);
        }
        System.out.println("M7_CONTROL_STOP_PANIC_WHILE_YIELDED=PASS");
        System.out.println("M7_CONTROL_1000_STOP_COALESCED=PASS");
    }

    public static void main(String[] args) throws Exception {
        require(args.length == 0, "no runtime guest source accepted");
        require(M7GeneratedActor.SOURCE_SHA256.matches("[0-9a-f]{64}"),
            "compiled guest source digest missing");
        compiled_sum_yield_resume_and_fairness();
        four_carrier_serial_fairness();
        priority_control_and_panic_conservation();
        M7CallbackIsolationAuditProbe.run();
        M9JavaSupervisorNoticeProbe.run();
        System.out.println("M7_COMPILED_GUEST_SOURCE_SHA256=" + M7GeneratedActor.SOURCE_SHA256);
        System.out.println("M7_RESTRICTED_COMPILED_ATTO_HOST_SCHEDULER=PASS");
        System.out.println("M7_SCOPE=RESTRICTED_STATIC_ATTO_LOOP_COOPERATE_NOT_FULL_COMPILER_OR_HARD_RT");
    }
}
