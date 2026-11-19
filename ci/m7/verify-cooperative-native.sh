#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
expected="${ATTO_EXPECTED_HEAD_SHA:-}"
if [[ ! "$expected" =~ ^[0-9a-f]{40}$ ]] || [[ "$(git rev-parse HEAD)" != "$expected" ]]; then
  echo 'M7_NOT_EXECUTED: exact Git HEAD required' >&2; exit 2
fi
git diff --quiet HEAD -- || { echo 'M7_NOT_EXECUTED: dirty source checkout' >&2; exit 2; }
test -z "$(git ls-files --others --exclude-standard)" || {
  echo 'M7_NOT_EXECUTED: unexpected untracked source' >&2; exit 2;
}
test -x "${JAVA_HOME:-}/bin/javac" && test -x "$JAVA_HOME/bin/native-image" || {
  echo 'M7_NOT_EXECUTED: GraalVM JDK25 Native Image missing' >&2; exit 2;
}
python3 -m unittest discover -s tests/m7 -p 'test_*.py' -v
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
source=fixtures/m7/cooperative_sum.atto
source_sha="$(sha256sum "$source" | cut -d ' ' -f 1)"
python3 tools/m7_compile_actor_continuation.py "$source" --source-sha256 "$source_sha" --out "$tmp/generated"
python3 - "$tmp/generated" "$source_sha" <<'PY'
import hashlib, json, pathlib, sys
folder = pathlib.Path(sys.argv[1])
manifest = json.loads((folder / "manifest.json").read_text())
generated = (folder / "M7GeneratedActor.java").read_bytes()
assert manifest["schema"] == "atto.m7.cooperate.compiled-restricted.v1"
assert manifest["status"] == "GENERATED_NOT_EXECUTED"
assert manifest["source_sha256"] == sys.argv[2]
assert manifest["java_sha256"] == hashlib.sha256(generated).hexdigest()
assert manifest["actor_name"] == "CooperativeSum"
assert manifest["canonical_compiler"] is False
print("M7_COMPILER_MANIFEST_SHA256_VERIFIED=PASS")
PY
# New M9 conformance is Rust-first. The legacy M7 compiler remains a
# restricted Python pilot and is tracked separately for migration.
command -v rustc >/dev/null || { echo M9_RUST_TOOLCHAIN_REQUIRED >&2; exit 2; }
rustc --edition=2021 --test tests/m9_notice_wire.rs -o "$tmp/m9-wire-tests"
"$tmp/m9-wire-tests" --nocapture
rustc --edition=2021 tests/m9_notice_wire.rs -o "$tmp/m9-wire-check"
echo 'M9_JAVA_RUST_CONFORMANCE_TESTS=PASS'
mkdir -p "$tmp/classes"
"$JAVA_HOME/bin/javac" -d "$tmp/classes" \
  src/m7/AttoM7CooperativeRuntime.java \
  tests/m7/M7CompiledActorProbe.java \
  tests/m7/M7CallbackIsolationAuditProbe.java \
  tests/m9/M9JavaSupervisorNoticeProbe.java \
  "$tmp/generated/M7GeneratedActor.java"
if ! timeout 120 "$JAVA_HOME/bin/java" -cp "$tmp/classes" M7CompiledActorProbe \
        > "$tmp/jvm" 2> "$tmp/jvm.err"; then
  echo 'M7_JVM_EXECUTION_FAILED' >&2
  cat "$tmp/jvm.err" >&2; exit 1
fi
(
  cd "$tmp"
  "$JAVA_HOME/bin/native-image" --no-fallback --gc=epsilon \
    -H:Name=m7-restricted-compiled-actor -cp "$tmp/classes" M7CompiledActorProbe
)
test -x "$tmp/m7-restricted-compiled-actor"
file "$tmp/m7-restricted-compiled-actor" | grep -Eq 'ELF|Mach-O' || {
  echo 'M7_NATIVE_EXECUTABLE_MISSING' >&2; exit 1;
}
echo "M7_NATIVE_SHA256=$(sha256sum "$tmp/m7-restricted-compiled-actor" | cut -d ' ' -f 1)"
if ! timeout 120 "$tmp/m7-restricted-compiled-actor" \
       > "$tmp/native" 2> "$tmp/native.err"; then
  echo 'M7_NATIVE_EXECUTION_FAILED' >&2
  cat "$tmp/native.err" >&2; exit 1
fi
test ! -s "$tmp/native.err" || { cat "$tmp/native.err" >&2; exit 1; }
cat "$tmp/native"
for marker in \
  M7_COMPILED_ATTO_PC_YIELD_RESUME=PASS \
  M7_COMPILED_ATTO_ACCEPTED_SETTLED=2 \
  M7_COMPILED_ACTOR_NEIGHBOR_PROGRESS=PASS \
  M7_COMPILED_CONTINUATION_REINCARNATION_FENCE=PASS \
  M7_SERIAL_32_ACTORS_4_CARRIERS=PASS \
  M7_CONTROL_STOP_PANIC_WHILE_YIELDED=PASS \
  M7_CONTROL_1000_STOP_COALESCED=PASS \
  M7_AUDIT_BLOCKED_FRAME_FACTORY_LOCK_ISOLATION=PASS \
  M7_AUDIT_BLOCKED_RESULT_NO_GLOBAL_MONITOR=PASS \
  M7_AUDIT_PANIC_SUSPENDED_FRAME_CONSERVATION=PASS \
  M7_AUDIT_PANIC_RUNNING_PC_QUISCES=PASS \
  M9_JAVA_PRIORITY_STOP_THEN_PANIC_NOTICE=PASS \
  M9_JAVA_SATURATED_DATA_CONTROL_INDEPENDENT=PASS \
  M9_JAVA_FAULT_MESSAGE_GENERATION_METADATA=PASS \
  M9_JAVA_ACCEPTED_FAILED_ABANDONED_CONSERVATION=PASS \
  M9_JAVA_REINCARNATION_FENCE=PASS \
  M9_JAVA_NUMERIC_ACTOR_ID_CANONICAL_WIRE=PASS \
  M9_JAVA_SCOPE=HOST_CONTROL_NOT_JAVA_COMPILER_FULL_ACTOR_PARITY \
  M7_RESTRICTED_COMPILED_ATTO_HOST_SCHEDULER=PASS \
  M7_SCOPE=RESTRICTED_STATIC_ATTO_LOOP_COOPERATE_NOT_FULL_COMPILER_OR_HARD_RT; do
  grep -Fx "$marker" "$tmp/native" >/dev/null || { echo "M7_NATIVE_MARKER_MISSING=$marker" >&2; exit 1; }
  grep -Fx "$marker" "$tmp/jvm" >/dev/null || { echo "M7_JVM_MARKER_MISSING=$marker" >&2; exit 1; }
done
grep -Fx "M7_COMPILED_GUEST_SOURCE_SHA256=$source_sha" "$tmp/native" >/dev/null || {
  echo 'M7_NATIVE_EXECUTED_DIFFERENT_GUEST_SOURCE' >&2; exit 1;
}
"$tmp/m9-wire-check" --verify-output "$tmp/jvm"
"$tmp/m9-wire-check" --verify-output "$tmp/native"
cmp "$tmp/jvm" "$tmp/native" || { echo 'M7_JVM_NATIVE_OUTPUT_DIFFERENCE' >&2; exit 1; }
echo 'M9_RUST_CROSS_JVM_NATIVE_WIRE_EQUALITY=PASS'
echo 'M7_COMPILED_STATIC_ATTO_COOPERATIVE_GRAALVM_NATIVE=PASS'
echo 'M7_EVIDENCE_SCOPE=RESTRICTED_COMPILED_ATTO_NOT_CANONICAL_FRONTEND_OR_HARD_REAL_TIME'
