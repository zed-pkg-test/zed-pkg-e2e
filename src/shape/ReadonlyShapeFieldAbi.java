package atto.shape;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.List;
import java.util.Objects;
import java.util.function.LongUnaryOperator;

/**
 * Compiler-side checked field witness and synchronous scalar projection.
 * This does not parse .atto functions or generate GraalVM guest code.
 * A structural formal is a readonly loan: no reflection, setters, actor state,
 * arbitrary property getters, async retention or nominal pointer coercion.
 */
public final class ReadonlyShapeFieldAbi {
    private ReadonlyShapeFieldAbi() {}

    public enum Scalar { INT, BOOL, STRING, FLOAT }

    public record Field(String name, Scalar scalar, int offset,
                        boolean public_visible, boolean actor_state,
                        boolean plain_data_field) {
        public Field {
            Objects.requireNonNull(name);
            Objects.requireNonNull(scalar);
            if (name.isEmpty() || offset < 0 || offset % 8 != 0) {
                throw new IllegalArgumentException("invalid compiled field descriptor");
            }
        }
    }

    public record Layout(String nominal_name, List<Field> fields, boolean sealed_actor_state) {
        public Layout {
            Objects.requireNonNull(nominal_name);
            fields = List.copyOf(fields);
            if (fields.size() > 32) throw new IllegalArgumentException("shape width bound");
            for (int i = 0; i < fields.size(); i++) {
                for (int j = i + 1; j < fields.size(); j++) {
                    if (fields.get(i).name().equals(fields.get(j).name())) {
                        throw new IllegalArgumentException("duplicate compiled member");
                    }
                }
            }
        }
    }

    /** A compiler-issued owner lease; cannot be used after a consuming move. */
    public static final class OwnerLease {
        private boolean moved;

        public void mark_moved() { moved = true; }
        private void require_live() {
            if (moved) throw new IllegalStateException("E_MOVED: moved structural owner");
        }
    }

    /**
     * For the first executable profile only one public int field is admitted.
     * A synchronous callback receives an i64 *value*, not an escapable object
     * handle or a mutable buffer. The source owner is never moved.
     */
    public static long with_int(Layout layout, String required_name,
                                ByteBuffer owned_bytes, OwnerLease owner,
                                LongUnaryOperator callback) {
        Objects.requireNonNull(layout);
        Objects.requireNonNull(required_name);
        Objects.requireNonNull(owned_bytes);
        Objects.requireNonNull(owner);
        Objects.requireNonNull(callback);
        owner.require_live();
        if (layout.sealed_actor_state()) {
            throw new IllegalArgumentException("E_STATE_BORROW: sealed actor state");
        }
        Field actual = null;
        for (Field field : layout.fields()) {
            if (field.name().equals(required_name)) { actual = field; break; }
        }
        if (actual == null) throw new IllegalArgumentException("E_TYPE: missing structural field");
        if (!actual.public_visible() || actual.actor_state() || !actual.plain_data_field()) {
            throw new IllegalArgumentException("E_PRIVATE: inaccessible structural field");
        }
        if (actual.scalar() != Scalar.INT) {
            throw new IllegalArgumentException("E_TYPE: expected signed int64 structural field");
        }
        if (actual.offset() > owned_bytes.limit() - Long.BYTES) {
            throw new IllegalArgumentException("E_BOUNDS: invalid compiled field offset");
        }
        // Independent of nominal class layout or unrelated field positions.
        // Never call instance methods or inspect a host object's properties.
        ByteBuffer readonly = owned_bytes.asReadOnlyBuffer().order(ByteOrder.LITTLE_ENDIAN);
        return callback.applyAsLong(readonly.getLong(actual.offset()));
    }
}
