package atto.compiler.asyncv1;

/** Compile-time source admission regressions, not a runtime Future scheduler. */
public final class AttoAsyncV1CompilerProbe {
    private AttoAsyncV1CompilerProbe() {}
    private static int checks;
    private static void check(boolean value) {
        if (!value) throw new AssertionError("checked async source admission");
        checks++;
    }
    private static void rejects(String source) {
        try {
            AttoAsyncV1Compiler.compile(source);
        } catch (AttoAsyncV1Compiler.Reject expected) {
            checks++;
            return;
        }
        throw new AssertionError("unsupported source was accepted: " + source);
    }
    public static void main(String[] args) {
        if (args.length != 0) throw new AssertionError("unexpected args");
        String ok = "fnc plus_one(int x): int { ^ x + 1; }"
            + " pub susp fnc main(): int { val pending = task { ^ 41; };"
            + " ^ pending |> susp |f| -> await f |> plus_one >> val answer: int; }";
        String generated = AttoAsyncV1Compiler.compile(ok);
        check(generated.contains("CompletableFuture.completedFuture(41)"));
        check(generated.contains("return Math.addExact(x, 1);"));
        check(generated.contains("ATTO_ASYNC_V1_RESULT"));
        rejects(ok.replace("susp |f|", "|f|"));
        rejects(ok.replace("await f", "await g"));
        rejects(ok.replace("|> plus_one", "|> minus_one"));
        rejects(ok.replace("^ pending", "^ missing"));
        rejects(ok.replace("41", "1_000"));
        rejects(ok.replace("41", "-41"));
        rejects(ok.replace("41", "2147483647"));
        rejects(ok.replace(">> val answer: int;", ">> val answer: float;"));
        rejects(ok.replace(">> val answer: int;", ">> val answer: int; ^ 42;"));
        rejects(ok.replace("|> plus_one", "|> plus_one |> plus_one"));
        rejects(ok.replace("fnc plus_one", "fnc plus_one //comment"));
        rejects(ok.replace(">> val answer", ">> val f"));
        rejects(ok + " class Other {}");
        rejects(ok + "\u0000");
        check(AttoAsyncV1Compiler.compile(ok.replace("41", "40"))
            .contains("completedFuture(40)"));
        System.out.println("ATTO_ASYNC_V1_ADMISSION_PASS checks=" + checks);
    }
}
