#!/usr/bin/env python3
"""Closed-world AttoLang actor -> static Java source generator (experimental).

This compiler accepts a deliberately *small and explicitly specified* subset.
It does not interpret guest bytecode at runtime; all actor operations are
statically emitted as Java application code for GraalVM Native Image AOT.

NOT a complete AttoLang parser, tenant sandbox, concurrent actor scheduler,
ownership checker or full-language compiler. Unsupported syntax fails closed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import os
import subprocess
import tempfile

TOKEN = re.compile(r"\s+|//[^\n]*|-?[0-9]+|[A-Za-z_][A-Za-z_0-9]*|[{}():;.=+^]", re.ASCII)
NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z", re.ASCII)
MAX_SOURCE = 32768
MAX_ACTORS = 16
MAX_ACTOR_INSTANCES = 64  # Prevent an unbounded number of per-actor native arenas.
MAX_SENDS = 1024  # Reachable below 32 KiB source / 50k-token caps; bounds native work.


class CompileError(ValueError):
    pass


def source_tokens(source):
    pos = 0
    tokens = []
    while pos < len(source):
        found = TOKEN.match(source, pos)
        if not found:
            raise CompileError("unsupported AttoLang token at byte/character position " + str(pos))
        value = found.group()
        pos = found.end()
        if value.isspace() or value.startswith("//"):
            continue
        tokens.append(value)
        if len(tokens) > 50000:
            raise CompileError("source token limit")
    return tokens


class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self, wanted=None):
        actual = self.peek()
        if actual is None:
            raise CompileError("unexpected end of AttoLang source")
        if wanted is not None and actual != wanted:
            raise CompileError(f"expected {wanted!r} but found {actual!r}")
        self.pos += 1
        return actual

    def identifier(self):
        x = self.take()
        if not NAME.fullmatch(x):
            raise CompileError("expected an identifier")
        if x == "guard":
            raise CompileError("reserved AttoLang guard keyword cannot be an identifier")
        return x

    def integer(self):
        token = self.take()
        if not re.fullmatch(r"-?[0-9]+", token, re.ASCII):
            raise CompileError("expected a signed integer literal")
        result = int(token)
        if not -(1 << 63) <= result < (1 << 63):
            raise CompileError("integer exceeds the AttoLang native i64 range")
        return result

    def return_marker(self):
        # ^ is a lexical return statement, never an arithmetic operator.
        marker = self.take()
        if marker not in ("return", "^"):
            raise CompileError("expected 'return' or '^'")
        return marker

    def actor(self):
        self.take("define")
        self.take("actor")
        name = self.identifier()
        self.take("as")
        self.take("{")
        self.take("let")
        field = self.identifier()
        self.take(":")
        self.take("int")
        self.take("=")
        initial = self.integer()
        self.take(";")
        self.take("pub")
        self.take("fnc")
        self.take("on_message")
        self.take("(")
        self.take("int")
        parameter = self.identifier()
        self.take(")")
        self.take(":")
        self.take("int")
        self.take("{")
        self.take(field)
        self.take("=")
        self.take(field)
        self.take("+")
        self.take(parameter)
        self.take(";")
        self.return_marker()
        self.take(field)
        self.take(";")
        self.take("}")
        self.take("}")
        if parameter == field:
            raise CompileError("actor message name aliases state field")
        return (name, field, parameter, initial)

    def main(self, kinds):
        self.take("pub")
        self.take("fnc")
        self.take("main")
        self.take("(")
        self.take(")")
        self.take(":")
        self.take("int")
        self.take("{")
        live = {}
        seen = set()
        actions = []
        sends = 0
        instances = 0
        guards = 0
        while True:
            token = self.peek()
            if token is None:
                raise CompileError("unterminated main")
            if token == "const":
                self.take("const")
                variable = self.identifier()
                self.take("=")
                self.take("spawn")
                actor_name = self.identifier()
                self.take("(")
                self.take(")")
                self.take(";")
                if variable in seen:
                    raise CompileError("actor handle redeclared: " + variable)
                instances += 1
                if instances > MAX_ACTOR_INSTANCES:
                    raise CompileError("actor instance budget exceeded")
                if actor_name not in kinds:
                    raise CompileError("unknown actor type: " + actor_name)
                seen.add(variable)
                live[variable] = actor_name
                actions.append(("spawn", variable, actor_name))
            elif token == "stop":
                self.take("stop")
                variable = self.identifier()
                self.take(";")
                if variable not in live:
                    raise CompileError("invalid or already retired actor handle: " + variable)
                del live[variable]
                actions.append(("stop", variable, None))
            elif token == "guard":
                # Experimental fail-on-false guard, restricted to the literal
                # bool subset. Do not allow a path to return while any
                # arena-backed actor remains alive: that would skip stop.
                if live:
                    raise CompileError("guard may exit only after all actor handles are stopped")
                guards += 1
                if guards > 8:
                    raise CompileError("guard v1 limit exceeded (8)")
                self.take("guard")
                condition = self.take()
                if condition not in ("true", "false"):
                    raise CompileError("guard condition must be a bool literal in the NativeAOT pilot")
                self.take("{")
                self.return_marker()
                early_value = self.integer()
                self.take(";")
                self.take("}")
                if self.peek() == "else":
                    raise CompileError(
                        "E_GUARD_ELSE_NOT_REQUIRED: Atto guard has no else arm")
                actions.append(("guard", condition == "true", early_value))
            elif token in ("return", "^"):
                self.return_marker()
                result = self.integer()
                self.take(";")
                self.take("}")
                if self.peek() is not None:
                    raise CompileError("unexpected code after main")
                if live:
                    raise CompileError("actor handle not stopped before return")
                if not actions or sends == 0:
                    raise CompileError("actor AOT fixture must execute at least one message")
                return actions, result, sends
            else:
                variable = self.identifier()
                self.take(".")
                self.take("send")
                self.take("(")
                message = self.integer()
                self.take(")")
                self.take(";")
                if variable not in live:
                    raise CompileError("message send to nonexistent or retired actor: " + variable)
                sends += 1
                if sends > MAX_SENDS:
                    raise CompileError("actor message budget exceeded")
                actions.append(("send", variable, message))


def parse_atto(source):
    p = Parser(source_tokens(source))
    kinds = {}
    while p.peek() == "define":
        actor = p.actor()
        if actor[0] in kinds:
            raise CompileError("duplicate actor type: " + actor[0])
        kinds[actor[0]] = actor
        if len(kinds) > MAX_ACTORS:
            raise CompileError("too many actors for static pilot")
    if not kinds:
        raise CompileError("at least one concrete actor required")
    actions, result, sends = p.main(kinds)
    return kinds, actions, result, sends


JAVA_ACTOR = """    private static final class CLASS implements AutoCloseable {
        private final java.lang.foreign.Arena arena = java.lang.foreign.Arena.ofConfined();
        private final java.lang.foreign.MemorySegment state =
                arena.allocate(java.lang.foreign.ValueLayout.JAVA_LONG);
        private boolean closed;
        CLASS() {
            state.set(java.lang.foreign.ValueLayout.JAVA_LONG, 0, INIT);
        }
        long send(long value) {
            if (closed) throw new IllegalStateException("send after retirement");
            long current = state.get(java.lang.foreign.ValueLayout.JAVA_LONG, 0);
            long next = Math.addExact(current, value);
            // Preflight *all* fallible arithmetic before writing actor state.
            // A failed send must not partially mutate state/counters.
            long nextDigest = Math.addExact(Math.multiplyExact(actorStateDigest, 31L), next);
            long nextCount = Math.addExact(sentMessages, 1L);
            state.set(java.lang.foreign.ValueLayout.JAVA_LONG, 0, next);
            sentMessages = nextCount;
            actorStateDigest = nextDigest;
            return next;
        }
        @Override public void close() {
            if (closed) throw new IllegalStateException("double retirement");
            closed = true;
            arena.close();
            closedActors++;
        }
    }
"""


def long_literal(i):
    return "Long.MIN_VALUE" if i == -(1 << 63) else str(i) + "L"


def generate(kinds, actions, result):
    aliases = {name: "Actor_" + str(i) for i, name in enumerate(kinds)}
    out = [
        "// MACHINE-GENERATED from a strictly verified AttoLang subset.",
        "// Contains statically lowered guest logic, NOT an interpreter or runtime parser.",
        "public final class AttoGeneratedMain {",
        "    private static long sentMessages = 0L;",
        "    private static long actorStateDigest = 0L;",
        "    private static long closedActors = 0L;",
    ]
    for name, _, _, initial in kinds.values():
        out.append(JAVA_ACTOR.replace("CLASS", aliases[name])
                   .replace("INIT", long_literal(initial)))
    out += ["    private static long guest_main() {"]
    variables = {}
    for kind, name, arg in actions:
        if kind == "spawn":
            variable = "handle_" + str(len(variables))
            variables[name] = variable
            out.append("        " + aliases[arg] + " " + variable + " = new " + aliases[arg] + "();")
        elif kind == "send":
            out.append("        " + variables[name] + ".send(" + long_literal(arg) + ");")
        elif kind == "guard":
            # A real generated Java conditional/return, folded only by
            # GraalVM's native compiler. No runtime source interpreter.
            out.append("        if (!" + ("true" if name else "false") + ") return " +
                       long_literal(arg) + ";")
        else:
            out.append("        " + variables[name] + ".close();")
    out += [
        "        return " + long_literal(result) + ";",
        "    }",
        "    private static long collections() {",
        "        long result = 0L;",
        "        var beans = java.lang.management.ManagementFactory.getGarbageCollectorMXBeans();",
        "        if (beans.isEmpty()) return -1L;",
        "        for (var bean : beans) {",
        "            long count = bean.getCollectionCount();",
        "            if (count < 0L) return -1L;",
        "            result = Math.addExact(result, count);",
        "        }",
        "        return result;",
        "    }",
        "    public static void main(String[] args) {",
        "        if (args.length != 0) throw new IllegalArgumentException(\"no runtime guest source accepted\");",
        "        long gcBefore = collections();",
        "        long guestResult = guest_main();",
        "        long gcAfter = collections();",
        '        System.out.println("ATTO_GUEST_NATIVE_AOT_RETURN=" + guestResult);',
        '        System.out.println("ATTO_GUEST_ACTOR_SENDS=" + sentMessages);',
        '        System.out.println("ATTO_GUEST_ACTOR_CLOSES=" + closedActors);',
        '        System.out.println("ATTO_GUEST_ACTOR_STATE_DIGEST=" + actorStateDigest);',
        '        System.out.println("ATTO_GUEST_GC_DELTA=" +',
        '            ((gcBefore >= 0L && gcAfter >= gcBefore) ? Long.toString(gcAfter-gcBefore) : "UNKNOWN"));',
        '        System.out.println("ATTO_NATIVE_AOT_SCOPE=STATIC_ATTO_ACTOR_SUBSET_NOT_TENANT_SANDBOX");',
        "    }",
        "}",
    ]
    return "\n".join(out) + "\n"



def compile_with_checked_exporter(source_bytes, exporter, claimed_sha256):
    """Compile from the canonical LLVM frontend's checked actor AST projection.

    The exporter MUST be an actual executable verified against caller's
    pinned binary hash. No ability to supply precomputed JSON: a checked
    binary is executed on exactly the source bytes being compiled.
    """
    executable = Path(exporter)
    if (not executable.is_file() or executable.is_symlink() or
            not os.access(executable, os.X_OK) or executable.stat().st_size > 150 * 1024 * 1024):
        raise CompileError("canonical exporter binary missing, unsafe or not executable")
    raw_executable = executable.read_bytes()
    if (not re.fullmatch(r"[0-9a-f]{64}", claimed_sha256) or
            hashlib.sha256(raw_executable).hexdigest() != claimed_sha256):
        raise CompileError("canonical exporter executable digest mismatch")
    with tempfile.TemporaryDirectory(prefix="atto-m5-canonical-") as temp:
        # Execute the *exact bytes just hashed*, not the original mutable
        # path that a competing writer could swap between check and exec.
        checked = Path(temp) / "checked-exporter"
        with checked.open("xb") as stream:
            stream.write(raw_executable)
        checked.chmod(0o500)
        if hashlib.sha256(checked.read_bytes()).hexdigest() != claimed_sha256:
            raise CompileError("canonical exporter private copy digest mismatch")
        pinned = Path(temp) / "input.atto"
        pinned.write_bytes(source_bytes)
        try:
            result = subprocess.run(
                [str(checked), str(pinned)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=False, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as err:
            raise CompileError("canonical checked frontend could not execute") from err
        if result.returncode != 0 or len(result.stdout) > 131072 or len(result.stderr) > 8192:
            raise CompileError("canonical checked frontend rejected source or exceeded its output budget")
        try:
            ir = decode_checked_ir(result.stdout)
        except (UnicodeError, ValueError) as err:
            raise CompileError("canonical checked frontend emitted invalid JSON") from err
    return validate_checked_actor_ir(ir)


def decode_checked_ir(data):
    """Strict canonical JSON: reject duplicates, NaN, Infinity, and bad UTF-8."""
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise CompileError("canonical checked IR has duplicate JSON key")
            result[key] = value
        return result

    def no_special_number(value):
        raise CompileError("canonical checked IR has non-finite JSON number")

    try:
        return json.loads(data.decode("utf-8", "strict"),
                          object_pairs_hook=unique_object,
                          parse_constant=no_special_number)
    except (UnicodeError, ValueError, TypeError) as err:
        raise CompileError("canonical checked IR rejected malformed JSON") from err


def validate_checked_actor_ir(ir):
    """A strict typed-IR shape validator; never independently authorizes guest source."""
    if not isinstance(ir, dict) or set(ir) != {
            "schema", "checker", "status", "actor", "spawns",
            "actions", "return_i64", "limits", "unsupported"}:
        raise CompileError("canonical checked IR schema or fields invalid")
    if (ir["schema"] != "atto.canonical-checked-actor-ir.v0" or
            ir["checker"] != "LLVM_PARSE_PROGRAM_OWNERSHIP_SCALAR_TYPES" or
            ir["status"] != "CHECKED_RESTRICTED_ACTOR_SLICE" or
            ir["limits"] != {"max_actor_instances": 64, "max_source_sends": 1024}):
        raise CompileError("canonical checked IR checker or type contract invalid")
    a = ir["actor"]
    if not isinstance(a, dict) or set(a) != {"name", "field", "initial", "parameter", "operation"}:
        raise CompileError("canonical actor record invalid")
    name, field, parameter = a["name"], a["field"], a["parameter"]
    if (not all(isinstance(v, str) and NAME.fullmatch(v) and len(v) <= 128
                for v in (name, field, parameter)) or field == parameter or
            a["operation"] != "checked_add_i64_return_field" or
            type(a["initial"]) is not int or not -(1 << 63) <= a["initial"] < (1 << 63)):
        raise CompileError("canonical actor typed additive handler invalid")
    if not isinstance(ir["spawns"], list) or not 1 <= len(ir["spawns"]) <= MAX_ACTOR_INSTANCES:
        raise CompileError("canonical actor spawn budget invalid")
    actions = []
    live = set()
    seen = set()
    for item in ir["spawns"]:
        if (not isinstance(item, dict) or set(item) != {"ref", "kind"} or
                not isinstance(item["ref"], str) or not NAME.fullmatch(item["ref"]) or
                item["kind"] != name or item["ref"] in seen):
            raise CompileError("canonical spawn identity or actor kind invalid")
        live.add(item["ref"])
        seen.add(item["ref"])
        actions.append(("spawn", item["ref"], name))
    ops = ir["actions"]
    if not isinstance(ops, list) or len(ops) > MAX_SENDS + MAX_ACTOR_INSTANCES:
        raise CompileError("canonical actor action budget invalid")
    sends = 0
    for op in ops:
        if (not isinstance(op, dict) or
                not isinstance(op.get("ref"), str) or
                not NAME.fullmatch(op["ref"]) or op["ref"] not in seen):
            raise CompileError("canonical actor handle missing")
        ref = op["ref"]
        if op.get("op") == "send" and set(op) == {"op", "ref", "value"}:
            if ref not in live or type(op["value"]) is not int or not -(1 << 63) <= op["value"] < (1 << 63):
                raise CompileError("canonical invalid/stale send or payload type")
            actions.append(("send", ref, op["value"]))
            sends += 1
            if sends > MAX_SENDS:
                raise CompileError("canonical message budget exceeded")
        elif op.get("op") == "stop" and set(op) == {"op", "ref"}:
            if ref not in live:
                raise CompileError("canonical duplicate or stale actor stop")
            live.remove(ref)
            actions.append(("stop", ref, None))
        else:
            raise CompileError("canonical unsupported actor action")
    if live or sends == 0:
        raise CompileError("canonical actor handles not closed or no messages executed")
    result = ir["return_i64"]
    if type(result) is not int or not -(1 << 63) <= result < (1 << 63):
        raise CompileError("canonical main return must be i64")
    if ir["unsupported"] != [
            "await", "critical-read", "critical-write", "rt_take",
            "rt_share", "dynamic_guest_code", "tenant_sandbox"]:
        # A missing unsupported effect must not be silently treated as
        # checked, and unfamiliar fields require a new reviewed schema.
        # In particular, no deep graph immutability/ownership capability
        # is projected by this narrow static actor slice.
        raise CompileError("canonical unsupported ownership or tenant operations silently omitted")
    return {name: (name, field, parameter, a["initial"])}, actions, result, sends


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source")
    ap.add_argument("--source-sha256", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--checked-exporter", help="run exact pinned canonical LLVM frontend IR exporter")
    ap.add_argument("--checked-exporter-sha256")
    args = ap.parse_args(argv)
    try:
        path = Path(args.source)
        if not path.is_file() or path.is_symlink() or path.suffix != ".atto":
            raise CompileError("only a regular nonsymlinked .atto source file is accepted")
        if path.stat().st_size == 0 or path.stat().st_size > MAX_SOURCE:
            raise CompileError("AttoLang source byte budget exceeded")
        raw = path.read_bytes()
        exact = hashlib.sha256(raw).hexdigest()
        if not re.fullmatch("[0-9a-f]{64}", args.source_sha256) or args.source_sha256 != exact:
            raise CompileError("source bytes do not match supplied SHA-256")
        source = raw.decode("utf-8", errors="strict")
        if bool(args.checked_exporter) != bool(args.checked_exporter_sha256):
            raise CompileError("canonical exporter binary and SHA256 must be provided together")
        if args.checked_exporter:
            kinds, actions, guest_result, sends = compile_with_checked_exporter(
                raw, args.checked_exporter, args.checked_exporter_sha256)
        else:
            kinds, actions, guest_result, sends = parse_atto(source)
        result = Path(args.out)
        if result.exists() or result.is_symlink():
            raise CompileError("output path exists; refusing to replace artifacts")
        java = generate(kinds, actions, guest_result)
        # The exact closed graph comes from parser-validated declarations,
        # not regex guesses over arbitrary user text. This application-specific
        # pilot has no generic, typeclass, dynamic-module or FFI source syntax.
        # A native Image can still include host-library internals: this is
        # NOT a full linked-binary reachability or zero-GC certificate.
        spawned_actor_types = {
            actor_type for kind, _, actor_type in actions if kind == "spawn"
        }
        declared_actor_nodes = [
            {"source_actor": name, "generated_type": "AttoGeneratedMain.Actor_" + str(index)}
            for index, name in enumerate(kinds)
        ]
        actor_roots = [
            {
                "source_actor": name,
                "generated_type": "AttoGeneratedMain.Actor_" + str(index),
                "handler": "AttoGeneratedMain.Actor_" + str(index) + ".send",
                "retirement": "AttoGeneratedMain.Actor_" + str(index) + ".close",
            }
            for index, name in enumerate(kinds) if name in spawned_actor_types
        ]
        for index in range(len(declared_actor_nodes)):
            if ("private static final class Actor_" + str(index)) not in java:
                raise CompileError("generated actor type missing from closed-world profile")
        if "private static long guest_main()" not in java:
            raise CompileError("generated entrypoint missing from closed-world profile")
        forbidden_static_java = (
            "Class.forName(", "java.lang.reflect.", "MethodHandles.lookup(",
            "Proxy.newProxyInstance(", "Runtime.getRuntime(", "ProcessBuilder(",
            "javax.script.", "Unsafe.allocateInstance(", "System.loadLibrary(",
        )
        if any(word in java for word in forbidden_static_java):
            raise CompileError("unapproved dynamic Java API in static actor lowering")
        closed_world = {
            "schema": "atto.static-actor.closed-world.pilot.v1",
            "status": "COMPILER_GENERATED_RESTRICTED_GRAPH_NOT_CERTIFIED",
            "single_source_sha256": exact,
            "entry_roots": ["AttoGeneratedMain.guest_main"],
            "declared_actor_nodes": declared_actor_nodes,
            "actor_roots": actor_roots,
            "actor_instance_spawns": sum(kind == "spawn" for kind, _, _ in actions),
            "action_count": len(actions),
            "action_kinds": sorted({kind for kind, _, _ in actions}),
            "generic_specializations": [],
            "typeclass_witnesses": [],
            "guest_dynamic_imports": [],
            "guest_dynamic_loading": False,
            "guest_runtime_reflection": False,
            "guest_runtime_codegen": False,
            "host_primitives": [
                "ffm.confined_actor_arena", "java.checked_arithmetic",
                "java.gc_metrics", "java.stdout_observation",
            ],
            "actor_storage_mode": "CONFINED_OFFHEAP_ARENA_WITH_EXPLICIT_RETIREMENT",
            "native_aot_artifact_verified": False,
            "authenticated_execution_receipt": False,
        }
        # Predict the expected exit from the already-validated literal
        # guards, but do not claim success until the native binary runs.
        expected_result = next((value for kind, condition, value in actions
                                if kind == "guard" and not condition), guest_result)
        manifest = {
            "schema": "atto.static-actor-graal-aot-pilot.v1",
            "input": path.name,
            "source_sha256": exact,
            "codegen_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "generated_java_sha256": hashlib.sha256(java.encode()).hexdigest(),
            "actor_kinds": len(kinds),
            "actor_sends": sends,
            "compiler_authority": "CANONICAL_LLVM_AST" if args.checked_exporter else "RESTRICTED_PYTHON_PILOT",
            "canonical_exporter_sha256": args.checked_exporter_sha256 if args.checked_exporter else None,
            "actor_instances": sum(1 for kind, _, _ in actions if kind == "spawn"),
            "closed_world": closed_world,
            "expected_guest_result": expected_result,
            "status": "GENERATED_SOURCE_NOT_YET_EXECUTED",
            "scope": "single-threaded static actor syntax subset, no tenant sandbox or full AttoLang compiler",
        }
        result.mkdir(parents=True, exist_ok=False)
        (result / "AttoGeneratedMain.java").write_text(java, encoding="utf-8")
        (result / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2)+"\n")
        print("ATTO_STATIC_GUEST_JAVA_SHA256="+manifest["generated_java_sha256"])
        print("ATTO_STATIC_GUEST_COMPILATION=SOURCE_GENERATED_NOT_EXECUTED")
        return 0
    except (CompileError, OSError, ValueError, UnicodeError) as exc:
        print("ATTO_AOT_COMPILE_REFUSED: "+str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
