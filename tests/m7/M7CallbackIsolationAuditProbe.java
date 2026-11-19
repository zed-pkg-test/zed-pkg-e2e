// M7 adversarial checks for scheduler-global lock isolation and PANIC quiescence.
// Actual Java carriers and thread interleavings; a bounded host probe, not hard-RT proof.
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.BooleanSupplier;

public final class M7CallbackIsolationAuditProbe {
    private static void require(boolean ok, String why) {
        if (!ok) throw new AssertionError(why);
    }
    private static void until(BooleanSupplier predicate, String what) throws Exception {
        long end = System.nanoTime() + TimeUnit.SECONDS.toNanos(12);
        while (!predicate.getAsBoolean() && System.nanoTime() < end) Thread.sleep(1);
        require(predicate.getAsBoolean(), "deadline: " + what);
    }
    private static void fast(long startNanos, String why) {
        require(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - startNanos) < 1500,
            "global lock blocked behind guest callback: " + why);
    }
    private static AttoM7CooperativeRuntime.Frame done_frame(long value) {
        return new AttoM7CooperativeRuntime.Frame() {
            private int pc;
            @Override public AttoM7CooperativeRuntime.Step advance(
                    AttoM7CooperativeRuntime.Slice slice) {
                pc = 1;
                return AttoM7CooperativeRuntime.Step.DONE;
            }
            @Override public long result() { return value; }
            @Override public int resume_pc() { return pc; }
        };
    }

    private static void blocked_factory_cannot_hold_global_scheduler_lock() throws Exception {
        try (var rt = new AttoM7CooperativeRuntime(2, 4, 4, 100)) {
            var entered = new CountDownLatch(1);
            var release = new CountDownLatch(1);
            AtomicInteger factoryCalls = new AtomicInteger();
            var blocked = rt.spawn("slow-factory", 2, value -> {
                if (factoryCalls.incrementAndGet() == 1) {
                    entered.countDown();
                    try {
                        require(release.await(10, TimeUnit.SECONDS), "factory gate deadline");
                    } catch (InterruptedException e) {
                        Thread.currentThread().interrupt();
                        throw new AssertionError(e);
                    }
                }
                return done_frame(value);
            });
            try {
                require(rt.try_send(blocked, 7) == AttoM7CooperativeRuntime.Admission.ACCEPTED,
                    "first send not accepted");
                require(entered.await(5, TimeUnit.SECONDS), "factory did not begin");
                long start = System.nanoTime();
                var mid = rt.snapshot(blocked);
                AttoM7CooperativeRuntime.check_conservation(mid);
                require(mid.inFlight() && mid.running() && mid.queued() == 0,
                    "factory has not reserved the accepted in-flight message: " + mid);
                require(rt.try_send(blocked, 3) == AttoM7CooperativeRuntime.Admission.ACCEPTED,
                    "second send starved while factory blocked");
                var neighbor = rt.spawn("factory-neighbor", 2,
                    M7CallbackIsolationAuditProbe::done_frame);
                require(rt.try_send(neighbor, 41) ==
                    AttoM7CooperativeRuntime.Admission.ACCEPTED,
                    "neighbor admission blocked by guest factory");
                fast(start, "factory snapshot/admission/spawn");
                until(() -> rt.snapshot(neighbor).completed() == 1,
                    "neighbor progress during blocked factory");
                start = System.nanoTime();
                require(rt.request_stop(blocked), "STOP blocked by factory");
                fast(start, "factory STOP");
                until(() -> rt.snapshot(blocked).controlsDelivered() == 1,
                    "CONTROL could not run while factory blocked");
                require(rt.try_send(blocked, 99) !=
                    AttoM7CooperativeRuntime.Admission.ACCEPTED,
                    "STOP admitted DATA into blocked factory");
                release.countDown();
                require(rt.await_terminal(blocked, 10_000),
                    "factory actor failed to drain accepted messages");
                var s = rt.snapshot(blocked);
                require(s.accepted() == 2 && s.completed() == 2 &&
                    s.resultSum() == 10 && s.abandoned() == 0 &&
                    s.maxConcurrentReceives() == 1, "factory accounting: " + s);
                AttoM7CooperativeRuntime.check_conservation(s);
                require(rt.request_stop(neighbor) && rt.await_terminal(neighbor, 10_000),
                    "healthy neighbor STOP failed");
            } finally {
                release.countDown();
            }
        }
        System.out.println("M7_AUDIT_BLOCKED_FRAME_FACTORY_LOCK_ISOLATION=PASS");
    }

    private static void blocked_result_and_snapshot_cannot_hold_scheduler_lock()
            throws Exception {
        try (var rt = new AttoM7CooperativeRuntime(2, 4, 4, 100)) {
            var entered = new CountDownLatch(1);
            var release = new CountDownLatch(1);
            var blocked = rt.spawn("slow-result", 2, value ->
                new AttoM7CooperativeRuntime.Frame() {
                    private int pc;
                    @Override public AttoM7CooperativeRuntime.Step advance(
                            AttoM7CooperativeRuntime.Slice slice) {
                        pc = 1;
                        return AttoM7CooperativeRuntime.Step.DONE;
                    }
                    @Override public long result() {
                        entered.countDown();
                        try {
                            require(release.await(10, TimeUnit.SECONDS),
                                "result gate deadline");
                        } catch (InterruptedException e) {
                            Thread.currentThread().interrupt();
                            throw new AssertionError(e);
                        }
                        return value;
                    }
                    @Override public int resume_pc() { return pc; }
                });
            try {
                require(rt.try_send(blocked, 17) ==
                    AttoM7CooperativeRuntime.Admission.ACCEPTED, "result send failed");
                require(entered.await(5, TimeUnit.SECONDS), "result callback did not begin");
                long start = System.nanoTime();
                var mid = rt.snapshot(blocked);
                require(mid.inFlight() && mid.running() && mid.resumePc() == -2,
                    "running frame leaked PC or lost in-flight reservation");
                AttoM7CooperativeRuntime.check_conservation(mid);
                var neighbor = rt.spawn("result-neighbor", 2,
                    M7CallbackIsolationAuditProbe::done_frame);
                require(rt.try_send(neighbor, 4) ==
                    AttoM7CooperativeRuntime.Admission.ACCEPTED, "neighbor send blocked");
                fast(start, "blocked result snapshot and neighbor spawn");
                until(() -> rt.snapshot(neighbor).completed() == 1,
                    "neighbor did not complete while frame.result blocked");
                require(rt.request_stop(blocked), "STOP blocked by frame.result");
                until(() -> rt.snapshot(blocked).controlsDelivered() == 1,
                    "supervisor CONTROL blocked behind result callback");
                release.countDown();
                require(rt.await_terminal(blocked, 10_000), "result actor did not retire");
                var s = rt.snapshot(blocked);
                require(s.completed() == 1 && s.accepted() == 1 && s.resultSum() == 17 &&
                    s.abandoned() == 0 && s.failed() == 0, "result accounting: " + s);
                AttoM7CooperativeRuntime.check_conservation(s);
                require(rt.request_stop(neighbor) && rt.await_terminal(neighbor, 10_000),
                    "neighbor retirement");
            } finally {
                release.countDown();
            }
        }
        System.out.println("M7_AUDIT_BLOCKED_RESULT_NO_GLOBAL_MONITOR=PASS");
    }

    private static void panic_abandons_physically_suspended_frame() throws Exception {
        // Exactly one carrier. Place a second actor ahead of the yielded target
        // in the ready FIFO and block it, proving the target is SUSPENDED.
        try (var rt = new AttoM7CooperativeRuntime(1, 4, 1, 100)) {
            var firstEntered = new CountDownLatch(1);
            var allowYield = new CountDownLatch(1);
            var neighborEntered = new CountDownLatch(1);
            var releaseNeighbor = new CountDownLatch(1);
            AtomicInteger resumed = new AtomicInteger();
            var target = rt.spawn("suspended-cancel", 2, value ->
                new AttoM7CooperativeRuntime.Frame() {
                    private int pc;
                    @Override public AttoM7CooperativeRuntime.Step advance(
                            AttoM7CooperativeRuntime.Slice slice) throws Exception {
                        if (pc == 0) {
                            firstEntered.countDown();
                            require(allowYield.await(10, TimeUnit.SECONDS),
                                "yield release deadline");
                            pc = 1;
                            slice.cooperate();
                            return AttoM7CooperativeRuntime.Step.YIELD;
                        }
                        resumed.incrementAndGet();
                        pc = 2;
                        return AttoM7CooperativeRuntime.Step.DONE;
                    }
                    @Override public long result() { return value; }
                    @Override public int resume_pc() { return pc; }
                });
            var neighbor = rt.spawn("blocked-next-actor", 2, value ->
                new AttoM7CooperativeRuntime.Frame() {
                    @Override public AttoM7CooperativeRuntime.Step advance(
                            AttoM7CooperativeRuntime.Slice slice) throws Exception {
                        neighborEntered.countDown();
                        require(releaseNeighbor.await(10, TimeUnit.SECONDS),
                            "neighbor release deadline");
                        return AttoM7CooperativeRuntime.Step.DONE;
                    }
                    @Override public long result() { return value; }
                    @Override public int resume_pc() { return 0; }
                });
            try {
                require(rt.try_send(target, 1) == AttoM7CooperativeRuntime.Admission.ACCEPTED,
                    "suspended target admission");
                require(firstEntered.await(5, TimeUnit.SECONDS), "target not started");
                require(rt.try_send(neighbor, 4) ==
                    AttoM7CooperativeRuntime.Admission.ACCEPTED, "neighbor not queued");
                allowYield.countDown();
                require(neighborEntered.await(5, TimeUnit.SECONDS),
                    "neighbor did not take carrier after target yielded");
                var mid = rt.snapshot(target);
                require(mid.inFlight() && !mid.running() && mid.yielded() == 1 &&
                    mid.resumePc() == 1, "target was not fully suspended: " + mid);
                AttoM7CooperativeRuntime.check_conservation(mid);
                require(rt.request_panic(target), "PANIC request rejected for suspended actor");
                require(rt.await_terminal(target, 10_000),
                    "PANIC did not retire suspended continuation without carrier");
                var done = rt.snapshot(target);
                require(done.accepted() == 1 && done.completed() == 0 &&
                    done.failed() == 0 && done.abandoned() == 1 &&
                    done.controlsDelivered() == 1 &&
                    done.lastControl() == AttoM7CooperativeRuntime.ControlKind.PANIC &&
                    !done.inFlight() && resumed.get() == 0,
                    "fatal CONTROL resumed or lost suspended mail: " + done);
                AttoM7CooperativeRuntime.check_conservation(done);
                require(rt.try_send(target, 2) == AttoM7CooperativeRuntime.Admission.STALE,
                    "reclaimed actor continuation regained send authority");
                require(rt.request_stop(neighbor), "STOP blocked neighbor");
                releaseNeighbor.countDown();
                require(rt.await_terminal(neighbor, 10_000), "neighbor did not retire");
            } finally {
                allowYield.countDown();
                releaseNeighbor.countDown();
            }
        }
        System.out.println("M7_AUDIT_PANIC_SUSPENDED_FRAME_CONSERVATION=PASS");
    }

    private static void panic_while_resume_pc_callback_is_running() throws Exception {
        try (var rt = new AttoM7CooperativeRuntime(2, 4, 1, 100)) {
            var inPc = new CountDownLatch(1);
            var releasePc = new CountDownLatch(1);
            AtomicInteger extraTurns = new AtomicInteger();
            var actor = rt.spawn("pc-in-flight", 2, value ->
                new AttoM7CooperativeRuntime.Frame() {
                    private int pc;
                    @Override public AttoM7CooperativeRuntime.Step advance(
                            AttoM7CooperativeRuntime.Slice slice) {
                        if (pc == 0) {
                            pc = 1;
                            slice.cooperate();
                            return AttoM7CooperativeRuntime.Step.YIELD;
                        }
                        extraTurns.incrementAndGet();
                        return AttoM7CooperativeRuntime.Step.DONE;
                    }
                    @Override public long result() { return value; }
                    @Override public int resume_pc() {
                        inPc.countDown();
                        try {
                            require(releasePc.await(10, TimeUnit.SECONDS),
                                "resume_pc release deadline");
                        } catch (InterruptedException e) {
                            Thread.currentThread().interrupt();
                            throw new AssertionError(e);
                        }
                        return pc;
                    }
                });
            try {
                require(rt.try_send(actor, 17) ==
                    AttoM7CooperativeRuntime.Admission.ACCEPTED, "actor initial send");
                require(inPc.await(5, TimeUnit.SECONDS), "resume_pc did not begin");
                var s = rt.snapshot(actor);
                require(s.inFlight() && s.running() && s.resumePc() == -2,
                    "snapshot accessed running frame PC: " + s);
                long start = System.nanoTime();
                require(rt.request_panic(actor), "PANIC could not reach running PC callback");
                fast(start, "PANIC during resume_pc");
                until(() -> rt.snapshot(actor).controlsDelivered() == 1,
                    "supervisor blocked by frame.resume_pc");
                releasePc.countDown();
                require(rt.await_terminal(actor, 10_000), "PANIC did not await frame quiescence");
                var done = rt.snapshot(actor);
                require(done.accepted() == 1 && done.abandoned() == 1 &&
                    done.completed() == 0 && done.failed() == 0 &&
                    extraTurns.get() == 0 && !done.inFlight(),
                    "PANIC raced resume PC and lost turn accounting: " + done);
                AttoM7CooperativeRuntime.check_conservation(done);
            } finally {
                releasePc.countDown();
            }
        }
        System.out.println("M7_AUDIT_PANIC_RUNNING_PC_QUISCES=PASS");
    }

    public static void run() throws Exception {
        blocked_factory_cannot_hold_global_scheduler_lock();
        blocked_result_and_snapshot_cannot_hold_scheduler_lock();
        panic_abandons_physically_suspended_frame();
        panic_while_resume_pc_callback_is_running();
    }
}
