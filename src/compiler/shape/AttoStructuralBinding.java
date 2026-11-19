package atto.compiler.shape;

import atto.shape.ReadonlyShapeFieldAbi;
import atto.shape.ReadonlyShapeFieldAbi.Field;
import atto.shape.ReadonlyShapeFieldAbi.Layout;
import atto.shape.ReadonlyShapeFieldAbi.OwnerLease;
import atto.shape.ReadonlyShapeFieldAbi.Scalar;
import java.nio.ByteBuffer;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Objects;
import java.util.Set;
import java.util.function.LongUnaryOperator;

/**
 * Compiler-side fail-closed structural read-loan admission against compiler-issued layouts.
 * This is an executable *host* type-checking stage, not the general .atto parser or HIR.
 */
public final class AttoStructuralBinding {
    public enum Kind { CLASS, STRUCT, ACTOR }
    public enum Lifetime { CALL_SCOPED, ACROSS_SUSPENSION }
    public record Requirement(String name, Scalar scalar) {
        public Requirement {
            Objects.requireNonNull(name);
            Objects.requireNonNull(scalar);
            if (!name.matches("[A-Za-z_][A-Za-z_0-9]*") || name.length() > 128)
                throw new IllegalArgumentException("E_SHAPE_NAME");
        }
    }
    public record Resolved(String name, Scalar scalar, int offset) {}
    public record Witness(Layout source, List<Resolved> fields) {
        public Witness {
            Objects.requireNonNull(source);
            fields = List.copyOf(fields);
        }
    }
    private AttoStructuralBinding() {}

    public static Witness admit(Kind sourceKind, Layout layout, List<Requirement> required,
                                Lifetime lifetime, boolean ownerAlreadyMoved) {
        Objects.requireNonNull(sourceKind);
        Objects.requireNonNull(layout);
        Objects.requireNonNull(required);
        Objects.requireNonNull(lifetime);
        if (ownerAlreadyMoved) throw new IllegalStateException("E_MOVED: owner consumed");
        if (lifetime != Lifetime.CALL_SCOPED)
            throw new IllegalArgumentException("E_BORROW_AWAIT: structural loan across suspension");
        if (sourceKind == Kind.ACTOR || layout.sealed_actor_state())
            throw new IllegalArgumentException("E_STATE_BORROW: actor state is sealed");
        if (required.isEmpty() || required.size() > 32)
            throw new IllegalArgumentException("E_SHAPE_WIDTH");
        List<Resolved> found = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        for (Requirement requirement : required) {
            Objects.requireNonNull(requirement);
            if (!seen.add(requirement.name()))
                throw new IllegalArgumentException("E_DUPLICATE_REQUIREMENT");
            Field actual = null;
            for (Field f : layout.fields()) {
                if (f.name().equals(requirement.name())) { actual = f; break; }
            }
            if (actual == null) throw new IllegalArgumentException("E_TYPE: missing field");
            if (!actual.public_visible() || actual.actor_state() || !actual.plain_data_field())
                throw new IllegalArgumentException("E_PRIVATE: inaccessible or computed field");
            if (actual.scalar() != requirement.scalar())
                throw new IllegalArgumentException("E_TYPE: scalar field type mismatch");
            if (actual.offset() > Integer.MAX_VALUE - Long.BYTES)
                throw new IllegalArgumentException("E_BOUNDS: field offset overflow");
            found.add(new Resolved(actual.name(), actual.scalar(), actual.offset()));
        }
        return new Witness(layout, found);
    }

    /** Only a checked, synchronous i64 value escapes; no buffer or actor state can escape. */
    public static long read_int(Witness witness, String required, ByteBuffer bytes,
                                OwnerLease owner, LongUnaryOperator callback) {
        Objects.requireNonNull(witness);
        Objects.requireNonNull(required);
        if (witness.fields().stream().noneMatch(f ->
                f.name().equals(required) && f.scalar() == Scalar.INT))
            throw new IllegalArgumentException("E_TYPE: not an admitted i64 field");
        return ReadonlyShapeFieldAbi.with_int(witness.source(), required, bytes, owner, callback);
    }
}
