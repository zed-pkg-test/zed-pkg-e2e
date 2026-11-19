package atto.compiler.shape;

import atto.shape.ReadonlyShapeFieldAbi.Field;
import atto.shape.ReadonlyShapeFieldAbi.Layout;
import atto.shape.ReadonlyShapeFieldAbi.OwnerLease;
import atto.shape.ReadonlyShapeFieldAbi.Scalar;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.List;
import static atto.compiler.shape.AttoStructuralBinding.*;

/** Positive and negative host binder checks. NOT real compiled Atto guest support. */
public final class AttoStructuralBindingProbe {
    private static int checks;
    private static void check(boolean ok, String name) {
        if (!ok) throw new AssertionError(name);
        checks++;
    }
    private static void reject(String code, Runnable action) {
        try { action.run(); }
        catch (RuntimeException e) {
            check(e.getMessage() != null && e.getMessage().contains(code), "wrong reject " + code);
            return;
        }
        throw new AssertionError("accepted invalid structural loan: " + code);
    }
    private static Field field(String name, Scalar s, int off, boolean visible,
                               boolean state, boolean plain) {
        return new Field(name, s, off, visible, state, plain);
    }
    public static void main(String[] ignored) {
        Layout dog = new Layout("pkg.Dog", List.of(
            field("age", Scalar.INT, 8, true, false, true),
            field("alive", Scalar.BOOL, 0, true, false, true)), false);
        Witness w = admit(Kind.CLASS, dog, List.of(
            new Requirement("age", Scalar.INT)), Lifetime.CALL_SCOPED, false);
        check(w.fields().size() == 1 && w.fields().get(0).offset() == 8,
              "independent field offset");
        ByteBuffer buf = ByteBuffer.allocateDirect(16).order(ByteOrder.LITTLE_ENDIAN);
        buf.putLong(8, 41);
        OwnerLease owner = new OwnerLease();
        check(read_int(w, "age", buf, owner, x -> x + 1) == 42, "checked value projection");
        check(!buf.isReadOnly(), "owned buffer not converted");
        reject("E_TYPE", () -> read_int(w, "alive", buf, owner, x -> x));
        reject("E_TYPE", () -> admit(Kind.CLASS, dog,
            List.of(new Requirement("age", Scalar.STRING)), Lifetime.CALL_SCOPED, false));
        reject("E_TYPE", () -> admit(Kind.CLASS, dog,
            List.of(new Requirement("height", Scalar.INT)), Lifetime.CALL_SCOPED, false));
        reject("E_DUPLICATE", () -> admit(Kind.CLASS, dog, List.of(
            new Requirement("age", Scalar.INT), new Requirement("age", Scalar.INT)),
            Lifetime.CALL_SCOPED, false));
        reject("E_BORROW_AWAIT", () -> admit(Kind.CLASS, dog,
            List.of(new Requirement("age", Scalar.INT)), Lifetime.ACROSS_SUSPENSION, false));
        reject("E_MOVED", () -> admit(Kind.CLASS, dog,
            List.of(new Requirement("age", Scalar.INT)), Lifetime.CALL_SCOPED, true));
        reject("E_STATE_BORROW", () -> admit(Kind.ACTOR, dog,
            List.of(new Requirement("age", Scalar.INT)), Lifetime.CALL_SCOPED, false));
        reject("E_STATE_BORROW", () -> admit(Kind.STRUCT, new Layout(
            "pkg.SealedActor", dog.fields(), true),
            List.of(new Requirement("age", Scalar.INT)), Lifetime.CALL_SCOPED, false));
        reject("E_PRIVATE", () -> admit(Kind.CLASS, new Layout("pkg.Private",
            List.of(field("age", Scalar.INT, 0, false, false, true)), false),
            List.of(new Requirement("age", Scalar.INT)), Lifetime.CALL_SCOPED, false));
        reject("E_PRIVATE", () -> admit(Kind.CLASS, new Layout("pkg.Computed",
            List.of(field("age", Scalar.INT, 0, true, false, false)), false),
            List.of(new Requirement("age", Scalar.INT)), Lifetime.CALL_SCOPED, false));
        reject("E_SHAPE_WIDTH", () -> admit(Kind.STRUCT, dog,
            List.of(), Lifetime.CALL_SCOPED, false));
        Witness structWitness = admit(Kind.STRUCT, dog,
            List.of(new Requirement("alive", Scalar.BOOL)), Lifetime.CALL_SCOPED, false);
        check(structWitness.fields().get(0).offset() == 0, "struct shape admission");
        owner.mark_moved();
        reject("E_MOVED", () -> read_int(w, "age", buf, owner, x -> x));
        System.out.println("ATTO_STRUCTURAL_HOST_BINDER_PASS checks=" + checks);
    }
}
