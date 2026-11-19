#!/usr/bin/env bash
# Restricted .atto completed-Future pipeline -> generated Java / Graal Native Image.
set -euo pipefail
cd "$(dirname "$0")/../.."
bash ci/admission/exact-tree.sh \
  src/compiler/asyncv1/AttoAsyncV1Compiler.java \
  tests/compiler/AttoAsyncV1CompilerProbe.java \
  fixtures/async_v1/completed_future.atto \
  ci/async/verify-compiled-completed-future.sh
test -x "$JAVA_HOME/bin/javac" && test -x "$JAVA_HOME/bin/native-image" || exit 2
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/classes"
"$JAVA_HOME/bin/javac" -Xlint:all -Werror -d "$work/classes" \
  src/compiler/asyncv1/AttoAsyncV1Compiler.java \
  tests/compiler/AttoAsyncV1CompilerProbe.java
"$JAVA_HOME/bin/java" -cp "$work/classes" atto.compiler.asyncv1.AttoAsyncV1CompilerProbe | tee "$work/probe"
grep -Fx "ATTO_ASYNC_V1_ADMISSION_PASS checks=18" "$work/probe"
"$JAVA_HOME/bin/java" -cp "$work/classes" atto.compiler.asyncv1.AttoAsyncV1Compiler \
  fixtures/async_v1/completed_future.atto "$work/AttoAsyncV1Guest.java"
"$JAVA_HOME/bin/javac" -Xlint:all -Werror -d "$work/classes" "$work/AttoAsyncV1Guest.java"
set +e
"$JAVA_HOME/bin/java" -cp "$work/classes" AttoAsyncV1Guest >"$work/jvm.out" 2>"$work/jvm.err"
status=$?
set -e
test "$status" -eq 42 || { cat "$work/jvm.err"; exit 1; }
grep -Fx 'ATTO_ASYNC_V1_RESULT=42' "$work/jvm.out"
(
  cd "$work"
  "$JAVA_HOME/bin/native-image" --no-fallback --gc=epsilon \
    -H:Name=atto-async-v1 -cp "$work/classes" AttoAsyncV1Guest
)
test -x "$work/atto-async-v1"
file "$work/atto-async-v1" | grep -Eq 'ELF|Mach-O'
set +e
timeout 60 "$work/atto-async-v1" >"$work/native.out" 2>"$work/native.err"
status=$?
set -e
test "$status" -eq 42 || { cat "$work/native.err"; exit 1; }
grep -Fx 'ATTO_ASYNC_V1_RESULT=42' "$work/native.out"
grep -Fx 'ATTO_ASYNC_SCOPE=COMPLETED_FUTURE_ONLY' "$work/native.out"
test ! -s "$work/native.err"
echo 'ATTO_ASYNC_V1_JVM_AND_NATIVE=PASS'
echo 'ATTO_GENERAL_FUTURE_SUSPENSION_AND_BORROW_CHECKER=NOT_VERIFIED'
