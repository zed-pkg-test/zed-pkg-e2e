#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
test -x "$JAVA_HOME/bin/javac"
test -x "$JAVA_HOME/bin/native-image"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/classes"
"$JAVA_HOME/bin/javac" -Xlint:all -Werror -d "$work/classes" \
  src/shape/ReadonlyShapeFieldAbi.java \
  src/compiler/shape/AttoStructuralBinding.java \
  tests/compiler/AttoStructuralBindingProbe.java
"$JAVA_HOME/bin/java" -cp "$work/classes" atto.compiler.shape.AttoStructuralBindingProbe >"$work/jvm.out"
grep -Fx 'ATTO_STRUCTURAL_HOST_BINDER_PASS checks=16' "$work/jvm.out"
(
 cd "$work"
 "$JAVA_HOME/bin/native-image" --no-fallback --gc=epsilon \
   -H:Name=shape-binder -cp "$work/classes" atto.compiler.shape.AttoStructuralBindingProbe
)
test -x "$work/shape-binder"
"$work/shape-binder" >"$work/native.out" 2>"$work/native.err"
grep -Fx 'ATTO_STRUCTURAL_HOST_BINDER_PASS checks=16' "$work/native.out"
test ! -s "$work/native.err"
echo ATTO_STRUCTURAL_GUEST_COMPILATION=NOT_VERIFIED
echo ATTO_STRUCTURAL_HOST_JVM_NATIVE=PASS
