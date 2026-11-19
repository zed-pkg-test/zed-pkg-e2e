// Executable host-only scheduler stress/over-budget conformance. Not a hard-RT proof.
import java.nio.ByteBuffer;
import java.nio.channels.Pipe;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.BooleanSupplier;

public final class M6ControlBudgetStressProbe {
    private static void require(boolean condition, String reason) {
        if (!condition) throw new AssertionError(reason);
    }
    private static void until(BooleanSupplier check, String label) throws Exception {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(25);
        while (!check.getAsBoolean() && System.nanoTime() < deadline) Thread.sleep(2);
        require(check.getAsBoolean(), "deadline expired: " + label);
    }
    private static void send(AttoM6Runtime rt, AttoM6Runtime.ActorRef ref,
                             long value) throws Exception {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(25);
        int failedAttempts = 0;
        for (;;) {
            var admission = rt.try_send(ref, value);
            if (admission == AttoM6Runtime.Admission.ACCEPTED) return;
            require(admission == AttoM6Runtime.Admission.FULL, "unexpected send: " + admission);
            require(System.nanoTime() < deadline, "send admission starved");
            if ((++failedAttempts & 511) == 0) Thread.yield();
            else Thread.onSpinWait();
        }
    }

    private static void control_idempotence_and_watchdog_during_blocked_turn() throws Exception {
        try (var rt = new AttoM6Runtime(2, 4, 10)) {
            CountDownLatch started = new CountDownLatch(1);
            CountDownLatch release = new CountDownLatch(1);
            AtomicLong sum = new AtomicLong();
            var blocked = rt.spawn("budget-blocked", 8, value -> {
                started.countDown();
                require(release.await(10, TimeUnit.SECONDS), "bounded test turn timeout");
                sum.addAndGet(value);
            });
            AtomicInteger neighbor = new AtomicInteger();
            var healthy = rt.spawn("budget-neighbor", 16, value -> neighbor.incrementAndGet());
            send(rt, blocked, 1);
            require(started.await(5, TimeUnit.SECONDS), "blocked callback not started");
            send(rt, blocked, 2);
            send(rt, blocked, 3);
            CountDownLatch begin = new CountDownLatch(1);
            List<Thread> stoppers = new ArrayList<>();
            AtomicReference<Throwable> failure = new AtomicReference<>();
            for (int n = 0; n < 4; n++) {
                Thread thread = new Thread(() -> {
                    try {
                        require(begin.await(5, TimeUnit.SECONDS), "stop flood start");
                        for (int i = 0; i < 500; i++)
                            require(rt.request_stop(blocked), "live STOP request failed");
                    } catch (Throwable e) { failure.compareAndSet(null, e); }
                }, "stop-flood-" + n);
                stoppers.add(thread);
                thread.start();
            }
            begin.countDown();
            until(() -> rt.snapshot(blocked).controlsDelivered() == 1,
                "priority STOP delivery while first turn active");
            until(() -> rt.budget_snapshot(blocked).watchdogNotices() == 1,
                "live turn watchdog notice before callback returns");
            require(rt.budget_snapshot(blocked).running(),
                "watchdog only fired after turn returned");
            send(rt, healthy, 17);
            until(() -> neighbor.get() == 1, "healthy neighbor under blocked callback");
            for (Thread stopper : stoppers) stopper.join(10_000);
            require(stoppers.stream().noneMatch(Thread::isAlive),
                "stoppers stuck on CONTROL flood");
            require(failure.get() == null, "STOP admission race: " + failure.get());
            var pending = rt.scheduler_snapshot();
            require(pending.controlDepth() == 0 && pending.readyDepth() <= 4 &&
                pending.registeredActors() <= 4,
                "unbounded terminal control work: " + pending);
            require(rt.try_send(blocked, 9) == AttoM6Runtime.Admission.CLOSED,
                "STOP failed to fence new DATA");
            release.countDown();
            require(rt.await_terminal(blocked, 10_000),
                "graceful STOP did not drain accepted DATA");
            var done = rt.snapshot(blocked);
            var budget = rt.budget_snapshot(blocked);
            require(done.controlsDelivered() == 1 &&
                done.lastControl() == AttoM6Runtime.ControlKind.STOP,
                "duplicate STOP notices under flood: " + done);
            require(done.accepted() == 3 && done.completed() == 3 &&
                done.abandoned() == 0 && sum.get() == 6,
                "graceful drain lost work: " + done);
            require(budget.watchdogNotices() == 1 && budget.overBudgetTurns() >= 1 &&
                budget.maxElapsedNanos() >= TimeUnit.MILLISECONDS.toNanos(10),
                "over-budget callback not tracked exactly once: " + budget);
            AttoM6Runtime.check_conservation(done);
            System.out.println("M6_BUDGET_WATCHDOG_LIVE_NOTICE=PASS");
            System.out.println("M6_CONTROL_DUPLICATE_STOP_2000_COALESCED=PASS");
            System.out.println("M6_BUDGET_NEIGHBOR_PROGRESS=PASS");
        }
    }

    private static void delivered_stop_escalates_to_panic() throws Exception {
        try (var rt = new AttoM6Runtime(2, 4, 20)) {
            CountDownLatch started = new CountDownLatch(1);
            CountDownLatch release = new CountDownLatch(1);
            var actor = rt.spawn("stop-then-panic", 3, value -> {
                if (value == 1) {
                    started.countDown();
                    require(release.await(10, TimeUnit.SECONDS), "release first receive");
                }
                if (value == -1) throw new IllegalArgumentException("forced panic");
            });
            send(rt, actor, 1);
            require(started.await(5, TimeUnit.SECONDS), "first receive not running");
            send(rt, actor, -1);
            send(rt, actor, 7);
            require(rt.request_stop(actor), "first STOP refused");
            until(() -> rt.snapshot(actor).controlsDelivered() == 1,
                "STOP did not reach supervisor before PANIC");
            require(rt.snapshot(actor).lastControl() == AttoM6Runtime.ControlKind.STOP,
                "wrong original CONTROL");
            release.countDown();
            require(rt.await_terminal(actor, 10_000),
                "PANIC did not retire actor after failed accepted turn");
            var s = rt.snapshot(actor);
            require(s.controlsDelivered() == 2 &&
                s.lastControl() == AttoM6Runtime.ControlKind.PANIC &&
                s.accepted() == 3 && s.completed() == 1 &&
                s.failed() == 1 && s.abandoned() == 1,
                "PANIC did not escalate delivered STOP exactly once: " + s);
            require(rt.scheduler_snapshot().readyDepth() == 0,
                "retired PANIC actor left a stale ready queue entry");
            AttoM6Runtime.check_conservation(s);
            System.out.println("M6_CONTROL_STOP_TO_PANIC_ESCALATION=PASS");
        }
    }

    private static void mpsc_stress_and_neighbor_fairness() throws Exception {
        try (var rt = new AttoM6Runtime(4, 8, 100)) {
            AtomicInteger active = new AtomicInteger();
            AtomicInteger maxActive = new AtomicInteger();
            AtomicLong completed = new AtomicLong();
            AtomicLong neighborCompleted = new AtomicLong();
            var main = rt.spawn("stress-main", 64, value -> {
                int count = active.incrementAndGet();
                maxActive.accumulateAndGet(count, Math::max);
                try {
                    require(value == 1, "corrupt stress payload");
                    completed.incrementAndGet();
                } finally { active.decrementAndGet(); }
            });
            var neighbor = rt.spawn("stress-neighbor", 32, value -> {
                require(value == 2, "wrong neighbor payload");
                neighborCompleted.incrementAndGet();
            });
            List<Thread> producers = new ArrayList<>();
            AtomicReference<Throwable> error = new AtomicReference<>();
            CountDownLatch begin = new CountDownLatch(1);
            for (int n = 0; n < 4; n++) {
                Thread producer = new Thread(() -> {
                    try {
                        require(begin.await(10, TimeUnit.SECONDS), "producer start expired");
                        for (int i = 0; i < 25_000; i++) send(rt, main, 1);
                    } catch (Throwable e) { error.compareAndSet(null, e); }
                }, "stress-producer-" + n);
                producers.add(producer); producer.start();
            }
            begin.countDown();
            for (int i = 0; i < 2_000; i++) send(rt, neighbor, 2);
            for (Thread producer : producers) producer.join(30_000);
            require(producers.stream().noneMatch(Thread::isAlive),
                "producer did not retire after 100k turns");
            require(error.get() == null, "producer failed: " + error.get());
            until(() -> rt.snapshot(main).completed() == 100_000,
                "100k MPSC mail conservation");
            until(() -> rt.snapshot(neighbor).completed() == 2_000,
                "healthy tenant fairness under hot producer load");
            require(completed.get() == 100_000 && neighborCompleted.get() == 2_000 &&
                maxActive.get() == 1,
                "hot mailbox lost/duplicated mail or serial actor overlapped");
            require(rt.request_stop(main) && rt.await_terminal(main, 10_000),
                "MPSC actor did not retire");
            require(rt.request_stop(neighbor) && rt.await_terminal(neighbor, 10_000),
                "MPSC neighbor did not retire");
            AttoM6Runtime.check_conservation(rt.snapshot(main));
            AttoM6Runtime.check_conservation(rt.snapshot(neighbor));
            var scheduler = rt.scheduler_snapshot();
            require(scheduler.readyDepth() == 0 && scheduler.controlDepth() == 0 &&
                scheduler.activeReceives() == 0, "scheduler retained stale tasks");
            System.out.println("M6_STRESS_MPSC_ACCEPTED_SETTLED=100000");
            System.out.println("M6_STRESS_NEIGHBOR_ACCEPTED_SETTLED=2000");
            System.out.println("M6_STRESS_SERIAL_MAX_RECEIVES=1");
        }
    }

    private static void readiness_vs_stop_with_generation_fencing() throws Exception {
        try (var rt = new AttoM6Runtime(4, 96, 100)) {
            final int rounds = 48;
            List<Pipe> pipes = new ArrayList<>();
            List<AttoM6Runtime.ActorRef> refs = new ArrayList<>();
            List<Thread> writers = new ArrayList<>();
            AtomicReference<Throwable> failure = new AtomicReference<>();
            CountDownLatch begin = new CountDownLatch(1);
            try {
                for (int i = 0; i < rounds; i++) {
                    var ref = rt.spawn("race-" + i, 1, value -> {
                        require(value == 42, "wrong race byte");
                    });
                    Pipe pipe = Pipe.open();
                    pipes.add(pipe); refs.add(ref);
                    rt.register_read(pipe.source(), ref);
                    Thread writer = new Thread(() -> {
                        try {
                            require(begin.await(10, TimeUnit.SECONDS), "race start");
                            pipe.sink().write(ByteBuffer.wrap(new byte[]{42}));
                        } catch (java.io.IOException expected) {
                            // A racing STOP may close the reader before the write.
                        } catch (Throwable e) { failure.compareAndSet(null, e); }
                    }, "write-vs-stop-" + i);
                    writers.add(writer); writer.start();
                }
                begin.countDown();
                for (var ref : refs) require(rt.request_stop(ref), "STOP rejected");
                for (Thread writer : writers) writer.join(10_000);
                require(writers.stream().noneMatch(Thread::isAlive), "racing writer hung");
                require(failure.get() == null, "unexpected writer error: " + failure.get());
                for (var ref : refs) {
                    require(rt.await_terminal(ref, 10_000), "race actor did not retire");
                    var s = rt.snapshot(ref);
                    AttoM6Runtime.check_conservation(s);
                    require(s.accepted() <= 1 && s.failed() == 0 &&
                        rt.try_send(ref, 1) == AttoM6Runtime.Admission.STALE,
                        "STOP admitted multiple bytes or stale mail");
                }
                until(() -> rt.reactor_snapshot().registrations() == 0,
                    "all STOP-fenced NIO registrations retired");
                require(rt.reactor_snapshot().bytesHeld() == 0, "held bytes leaked");
            } finally {
                begin.countDown();
                for (Thread writer : writers) writer.join(10_000);
                for (Pipe pipe : pipes) {
                    try { pipe.source().close(); } catch (Exception ignored) {}
                    try { pipe.sink().close(); } catch (Exception ignored) {}
                }
            }
        }
        System.out.println("M6_STRESS_READINESS_STOP_RACES=48");
    }

    public static void run() throws Exception {
        control_idempotence_and_watchdog_during_blocked_turn();
        delivered_stop_escalates_to_panic();
        mpsc_stress_and_neighbor_fairness();
        readiness_vs_stop_with_generation_fencing();
    }
}
