// Test the real JDK foreign-memory host adapter, not compiled .atto guest.
public final class ActorRef32ArenaTest {
    private static int checks;
    private static void check(boolean yes, String what) {
        checks++;
        if (!yes) throw new AssertionError(what);
    }
    private static void reject(Runnable action, String what) {
        checks++;
        try { action.run(); }
        catch (IllegalArgumentException | IllegalStateException |
               IndexOutOfBoundsException expected) { return; }
        throw new AssertionError(what);
    }
    private static void stop_twice(ActorRef32Arena value) {
        value.close();
        value.close();
    }
    public static void main(String[] args) throws InterruptedException {
        var a = new ActorRef32Arena.Owner(7, 9, 1);
        var b = new ActorRef32Arena.Owner(7, 10, 1);
        var restart = new ActorRef32Arena.Owner(7, 9, 2);
        try (var first = new ActorRef32Arena(a, 2);
             var second = new ActorRef32Arena(b, 2)) {
            check(first.capacity() == 2, "capacity");
            var pa = first.allocate(a, 101);
            var pb = second.allocate(b, 101);
            check(pa != null && pb != null &&
                  pa.guest_token() == pb.guest_token(),
                  "colliding numeric handles across actors");
            check(ActorRef32Arena.TrustedRef.class.getConstructors().length == 0,
                  "trusted host witness must have no public constructors");
            first.write_i64(pa, a, 101, 0, 10);
            first.write_i64(pa, a, 101, 8, 20);
            first.write_i64(pa, a, 101, 16, 3);
            check(first.read_i64(pa, a, 101, 0) == 10, "point x");
            check(first.read_i64(pa, a, 101, 8) == 20, "point y");
            check(first.read_i64(pa, a, 101, 16) == 3, "velocity");
            reject(() -> first.read_i64(pa, a, 101, 32), "OOB accepted");
            reject(() -> first.write_i64(pa, a, 101, 1, 9), "misaligned field");
            reject(() -> first.read_i64(pa, a, 999, 0), "type substitution");
            reject(() -> first.read_i64(pa, b, 101, 0), "foreign caller");
            reject(() -> first.read_i64(pa, restart, 101, 0), "restart context");
            reject(() -> first.read_i64(pb, a, 101, 0), "wrong-origin handle");
            reject(() -> second.read_i64(pa, b, 101, 0), "sibling numeric collision");
            reject(() -> first.release(pb, a, 101), "foreign release");
            reject(() -> first.release(pa, a, 999), "wrong type release");
            first.release(pa, a, 101);
            reject(() -> first.release(pa, a, 101), "double free accepted");
            var next = first.allocate(a, 101);
            check(next != null && next.guest_token() != pa.guest_token(), "generation not advanced");
            reject(() -> first.read_i64(pa, a, 101, 0), "stale handle ABA");
            check(first.read_i64(next, a, 101, 0) == 0, "released bytes not scrubbed");
            reject(() -> first.allocate(a, 0), "type zero accepted");
            reject(() -> first.allocate(b, 101), "foreign allocation accepted");
            // Concurrent actor turns may arrive on multiple Java carriers.
            // Fields and slot sidecars must serialize against one another.
            final long left = 0x1122334455667788L;
            final long right = 0x0102030405060708L;
            final var linear = new java.util.concurrent.atomic.AtomicBoolean(true);
            first.write_i64(next, a, 101, 0, left);
            Thread[] workers = new Thread[6];
            for (int i = 0; i < workers.length; i++) {
                final int worker = i;
                workers[i] = new Thread(() -> {
                    try {
                        for (int j = 0; j < 500; j++) {
                            if (worker < 3) {
                                first.write_i64(next, a, 101, 0,
                                                (j & 1) == 0 ? left : right);
                            } else {
                                long observed = first.read_i64(next, a, 101, 0);
                                if (observed != left && observed != right)
                                    linear.set(false);
                            }
                        }
                    } catch (RuntimeException error) { linear.set(false); }
                });
                workers[i].start();
            }
            for (Thread worker : workers) worker.join();
            check(linear.get(), "torn or racy concurrent field access");
            final var admitted = new java.util.concurrent.atomic.AtomicInteger();
            for (int i = 0; i < 4; i++) {
                workers[i] = new Thread(() -> {
                    try {
                        for (int j = 0; j < 300; j++) {
                            var temp = first.allocate(a, 102);
                            if (temp == null) continue; // expected slot backpressure
                            admitted.incrementAndGet();
                            first.write_i64(temp, a, 102, 0, 42);
                            first.release(temp, a, 102);
                        }
                    } catch (RuntimeException error) { linear.set(false); }
                });
                workers[i].start();
            }
            for (int i = 0; i < 4; i++) workers[i].join();
            check(linear.get() && admitted.get() > 0,
                  "concurrent Ref32 slot allocation and release");
            long observed = first.read_i64(next, a, 101, 0);
            check(observed == left || observed == right,
                  "concurrent field storage preserved");
            stop_twice(first);
            check(first.is_closed(), "actor STOP");
            reject(() -> first.read_i64(next, a, 101, 0), "post-STOP read accepted");
            reject(() -> first.allocate(a, 101), "post-STOP allocate");
            check(second.read_i64(pb, b, 101, 0) == 0, "sibling alive after STOP");
        }
        try (var wraps = new ActorRef32Arena(a, 1)) {
            ActorRef32Arena.TrustedRef prior = null;
            for (int gen = 1; gen <= ActorRef32Arena.MAX_GENERATION; gen++) {
                var token = wraps.allocate(a, 15);
                check(token != null && (token.guest_token() >>> ActorRef32Arena.SLOT_BITS) == gen,
                      "generation sequence");
                if (prior != null) {
                    final var stale = prior;
                    reject(() -> wraps.read_i64(stale, a, 15, 0),
                           "stale generation unexpectedly valid");
                }
                wraps.release(token, a, 15);
                prior = token;
            }
            check(wraps.allocate(a, 15) == null, "generation wrapped");
            final var last = prior;
            reject(() -> wraps.read_i64(last, a, 15, 0), "retired slot revived");
        }
        reject(() -> new ActorRef32Arena(a, 0), "zero slot count");
        System.out.println("ATTO_REF32_JAVA_HOST_PASS checks=" + checks +
                           " note=not_compiled_atto_guest");
    }
}
