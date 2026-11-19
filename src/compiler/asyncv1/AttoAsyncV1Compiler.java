package atto.compiler.asyncv1;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Restrictive, statically generating .atto source compiler for one completed-Future
 * pipeline. NOT a full parser, suspending scheduler, cancellation, ownership checker
 * or production asynchronous frontend.
 */
public final class AttoAsyncV1Compiler {
    private static final Pattern TOKENS = Pattern.compile(
        "\\s+|\\|>|>>|->|[A-Za-z_][A-Za-z_0-9]*|[0-9]+|[{}():;^+=|]");
    private AttoAsyncV1Compiler() {}

    public static final class Reject extends IllegalArgumentException {
        private static final long serialVersionUID = 1L;
        Reject(String reason) { super(reason); }
    }
    private static final class Parser {
        private final List<String> tokens = new ArrayList<>();
        private int index;
        Parser(String source) {
            if (source == null || source.length() > 8192)
                throw new Reject("E_ASYNC_SOURCE_SIZE");
            Matcher matcher = TOKENS.matcher(source);
            int at = 0;
            while (at < source.length()) {
                matcher.region(at, source.length());
                if (!matcher.lookingAt())
                    throw new Reject("E_ASYNC_UNKNOWN_TOKEN at " + at);
                String token = matcher.group();
                if (!token.isBlank()) tokens.add(token);
                if (tokens.size() > 256) throw new Reject("E_ASYNC_TOKEN_BUDGET");
                at = matcher.end();
            }
        }
        String next() {
            if (index == tokens.size()) throw new Reject("E_ASYNC_INCOMPLETE");
            return tokens.get(index++);
        }
        void expect(String value) {
            String got = next();
            if (!got.equals(value))
                throw new Reject("E_ASYNC_EXPECTED_" + value + " got " + got);
        }
        String ident() {
            String value = next();
            if (!value.matches("[A-Za-z_][A-Za-z_0-9]*"))
                throw new Reject("E_ASYNC_INVALID_IDENTIFIER");
            return value;
        }
        int smallInt() {
            String value = next();
            if (!value.matches("[0-9]{1,6}"))
                throw new Reject("E_ASYNC_INTEGER_RANGE");
            return Integer.parseInt(value);
        }
        boolean done() { return index == tokens.size(); }
    }
    public static String compile(String source) {
        Parser p = new Parser(source);
        p.expect("fnc");
        String fun = p.ident();
        p.expect("("); p.expect("int");
        String param = p.ident();
        p.expect(")"); p.expect(":"); p.expect("int");
        p.expect("{"); p.expect("^"); p.expect(param); p.expect("+");
        int increment = p.smallInt();
        p.expect(";"); p.expect("}");
        p.expect("pub"); p.expect("susp"); p.expect("fnc"); p.expect("main");
        p.expect("("); p.expect(")"); p.expect(":"); p.expect("int");
        p.expect("{"); p.expect("val"); String future = p.ident();
        p.expect("="); p.expect("task"); p.expect("{"); p.expect("^");
        int value = p.smallInt();
        p.expect(";"); p.expect("}"); p.expect(";");
        p.expect("^"); p.expect(future); p.expect("|>");
        p.expect("susp"); p.expect("|"); String binding = p.ident();
        p.expect("|"); p.expect("->"); p.expect("await");
        String awaited = p.ident();
        if (!binding.equals(awaited)) throw new Reject("E_AWAIT_BINDER_MISMATCH");
        p.expect("|>"); String invoked = p.ident();
        if (!fun.equals(invoked)) throw new Reject("E_PIPE_FUNCTION_MISMATCH");
        p.expect(">>"); p.expect("val"); String terminal = p.ident();
        p.expect(":"); p.expect("int"); p.expect(";");
        p.expect("}");
        if (!p.done()) throw new Reject("E_ASYNC_TRAILING_SOURCE");
        if (future.equals(terminal) || terminal.equals(binding))
            throw new Reject("E_ASYNC_DUPLICATE_LOCAL");
        // All three expressions are checked before generating code; no Atto
        // expression is evaluated by a runtime source interpreter.
        int expected = Math.addExact(value, increment);
        return "import java.util.concurrent.CompletableFuture;\n"
            + "public final class AttoAsyncV1Guest {\n"
            + "  private static int " + fun + "(int " + param + ") {\n"
            + "    return Math.addExact(" + param + ", " + increment + ");\n"
            + "  }\n"
            + "  public static int execute() {\n"
            + "    CompletableFuture<Integer> " + future
            + " = CompletableFuture.completedFuture(" + value + ");\n"
            + "    int " + terminal + " = " + fun + "(" + future + ".join());\n"
            + "    return " + terminal + ";\n"
            + "  }\n"
            + "  public static void main(String[] args) {\n"
            + "    if (args.length != 0) throw new IllegalArgumentException();\n"
            + "    int result = execute();\n"
            + "    System.out.println(\"ATTO_ASYNC_V1_RESULT=\" + result);\n"
            + "    System.out.println(\"ATTO_ASYNC_SCOPE=COMPLETED_FUTURE_ONLY\");\n"
            + "    System.exit(result);\n"
            + "  }\n"
            + "}\n";
    }
    public static void main(String[] args) throws IOException {
        if (args.length != 2) throw new Reject("E_ASYNC_CLI_ARGUMENTS");
        Path input = Path.of(args[0]).toAbsolutePath().normalize();
        Path output = Path.of(args[1]).toAbsolutePath().normalize();
        if (!input.toString().endsWith(".atto") ||
            !Files.isRegularFile(input, LinkOption.NOFOLLOW_LINKS) ||
            Files.size(input) > 8192 || Files.exists(output, LinkOption.NOFOLLOW_LINKS))
            throw new Reject("E_ASYNC_SOURCE_OR_OUTPUT_ADMISSION");
        String generated = compile(Files.readString(input, StandardCharsets.UTF_8));
        Path temp = Files.createTempFile(output.getParent(), ".atto-async-v1-", ".tmp");
        try {
            Files.writeString(temp, generated, StandardCharsets.UTF_8);
            Files.createLink(output, temp); // atomic: never overwrite a planted path
        } finally {
            Files.deleteIfExists(temp);
        }
        System.out.println("ATTO_ASYNC_V1_COMPILED=PASS");
    }
}
