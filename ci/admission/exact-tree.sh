#!/usr/bin/env bash
# Admission guard: call BEFORE writing build outputs in any workflow.
# Zero-step GitHub jobs and standalone marker strings are never test evidence.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
expected="${ATTO_EXPECTED_HEAD_SHA:-}"
if [[ ! "$expected" =~ ^[a-f0-9]{40}$ ]] || [[ "$(git rev-parse --verify HEAD)" != "$expected" ]]; then
  echo 'ATTO_SOURCE_HEAD_MISMATCH' >&2; exit 2
fi
if ! git diff --quiet HEAD -- || ! git diff --cached --quiet || [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
  echo 'ATTO_SOURCE_DIRTY' >&2; exit 2
fi
expected_tree="$(git rev-parse 'HEAD^{tree}')"
if [[ "$(git write-tree)" != "$expected_tree" ]]; then
  echo 'ATTO_SOURCE_TREE_MISMATCH' >&2; exit 2
fi
# Require a nonempty set of tracked source files; callers may pin exact blob SHAs.
if [[ "$#" -eq 0 ]]; then echo 'ATTO_NO_PINNED_FILES' >&2; exit 2; fi
for path in "$@"; do
  if [[ "$path" == /* || "$path" == *..* || "$path" == -* ]] || ! git ls-files --error-unmatch -- "$path" >/dev/null 2>&1; then
    echo "ATTO_UNTRACKED_PROOF_FILE=$path" >&2; exit 2
  fi
  mode="$(git ls-files -s -- "$path" | awk '{print $1}')"
  if [[ "$mode" != '100644' && "$mode" != '100755' ]]; then
    echo "ATTO_UNSAFE_PROOF_MODE=$path" >&2; exit 2
  fi
  [[ "$(git hash-object -- "$path")" == "$(git rev-parse "HEAD:$path")" ]] || {
    echo "ATTO_PROOF_BLOB_MISMATCH=$path" >&2; exit 2
  }
done
echo "ATTO_EXACT_TREE_GATE=PASS sha=$expected tree=$expected_tree files=$#"
