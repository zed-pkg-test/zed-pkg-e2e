package atto.compiler.adt;

import java.util.ArrayList;
import java.util.EnumSet;
import java.util.HashSet;
import java.util.List;
import java.util.Objects;
import java.util.Set;
import java.util.function.LongPredicate;

/**
 * Static, deterministic Result/Option match-arm plan and tag-first guard dispatch.
 * Not canonical .atto parser/HIR/AOT lowering; no owned heap payloads are lowered here.
 */
public final class AttoAdtPatternPlanner {
    public enum Family { RESULT, OPTION }
    public enum Position { MATCH, CONSTRUCTOR }
    public enum Tag { OK, ERR, SOME, NONE, WILDCARD }
    public record Pattern(Tag tag, String binding) {
        public Pattern { Objects.requireNonNull(tag); }
        public boolean discards_payload() { return binding == null; }
    }
    public record Arm(Pattern pattern, boolean guarded, LongPredicate guard, String label) {
        public Arm {
            Objects.requireNonNull(pattern);
            Objects.requireNonNull(label);
            if (guarded && guard == null) throw new IllegalArgumentException("E_GUARD_MISSING");
            if (!guarded && guard != null) throw new IllegalArgumentException("E_GUARD_UNEXPECTED");
        }
    }
    public record Selection(int source_index, String label, boolean bound,
                            long bound_value) {}
    public record Plan(Family family, List<Arm> arms, boolean exhaustive) {
        public Plan {
            Objects.requireNonNull(family);
            arms = List.copyOf(arms);
        }
        /** A guarded arm reads a payload only after its tag has been checked. */
        public Selection select(Tag actual, long payload) {
            if (actual == Tag.WILDCARD || !isAllowed(family, actual))
                throw new IllegalArgumentException("E_VARIANT_FAMILY");
            for (int i = 0; i < arms.size(); i++) {
                Arm arm = arms.get(i);
                if (arm.pattern().tag() != Tag.WILDCARD && arm.pattern().tag() != actual)
                    continue;
                if (arm.guarded() && !arm.guard().test(payload)) continue;
                return new Selection(i, arm.label(), arm.pattern().binding() != null, payload);
            }
            throw new IllegalStateException("E_MATCH_NONEXHAUSTIVE");
        }
    }
    private AttoAdtPatternPlanner() {}

    private static boolean isAllowed(Family family, Tag tag) {
        return switch (family) {
            case RESULT -> tag == Tag.OK || tag == Tag.ERR;
            case OPTION -> tag == Tag.SOME || tag == Tag.NONE;
        };
    }

    private static IllegalArgumentException error(String code) {
        return new IllegalArgumentException(code);
    }

    public static Pattern parse(String source, Family family, Position position) {
        Objects.requireNonNull(source);
        Objects.requireNonNull(family);
        Objects.requireNonNull(position);
        if (source.equals("_")) {
            if (position == Position.CONSTRUCTOR) throw error("E_CONSTRUCTOR_PATTERN");
            return new Pattern(Tag.WILDCARD, null);
        }
        int left = source.indexOf('(');
        String rawTag = left < 0 ? source : source.substring(0, left);
        // Atto nominal variant constructors are case-sensitive. Case folding
        // would incorrectly accept ok()/ERR(_) and change match admission.
        Tag tag = switch (rawTag) {
            case "Ok" -> Tag.OK;
            case "Err" -> Tag.ERR;
            case "Some" -> Tag.SOME;
            case "None" -> Tag.NONE;
            default -> throw error("E_VARIANT_TAG");
        };
        if (!isAllowed(family, tag)) throw error("E_VARIANT_FAMILY");
        if (tag == Tag.NONE) {
            if (left >= 0) throw error("E_NULLARY_ARITY");
            return new Pattern(tag, null);
        }
        if (left < 0 || !source.endsWith(")") || source.indexOf('(', left + 1) >= 0)
            throw error("E_PATTERN_SYNTAX");
        String inside = source.substring(left + 1, source.length() - 1);
        if (position == Position.CONSTRUCTOR) {
            if (inside.isEmpty() || inside.equals("_")) throw error("E_CONSTRUCTOR_ARITY");
            if (!inside.matches("[A-Za-z_][A-Za-z_0-9]*"))
                throw error("E_CONSTRUCTOR_EXPRESSION_UNSUPPORTED");
            return new Pattern(tag, inside);
        }
        if (inside.isEmpty() || inside.equals("_")) return new Pattern(tag, null);
        if (inside.length() > 128 || !inside.matches("[A-Za-z_][A-Za-z_0-9]*")
                || Set.of("let", "val", "return", "match", "when", "await").contains(inside))
            throw error("E_PATTERN_BINDER");
        return new Pattern(tag, inside);
    }

    /**
     * An unguarded arm covers its whole tag. A guard never establishes exhaustive
     * coverage; later arms after an unguarded tag/default are unreachable.
     */
    public static Plan check(Family family, List<Arm> input, boolean requireExhaustive) {
        Objects.requireNonNull(family);
        Objects.requireNonNull(input);
        if (input.isEmpty() || input.size() > 128) throw error("E_MATCH_ARM_BUDGET");
        List<Arm> arms = new ArrayList<>();
        Set<Tag> covered = new HashSet<>();
        boolean defaultCovered = false;
        for (Arm arm : input) {
            Objects.requireNonNull(arm);
            Tag tag = arm.pattern().tag();
            if (tag != Tag.WILDCARD && !isAllowed(family, tag))
                throw error("E_VARIANT_FAMILY");
            if (defaultCovered || covered.contains(tag))
                throw error("E_MATCH_UNREACHABLE");
            arms.add(arm);
            if (!arm.guarded()) {
                if (tag == Tag.WILDCARD) defaultCovered = true;
                else covered.add(tag);
            }
        }
        EnumSet<Tag> required = family == Family.RESULT ?
            EnumSet.of(Tag.OK, Tag.ERR) : EnumSet.of(Tag.SOME, Tag.NONE);
        boolean complete = defaultCovered || covered.containsAll(required);
        if (requireExhaustive && !complete) throw error("E_MATCH_NONEXHAUSTIVE");
        return new Plan(family, arms, complete);
    }
}
