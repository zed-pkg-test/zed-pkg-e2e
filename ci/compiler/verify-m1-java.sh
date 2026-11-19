#!/usr/bin/env bash
# Bounded native Java ObjectStorage M1 compiler and actual generated guest.
set -euo pipefail
cd "$(dirname "$0")/../.."
if [[ -n "$ATTO_EXPECTED_HEAD_SHA" ]]; then
  [[ "$ATTO_EXPECTED_HEAD_SHA" =~ ^[0-9a-f]{40}$ ]]
  [[ "$(git rev-parse HEAD)" = "$ATTO_EXPECTED_HEAD_SHA" ]]
fi
test -x "$JAVA_HOME/bin/javac"
test -x "$JAVA_HOME/bin/java"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/compiler" "$work/guest"
"$JAVA_HOME/bin/javac" -Xlint:all -Werror -d "$work/compiler" \
  src/compiler/m1/AttoObjectM1JavaCompiler.java \
  tests/compiler/AttoObjectM1JavaCompilerProbe.java
"$JAVA_HOME/bin/java" -cp "$work/compiler" atto.compiler.m1.AttoObjectM1JavaCompilerProbe \
  fixtures/object_m1/point_particle.atto | tee "$work/probe.out"
grep -F 'ATTO_JAVA_M1_COMPILER_PROBE_PASS checks=' "$work/probe.out"
sha="$(sha256sum fixtures/object_m1/point_particle.atto | cut -d' ' -f1)"
"$JAVA_HOME/bin/java" -cp "$work/compiler" atto.compiler.m1.AttoObjectM1JavaCompiler \
  fixtures/object_m1/point_particle.atto --source-sha256 "$sha" \
  --out "$work/AttoObjectStorageM1.java"
"$JAVA_HOME/bin/javac" -Xlint:all -Werror -d "$work/guest" \
  src/memory/ActorRef32Arena.java "$work/AttoObjectStorageM1.java"
set +e
"$JAVA_HOME/bin/java" -cp "$work/guest" AttoObjectStorageM1 >"$work/jvm.out" 2>"$work/jvm.err"
exit_code=$?
set -e
[[ "$exit_code" -eq 13 ]]
grep -Fx 'ATTO_OBJECT_M1_RESULT=13' "$work/jvm.out"
grep -Fx 'ATTO_OBJECT_M1_SCOPE=CLOSED_WORLD_SINGLE_ACTOR' "$work/jvm.out"
test ! -s "$work/jvm.err"
echo ATTO_M1_JAVA_COMPILED_GUEST_JVM=PASS
if [[ "$ATTO_NATIVE_IMAGE_REQUIRED" = 1 ]]; then
  test -x "$JAVA_HOME/bin/native-image"
  (
    cd "$work"
    "$JAVA_HOME/bin/native-image" --no-fallback --gc=epsilon -H:+SharedArenaSupport \
      -H:Name=atto-m1-java -cp "$work/guest" AttoObjectStorageM1
  )
  test -s "$work/atto-m1-java" && test -x "$work/atto-m1-java"
  set +e
  timeout 60 "$work/atto-m1-java" >"$work/native.out" 2>"$work/native.err"
  exit_code=$?
  set -e
  [[ "$exit_code" -eq 13 ]]
  grep -Fx 'ATTO_OBJECT_M1_RESULT=13' "$work/native.out"
  test ! -s "$work/native.err"
  echo ATTO_M1_JAVA_COMPILED_GUEST_NATIVE=PASS
fi
echo ATTO_M1_GENERAL_FRONTEND_AND_OTHER_BACKENDS=NOT_VERIFIED
