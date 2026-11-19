#!/usr/bin/env bash
# Host-only readonly structural-field witness; no compiled .atto guest assumed.
set -euo pipefail
cd "$(dirname "$0")/../.."
[[ "${ATTO_EXPECTED_HEAD_SHA:-}" =~ ^[0-9a-f]{40}$ ]] && [[ "$(git rev-parse HEAD)" == "$ATTO_EXPECTED_HEAD_SHA" ]] || { echo SHAPE_HEAD_MISMATCH >&2; exit 2; }
git diff --quiet HEAD -- && [[ -z "$(git ls-files --others --exclude-standard)" ]] || { echo SHAPE_DIRTY_SOURCE >&2; exit 2; }
test -x "$JAVA_HOME/bin/javac" && test -x "$JAVA_HOME/bin/native-image" || { echo SHAPE_JDK25_NATIVE_NOT_FOUND >&2; exit 2; }
for f in src/shape/ReadonlyShapeFieldAbi.java tests/shape/ReadonlyShapeFieldAbiProbe.java; do
  [[ "$(git hash-object "$f")" == "$(git rev-parse "HEAD:$f")" ]] || { echo "SHAPE_BLOB_SHA_MISMATCH=$f" >&2; exit 2; }
done
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/classes"
"$JAVA_HOME/bin/javac" -Xlint:all -Werror -d "$work/classes" \
  src/shape/ReadonlyShapeFieldAbi.java tests/shape/ReadonlyShapeFieldAbiProbe.java
"$JAVA_HOME/bin/java" -cp "$work/classes" atto.shape.ReadonlyShapeFieldAbiProbe | tee "$work/jvm.out"
grep -Fx 'JAVA_SHAPE_FIELD_WITNESS_PASS checks=24' "$work/jvm.out"
(
  cd "$work"
  "$JAVA_HOME/bin/native-image" --no-fallback --gc=epsilon \
    -H:Name=atto-shape-abi -cp "$work/classes" atto.shape.ReadonlyShapeFieldAbiProbe
)
test -x "$work/atto-shape-abi" && test -s "$work/atto-shape-abi" || { echo SHAPE_NATIVE_BINARY_MISSING >&2; exit 1; }
file "$work/atto-shape-abi" | grep -Eq 'ELF|Mach-O'
timeout 60 "$work/atto-shape-abi" > "$work/native.out" 2> "$work/native.err"
cat "$work/native.out"
grep -Fx 'JAVA_SHAPE_FIELD_WITNESS_PASS checks=24' "$work/native.out"
test ! -s "$work/native.err" || { cat "$work/native.err" >&2; exit 1; }
echo SHAPE_HOST_JVM_AND_NATIVE_IMAGE=PASS
echo SHAPE_COMPILED_ATTO_GUEST=NOT_VERIFIED
