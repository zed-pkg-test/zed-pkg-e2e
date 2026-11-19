#!/usr/bin/env python3
"""Atto ObjectStorage M1: deliberately closed-world, typed .atto -> Java/LLVM AOT.
This is a standalone experimental frontend, NOT the canonical general parser.
Exact-source hash admission, fixed shape, no executable guest source at runtime.
"""
import argparse
import hashlib
from pathlib import Path
import re
import sys

TOKEN = re.compile(r"\s+|//[^\n]*|-?[0-9]+|[A-Za-z_][A-Za-z_0-9]*|[{}():;.=+,-]", re.ASCII)
NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z", re.ASCII)
NUMBER = re.compile(r"-?[0-9]+\Z", re.ASCII)
MIN_I64 = -(1 << 63)
MAX_I64 = (1 << 63) - 1


class Reject(ValueError):
    pass


def tokenize(source):
    out, at = [], 0
    while at < len(source):
        match = TOKEN.match(source, at)
        if match is None:
            raise Reject("unsupported token or feature (no output emitted)")
        token = match.group()
        at = match.end()
        if not token.isspace() and not token.startswith("//"):
            out.append(token)
        if len(out) > 1000:
            raise Reject("object M1 token budget exceeded")
    return out


class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.i = 0

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self, expected):
        if self.peek() != expected:
            raise Reject("expected " + expected + " in closed-world ObjectStorage M1")
        self.i += 1

    def ident(self):
        value = self.peek()
        if value is None or not NAME.fullmatch(value) or value in {
            "return", "define", "class", "struct", "as", "new", "let", "end"
        }:
            raise Reject("expected safe identifier")
        self.i += 1
        return value

    def num(self):
        value = self.peek()
        if value is None or not NUMBER.fullmatch(value):
            raise Reject("expected signed i64 literal")
        number = int(value)
        if not MIN_I64 <= number <= MAX_I64:
            raise Reject("i64 literal out of range")
        self.i += 1
        return number

    def close_decl(self):
        self.take("end")
        if self.peek() == ";":
            self.take(";")

    def term(self, local, obj):
        value = self.peek()
        if value is not None and NUMBER.fullmatch(value):
            return ("literal", self.num())
        name = self.ident()
        self.take(".")
        field = self.ident()
        if name == local and field in ("x", "y"):
            return ("inline", 0 if field == "x" else 1)
        if name == obj:
            if field == "velocity":
                return ("object", 2)
            if field == "position":
                self.take(".")
                subfield = self.ident()
                if subfield in ("x", "y"):
                    return ("object", 0 if subfield == "x" else 1)
        raise Reject("invalid typed field path")

    def parse(self):
        self.take("define")
        self.take("struct")
        self.take("Point")
        self.take("as")
        for name in ("x", "y"):
            self.take("int")
            self.take(name)
            self.take(";")
        self.close_decl()

        self.take("define")
        self.take("class")
        self.take("Particle")
        self.take("as")
        self.take("final")
        self.take("Point")
        self.take("position")
        self.take(";")
        self.take("int")
        self.take("velocity")
        self.take(";")
        self.close_decl()

        self.take("pub")
        self.take("fnc")
        self.take("main")
        self.take("(")
        self.take(")")
        self.take(":")
        self.take("int")
        self.take("{")
        self.take("let")
        self.take("Point")
        local = self.ident()
        self.take("=")
        self.take("Point")
        self.take("(")
        self.take("x")
        self.take("=")
        x = self.num()
        self.take(",")
        self.take("y")
        self.take("=")
        y = self.num()
        self.take(")")
        self.take(";")
        self.take("let")
        self.take("Particle")
        obj = self.ident()
        if obj == local:
            raise Reject("nominal owner name collides with inline value")
        self.take("=")
        self.take("new")
        self.take("Particle")
        self.take("(")
        self.take("position")
        self.take("=")
        self.take(local)
        self.take(",")
        self.take("velocity")
        self.take("=")
        velocity = self.num()
        self.take(")")
        self.take(";")
        self.take("return")
        terms = [self.term(local, obj)]
        ops = []
        while self.peek() in ("+", "-"):
            ops.append(self.peek())
            self.i += 1
            terms.append(self.term(local, obj))
            if len(terms) > 8:
                raise Reject("expression depth budget exceeded")
        self.take(";")
        self.take("}")
        if self.peek() is not None:
            raise Reject("unexpected trailing guest program tokens")
        values = [x, y, velocity]
        def val(term):
            return term[1] if term[0] == "literal" else values[term[1]]
        result = val(terms[0])
        for op, term in zip(ops, terms[1:]):
            result = result + val(term) if op == "+" else result - val(term)
            if not MIN_I64 <= result <= MAX_I64:
                raise Reject("checked arithmetic overflow")
        if not 0 <= result <= 255:
            raise Reject("M1 native process exit result must fit 0..255")
        return {"x": x, "y": y, "velocity": velocity,
                "terms": terms, "ops": ops, "result": result,
                "type": "Particle", "owner_domain": "main.actor.local",
                "field_payload_bytes": 24, "inline_struct_bytes": 16}


def java_i64(value):
    return "Long.MIN_VALUE" if value == MIN_I64 else str(value) + "L"


def render_java(plan):
    # Object storage is the tested actor-owned FFM arena; Point stays in two
    # unboxed primitive locals. Host TrustedRef still allocates a Java wrapper.
    fields = [java_i64(plan["x"]), java_i64(plan["y"]),
              java_i64(plan["velocity"])]
    def expr(term):
        kind, value = term
        if kind == "literal":
            return java_i64(value)
        if kind == "inline":
            return "point_x" if value == 0 else "point_y"
        return "arena.read_i64(particle, actor, 101, " + str(value * 8) + ")"
    expression = expr(plan["terms"][0])
    for operator, term in zip(plan["ops"], plan["terms"][1:]):
        method = "addExact" if operator == "+" else "subtractExact"
        expression = "Math." + method + "(" + expression + ", " + expr(term) + ")"
    return """// Generated only from type-checked, bounded closed-world .atto source.
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
""" % (*fields, expression)


def render_llvm(plan):
    # Real native IR calls the trusted actor heap bridge, never raw pointer
    # reads in guest IR. The bridge is exactly one single-actor namespace.
    lines = [
        "; Generated from closed-world typed .atto source; single actor only.",
        "declare i32 @atto_m1_allocate()",
        "declare void @atto_m1_write(i32, i32, i64)",
        "declare i64 @atto_m1_read(i32, i32)",
        "declare i32 @atto_m1_finish(i32, i64)",
        "declare { i64, i1 } @llvm.sadd.with.overflow.i64(i64, i64)",
        "declare { i64, i1 } @llvm.ssub.with.overflow.i64(i64, i64)",
        "declare void @llvm.trap()",
        "define i32 @main() {",
        "entry:",
        "  %particle = call i32 @atto_m1_allocate()"
    ]
    for index, value in enumerate((plan["x"], plan["y"], plan["velocity"])):
        lines.append("  call void @atto_m1_write(i32 %particle, i32 " +
                     str(index) + ", i64 " + str(value) + ")")
    t = 0
    def value(term):
        nonlocal t
        kind, v = term
        if kind == "literal":
            return str(v)
        if kind == "inline":
            return str(plan["x"] if v == 0 else plan["y"])
        t += 1
        name = "%field" + str(t)
        lines.append("  " + name + " = call i64 @atto_m1_read(i32 %particle, i32 " + str(v) + ")")
        return name
    accumulator = value(plan["terms"][0])
    for i, (operator, term) in enumerate(zip(plan["ops"], plan["terms"][1:])):
        right = value(term)
        pair = "%pair" + str(i)
        result = "%sum" + str(i)
        overflow = "%overflow" + str(i)
        intr = "sadd" if operator == "+" else "ssub"
        ok_label = "next" + str(i)
        err_label = "fail" + str(i)
        lines += [
            "  " + pair + " = call { i64, i1 } @llvm." + intr +
            ".with.overflow.i64(i64 " + accumulator + ", i64 " + right + ")",
            "  " + result + " = extractvalue { i64, i1 } " + pair + ", 0",
            "  " + overflow + " = extractvalue { i64, i1 } " + pair + ", 1",
            "  br i1 " + overflow + ", label %" + err_label + ", label %" + ok_label,
            err_label + ":",
            "  call void @llvm.trap()",
            "  unreachable",
            ok_label + ":",
        ]
        accumulator = result
    lines += [
        "  %rc = call i32 @atto_m1_finish(i32 %particle, i64 " + accumulator + ")",
        "  ret i32 %rc",
        "}",
    ]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source")
    ap.add_argument("--source-sha256", required=True)
    ap.add_argument("--backend", required=True, choices=("java", "llvm"))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    try:
        source = Path(args.source)
        output = Path(args.out)
        if not source.is_file() or source.is_symlink() or source.suffix != ".atto":
            raise Reject("source must be a regular, nonsymlinked .atto file")
        if output.exists() or output.is_symlink():
            raise Reject("output path already exists")
        if not 0 < source.stat().st_size <= 16384:
            raise Reject("source byte limit exceeded")
        raw = source.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        if not re.fullmatch(r"[0-9a-f]{64}", args.source_sha256) or sha != args.source_sha256:
            raise Reject("source SHA-256 mismatch")
        plan = Parser(tokenize(raw.decode("utf-8", errors="strict"))).parse()
        result = render_java(plan) if args.backend == "java" else render_llvm(plan)
        # Exclusive O_EXCL file creation: do not overwrite an output planted
        # between admission and emission, including a symlink.
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            written = stream.write(result)
        if written != len(result) or not output.is_file() or output.stat().st_size == 0:
            raise Reject("generated backend artifact missing or incomplete")
        print("ATTO_OBJECT_M1_COMPILE_PASS backend=" + args.backend +
              " source_sha256=" + sha + " expected_exit=" + str(plan["result"]))
    except (Reject, UnicodeError, OSError, ValueError) as exc:
        # Never emit a partial artifact or an LLVM stub on rejection.
        print("ATTO_OBJECT_M1_REJECT: " + str(exc)[:160], file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
