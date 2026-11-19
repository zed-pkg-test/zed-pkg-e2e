package atto.compiler.m1;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;

/** JVM admission, compilation, source-rejection, and atomic artifact probes. */
public final class AttoObjectM1JavaCompilerProbe {
    private static int checked;
    private AttoObjectM1JavaCompilerProbe() {}

    private static void check(boolean condition, String reason) {
        if (!condition) throw new AssertionError(reason);
        checked++;
    }

    private static String sha(byte[] bytes) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }

    private static void rejected(Path source, Path output) throws Exception {
        int status = AttoObjectM1JavaCompiler.compile(
            source, sha(Files.readAllBytes(source)), output);
        check(status == 2, "bad source unexpectedly compiled");
        check(!Files.exists(output, LinkOption.NOFOLLOW_LINKS), "reject left output");
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 1) throw new IllegalArgumentException("fixture path required");
        byte[] original = Files.readAllBytes(Path.of(args[0]));
        String fixture = new String(original, StandardCharsets.UTF_8);
        Path dir = Files.createTempDirectory("atto-m1-java-probe-");
        try {
            Path source = dir.resolve("fixture.atto");
            Files.write(source, original);
            Path artifact = dir.resolve("AttoObjectStorageM1.java");
            check(AttoObjectM1JavaCompiler.compile(source, sha(original), artifact) == 0,
                  "valid source failed compilation");
            String generated = Files.readString(artifact);
            check(generated.contains("Math.addExact("), "missing checked arithmetic");
            check(generated.contains("arena.read_i64(particle, actor, 101, 0)"),
                  "missing actor-owned storage read");
            check(generated.contains("System.exit(Math.toIntExact(result))"),
                  "missing real program exit");
            // An existing caller-owned destination must remain unchanged.
            check(AttoObjectM1JavaCompiler.compile(source, sha(original), artifact) == 2,
                  "clobbered existing output");
            check(Files.readString(artifact).equals(generated), "changed existing artifact");
            check(AttoObjectM1JavaCompiler.compile(source, "0".repeat(64),
                dir.resolve("bad-sha.java")) == 2, "accepted bad source hash");
            check(!Files.exists(dir.resolve("bad-sha.java")), "hash reject emitted artifact");

            Path other = dir.resolve("other.atto");
            Files.writeString(other, fixture.replace("velocity=3", "velocity=4"));
            Path output = dir.resolve("other.java");
            check(AttoObjectM1JavaCompiler.compile(other,
                sha(Files.readAllBytes(other)), output) == 0, "different valid source rejected");
            check(Files.readString(output).contains("16, 4L"),
                  "new velocity not lowered into Java");
            check(!Files.readString(output).equals(generated),
                  "compiled artifact did not change with source");

            Files.writeString(other, fixture.replace("particle.position.x",
                                                     "particle.position.secret"));
            rejected(other, dir.resolve("bad-field.java"));
            Files.writeString(other, fixture.replace("int y;", "int z;"));
            rejected(other, dir.resolve("bad-shape.java"));
            Files.writeString(other, fixture + "new Foo();\n");
            rejected(other, dir.resolve("bad-tail.java"));
            Files.writeString(other, fixture.replace("velocity=3",
                                                     "velocity=9223372036854775808"));
            rejected(other, dir.resolve("bad-range.java"));
            Files.writeString(other, fixture.replace(
                "particle.position.x + particle.velocity",
                "9223372036854775807 + 1"));
            rejected(other, dir.resolve("bad-overflow.java"));
            Files.writeString(other, fixture.replace("velocity=3", "velocity=🔒"));
            rejected(other, dir.resolve("bad-unicode.java"));
            check(Files.list(dir).noneMatch(p ->
                  p.getFileName().toString().startsWith(".atto-m1-java-")),
                  "temporary compiler files leaked");
            // Reject symlink input even if the target is a valid .atto file.
            Path link = dir.resolve("source-link.atto");
            Files.createSymbolicLink(link, source.getFileName());
            check(AttoObjectM1JavaCompiler.compile(link, sha(original),
                dir.resolve("bad-link.java")) == 2, "accepted source symlink");
            check(!Files.exists(dir.resolve("bad-link.java")), "link reject emitted output");
            // A pre-existing symlink output must not be followed or overwritten.
            Path planted = dir.resolve("planted.java");
            Files.createSymbolicLink(planted, artifact.getFileName());
            check(AttoObjectM1JavaCompiler.compile(source, sha(original), planted) == 2,
                  "overwrote output symlink");
            check(Files.readString(artifact).equals(generated), "changed output symlink target");
        } finally {
            try (var walk = Files.walk(dir)) {
                for (Path p : walk.sorted((a, b) -> b.compareTo(a)).toList()) {
                    Files.deleteIfExists(p);
                }
            }
        }
        System.out.println("ATTO_JAVA_M1_COMPILER_PROBE_PASS checks=" + checked);
    }
}
