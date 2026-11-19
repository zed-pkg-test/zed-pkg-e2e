package atto.compiler.m1;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.nio.channels.FileChannel;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Objects;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Restricted, standalone ObjectStorage M1 .atto-to-Java compiler.
 * NOT the canonical Atto frontend; no LLVM, dynamic classes, actor transfer or suspension.
 * The accepted Point/Particle subset is intentionally equivalent to the legacy M1 pilot.
 */
public final class AttoObjectM1JavaCompiler {
    private static final Pattern TOKEN = Pattern.compile(
        "\\s+|//[^\\n]*|-?[0-9]+|[A-Za-z_][A-Za-z_0-9]*|[{}():;.=+,-]");
    private static final Pattern NAME = Pattern.compile("[A-Za-z_][A-Za-z_0-9]*");
    private static final Pattern NUMBER = Pattern.compile("-?[0-9]+");
    private static final Pattern HASH = Pattern.compile("[0-9a-f]{64}");
    private AttoObjectM1JavaCompiler() {}

    private static IllegalArgumentException reject(String reason) {
        return new IllegalArgumentException("ATTO_M1_REJECT: " + reason);
    }

    private static List<String> tokenize(String source) {
        List<String> tokens = new ArrayList<>();
        Matcher scanner = TOKEN.matcher(source);
        int offset = 0;
        while (offset < source.length()) {
            scanner.region(offset, source.length());
            if (!scanner.lookingAt()) throw reject("unsupported token or feature");
            String token = scanner.group();
            offset = scanner.end();
            if (!token.isBlank() && !token.startsWith("//")) tokens.add(token);
            if (tokens.size() > 1000) throw reject("token budget exceeded");
        }
        return List.copyOf(tokens);
    }

    private record Term(String kind, long value) {}
    private record Plan(long x, long y, long velocity, List<Term> terms,
                        List<String> operators, long exit) {}

    private static final class Parser {
        private final List<String> tokens;
        private int index;

        Parser(List<String> tokens) { this.tokens = tokens; }

        String peek() { return index < tokens.size() ? tokens.get(index) : null; }

        void take(String token) {
            if (!token.equals(peek())) throw reject("expected " + token);
            index++;
        }

        String identifier() {
            String id = peek();
            if (id == null || !NAME.matcher(id).matches()
                    || List.of("return", "define", "class", "struct", "as",
                               "new", "let", "end").contains(id)) {
                throw reject("expected safe identifier");
            }
            index++;
            return id;
        }

        long integer() {
            String n = peek();
            if (n == null || !NUMBER.matcher(n).matches()) throw reject("expected signed i64");
            try {
                long value = Long.parseLong(n);
                index++;
                return value;
            } catch (NumberFormatException e) {
                throw reject("signed i64 literal out of range");
            }
        }

        void closeDeclaration() {
            take("end");
            if (";".equals(peek())) take(";");
        }

        Term term(String local, String owner) {
            if (peek() != null && NUMBER.matcher(peek()).matches())
                return new Term("literal", integer());
            String name = identifier();
            take(".");
            String member = identifier();
            if (name.equals(local)) {
                if (member.equals("x")) return new Term("inline", 0);
                if (member.equals("y")) return new Term("inline", 1);
            }
            if (name.equals(owner)) {
                if (member.equals("velocity")) return new Term("object", 2);
                if (member.equals("position")) {
                    take(".");
                    String field = identifier();
                    if (field.equals("x")) return new Term("object", 0);
                    if (field.equals("y")) return new Term("object", 1);
                }
            }
            throw reject("invalid typed field path");
        }

        Plan parse() {
            take("define"); take("struct"); take("Point"); take("as");
            take("int"); take("x"); take(";");
            take("int"); take("y"); take(";"); closeDeclaration();
            take("define"); take("class"); take("Particle"); take("as");
            take("final"); take("Point"); take("position"); take(";");
            take("int"); take("velocity"); take(";"); closeDeclaration();
            take("pub"); take("fnc"); take("main"); take("("); take(")");
            take(":"); take("int"); take("{");
            take("let"); take("Point");
            String local = identifier();
            take("="); take("Point"); take("("); take("x"); take("=");
            long x = integer();
            take(","); take("y"); take("="); long y = integer();
            take(")"); take(";");
            take("let"); take("Particle");
            String owner = identifier();
            if (owner.equals(local)) throw reject("owner name collides with inline value");
            take("="); take("new"); take("Particle"); take("(");
            take("position"); take("="); take(local);
            take(","); take("velocity"); take("="); long velocity = integer();
            take(")"); take(";");
            take("return");
            List<Term> terms = new ArrayList<>();
            List<String> operators = new ArrayList<>();
            terms.add(term(local, owner));
            while ("+".equals(peek()) || "-".equals(peek())) {
                operators.add(peek()); index++;
                terms.add(term(local, owner));
                if (terms.size() > 8) throw reject("expression depth budget exceeded");
            }
            take(";"); take("}");
            if (peek() != null) throw reject("trailing guest source");
            long value = resolve(terms.get(0), x, y, velocity);
            for (int i = 0; i < operators.size(); i++) {
                long rhs = resolve(terms.get(i + 1), x, y, velocity);
                try {
                    value = operators.get(i).equals("+") ?
                        Math.addExact(value, rhs) : Math.subtractExact(value, rhs);
                } catch (ArithmeticException e) {
                    throw reject("checked arithmetic overflow");
                }
            }
            if (value < 0 || value > 255) throw reject("exit result must fit 0..255");
            return new Plan(x, y, velocity, List.copyOf(terms),
                            List.copyOf(operators), value);
        }
    }

    private static long resolve(Term t, long x, long y, long velocity) {
        if (t.kind.equals("literal")) return t.value;
        return switch ((int)t.value) {
            case 0 -> x;
            case 1 -> y;
            case 2 -> velocity;
            default -> throw reject("invalid field offset");
        };
    }

    private static String literal(long value) {
        return value == Long.MIN_VALUE ? "Long.MIN_VALUE" : value + "L";
    }

    private static String expression(Term t) {
        if (t.kind.equals("literal")) return literal(t.value);
        if (t.kind.equals("inline")) return t.value == 0 ? "point_x" : "point_y";
        return "arena.read_i64(particle, actor, 101, " + (t.value * 8) + ")";
    }

    private static String emit(Plan p) {
        String expr = expression(p.terms.get(0));
        for (int i = 0; i < p.operators.size(); i++) {
            String op = p.operators.get(i).equals("+") ? "addExact" : "subtractExact";
            expr = "Math." + op + "(" + expr + ", " + expression(p.terms.get(i + 1)) + ")";
        }
        return """
            // Generated by the bounded Atto ObjectStorage M1 Java compiler.
            // Single actor, fixed Point/Particle types, no dynamic guest source.
            public final class AttoObjectStorageM1 {
                public static void main(String[] args) {
                    if (args.length != 0) throw new IllegalArgumentException("no runtime guest input");
                    var actor = new ActorRef32Arena.Owner(1, 1, 1);
                    long result;
                    try (var arena = new ActorRef32Arena(actor, 2)) {
                        long point_x = %s;
                        long point_y = %s;
                        var particle = arena.allocate(actor, 101);
                        if (particle == null) throw new IllegalStateException("actor heap quota refusal");
                        arena.write_i64(particle, actor, 101, 0, point_x);
                        arena.write_i64(particle, actor, 101, 8, point_y);
                        arena.write_i64(particle, actor, 101, 16, %s);
                        result = %s;
                        arena.release(particle, actor, 101);
                    }
                    System.out.println("ATTO_OBJECT_M1_RESULT=" + result);
                    System.out.println("ATTO_OBJECT_M1_SCOPE=CLOSED_WORLD_SINGLE_ACTOR");
                    System.exit(Math.toIntExact(result));
                }
            }
            """.formatted(literal(p.x), literal(p.y), literal(p.velocity), expr);
    }

    /**
     * Returns a compile exit status without terminating the caller (for host probes).
     * No file is published if source checks, parsing, rendering, or writes fail.
     */
    public static int compile(Path source, String expectedSha, Path output) {
        Path temporary = null;
        try {
            Objects.requireNonNull(source);
            Objects.requireNonNull(output);
            if (Files.isSymbolicLink(source) || !Files.isRegularFile(source, LinkOption.NOFOLLOW_LINKS)
                    || !source.toString().endsWith(".atto"))
                throw reject("source must be regular nonsymlinked .atto");
            Path parent = output.toAbsolutePath().getParent();
            if (parent == null || !Files.isDirectory(parent, LinkOption.NOFOLLOW_LINKS))
                throw reject("output parent must be a real directory");
            if (Files.exists(output, LinkOption.NOFOLLOW_LINKS))
                throw reject("output already exists");
            long size = Files.size(source);
            if (size < 1 || size > 16384) throw reject("source byte budget");
            byte[] bytes = Files.readAllBytes(source);
            if (bytes.length != size) throw reject("source changed during admission");
            String actual = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
            if (expectedSha == null || !HASH.matcher(expectedSha).matches()
                    || !actual.equals(expectedSha)) throw reject("source SHA-256 mismatch");
            String code = StandardCharsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(bytes)).toString();
            Plan plan = new Parser(tokenize(code)).parse();
            String javaCode = emit(plan);
            temporary = Files.createTempFile(parent, ".atto-m1-java-", ".tmp");
            Files.writeString(temporary, javaCode, StandardCharsets.UTF_8,
                              StandardOpenOption.WRITE, StandardOpenOption.TRUNCATE_EXISTING);
            try (FileChannel channel = FileChannel.open(temporary, StandardOpenOption.WRITE)) {
                channel.force(true);
            }
            // Atomic same-directory publish, no replace of attacker-planted file/symlink.
            Files.createLink(output, temporary);
            System.out.println("ATTO_M1_JAVA_COMPILE_PASS sha256=" + actual
                               + " expected_exit=" + plan.exit);
            return 0;
        } catch (IOException | NoSuchAlgorithmException | IllegalArgumentException ex) {
            System.err.println("ATTO_M1_JAVA_REJECT: " + ex.getMessage());
            return 2;
        } finally {
            if (temporary != null) {
                try { Files.deleteIfExists(temporary); }
                catch (IOException e) { System.err.println("ATTO_M1_TEMP_CLEANUP_FAILED"); }
            }
        }
    }

    public static void main(String[] args) {
        if (args.length != 5 || !args[1].equals("--source-sha256")
                || !args[3].equals("--out")) {
            System.err.println("Usage: <source.atto> --source-sha256 <64 hex> --out <new.java>");
            System.exit(2);
        }
        System.exit(compile(Path.of(args[0]), args[2], Path.of(args[4])));
    }
}
