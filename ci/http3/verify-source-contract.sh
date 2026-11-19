#!/usr/bin/env bash
# Executable source/ABI admission only: NOT a Java guest or QUIC/TLS runtime test.
set -euo pipefail
cd "$(dirname "$0")/../.."
expected="${ATTO_EXPECTED_HEAD_SHA:-}"
[[ "$expected" =~ ^[0-9a-f]{40}$ ]] && [[ "$(git rev-parse HEAD)" == "$expected" ]] || { echo 'HTTP3_SOURCE_SHA_MISMATCH' >&2; exit 2; }
git diff --quiet HEAD -- && [[ -z "$(git ls-files --others --exclude-standard)" ]] || { echo 'HTTP3_DIRTY_CHECKOUT' >&2; exit 2; }
contract=contracts/http3-host-abi-v1.json
jq -e '.schema == "atto.net.http3.host-abi.v1" and .status == "SPECIFICATION_ONLY_NO_TRANSPORT_ENGINE_BOUND" and (.operations | length == 11) and (.operations | map(.name) | unique | length == 11)' "$contract" >/dev/null
diff -u <(printf '%s\n' stdlib/net/http_policy.atto stdlib/net/quic_varint.atto stdlib/net/http3_frames.atto) <(jq -r '.canonical_source[]' "$contract")
declare -A pinned=(
  [contracts/http3-host-abi-v1.json]=a6ac1197e13554429ffc64b3f205e1b820b0c49c
  [stdlib/net/http_policy.atto]=68831fd1022f09ac5a4fd74b29fc34a6dcf9e13a
  [stdlib/net/quic_varint.atto]=838929f85b66a646fb7b018dd0a882e7fe96a5d5
  [stdlib/net/http3_frames.atto]=a30d5301be194e91737fa17ac8c17d976619929a
)
for path in "${!pinned[@]}"; do
  [[ -f "$path" && ! -L "$path" ]] || { echo "HTTP3_MISSING=$path" >&2; exit 2; }
  [[ "$(git hash-object "$path")" == "${pinned[$path]}" ]] || { echo "HTTP3_BLOB_MISMATCH=$path" >&2; exit 2; }
done
count="$(grep -h -E '^pub fnc [a-z_0-9]+\(' stdlib/net/*.atto | wc -l | tr -d ' ')"
[[ "$count" == 23 ]] || { echo "HTTP3_UNEXPECTED_FUNCTION_COUNT=$count" >&2; exit 2; }
echo HTTP3_STAGED_SOURCE_CONTRACT_PASS=23_FUNCTIONS_11_HOST_ABI_OPERATIONS
echo HTTP3_JAVA_COMPILED_GUEST=NOT_VERIFIED
echo HTTP3_NETWORK_WIRE_ENGINE=NOT_IMPLEMENTED
