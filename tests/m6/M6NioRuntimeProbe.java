// Executed host-JVM / Native Image M6 NIO+carrier+supervisor conformance subset.
import java.nio.ByteBuffer;
import java.nio.channels.Pipe;
import java.nio.channels.ServerSocketChannel;
import java.nio.channels.SocketChannel;
import java.net.InetSocketAddress;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

public final class M6NioRuntimeProbe {
    private static void require(boolean ok, String why) { if (!ok) throw new AssertionError(why); }
    private static void until(BooleanSupplier predicate, String what) throws Exception {
        long end = System.nanoTime() + TimeUnit.SECONDS.toNanos(10);
        while (!predicate.get() && System.nanoTime() < end) Thread.sleep(2);
        require(predicate.get(), "timed out: " + what);
    }
    @FunctionalInterface private interface BooleanSupplier { boolean get(); }
    private static void send(AttoM6Runtime rt, AttoM6Runtime.ActorRef ref, long value) throws Exception {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(10);
        for (;;) {
            var result = rt.try_send(ref, value);
            if (result == AttoM6Runtime.Admission.ACCEPTED) return;
            require(result == AttoM6Runtime.Admission.FULL, "unexpected admission: " + result);
            require(System.nanoTime() < deadline, "producer deadline exceeded");
            Thread.onSpinWait();
        }
    }
    private static void nio_and_backpressure() throws Exception {
        try (var rt = new AttoM6Runtime(2, 8)) {
            var gate = new CountDownLatch(1);
            var started = new CountDownLatch(1);
            AtomicLong sum = new AtomicLong();
            var target = rt.spawn("nio", 1, v -> {
                started.countDown();
                if (v == 7) require(gate.await(5, TimeUnit.SECONDS), "release timed out");
                sum.addAndGet(v);
            });
            Pipe p = Pipe.open();
            rt.register_read(p.source(), target);
            p.sink().write(ByteBuffer.wrap(new byte[] {7, 8, 9, 10, 11}));
            require(started.await(5, TimeUnit.SECONDS), "selector failed to dispatch byte 7");
            until(() -> rt.snapshot(target).accepted() == 2, "first mailbox saturation");
            require(rt.snapshot(target).queued() == 1, "NIO did not saturate bounded mailbox");
            gate.countDown();
            until(() -> rt.snapshot(target).completed() == 5, "I/O completion without loss");
            require(sum.get() == 45, "NIO payload loss / duplicate bytes");
            var s = rt.reactor_snapshot();
            require(s.bytesAdmitted() == 5 && s.bytesHeld() == 0, "selector dropped pending readiness");
            rt.cancel_read(p.source());
            require(!p.source().isOpen(), "NIO cancel did not close registration");
            p.sink().close();
            require(rt.request_stop(target) && rt.await_terminal(target, 10_000), "NIO actor stop");
            AttoM6Runtime.check_conservation(rt.snapshot(target));
            System.out.println("M6_NIO_READ_ONE_SHOT_BACKPRESSURE=PASS");
        }
    }

    private static void nio_loopback_socket_eof() throws Exception {
        try (var rt = new AttoM6Runtime(2, 8);
             var server = ServerSocketChannel.open()) {
            server.bind(new InetSocketAddress("127.0.0.1", 0));
            try (var client = SocketChannel.open(server.getLocalAddress());
                 var accepted = server.accept()) {
                AtomicLong sum = new AtomicLong();
                var target = rt.spawn("loopback", 4, sum::addAndGet);
                rt.register_read(accepted, target);
                ByteBuffer body = ByteBuffer.wrap(new byte[] {20, 22});
                while (body.hasRemaining()) client.write(body);
                until(() -> rt.snapshot(target).completed() == 2, "TCP loopback delivery");
                client.close();
                until(() -> rt.reactor_snapshot().eof() == 1, "TCP EOF cancellation");
                var io = rt.reactor_snapshot();
                require(io.registrations() == 0 && io.bytesAdmitted() == 2 &&
                    io.bytesRejected() == 0 && sum.get() == 42,
                    "TCP readiness/EOF/resource close failed: " + io);
                require(!accepted.isOpen(), "EOF left host descriptor registered");
                require(rt.request_stop(target) && rt.await_terminal(target, 10_000), "TCP actor STOP");
                AttoM6Runtime.check_conservation(rt.snapshot(target));
            }
            System.out.println("M6_NIO_LOOPBACK_SOCKET_EOF=PASS");
        }
    }

    private static void control_and_shutdown() throws Exception {
        try (var rt = new AttoM6Runtime(2, 8)) {
            AtomicLong neighborSum = new AtomicLong();
            var neighbor = rt.spawn("neighbor", 64, neighborSum::addAndGet);
            var entered = new CountDownLatch(1);
            var release = new CountDownLatch(1);
            AtomicLong badSum = new AtomicLong();
            var bad = rt.spawn("bad", 3, v -> {
                if (v == 1) { entered.countDown(); require(release.await(5, TimeUnit.SECONDS), "bad stalled"); }
                if (v == -1) throw new ArithmeticException("checked overflow");
                badSum.addAndGet(v);
            });
            require(rt.try_send(bad, 1) == AttoM6Runtime.Admission.ACCEPTED, "initial bad send");
            require(entered.await(5, TimeUnit.SECONDS), "first bad handler never started");
            require(rt.try_send(bad, -1) == AttoM6Runtime.Admission.ACCEPTED, "fault send");
            require(rt.try_send(bad, 99) == AttoM6Runtime.Admission.ACCEPTED, "queued after fault");
            require(rt.try_send(bad, 123) == AttoM6Runtime.Admission.ACCEPTED, "full panic mailbox setup");
            require(rt.try_send(bad, 500) == AttoM6Runtime.Admission.FULL,
                "saturated DATA mailbox illegally admitted another turn");
            send(rt, neighbor, 41);
            release.countDown();
            require(rt.await_terminal(bad, 10_000), "panic CONTROL not delivered");
            var s = rt.snapshot(bad);
            require(s.accepted() == 4 && s.completed() == 1 && s.failed() == 1 &&
                s.abandoned() == 2 && s.controlsDelivered() == 1 &&
                s.lastControl() == AttoM6Runtime.ControlKind.PANIC && badSum.get() == 1,
                "PANIC failure/abandon or supervisor CONTROL count mismatch: " + s);
            require(rt.try_send(bad, 7) == AttoM6Runtime.Admission.STALE, "retired actor ref admitted");
            until(() -> neighborSum.get() == 41, "neighbor under panic pressure");
            var newBad = rt.spawn("bad", 2, v -> {});
            require(newBad.generation() == bad.generation() + 1, "reincarnation did not increment");
            require(rt.try_send(bad, 9) == AttoM6Runtime.Admission.STALE, "old generation not fenced");
            require(rt.request_stop(newBad) && rt.await_terminal(newBad, 10_000), "new generation STOP");
            AttoM6Runtime.check_conservation(s);
            System.out.println("M6_SUPERVISOR_CONTROL_PANIC_FENCING=PASS");
        }
    }

    private static void strict_control_before_data_resume() throws Exception {
        try (var rt = new AttoM6Runtime(1, 4)) {
            var firstEntered = new CountDownLatch(1);
            var allowFirst = new CountDownLatch(1);
            AtomicInteger turns = new AtomicInteger();
            final AttoM6Runtime.ActorRef[] ref = new AttoM6Runtime.ActorRef[1];
            ref[0] = rt.spawn("graceful", 4, v -> {
                if (turns.incrementAndGet() == 1) {
                    firstEntered.countDown();
                    require(allowFirst.await(5, TimeUnit.SECONDS), "first turn blocked");
                } else require(rt.snapshot(ref[0]).controlsDelivered() == 1,
                    "DATA resumed before pending STOP CONTROL dispatch");
            });
            send(rt, ref[0], 1);
            require(firstEntered.await(5, TimeUnit.SECONDS), "first handler missing");
            send(rt, ref[0], 2);
            require(rt.request_stop(ref[0]), "stop request rejected");
            require(rt.try_send(ref[0], 3) == AttoM6Runtime.Admission.CLOSED,
                "STOP admitted mail after closing admission");
            allowFirst.countDown();
            require(rt.await_terminal(ref[0], 10_000), "graceful STOP failed to drain");
            var s = rt.snapshot(ref[0]);
            require(turns.get() == 2 && s.completed() == 2 && s.abandoned() == 0 &&
                s.controlsDelivered() == 1 && s.lastControl() == AttoM6Runtime.ControlKind.STOP,
                "graceful drain mismatch: " + s);
            AttoM6Runtime.check_conservation(s);
            System.out.println("M6_STRICT_CONTROL_BEFORE_DATA=PASS");
        }
    }

    private static void pooled_serial_actor_and_neighbor() throws Exception {
        try (var rt = new AttoM6Runtime(4, 8)) {
            AtomicInteger active = new AtomicInteger(), maxActive = new AtomicInteger();
            AtomicLong sum = new AtomicLong(), neighbor = new AtomicLong();
            var serial = rt.spawn("serial", 32, v -> {
                int now = active.incrementAndGet();
                maxActive.accumulateAndGet(now, Math::max);
                try { sum.addAndGet(v); Thread.yield(); }
                finally { active.decrementAndGet(); }
            });
            var sibling = rt.spawn("sibling", 32, neighbor::addAndGet);
            List<Thread> producers = new ArrayList<>();
            AtomicInteger failures = new AtomicInteger();
            for (int n=0;n<4;n++) {
                Thread producer = new Thread(() -> {
                    try { for (int i=0;i<250;i++) send(rt, serial, 1); }
                    catch (Exception e) { failures.incrementAndGet(); }
                }, "m6-test-producer-" + n);
                producers.add(producer); producer.start();
            }
            for(int i=0;i<100;i++) send(rt, sibling, 2);
            for (Thread p : producers) p.join(10_000);
            require(producers.stream().noneMatch(Thread::isAlive), "producer did not finish");
            require(failures.get() == 0, "producer error");
            until(() -> rt.snapshot(serial).completed() == 1000, "serial actor completion");
            until(() -> rt.snapshot(sibling).completed() == 100, "neighbor progress");
            require(sum.get() == 1000 && neighbor.get() == 200 && maxActive.get() == 1,
                "serial actor ran on two threads concurrently or lost mail");
            require(rt.request_stop(serial) && rt.await_terminal(serial, 10_000), "serial retirement");
            AttoM6Runtime.check_conservation(rt.snapshot(serial));
            System.out.println("M6_POOLED_SERIAL_CARRIER_MPSC=1000");
            System.out.println("M6_POOLED_NEIGHBOR_PROGRESS=100");
            System.out.println("M6_SERIAL_MAX_CONCURRENT_RECEIVES=" + maxActive.get());
        }
    }
    private static void close_races_and_registration_fencing() throws Exception {
        var rt = new AttoM6Runtime(2, 8);
        var entered = new CountDownLatch(1);
        var release = new CountDownLatch(1);
        var errors = new java.util.concurrent.atomic.AtomicReference<Throwable>();
        var actor = rt.spawn("closing", 1, v -> {
            entered.countDown();
            require(release.await(5, TimeUnit.SECONDS), "close race release");
        });
        send(rt, actor, 1);
        require(entered.await(5, TimeUnit.SECONDS), "close race worker missing");
        Thread closer = new Thread(() -> {
            try { rt.close(); } catch (Throwable t) { errors.set(t); }
        }, "m6-close-thread");
        closer.start();
        until(() -> !rt.snapshot(actor).accepting(), "STOP admission fence");
        require(rt.try_send(actor, 2) == AttoM6Runtime.Admission.CLOSED,
            "send raced through global STOP gate");
        boolean prevented = false;
        try { rt.spawn("too-late", 1, v -> {}); }
        catch (IllegalStateException expected) { prevented = true; }
        require(prevented, "runtime permitted actor creation during close");
        release.countDown();
        closer.join(10_000);
        require(!closer.isAlive() && errors.get() == null, "global close did not quiesce: " + errors.get());
        rt.close(); // Idempotent after physical selector and carrier shutdown.
        var snapshot = rt.snapshot(actor);
        require(snapshot.retired() && snapshot.accepted() == 1 && snapshot.completed() == 1,
            "graceful global close failed to drain");
        AttoM6Runtime.check_conservation(snapshot);
        System.out.println("M6_GLOBAL_CLOSE_SPAWN_FENCE=PASS");
    }
    public static void main(String[] args) throws Exception {
        require(args.length == 0, "no external guest source accepted");
        nio_and_backpressure();
        nio_loopback_socket_eof();
        strict_control_before_data_resume();
        control_and_shutdown();
        pooled_serial_actor_and_neighbor();
        close_races_and_registration_fencing();
        System.out.println("M6_NIO_SCHEDULER_SUPERVISOR_NATIVE_HOST=PASS");
        System.out.println("M6_SCOPE=HOST_SUBSET_NOT_ATTO_GUEST_OR_HARD_REAL_TIME_PROOF");
    }
}
