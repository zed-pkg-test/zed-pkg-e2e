#!/usr/bin/env bash
# Credential-free contract tests for sync_to_r2.sh using a fake AWS CLI.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYNC="$ROOT/sync_to_r2.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

FAKE_BIN="$WORK/bin"
FAKE_LOG="$WORK/aws.log"
TREE="$WORK/tree"
mkdir -p "$FAKE_BIN" \
  "$TREE/.well-known" \
  "$TREE/index/acme" \
  "$TREE/pkgs/acme/demo"

printf '%s\n' '{"schema_version":0}' > "$TREE/.well-known/zpkg-registry.json"
printf '%s\n' '{"version":"1.0.0"}' > "$TREE/index/acme/demo"
printf 'artifact\n' > "$TREE/pkgs/acme/demo/1.0.0.tar.zst"
printf '%s\n' '{"seq":1}' > "$TREE/checkpoint.json"

cat > "$FAKE_BIN/aws" <<'FAKE_AWS'
#!/usr/bin/env bash
set -euo pipefail
: "${FAKE_AWS_LOG:?}"

if [[ "${1:-}" != "s3api" ]]; then
  echo "unexpected aws service: ${1:-<missing>}" >&2
  exit 90
fi
operation="${2:-}"
shift 2

case "$operation" in
  head-object)
    printf 'HEAD\n' >> "$FAKE_AWS_LOG"
    if [[ "${FAKE_AWS_HEAD_EXISTS:-false}" == "true" ]]; then
      printf '%s\n' '{"ContentLength":1}'
      exit 0
    fi
    exit 255
    ;;
  put-object)
    key=""
    body=""
    content_type=""
    cache_control=""
    bucket=""
    while [[ "$#" -gt 0 ]]; do
      case "$1" in
        --key) key="$2"; shift 2 ;;
        --body) body="$2"; shift 2 ;;
        --content-type) content_type="$2"; shift 2 ;;
        --cache-control) cache_control="$2"; shift 2 ;;
        --bucket) bucket="$2"; shift 2 ;;
        --endpoint-url|--region|--query|--output) shift 2 ;;
        *) echo "unexpected aws argument: $1" >&2; exit 91 ;;
      esac
    done
    [[ -n "$key" && -n "$body" && -n "$content_type" && -n "$cache_control" && -n "$bucket" ]]
    [[ -f "$body" ]]
    printf 'PUT|%s|%s|%s|%s|%s\n' \
      "$key" "$content_type" "$cache_control" "$body" "$bucket" \
      >> "$FAKE_AWS_LOG"
    printf '%s\n' '"fake-etag"'
    ;;
  *)
    echo "unexpected aws operation: $operation" >&2
    exit 92
    ;;
esac
FAKE_AWS
chmod +x "$FAKE_BIN/aws"

run_sync() {
  local head_exists="$1"
  local allow_replace="$2"
  shift 2
  : > "$FAKE_LOG"
  PATH="$FAKE_BIN:$PATH" \
  FAKE_AWS_LOG="$FAKE_LOG" \
  FAKE_AWS_HEAD_EXISTS="$head_exists" \
  ZPKG_R2_ACCESS_KEY_ID="test-access-key" \
  ZPKG_R2_SECRET_ACCESS_KEY="test-secret-key" \
  ZPKG_R2_ENDPOINT="https://example.invalid" \
  ZPKG_ALLOW_V0_REPLACE="$allow_replace" \
    "$SYNC" "$@"
}

assert_upload_contract() {
  local expected="$WORK/expected-keys.txt"
  local actual="$WORK/actual-keys.txt"
  cat > "$expected" <<'KEYS'
.well-known/zpkg-registry.json
index/acme/demo
pkgs/acme/demo/1.0.0.tar.zst
checkpoint.json
KEYS
  awk -F'|' '$1 == "PUT" { print $2 }' "$FAKE_LOG" > "$actual"
  cmp "$expected" "$actual"

  [[ "$(tail -n 1 "$actual")" == "checkpoint.json" ]]
  grep -F 'PUT|.well-known/zpkg-registry.json|application/json|no-cache|' "$FAKE_LOG"
  grep -F 'PUT|index/acme/demo|application/x-ndjson|no-cache|' "$FAKE_LOG"
  grep -F 'PUT|pkgs/acme/demo/1.0.0.tar.zst|application/zstd|public, max-age=31536000, immutable|' "$FAKE_LOG"
  grep -F 'PUT|checkpoint.json|application/json|no-cache|' "$FAKE_LOG"
  [[ "$(grep -c '^PUT|' "$FAKE_LOG")" -eq 4 ]]
  [[ "$(grep -c '^HEAD$' "$FAKE_LOG")" -eq 1 ]]
}

# Fresh dedicated bucket: all ordinary objects first, checkpoint last.
run_sync false false "$TREE" test-static-registry > "$WORK/fresh.out"
grep -F 'uploaded 4 objects to r2://test-static-registry (checkpoint last)' "$WORK/fresh.out"
assert_upload_contract

# Existing mutable v0 checkpoint: fail before any write unless explicitly overridden.
set +e
run_sync true false "$TREE" test-static-registry \
  > "$WORK/refused.out" 2> "$WORK/refused.err"
status=$?
set -e
[[ "$status" -eq 3 ]]
grep -F 'refusing to replace an existing v0 checkpoint' "$WORK/refused.err"
[[ "$(grep -c '^PUT|' "$FAKE_LOG" || true)" -eq 0 ]]
[[ "$(grep -c '^HEAD$' "$FAKE_LOG")" -eq 1 ]]

# Explicit disposable-test override preserves the same checkpoint-last order.
run_sync true true "$TREE" test-static-registry > "$WORK/override.out"
assert_upload_contract

# Missing publication pointer fails before the AWS boundary.
TREE_WITHOUT_CHECKPOINT="$WORK/tree-without-checkpoint"
cp -R "$TREE" "$TREE_WITHOUT_CHECKPOINT"
rm "$TREE_WITHOUT_CHECKPOINT/checkpoint.json"
set +e
run_sync false false "$TREE_WITHOUT_CHECKPOINT" test-static-registry \
  > "$WORK/missing.out" 2> "$WORK/missing.err"
status=$?
set -e
[[ "$status" -eq 2 ]]
grep -F 'missing ' "$WORK/missing.err"
[[ ! -s "$FAKE_LOG" ]]

printf '%s\n' 'static R2 sync contract: PASS'
