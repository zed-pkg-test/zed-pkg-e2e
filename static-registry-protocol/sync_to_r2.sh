#!/usr/bin/env bash
# Upload a built static-registry tree to R2 with the cache semantics the
# protocol sketch prescribes: content-addressed pkgs are immutable; index,
# discovery, and checkpoint are short-TTL + stale-while-revalidate.
#
# Env: ZPKG_R2_ACCESS_KEY_ID / ZPKG_R2_SECRET_ACCESS_KEY / ZPKG_R2_ENDPOINT
# Usage: sync_to_r2.sh <tree-dir> <bucket>
set -euo pipefail
TREE="$(cd "$1" && pwd)"; BUCKET="$2"
export AWS_ACCESS_KEY_ID="$ZPKG_R2_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$ZPKG_R2_SECRET_ACCESS_KEY"
EP=(--endpoint-url "$ZPKG_R2_ENDPOINT" --region auto)

put() { # rel content-type cache-control
  aws s3api put-object "${EP[@]}" --bucket "$BUCKET" --key "$1" \
    --body "$TREE/$1" --content-type "$2" --cache-control "$3" \
    --query ETag --output text
}

cd "$TREE"
count=0
while IFS= read -r -d '' f; do
  rel="${f#./}"
  case "$rel" in
    pkgs/*.tar.zst) put "$rel" application/zstd "public, max-age=31536000, immutable" >/dev/null ;;
    index/*)        put "$rel" application/x-ndjson "public, max-age=60, stale-while-revalidate=600" >/dev/null ;;
    *)              put "$rel" application/json "public, max-age=60, stale-while-revalidate=600" >/dev/null ;;
  esac
  count=$((count+1))
done < <(find . -type f -print0 | sort -z)
echo "uploaded $count objects to r2://$BUCKET"
