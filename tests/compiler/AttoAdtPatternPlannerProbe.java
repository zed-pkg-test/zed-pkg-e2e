package atto.compiler.adt;

import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;
import static atto.compiler.adt.AttoAdtPatternPlanner.*;

/** Host validation of exactly-once tag admission, guards, and payload binders. */
public final class AttoAdtPatternPlannerProbe {
    private static int checks;
    private static void ok(boolean yes, String why) {
        if (!yes) throw new AssertionError(why);
        checks++;
    }
    private static void rejects(String code, Runnable action) {
        try { action.run(); }
        catch (RuntimeException e) {
            ok(e.getMessage() != null && e.getMessage().contains(code), "wrong error " + code);
            return;
        }
        throw new AssertionError("accepted invalid ADT pattern: " + code);
    }
    private static Arm arm(String p, Family f, String label) {
        return new Arm(parse(p, f, Position.MATCH), false, null, label);
    }
    public static void main(String[] args) {
        ok(parse("Ok()", Family.RESULT, Position.MATCH).discards_payload(), "empty Ok discard");
        ok(parse("Ok(_)", Family.RESULT, Position.MATCH).discards_payload(), "wild Ok discard");
        ok(parse("Err()", Family.RESULT, Position.MATCH).discards_payload(), "empty Err discard");
        ok(parse("Err(_)", Family.RESULT, Position.MATCH).discards_payload(), "wild Err discard");
        ok(parse("Some()", Family.OPTION, Position.MATCH).discards_payload(), "empty Some discard");
        ok(parse("Some(_)", Family.OPTION, Position.MATCH).discards_payload(), "wild Some discard");
        ok(parse("None", Family.OPTION, Position.MATCH).discards_payload(), "nullary None");
        ok("item".equals(parse("Ok(item)", Family.RESULT, Position.MATCH).binding()), "named binder");
        rejects("E_CONSTRUCTOR_ARITY", () -> parse("Ok()", Family.RESULT, Position.CONSTRUCTOR));
        rejects("E_CONSTRUCTOR_ARITY", () -> parse("Err(_)", Family.RESULT, Position.CONSTRUCTOR));
        rejects("E_CONSTRUCTOR_ARITY", () -> parse("Some()", Family.OPTION, Position.CONSTRUCTOR));
        rejects("E_NULLARY_ARITY", () -> parse("None()", Family.OPTION, Position.CONSTRUCTOR));
        rejects("E_VARIANT_FAMILY", () -> parse("Some(_)", Family.RESULT, Position.MATCH));
        rejects("E_PATTERN_BINDER", () -> parse("Ok(await)", Family.RESULT, Position.MATCH));
        rejects("E_PATTERN_SYNTAX", () -> parse("Ok", Family.RESULT, Position.MATCH));
        rejects("E_VARIANT_TAG", () -> parse("Success(x)", Family.RESULT, Position.MATCH));
        Plan plan = check(Family.RESULT, List.of(
            arm("Ok()", Family.RESULT, "ok"),
            arm("Err(error)", Family.RESULT, "err")), true);
        ok(plan.exhaustive(), "exhaustive result match");
        ok(plan.select(Tag.OK, 11).label().equals("ok")
           && !plan.select(Tag.OK, 11).bound(), "Ok() must not bind");
        ok(plan.select(Tag.ERR, 7).bound()
           && plan.select(Tag.ERR, 7).bound_value() == 7, "Err binder");
        AtomicInteger calls = new AtomicInteger();
        Arm guarded = new Arm(parse("Err(value)", Family.RESULT, Position.MATCH),
                              true, v -> { calls.incrementAndGet(); return v == 4; }, "guard");
        Plan guards = check(Family.RESULT, List.of(
            guarded, arm("Err()", Family.RESULT, "err-fallback"),
            arm("Ok()", Family.RESULT, "ok-fallback")), true);
        ok(guards.select(Tag.OK, 99).label().equals("ok-fallback")
           && calls.get() == 0, "wrong-tag payload guard evaluated");
        ok(guards.select(Tag.ERR, 4).label().equals("guard")
           && calls.get() == 1, "guard did not run once");
        ok(guards.select(Tag.ERR, 5).label().equals("err-fallback")
           && calls.get() == 2, "failed guard skipped following arm");
        rejects("E_MATCH_UNREACHABLE", () -> check(Family.RESULT, List.of(
            arm("Ok()", Family.RESULT, "first"), arm("Ok(_)", Family.RESULT, "second"),
            arm("Err()", Family.RESULT, "third")), true));
        rejects("E_MATCH_NONEXHAUSTIVE", () -> check(Family.RESULT,
            List.of(guarded, arm("Ok()", Family.RESULT, "ok")), true));
        rejects("E_MATCH_UNREACHABLE", () -> check(Family.RESULT, List.of(
            arm("_", Family.RESULT, "default"), arm("Ok()", Family.RESULT, "later")), true));
        Plan option = check(Family.OPTION, List.of(
            arm("Some(_)", Family.OPTION, "some"), arm("None", Family.OPTION, "none")), true);
        ok(option.select(Tag.NONE, 0).label().equals("none"), "None branch");
        ok(option.select(Tag.SOME, 0).label().equals("some"), "Some(0) branch");
        System.out.println("ATTO_ADT_PATTERN_HOST_PLAN_PASS checks=" + checks);
    }
}
