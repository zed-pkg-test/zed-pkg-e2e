// Atto Java/Graal actor-local typed off-heap storage proof-of-concept.
// HOST TEST ADAPTER ONLY: not yet lowered from general .atto source and not a
// secure tenant sandbox, generational guest ABI or a zero-host-GC assertion.
// JDK 25 FFM; allocation and typed scalar accesses have no per-message boxing.
import java.lang.foreign.Arena;
import java.lang.foreign.MemorySegment;
import java.lang.foreign.ValueLayout;
import java.util.Objects;

public final class ActorRef32Arena implements AutoCloseable {
    public record Owner(long tenant, long actor, long incarnation) {}
    // Trusted HOST witness: never the four-byte guest ABI. Constructor is
    // private, so ordinary callers cannot relabel another actor's numeric token.
    // Future guest lowering must prove the same origin in typed HIR/MIR without
    // allocating one Java wrapper per guest object.
    public static final class TrustedRef {
        private final int token;
        private final Owner origin;
        private TrustedRef(int token, Owner origin) {
            this.token = token;
            this.origin = origin;
        }
        public int guest_token() { return token; }
    }
    public static final int SLOT_BITS = 20;
    public static final int SLOT_MASK = (1 << SLOT_BITS) - 1;
    public static final int MAX_GENERATION = (1 << (32 - SLOT_BITS)) - 1;
    private static final int OBJECT_BYTES = 32;
    private final Owner owner;
    private final Arena arena;
    private final MemorySegment storage;
    private final int[] generation;
    private final int[] type_id;
    private final boolean[] live;
    private final boolean[] retired;
    private boolean closed;

    public ActorRef32Arena(Owner owner, int slot_count) {
        this.owner = Objects.requireNonNull(owner, "owner");
        if (slot_count < 1 || slot_count > SLOT_MASK)
            throw new IllegalArgumentException("invalid Ref32 slot capacity");
        generation = new int[slot_count];
        type_id = new int[slot_count];
        live = new boolean[slot_count];
        retired = new boolean[slot_count];
        for (int i = 0; i < slot_count; i++) generation[i] = 1;
        // Arena.ofShared permits carrier migration; it does NOT make shared
        // mutable actor state automatically race-safe. Access remains synchronized.
        arena = Arena.ofShared();
        storage = arena.allocate(Math.multiplyExact((long) slot_count, OBJECT_BYTES), 8);
        storage.fill((byte) 0);
    }

    // A bare four-byte guest token is NOT a cross-actor object capability:
    // current owner and immutable ref origin must be proven by typed lowering.
    // Host caller may only supply a trusted actor execution context, not a
    // guest-controlled origin witness.
    private void check_origin(Owner ref_origin, Owner caller) {
        if (closed || !owner.equals(ref_origin) || !owner.equals(caller))
            throw new IllegalStateException("foreign or retired actor reference");
    }

    public synchronized TrustedRef allocate(Owner caller, int type) {
        check_origin(owner, caller);
        if (type <= 0) throw new IllegalArgumentException("invalid static type id");
        for (int i = 0; i < live.length; i++) {
            if (!live[i] && !retired[i]) {
                live[i] = true;
                type_id[i] = type;
                return new TrustedRef((generation[i] << SLOT_BITS) | (i + 1), owner);
            }
        }
        return null; // quota refusal, without mutating any live allocation.
    }

    private int checked(TrustedRef ref, Owner caller,
                        int type, int byte_offset) {
        if (ref == null) throw new IllegalStateException("null actor reference");
        check_origin(ref.origin, caller);
        int token = ref.token;
        int i = (token & SLOT_MASK) - 1;
        int g = token >>> SLOT_BITS;
        if (i < 0 || i >= live.length || g == 0 ||
            !live[i] || retired[i] || generation[i] != g ||
            type <= 0 || type_id[i] != type)
            throw new IllegalStateException("stale or mismatched Ref32");
        if (byte_offset < 0 || byte_offset > OBJECT_BYTES - Long.BYTES ||
            (byte_offset & 7) != 0)
            throw new IndexOutOfBoundsException("invalid typed field offset");
        return i;
    }

    public synchronized void write_i64(TrustedRef ref, Owner caller,
                                       int type, int byte_offset, long value) {
        int i = checked(ref, caller, type, byte_offset);
        storage.set(ValueLayout.JAVA_LONG, (long) i * OBJECT_BYTES + byte_offset, value);
    }
    public synchronized long read_i64(TrustedRef ref, Owner caller,
                                      int type, int byte_offset) {
        int i = checked(ref, caller, type, byte_offset);
        return storage.get(ValueLayout.JAVA_LONG, (long) i * OBJECT_BYTES + byte_offset);
    }

    public synchronized void release(TrustedRef ref, Owner caller, int type) {
        int i = checked(ref, caller, type, 0);
        storage.asSlice((long) i * OBJECT_BYTES, OBJECT_BYTES).fill((byte) 0);
        live[i] = false;
        type_id[i] = 0;
        // 12-bit generation never wraps: retire the slot on exhaustion.
        if (generation[i] == MAX_GENERATION) retired[i] = true;
        else generation[i]++;
    }

    // Only after all actor loans/continuations/callbacks have quiesced.
    // Cached metadata is never used to dereference storage after arena.close().
    @Override public synchronized void close() {
        if (closed) return;
        storage.fill((byte) 0);
        arena.close();
        closed = true;
        for (int i = 0; i < live.length; i++) {
            live[i] = false;
            retired[i] = true;
            type_id[i] = 0;
        }
    }
    public synchronized boolean is_closed() { return closed; }
    public int capacity() { return live.length; }
}
