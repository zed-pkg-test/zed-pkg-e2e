#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
expected="${ATTO_EXPECTED_HEAD_SHA:-}"
if [[ ! "$expected" =~ ^[0-9a-f]{40}$ ]] || [[ "$(git rev-parse HEAD)" != "$expected" ]]; then
  echo 'M9_JAVA_NOT_EXECUTED_EXACT_HEAD' >&2; exit 2
fi
# The M8 gate calls the complete M7 compiled guest Native Image suite, now
# requiring real M9 priority supervisor notice and conservation markers.
bash ci/m8/verify-typed-ir-native.sh
echo 'M9_JAVA_SUPERVISOR_NOTICE_NATIVE_EXECUTED=PASS'
echo 'M9_JAVA_SCOPE=RESTRICTED_GRAALVM_HOST_NOTICE_NOT_FULL_BACKEND_PARITY'
