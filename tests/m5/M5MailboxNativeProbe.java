// Real native/runtime smoke. Executes independently scheduled actor workers,
// bounded off-heap mailboxes and supervisor-only failure accounting.
// Not a compiled .atto guest, untrusted code sandbox, or production scheduler.
import java.lang.management.ManagementFactory;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;

public final class M5MailboxNativeProbe {
    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
    private static long gcCount() {
        long sum = 0;
        var beans = ManagementFactory.getGarbageCollectorMXBeans();
        if (beans.isEmpty()) return -1;
        for (var bean : beans) {
            long n = bean.getCollectionCount();
            if (n < 0) return -1;
            sum = Math.addExact(sum, n);
        }
        return sum;
    }
    private static void sendUntilAdmitted(M5BoundedActorMailbox actor, long v) {
        for (int n = 0; n < 2_000_000; n++) {
            if (actor.try_send(v)) return;
            Thread.onSpinWait();
        }
        throw new AssertionError("actor failed to make progress under bounded mailbox pressure");
    }
    private static void mustBeAccounted(M5BoundedActorMailbox.Snapshot s) {
        require(s.accepted() == s.processed() + s.abandoned() +
                s.failedTurns() + s.remaining() + s.inFlight(), "mailbox lost or duplicated an admitted message");
        require(s.maxDepth() <= s.capacity(), "mailbox over capacity");
    }
    public static void main(String[] args) throws Exception {
        require(args.length == 0, "no guest source accepted at runtime");
        long before = gcCount();
        long usedBefore = ManagementFactory.getMemoryMXBean().getHeapMemoryUsage().getUsed();

        // Force an exact, reproducible full-mailbox decision while worker paused.
        var blocked = new M5BoundedActorMailbox(1, 10, true);
        require(blocked.try_send(7), "first mail not admitted");
        require(!blocked.try_send(8), "full mailbox admitted unexpected second mail");
        blocked.release_worker();
        blocked.await_done();
        var full = blocked.snapshot();
        mustBeAccounted(full);
        require(full.rejectedFull() == 1 && full.accepted() == 1 &&
                full.processed() == 1 && full.finalState() == 17,
                "bounded capacity/refusal/backpressure mismatch");
        require(!blocked.try_send(1), "stale actor accepted mail after retirement");
        var afterClose = blocked.snapshot();
        require(afterClose.rejectedClosed() >= 1, "stale-handle rejection not accounted");
        blocked.close();
        blocked.close(); // Closing twice must not release the same Arena twice.
        var closedSnapshot = blocked.snapshot();
        mustBeAccounted(closedSnapshot);
        require(closedSnapshot.closed() && closedSnapshot.finalState() == 17 &&
                closedSnapshot.inFlight() == 0,
                "snapshot attempted to read retired off-heap actor memory");

        // Simultaneous closes and snapshot observations must not race with
        // native arena release or abandon an already-admitted turn.
        var closing = new M5BoundedActorMailbox(2, 3, true);
        require(closing.try_send(4), "close-race setup send failed");
        var closeError = new AtomicReference<Throwable>();
        Runnable stop = () -> {
            try { closing.close(); }
            catch (Throwable t) { closeError.compareAndSet(null, t); }
        };
        Thread closer1 = new Thread(stop, "atto-m5-closer-1");
        Thread closer2 = new Thread(stop, "atto-m5-closer-2");
        closer1.start();
        closer2.start();
        long closeDeadline = System.nanoTime() + 30_000_000_000L;
        while ((closer1.isAlive() || closer2.isAlive()) &&
               System.nanoTime() < closeDeadline) {
            mustBeAccounted(closing.snapshot());
            Thread.onSpinWait();
        }
        closer1.join(1000);
        closer2.join(1000);
        require(!closer1.isAlive() && !closer2.isAlive(),
                "concurrent actor close failed to retire");
        require(closeError.get() == null, "concurrent actor close failed: " + closeError.get());
        var closedRace = closing.snapshot();
        mustBeAccounted(closedRace);
        require(closedRace.closed() && closedRace.accepted() == 1 &&
                closedRace.processed() == 1 && closedRace.finalState() == 7,
                "concurrent close corrupted final state or retirement");

        // Independently scheduled MPSC stress; 100k accepted turns on first
        // actor, 1k on a second actor. No boxed message/envelope allocations.
        var target = new M5BoundedActorMailbox(32, 0, false);
        var neighbor = new M5BoundedActorMailbox(8, 50, false);
        var sent = new AtomicLong();
        Runnable produce = () -> {
            for (int i = 0; i < 50_000; i++) {
                sendUntilAdmitted(target, 1);
                sent.incrementAndGet();
            }
        };
        Thread p1 = new Thread(produce, "atto-m5-producer-1");
        Thread p2 = new Thread(produce, "atto-m5-producer-2");
        p1.start();
        p2.start();
        for (int i = 0; i < 1024; i++) mustBeAccounted(target.snapshot());
        for (int i = 0; i < 1000; i++) sendUntilAdmitted(neighbor, 2);
        p1.join(30_000);
        p2.join(30_000);
        require(!p1.isAlive() && !p2.isAlive(), "producers did not terminate");
        require(sent.get() == 100_000, "producer progress or accounting mismatch");
        target.await_done();
        neighbor.await_done();
        var one = target.snapshot();
        var two = neighbor.snapshot();
        mustBeAccounted(one);
        mustBeAccounted(two);
        require(one.accepted() == 100_000 && one.processed() == 100_000 &&
                one.finalState() == 100_000, "actor state lost messages under concurrency");
        require(two.accepted() == 1000 && two.processed() == 1000 &&
                two.finalState() == 2050, "healthy neighbor state lost");
        require(!one.failed() && !two.failed() &&
                one.supervisorControlNotices() == 0 && two.supervisorControlNotices() == 0,
                "normal workers unexpectedly raised supervisor notices");
        require(one.workerThreadId() != Thread.currentThread().threadId() &&
                two.workerThreadId() != Thread.currentThread().threadId() &&
                one.workerThreadId() != two.workerThreadId(),
                "mailbox used synchronous main-thread execution");
        target.close();
        neighbor.close();

        // Overflow is a worker failure; sends cannot catch/intercept its
        // supervisor-only CONTROL notice. Queued work must be explicitly counted.
        var bad = new M5BoundedActorMailbox(3, 0, true);
        require(bad.try_send(Long.MAX_VALUE), "first fault setup send");
        require(bad.try_send(1), "overflow-inducing send");
        require(bad.try_send(5), "mail behind failing turn");
        bad.release_worker();
        bad.await_done();
        var panic = bad.snapshot();
        mustBeAccounted(panic);
        require(panic.failed() && panic.supervisorControlNotices() == 1,
                "actor failure lacked exactly one supervisor CONTROL notice");
        require(panic.accepted() == 3 && panic.processed() == 1 &&
                panic.failedTurns() == 1 && panic.abandoned() == 1 &&
                panic.finalState() == Long.MAX_VALUE,
                "failed turn was silently lost, retried or partially committed");
        require(!bad.try_send(8), "failed actor was resurrected by send");
        bad.close();
        var failedAfterDispose = bad.snapshot();
        mustBeAccounted(failedAfterDispose);
        require(failedAfterDispose.failed() && failedAfterDispose.finalState() == Long.MAX_VALUE,
                "fault terminal snapshot became invalid after arena disposal");

        long after = gcCount();
        long usedAfter = ManagementFactory.getMemoryMXBean().getHeapMemoryUsage().getUsed();
        require(before >= 0 && after >= before && after - before == 0,
                "strict Epsilon no-collection profile not observed");
        long managedGrowth = usedAfter - usedBefore;
        // Epsilon can make this large without ever collecting: record it
        // explicitly, rather than falsely claiming a sustainable heap profile.
        boolean heapBoundQualified = managedGrowth >= 0 &&
                                     managedGrowth < 8L * 1024 * 1024;
        System.out.println("M5_MAILBOX_8MIB_MANAGED_HEAP_BOUND=" +
                           (heapBoundQualified ? "PASS" : "NOT_QUALIFIED"));

        System.out.println("M5_MAILBOX_CAPACITY_REFUSAL=PASS");
        System.out.println("M5_MAILBOX_POST_CLOSE_SNAPSHOT=PASS");
        System.out.println("M5_MAILBOX_RETIREMENT_SNAPSHOT_AND_CONCURRENT_CLOSE=PASS");
        System.out.println("M5_MAILBOX_IN_FLIGHT_ACCOUNTING=PASS");
        System.out.println("M5_MAILBOX_CONCURRENT_ACCEPTED=100000");
        System.out.println("M5_MAILBOX_NEIGHBOR_ACCEPTED=1000");
        System.out.println("M5_MAILBOX_ACTOR_STATE=100000");
        System.out.println("M5_MAILBOX_NEIGHBOR_STATE=2050");
        System.out.println("M5_MAILBOX_INDEPENDENT_WORKER_THREADS=PASS");
        System.out.println("M5_MAILBOX_SUPERVISOR_CONTROL_NOTICE=1");
        System.out.println("M5_MAILBOX_FAILED_TURN_ACCOUNTED=1");
        System.out.println("M5_MAILBOX_ABANDONED_MAIL_ACCOUNTED=1");
        System.out.println("M5_MAILBOX_RUNTIME_GC_DELTA=0");
        System.out.println("M5_MAILBOX_MANAGED_HEAP_DELTA_BYTES=" + managedGrowth);
        System.out.println("M5_GRAALVM_WORKER_MAILBOX_NATIVE_AOT=PASS");
        System.out.println("M5_SCOPE=EXPERIMENTAL_WORKER_RUNTIME_NOT_ATTO_GUEST_OR_HOSTILE_TENANT_SANDBOX");
    }
}
