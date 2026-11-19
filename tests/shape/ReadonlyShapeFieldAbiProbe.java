package atto.shape;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.List;

import static atto.shape.ReadonlyShapeFieldAbi.*;

public final class ReadonlyShapeFieldAbiProbe {
    private static int passed;
    private static void check(boolean condition, String what) {
        if (!condition) throw new AssertionError(what);
        passed++;
    }
    private static void rejects(String error, Runnable body) {
        try { body.run(); }
        catch (RuntimeException e) {
            check(e.getMessage() != null && e.getMessage().contains(error),
                  "expected " + error + " but got " + e);
            return;
        }
        throw new AssertionError("expected failure: " + error);
    }
    private static Layout layout(Field... fields) {
        return new Layout("test", List.of(fields), false);
    }
    private static Field field(String name, Scalar type, int offset) {
        return new Field(name, type, offset, true, false, true);
    }
    private static ByteBuffer data(long left, long right) {
        return ByteBuffer.allocateDirect(16).order(ByteOrder.LITTLE_ENDIAN)
            .putLong(0, left).putLong(8, right);
    }

    public static void main(String[] args) {
        OwnerLease dog = new OwnerLease(), cat = new OwnerLease();
        Layout first = layout(field("foo", Scalar.INT, 0));
        Layout second = layout(field("extra", Scalar.INT, 0), field("foo", Scalar.INT, 8));
        ByteBuffer dog_bytes = data(41, 0), cat_bytes = data(5, 42);
        long value = with_int(first, "foo", dog_bytes, dog, x -> x)
                   + with_int(second, "foo", cat_bytes, cat, x -> x);
        check(value == 83, "different class field offsets");
        check(with_int(second, "foo", cat_bytes, cat, x -> x) == 42, "owner reuse");
        check(!dog_bytes.isReadOnly() && !cat_bytes.isReadOnly(), "source not mutated or moved");
        rejects("E_TYPE", () -> with_int(second, "absent", cat_bytes, cat, x -> x));
        rejects("E_TYPE", () -> with_int(layout(field("foo", Scalar.BOOL, 0)), "foo", dog_bytes, dog, x -> x));
        rejects("E_PRIVATE", () -> with_int(layout(new Field("foo", Scalar.INT, 0, false, false, true)), "foo", dog_bytes, dog, x -> x));
        rejects("E_PRIVATE", () -> with_int(layout(new Field("foo", Scalar.INT, 0, true, true, true)), "foo", dog_bytes, dog, x -> x));
        rejects("E_PRIVATE", () -> with_int(layout(new Field("foo", Scalar.INT, 0, true, false, false)), "foo", dog_bytes, dog, x -> x));
        rejects("E_STATE_BORROW", () -> with_int(new Layout("Actor", List.of(field("foo", Scalar.INT, 0)), true), "foo", dog_bytes, dog, x -> x));
        rejects("E_BOUNDS", () -> with_int(layout(field("foo", Scalar.INT, 16)), "foo", dog_bytes, dog, x -> x));
        rejects("duplicate", () -> layout(field("foo", Scalar.INT, 0), field("foo", Scalar.INT, 8)));
        rejects("invalid compiled", () -> layout(new Field("foo", Scalar.INT, 1, true, false, true)));
        dog.mark_moved();
        rejects("E_MOVED", () -> with_int(first, "foo", dog_bytes, dog, x -> x));
        System.out.println("JAVA_SHAPE_FIELD_WITNESS_PASS checks=" + passed);
    }
}
