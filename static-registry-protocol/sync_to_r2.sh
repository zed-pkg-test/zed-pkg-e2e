#!/usr/bin/env bash
# Upload a built static-registry v0 tree to a dedicated R2 test bucket.
#
# IMPORTANT: v0 uses mutable index paths. It is safe for a first publication to
# an empty/dedicated test bucket, but it cannot provide collection-atomic rolling
# updates. The signed v1 contract uses immutable snapshot prefixes selected by a
# stable checkpoint. Until the fixture is upgraded, replacing an existing v0
# checkpoint fails closed unless the operator explicitly acknowledges the risk.
#
# Env:
#   ZPKG_R2_ACCESS_KEY_ID
#   ZPKG_R2_SECRET_ACCESS_KEY
#   ZPKG_R2_ENDPOINT
#   ZPKG_ALLOW_V0_REPLACE=true   # explicit non-production override only
# Usage: sync_to_r2.sh <tree-dir> <bucket>
set -euo pipefail

if [[ "$#" -ne 2 ]]; then
  echo "usage: $0 <tree-dir> <bucket>" >&2
  exit 2
fi

: "${ZPKG_R2_ACCESS_KEY_ID:?ZPKG_R2_ACCESS_KEY_ID is required}"
: "${ZPKG_R2_SECRET_ACCESS_KEY:?ZPKG_R2_SECRET_ACCESS_KEY is required}"
: "${ZPKG_R2_ENDPOINT:?ZPKG_R2_ENDPOINT is required}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TREE="$(cd "$1" && pwd)"
BUCKET="$2"
CHECKPOINT="checkpoint.json"

if [[ ! -f "$TREE/$CHECKPOINT" ]]; then
  echo "missing $TREE/$CHECKPOINT" >&2
  exit 2
fi

# Never publish a tree whose checkpoint, index, or package bytes already fail
# the same local conformance boundary used by CI.
if ! python3 "$SCRIPT_DIR/check_static_registry.py" --base "$TREE" >/dev/null; then
  echo "refusing to upload a static registry tree that fails local conformance" >&2
  exit 2
fi

export AWS_ACCESS_KEY_ID="$ZPKG_R2_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$ZPKG_R2_SECRET_ACCESS_KEY"
EP=(--endpoint-url "$ZPKG_R2_ENDPOINT" --region auto)

# A pre-existing v0 checkpoint means readers may already hold the old mutable
# index paths. Updating those paths cannot be made collection-atomic merely by
# writing the new checkpoint last, so require an explicit test-only override.
if aws s3api head-object "${EP[@]}" --bucket "$BUCKET" --key "$CHECKPOINT" \
  >/dev/null 2>&1; then
  if [[ "${ZPKG_ALLOW_V0_REPLACE:-false}" != "true" ]]; then
    cat >&2 <<'EOF'
refusing to replace an existing v0 checkpoint: mutable v0 indexes cannot provide
atomic rolling publication. Use a fresh dedicated test bucket, upgrade to the
v1 immutable-snapshot layout, or set ZPKG_ALLOW_V0_REPLACE=true only for an
explicitly disposable non-production experiment.
EOF
    exit 3
  fi
fi

put() { # rel content-type cache-control
  local rel="$1"
  local content_type="$2"
  local cache_control="$3"
  aws s3api put-object "${EP[@]}" \
    --bucket "$BUCKET" \
    --key "$rel" \
    --body "$TREE/$rel" \
    --content-type "$content_type" \
    --cache-control "$cache_control" \
    --query ETag \
    --output text
}

upload_object() {
  local rel="$1"
  case "$rel" in
    pkgs/*.tar.zst)
      put "$rel" application/zstd "public, max-age=31536000, immutable" >/dev/null
      ;;
    index/*)
      # v0 indexes are mutable. Revalidate rather than serving stale data after
      # a checkpoint transition. v1 snapshot indexes become immutable.
      put "$rel" application/x-ndjson "no-cache" >/dev/null
      ;;
    *)
      put "$rel" application/json "no-cache" >/dev/null
      ;;
  esac
}

UPLOAD_LIST="$(mktemp)"
trap 'rm -f "$UPLOAD_LIST"' EXIT

# Produce a portable, NUL-delimited lexical inventory. GNU `sort -z` is not
# available on the macOS runner, and newline-delimited filenames are unsafe.
python3 - "$TREE" "$CHECKPOINT" > "$UPLOAD_LIST" <<'PY'
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
checkpoint = sys.argv[2]
entries = []
for path in root.rglob("*"):
    if path.is_symlink():
        raise SystemExit(f"static registry tree contains a symbolic link: {path}")
    if path.is_file():
        relative = path.relative_to(root).as_posix()
        if relative != checkpoint:
            entries.append(relative)
for relative in sorted(entries):
    os.write(sys.stdout.fileno(), relative.encode("utf-8") + b"\0")
PY

count=0

# Publish every object except the stable checkpoint first.
while IFS= read -r -d '' rel; do
  upload_object "$rel"
  count=$((count + 1))
done < "$UPLOAD_LIST"

# The stable checkpoint is the only publication pointer and is always last.
put "$CHECKPOINT" application/json "no-cache" >/dev/null
count=$((count + 1))

echo "uploaded $count objects to r2://$BUCKET (checkpoint last)"
