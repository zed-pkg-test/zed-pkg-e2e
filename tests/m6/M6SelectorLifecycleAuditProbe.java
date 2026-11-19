// Executable adversarial M6 selector/lifecycle checks.
// Actual Java NIO channels and OS threads; host-only, not compiled .atto guest.
import java.nio.ByteBuffer;
import java.nio.channels.Pipe;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicIntegerArray;
import java.util.concurrent.atomic.AtomicReference;

public final class M6SelectorLifecycleAuditProbe {
    private static void require(boolean ok, String reason) {
        if (!ok) throw new AssertionError(reason);
    }

    private static void until(java.util.function.BooleanSupplier predicate, String label)
            throws Exception {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(15);
        while (!predicate.getAsBoolean() && System.nanoTime() < deadline) Thread.sleep(2);
        require(predicate.getAsBoolean(), "deadline exceeded: " + label);
    }

    private static void selected_key_budget_and_admission() throws Exception {
        // More than the reactor's 64-key per-pass budget; all descriptors
        // already have data when registered. Registration callers race to
        // enqueue commands and force bulk selected-key processing.
        final int count = 96;
        try (var rt = new AttoM6Runtime(4, 128)) {
            List<Pipe> pipes = new ArrayList<>();
            List<AttoM6Runtime.ActorRef> refs = new ArrayList<>();
            List<Thread> registrars = new ArrayList<>();
            CountDownLatch start = new CountDownLatch(1);
            AtomicReference<Throwable> failure = new AtomicReference<>();
            AtomicIntegerArray seen = new AtomicIntegerArray(count);
            AtomicInteger complete = new AtomicInteger();
            try {
                for (int i = 0; i < count; i++) {
                    final int index = i;
                    var actor = rt.spawn("selected-" + i, 1, value -> {
                        require(value == (index + 1), "selector delivered wrong byte/actor");
                        require(seen.incrementAndGet(index) == 1, "duplicate selector dispatch");
                        complete.incrementAndGet();
                    });
                    Pipe pipe = Pipe.open();
                    // Bound and fill a real kernel pipe before registration.
                    pipe.sink().write(ByteBuffer.wrap(new byte[] {(byte) (i + 1)}));
                    refs.add(actor); pipes.add(pipe);
                    var thread = new Thread(() -> {
                        try {
                            require(start.await(5, TimeUnit.SECONDS),
                                "registrar start deadline");
                            rt.register_read(pipe.source(), actor);
                        } catch (Throwable e) { failure.compareAndSet(null, e); }
                    }, "atto-m6-register-" + i);
                    registrars.add(thread);
                    thread.start();
                }
                start.countDown();
                for (Thread thread : registrars) thread.join(15_000);
                require(registrars.stream().noneMatch(Thread::isAlive),
                    "selector registration stalled");
                require(failure.get() == null, "registration failure: " + failure.get());
                until(() -> complete.get() == count, "all 96 selected-key deliveries");
                for (int i = 0; i < count; i++)
                    require(seen.get(i) == 1, "starved selected key " + i);
                var snapshot = rt.reactor_snapshot();
                require(snapshot.bytesAdmitted() == count && snapshot.bytesHeld() == 0,
                    "selected-key backlog was lost: " + snapshot);
                for (Pipe pipe : pipes) rt.cancel_read(pipe.source());
                for (var ref : refs) {
                    require(rt.request_stop(ref), "actor STOP rejected");
                    require(rt.await_terminal(ref, 10_000), "actor did not retire");
                    AttoM6Runtime.check_conservation(rt.snapshot(ref));
                }
            } finally {
                for (Thread thread : registrars) thread.join(15_000);
                for (Pipe pipe : pipes) {
                    try { pipe.sink().close(); } catch (Exception ignored) {}
                    try { pipe.source().close(); } catch (Exception ignored) {}
                }
            }
        }
        System.out.println("M6_AUDIT_BUDGETED_SELECTED_KEYS_96=PASS");
    }

    private static void external_close_cannot_kill_reactor() throws Exception {
        try (var rt = new AttoM6Runtime(2, 4)) {
            var dropped = rt.spawn("externally-closed", 2, v -> {});
            Pipe closed = Pipe.open();
            rt.register_read(closed.source(), dropped);
            // External close races interest rearm; cancelled SelectionKey
            // must detach only this registration, not terminate selector loop.
            closed.source().close();
            closed.sink().close();
            until(() -> rt.reactor_snapshot().registrations() == 0,
                "externally closed selector channel cleanup");

            AtomicInteger received = new AtomicInteger();
            var alive = rt.spawn("still-alive", 2, v -> {
                require(v == 42, "unexpected downstream readiness");
                received.incrementAndGet();
            });
            Pipe healthy = Pipe.open();
            try {
                rt.register_read(healthy.source(), alive);
                healthy.sink().write(ByteBuffer.wrap(new byte[]{42}));
                until(() -> received.get() == 1, "subsequent ready channel");
                require(rt.reactor_snapshot().bytesAdmitted() == 1,
                    "reactor stopped on unrelated cancelled selection key");
                rt.cancel_read(healthy.source());
                require(rt.request_stop(alive) && rt.await_terminal(alive, 10_000),
                    "healthy actor did not retire");
                require(rt.request_stop(dropped) && rt.await_terminal(dropped, 10_000),
                    "cancelled actor did not retire");
            } finally {
                healthy.source().close();
                healthy.sink().close();
            }
        }
        System.out.println("M6_AUDIT_EXTERNAL_CHANNEL_CLOSE_SURVIVES=PASS");
    }

    private static void selector_calls_fail_fast_after_retirement() throws Exception {
        var rt = new AttoM6Runtime(1, 2);
        var actor = rt.spawn("lifecycle", 2, v -> {});
        Pipe pipe = Pipe.open();
        rt.register_read(pipe.source(), actor);
        rt.close();
        long begin = System.nanoTime();
        int rejected = 0;
        try { rt.cancel_read(pipe.source()); }
        catch (IllegalStateException expected) { rejected++; }
        try { rt.register_read(pipe.source(), actor); }
        catch (IllegalStateException expected) { rejected++; }
        try { rt.reactor_snapshot(); }
        catch (IllegalStateException expected) { rejected++; }
        long elapsedMs = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - begin);
        pipe.sink().close();
        require(rejected == 3, "post-retirement selector APIs did not fail closed");
        require(elapsedMs < 1000, "post-retirement selector API blocked waiting for dead reactor");
        rt.close(); // still idempotent
        System.out.println("M6_AUDIT_POST_CLOSE_SELECTOR_FAST_FAIL=PASS");
    }

    public static void run() throws Exception {
        selected_key_budget_and_admission();
        external_close_cannot_kill_reactor();
        selector_calls_fail_fast_after_retirement();
    }
}
