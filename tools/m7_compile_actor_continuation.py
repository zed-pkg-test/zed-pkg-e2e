#!/usr/bin/env python3
"""Fail-closed compiler for the FIRST bounded M7 .atto actor continuation subset.

It emits real Java source, not a runtime interpreter. It recognizes exactly one
loop/accumulator/rt-cooperate actor receive grammar and rejects everything else.
Do not present this as the canonical AttoLang Java, Rust or LLVM compiler.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

TOKEN = re.compile(r"\s+|//[^\n]*|[A-Za-z_][A-Za-z_0-9]*|-?[0-9]+|[{}():;=+<]", re.ASCII)
NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z", re.ASCII)
LIMIT = 8192

class CompileError(ValueError):
    pass

def tokenize(source):
    if len(source.encode("utf-8")) > LIMIT:
        raise CompileError("source byte budget exceeded")
    pos, result = 0, []
    while pos < len(source):
        m = TOKEN.match(source, pos)
        if not m:
            raise CompileError(f"unsupported token at offset {pos}")
        word = m.group()
        pos = m.end()
        if not word.isspace() and not word.startswith("//"):
            result.append(word)
        if len(result) > 1024:
            raise CompileError("source token budget exceeded")
    return result

class Parser:
    def __init__(self, tokens):
        self.tokens, self.offset = tokens, 0
    def take(self, word=None):
        if self.offset >= len(self.tokens):
            raise CompileError("unexpected end of source")
        actual = self.tokens[self.offset]
        self.offset += 1
        if word is not None and actual != word:
            raise CompileError(f"expected {word!r} but found {actual!r}")
        return actual
    def name(self):
        name = self.take()
        if not NAME.fullmatch(name) or name in {
            "rt", "cooperate", "int", "while", "return", "let", "pub", "fnc",
            "actor", "define", "as"
        }:
            raise CompileError("invalid or reserved identifier")
        return name
    def number(self):
        token = self.take()
        if not re.fullmatch(r"-?[0-9]+", token, re.ASCII):
            raise CompileError("expected integer literal")
        value = int(token)
        if not -(1 << 63) <= value <= (1 << 63) - 1:
            raise CompileError("literal outside signed i64")
        return value
    def parse(self):
        self.take("define"); self.take("actor")
        name = self.name()
        self.take("as"); self.take("{")
        self.take("pub"); self.take("fnc"); self.take("on_message")
        self.take("("); self.take("int")
        amount = self.name()
        self.take(")"); self.take(":"); self.take("int"); self.take("{")
        self.take("let"); total = self.name()
        self.take(":"); self.take("int"); self.take("=")
        initial_total = self.number(); self.take(";")
        self.take("let"); index = self.name()
        self.take(":"); self.take("int"); self.take("=")
        initial_index = self.number(); self.take(";")
        if len({amount,total,index}) != 3:
            raise CompileError("aliased/overlapping typed live slots")
        self.take("while"); self.take("("); self.take(index)
        self.take("<"); self.take(amount); self.take(")"); self.take("{")
        self.take(total); self.take("="); self.take(total); self.take("+")
        self.take(index); self.take(";")
        self.take(index); self.take("="); self.take(index); self.take("+")
        increment = self.number(); self.take(";")
        if increment != 1 or initial_index < 0:
            raise CompileError("M7 restricted profile requires monotonic +1 index")
        self.take("rt"); self.take("cooperate"); self.take(";")
        self.take("}"); self.take("return"); self.take(total); self.take(";")
        self.take("}"); self.take("}")
        if self.offset != len(self.tokens):
            raise CompileError("unsupported trailing tokens")
        return dict(name=name, amount=amount, total=total, index=index,
                    initial_total=initial_total, initial_index=initial_index)

def make_m8_ir(spec):
    """Bounded typed IR receipt for the restricted compiled actor profile.

    Ownership/borrow claims here are valid ONLY because Parser.parse() admits
    two plain i64 locals and no borrow, critical, await, select or FFI.
    This is not the production Java or LLVM type checker.
    """
    return {
        "schema": "atto.m8.cooperative-ir.v1",
        "scope": "RESTRICTED_PYTHON_ACTOR_COMPILER_NOT_CANONICAL_FRONTEND",
        "actor": spec["name"],
        "parameter": {"name": spec["amount"], "type": "owned:i64"},
        "live_slots": [
            {"name": spec["total"], "type": "owned:i64",
             "initial": spec["initial_total"]},
            {"name": spec["index"], "type": "owned:i64",
             "initial": spec["initial_index"]}
        ],
        "instructions": [
            {"pc": 0, "op": "ENTRY", "next": 1},
            {"pc": 1, "op": "LOOP_GUARD", "on_true": 2, "on_false": 4},
            {"pc": 2, "op": "CHECKED_ACCUMULATE_AND_INCREMENT", "next": 3},
            {"pc": 3, "op": "COOPERATE", "resume_pc": 1},
            {"pc": 4, "op": "DONE"}
        ],
        "budget_reductions": {"min": 1, "max": 65536},
        "borrow_across_suspend": False,
        "critical_loan_across_suspend": False,
        "frame_generation_required": True
    }


def validate_m8_ir(ir):
    """Reject malformed typed plans before any executable output is produced."""
    if ir["schema"] != "atto.m8.cooperative-ir.v1":
        raise CompileError("unknown M8 safe-point IR schema")
    instructions = ir["instructions"]
    if [item["pc"] for item in instructions] != list(range(5)):
        raise CompileError("missing or duplicate safe-point PC")
    if [item["op"] for item in instructions] != [
            "ENTRY", "LOOP_GUARD", "CHECKED_ACCUMULATE_AND_INCREMENT",
            "COOPERATE", "DONE"]:
        raise CompileError("invalid M8 safe-point IR operations")
    if instructions[3]["resume_pc"] != 1:
        raise CompileError("M8 must save resume PC before returning YIELD")
    if ir["budget_reductions"] != {"min": 1, "max": 65536}:
        raise CompileError("unbounded M8 reductions")
    if ir["borrow_across_suspend"] or ir["critical_loan_across_suspend"]:
        raise CompileError("unproven borrow/critical loan across suspension")
    if not ir["frame_generation_required"]:
        raise CompileError("missing continuation incarnation fence")
    slots = ir["live_slots"]
    if len(slots) != 2 or slots[0]["name"] == slots[1]["name"]:
        raise CompileError("aliased M8 live slots")
    if any(s["type"] != "owned:i64" for s in slots):
        raise CompileError("unsupported M8 continuation ownership/type")


def render(spec, sha):
    # Every identifier was validated as an ASCII Java identifier without injection.
    # All other generated source is constant and strictly typed.
    return f"""// GENERATED by the restricted M7 .atto source compiler; no interpreter.
public final class M7GeneratedActor {{
    public static final String SOURCE_SHA256 = "{sha}";
    public static final String ACTOR_NAME = "{spec['name']}";
    private M7GeneratedActor() {{}}
    public static AttoM7CooperativeRuntime.Frame new_frame(long value) {{
        return new MessageFrame(value);
    }}
    private static final class MessageFrame implements AttoM7CooperativeRuntime.Frame {{
        private final long amount;
        private long total = {spec['initial_total']}L;
        private long index = {spec['initial_index']}L;
        private int pc;
        MessageFrame(long amount) {{ this.amount = amount; }}
        @Override public int resume_pc() {{ return pc; }}
        @Override public long result() {{
            if (pc != 4) throw new IllegalStateException("result before DONE");
            return total;
        }}
        @Override public AttoM7CooperativeRuntime.Step advance(
                AttoM7CooperativeRuntime.Slice slice) {{
            // PC 0=entry, 1=loop, 2=committed safe point, 3=return, 4=DONE.
            // The loop's side effects execute ONCE before PC advances to 2.
            for (;;) {{
                switch (pc) {{
                    case 0:
                        pc = 1;
                        continue;
                    case 1:
                        if (index >= amount) {{ pc = 3; continue; }}
                        total = Math.addExact(total, index);
                        index = Math.addExact(index, 1L);
                        pc = 2;
                        continue;
                    case 2:
                        pc = 1; // Commit next PC BEFORE yielding to the scheduler.
                        if (slice.cooperate())
                            return AttoM7CooperativeRuntime.Step.YIELD;
                        continue;
                    case 3:
                        pc = 4;
                        return AttoM7CooperativeRuntime.Step.DONE;
                    default:
                        throw new IllegalStateException("stale/invalid continuation PC");
                }}
            }}
        }}
    }}
}}
"""

def compile_source(source):
    tokens = tokenize(source)
    parsed = Parser(tokens).parse()
    sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return parsed, sha, render(parsed, sha)

def main():
    arg = argparse.ArgumentParser()
    arg.add_argument("source", type=Path)
    arg.add_argument("--out", required=True, type=Path)
    arg.add_argument("--source-sha256", required=True)
    options = arg.parse_args()
    try:
        data = options.source.read_bytes()
        source = data.decode("utf-8")
        parsed, sha, generated = compile_source(source)
        typed_ir = make_m8_ir(parsed)
        validate_m8_ir(typed_ir)
        if hashlib.sha256(data).hexdigest() != sha or sha != options.source_sha256:
            raise CompileError("pinned source SHA256 mismatch")
        options.out.mkdir(parents=True, exist_ok=True)
        output = options.out / "M7GeneratedActor.java"
        output.write_text(generated, encoding="utf-8")
        ir_path = options.out / "m8-typed-ir.json"
        ir_path.write_text(
            json.dumps(typed_ir, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest = {
            "schema": "atto.m7.cooperate.compiled-restricted.v1",
            "status": "GENERATED_NOT_EXECUTED",
            "source_sha256": sha,
            "java_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "m8_ir_sha256": hashlib.sha256(ir_path.read_bytes()).hexdigest(),
            "m8_ir_schema": typed_ir["schema"],
            "actor_name": parsed["name"],
            "compiler_scope": "restricted signed-i64 loop/rt-cooperate actor profile",
            "canonical_compiler": False
        }
        (options.out / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("M7_RESTRICTED_COMPILER_SOURCE_SHA256=" + sha)
    except (UnicodeError, OSError, CompileError) as err:
        arg.exit(2, f"M7_COMPILATION_REJECTED: {err}\n")

if __name__ == "__main__":
    main()
