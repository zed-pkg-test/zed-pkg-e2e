#!/usr/bin/env bash
# Real restricted .atto -> Java/JVM + GraalVM Native Image M1 guest execution.
# This DOES NOT certify the canonical Atto frontend or cross-actor object safety.
set -euo pipefail
cd "$(dirname "$0")/../.."
[[ "${ATTO_EXPECTED_HEAD_SHA:-}" =~ ^[0-9a-f]{40}$ ]] && [[ "$(git rev-parse HEAD)" == "$ATTO_EXPECTED_HEAD_SHA" ]] || { echo 'M1_SOURCE_HEAD_MISMATCH' >&2; exit 2; }
git diff --quiet HEAD -- && [[ -z "$(git ls-files --others --exclude-standard)" ]] || { echo 'M1_SOURCE_DIRTY' >&2; exit 2; }
test -x "$JAVA_HOME/bin/javac" && test -x "$JAVA_HOME/bin/native-image" || { echo 'M1_NATIVE_IMAGE_NOT_AVAILABLE' >&2; exit 2; }
fixture=fixtures/object_m1/point_particle.atto
[[ "$(git hash-object "$fixture")" == "b8f5e4ed101ef7a21be72f7999578d3dc52c98ef" ]] || { echo 'M1_FIXTURE_NOT_PINNED' >&2; exit 2; }
export PYTHONDONTWRITEBYTECODE=1
python3 tests/test_object_storage_m1.py --backend java \
  --generator tools/atto_object_storage_m1.py --fixture "$fixture" \
  --java-arena src/memory/ActorRef32Arena.java \
  --java "$JAVA_HOME/bin/java" --javac "$JAVA_HOME/bin/javac"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
source_sha="$(sha256sum "$fixture" | cut -d ' ' -f 1)"
python3 tools/atto_object_storage_m1.py "$fixture" --source-sha256 "$source_sha" --backend java --out "$work/AttoObjectStorageM1.java"
mkdir -p "$work/classes"
"$JAVA_HOME/bin/javac" -Xlint:all -Werror -d "$work/classes" \
  src/memory/ActorRef32Arena.java "$work/AttoObjectStorageM1.java"
(
  cd "$work"
  "$JAVA_HOME/bin/native-image" --no-fallback --gc=epsilon -H:+SharedArenaSupport \
    -H:Name=atto-object-m1 -cp "$work/classes" AttoObjectStorageM1
)
test -s "$work/atto-object-m1" && test -x "$work/atto-object-m1"
file "$work/atto-object-m1" | grep -Eq 'ELF|Mach-O'
set +e
timeout 60 "$work/atto-object-m1" > "$work/native.out" 2> "$work/native.err"
code=$?
set -e
cat "$work/native.out"
[[ "$code" -eq 13 ]] || { echo "M1_NATIVE_EXIT_MISMATCH=$code" >&2; cat "$work/native.err" >&2; exit 1; }
grep -Fx 'ATTO_OBJECT_M1_RESULT=13' "$work/native.out"
grep -Fx 'ATTO_OBJECT_M1_SCOPE=CLOSED_WORLD_SINGLE_ACTOR' "$work/native.out"
test ! -s "$work/native.err" || { cat "$work/native.err" >&2; exit 1; }
echo M1_REAL_JVM_AND_GRAAL_NATIVE_GUEST=PASS
echo M1_CANONICAL_FRONTEND_AND_MULTI_ACTOR_PARITY=NOT_VERIFIED
