#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
bash ci/admission/exact-tree.sh src/compiler/adt/AttoAdtPatternPlanner.java tests/compiler/AttoAdtPatternPlannerProbe.java ci/compiler/verify-adt-pattern.sh
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/classes"
"$JAVA_HOME/bin/javac" -Xlint:all -Werror -d "$work/classes" \
  src/compiler/adt/AttoAdtPatternPlanner.java tests/compiler/AttoAdtPatternPlannerProbe.java
"$JAVA_HOME/bin/java" -cp "$work/classes" atto.compiler.adt.AttoAdtPatternPlannerProbe >"$work/jvm.out"
grep -Fx 'ATTO_ADT_PATTERN_HOST_PLAN_PASS checks=27' "$work/jvm.out"
(
  cd "$work"
  "$JAVA_HOME/bin/native-image" --no-fallback --gc=epsilon -H:Name=adt-plan \
    -cp "$work/classes" atto.compiler.adt.AttoAdtPatternPlannerProbe
)
"$work/adt-plan" >"$work/native.out" 2>"$work/native.err"
grep -Fx 'ATTO_ADT_PATTERN_HOST_PLAN_PASS checks=27' "$work/native.out"
test ! -s "$work/native.err"
echo ATTO_ADT_PREHIR_HOST_JVM_NATIVE=PASS
echo ATTO_ADT_COMPILED_GUEST_AOT=NOT_VERIFIED
