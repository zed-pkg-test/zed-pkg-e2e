#!/usr/bin/env bash
# Closed-world Atto source -> static Java -> GraalVM Epsilon Native Image.
# Deliberately NOT full-language actor lowering, async supervision or sandbox.
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo"
expected="$ATTO_EXPECTED_CARRIER_HEAD"
if [[ ! "$expected" =~ ^[0-9a-f]{40}$ ]] || [[ "$(git rev-parse HEAD)" != "$expected" ]]; then
  echo 'ATTO_STATIC_AOT_NOT_EXECUTED: exact carrier Git head required' >&2
  exit 2
fi
git diff --quiet HEAD -- || { echo 'ATTO_STATIC_AOT_NOT_EXECUTED: dirty checkout' >&2; exit 2; }
test -z "$(git ls-files --others --exclude-standard)" || {
  echo 'ATTO_STATIC_AOT_NOT_EXECUTED: unexpected untracked source' >&2; exit 2;
}
test -x "$JAVA_HOME/bin/native-image" && test -x "$JAVA_HOME/bin/javac" || {
  echo 'ATTO_STATIC_AOT_NOT_EXECUTED: actual GraalVM Native Image JDK required' >&2; exit 2;
}
python3 -m unittest discover -s tests -p test_static_actor_aot.py -v
python3 -m unittest discover -s tests -p test_static_actor_aot_hardening.py -v
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
source="${ATTO_STATIC_GUEST_FIXTURE:-${ATTO_M4_FIXTURE:-fixtures/actor_counter.atto}}"
case "$source" in
  fixtures/actor_counter.atto|fixtures/m4/shared_actor_counter.atto) expected_result=43 ;;
  fixtures/actor_counter_guard.atto) expected_result=7 ;;
  *) echo "ATTO_STATIC_AOT_NOT_EXECUTED: fixture path not admitted" >&2; exit 2 ;;
esac
if [[ "$source" == "fixtures/m4/shared_actor_counter.atto" ]]; then
  exact_blob="$(git hash-object "$source")"
  if [[ "$exact_blob" != "0f9aca24deb195d7f604a1bfb944e237629803f4" ]]; then
    echo "ATTO_M4_JAVA_REFUSED: not the identical M4 source Git blob" >&2; exit 2;
  fi
  echo "ATTO_M4_IDENTICAL_SOURCE_GIT_BLOB=$exact_blob"
fi
source_pin="$(sha256sum "$source" | cut -d ' ' -f 1)"
python3 tools/atto_static_actor_aot.py "$source" --source-sha256 "$source_pin" --out "$tmp/generated"
python3 - "$tmp/generated" "$source_pin" "$expected_result" <<'PY'
import hashlib, json, pathlib, sys
folder=pathlib.Path(sys.argv[1])
m=json.loads((folder/"manifest.json").read_text())
java=(folder/"AttoGeneratedMain.java").read_bytes()
assert m["schema"]=="atto.static-actor-graal-aot-pilot.v1"
assert m["status"]=="GENERATED_SOURCE_NOT_YET_EXECUTED"
assert m["source_sha256"]==sys.argv[2]
assert m["generated_java_sha256"]==hashlib.sha256(java).hexdigest()
assert m["actor_kinds"]==1 and m["actor_instances"]==2 and m["actor_sends"]==3 and m["expected_guest_result"]==int(sys.argv[3])
cw=m["closed_world"]
assert cw["schema"]=="atto.static-actor.closed-world.pilot.v1"
assert cw["status"]=="COMPILER_GENERATED_RESTRICTED_GRAPH_NOT_CERTIFIED"
assert cw["single_source_sha256"]==m["source_sha256"]
assert cw["entry_roots"]==["AttoGeneratedMain.guest_main"]
assert len(cw["declared_actor_nodes"])==1 and len(cw["actor_roots"])==1
assert cw["actor_roots"][0]["handler"]=="AttoGeneratedMain.Actor_0.send"
assert cw["actor_roots"][0]["retirement"]=="AttoGeneratedMain.Actor_0.close"
assert cw["actor_instance_spawns"]==2
assert cw["action_count"]==(8 if int(sys.argv[3])==7 else 7)
assert cw["action_kinds"]==(["guard","send","spawn","stop"] if int(sys.argv[3])==7 else ["send","spawn","stop"])
assert cw["guest_dynamic_imports"]==[] and cw["generic_specializations"]==[]
assert cw["typeclass_witnesses"]==[]
for flag in ("guest_dynamic_loading","guest_runtime_reflection","guest_runtime_codegen",
             "native_aot_artifact_verified","authenticated_execution_receipt"):
    assert cw[flag] is False, flag
print("ATTO_STATIC_SOURCE_MANIFEST_VERIFIED=1")
print("ATTO_RESTRICTED_CLOSED_WORLD_GRAPH_VERIFIED=1; CERTIFIED=false")
print("ATTO_GUEST_SOURCE_SHA256="+m["source_sha256"])
print("ATTO_GENERATED_JAVA_SHA256="+m["generated_java_sha256"])
PY
mkdir -p "$tmp/classes"
"$JAVA_HOME/bin/javac" -d "$tmp/classes" "$tmp/generated/AttoGeneratedMain.java" "$repo/tests/AttoGeneratedAtomicityProbe.java"
"$JAVA_HOME/bin/java" -cp "$tmp/classes" AttoGeneratedAtomicityProbe
(
  cd "$tmp"
  "$JAVA_HOME/bin/native-image" --no-fallback --gc=epsilon \
    -H:Name=atto-static-actor-guest -cp "$tmp/classes" AttoGeneratedMain
)
test -s "$tmp/atto-static-actor-guest" && test -x "$tmp/atto-static-actor-guest"
file "$tmp/atto-static-actor-guest" | grep -Eq 'ELF|Mach-O' || {
  echo 'ATTO_STATIC_AOT_NOT_EXECUTED: artifact not native OS machine code' >&2; exit 1;
}
sha256sum "$tmp/atto-static-actor-guest"
echo "ATTO_GRAAL_NATIVE_GUEST_SHA256=$(sha256sum "$tmp/atto-static-actor-guest" | cut -d ' ' -f 1)"
native_exit=0
"$tmp/atto-static-actor-guest" > "$tmp/stdout" 2> "$tmp/stderr" || native_exit=$?
if (( native_exit != 0 )); then
  echo "ATTO_STATIC_AOT_NOT_EXECUTED: native binary exited $native_exit" >&2
  cat "$tmp/stderr" >&2
  cat "$tmp/stdout" >&2
  exit "$native_exit"
fi
cat "$tmp/stdout"
if [[ -s "$tmp/stderr" ]]; then
  echo 'ATTO_STATIC_AOT_UNEXPECTED_NATIVE_STDERR' >&2
  cat "$tmp/stderr" >&2
  exit 1
fi
grep -Fx "ATTO_GUEST_NATIVE_AOT_RETURN=$expected_result" "$tmp/stdout"
grep -Fx 'ATTO_GUEST_ACTOR_SENDS=3' "$tmp/stdout"
grep -Fx 'ATTO_GUEST_ACTOR_CLOSES=2' "$tmp/stdout"
grep -Fx 'ATTO_GUEST_ACTOR_STATE_DIGEST=7021' "$tmp/stdout"
grep -Fx 'ATTO_GUEST_GC_DELTA=0' "$tmp/stdout"
grep -Fx 'ATTO_NATIVE_AOT_SCOPE=STATIC_ATTO_ACTOR_SUBSET_NOT_TENANT_SANDBOX' "$tmp/stdout"
echo 'ATTO_STATIC_ACTOR_GRAAL_EPSILON_NATIVE_GUEST=PASSED'
echo "ATTO_STATIC_GUEST_FIXTURE_VERIFIED=$source"
echo "ATTO_STATIC_GUEST_COMPILER_SOURCE_SHA256=$(sha256sum tools/atto_static_actor_aot.py | cut -d ' ' -f 1)"
echo 'EVIDENCE_SCOPE=APPLICATION_SPECIFIC_NATIVE_AOT_SMALL_ATTO_ACTOR_SUBSET_NO_TENANT_SANDBOX'
