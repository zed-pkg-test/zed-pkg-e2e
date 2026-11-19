#!/usr/bin/env bash
set -euo pipefail
# Keep the Git checkout clean for M7's exact-source native verifier.
# Python's default __pycache__ side effects are not source code.
export PYTHONDONTWRITEBYTECODE=1
cd "$(dirname "$0")/../.."
expected="$ATTO_EXPECTED_HEAD_SHA"
if [[ ! "$expected" =~ ^[0-9a-f]{40}$ ]] || [[ "$(git rev-parse HEAD)" != "$expected" ]]; then
  echo M8_JAVA_IR_NOT_EXECUTED_EXACT_HEAD >&2; exit 2
fi
python3 -m unittest discover -s tests/m8 -p 'test_*.py' -v
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
fixture=fixtures/m7/cooperative_sum.atto
sha="$(sha256sum "$fixture" | cut -d ' ' -f 1)"
python3 tools/m7_compile_actor_continuation.py "$fixture" \
  --out "$work/generated" --source-sha256 "$sha"
python3 - "$work/generated" "$sha" <<'PY'
import hashlib,json,pathlib,sys
out=pathlib.Path(sys.argv[1])
m=json.loads((out/"manifest.json").read_text())
raw=(out/"m8-typed-ir.json").read_bytes()
ir=json.loads(raw)
assert m["source_sha256"]==sys.argv[2]
assert m["m8_ir_sha256"]==hashlib.sha256(raw).hexdigest()
assert m["m8_ir_schema"]=="atto.m8.cooperative-ir.v1"
assert m["canonical_compiler"] is False
assert ir["schema"]=="atto.m8.cooperative-ir.v1"
assert [x["pc"] for x in ir["instructions"]]==[0,1,2,3,4]
assert ir["instructions"][3]["resume_pc"]==1
assert ir["budget_reductions"]=={"min":1,"max":65536}
assert not ir["borrow_across_suspend"]
assert not ir["critical_loan_across_suspend"]
print("M8_JAVA_TYPED_IR_MANIFEST_SHA256=PASS")
PY
bash ci/m7/verify-cooperative-native.sh
echo 'M8_JAVA_RESTRICTED_TYPED_IR_GRAALVM_NATIVE=PASS'
echo 'M8_SCOPE=RESTRICTED_JAVA_AOT_BRIDGE_NOT_CANONICAL_FRONTEND'
