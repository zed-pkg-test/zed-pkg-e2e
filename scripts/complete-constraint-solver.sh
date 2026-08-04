#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: complete-constraint-solver.sh ZED_BIN WORK_ROOT
EOF
  exit 64
}

[[ $# -eq 2 ]] || usage
zed="$1"
work_root="$2"

[[ -x "$zed" ]] || { echo "zed binary is not executable: $zed" >&2; exit 66; }
[[ ! -e "$work_root" ]] || { echo "work root must be fresh: $work_root" >&2; exit 73; }

zed="$(cd "$(dirname "$zed")" && pwd)/$(basename "$zed")"
mkdir -p "$work_root"
work_root="$(cd "$work_root" && pwd)"
registry="$work_root/registry"
publisher_home="$work_root/publisher-home"
mkdir -p "$registry" "$publisher_home"
file_registry="file://$registry"
org="solver-yank-e2e"

unset ZED_PKG_TOKEN ZED_PKG_REGISTRY ZED_PKG_HOME ZED_PKG_TARGET \
  ZED_PKG_ADAPTER ZED_PKG_INSTALL_CONCURRENCY ZED_PKG_ALLOW_NO_MANIFEST \
  ZED_PKG_DO_NOT_WRITE_NEW_MANIFEST ZED_PKG_INTERACTIVE
export CI=true GIT_TERMINAL_PROMPT=0 RUST_BACKTRACE=1

publish_package() {
  local name="$1"
  local version="$2"
  shift 2
  local source="$work_root/publish/${name}-${version//[^A-Za-z0-9._-]/-}"
  mkdir -p "$source"
  {
    cat <<EOF
[package]
org = "$org"
name = "$name"
version = "$version"
description = "DEN-1553 yanked-candidate hardening fixture"
license = "MIT"

[package.repository]
vcs = "git"
url = "https://example.invalid/$org/$name"
EOF
    if [[ $# -gt 0 ]]; then
      printf '\n[dependencies]\n'
      local spec key requirement
      for spec in "$@"; do
        key="${spec%%=*}"
        requirement="${spec#*=}"
        printf '"%s" = "%s"\n' "$key" "$requirement"
      done
    fi
  } >"$source/.zpkg.toml"
  printf '%s/%s@%s\n' "$org" "$name" "$version" >"$source/payload.txt"
  (
    cd "$source"
    "$zed" \
      --registry "$file_registry" \
      --home "$publisher_home" \
      publish --skip-vcs-checks
  )
}

write_consumer() {
  local root="$1"
  mkdir -p "$root"
  cat >"$root/.zpkg.toml" <<EOF
[package]
org = "solver-consumer"
name = "yanked-overlap"
version = "0.1.0"
description = "DEN-1553 yanked-candidate consumer"

[package.repository]
vcs = "git"
url = "https://example.invalid/solver-consumer/yanked-overlap"

[dependencies]
"$org/left" = "=1.0.0"
"$org/right" = "=1.0.0"
EOF
}

install_project() {
  local root="$1"
  local home="$2"
  shift 2
  (
    cd "$root"
    "$zed" \
      --registry "$file_registry" \
      --home "$home" \
      install --adapter none --install-mode copy "$@"
  )
}

assert_selected_shared() {
  local lock="$1"
  python3 - "$lock" "$org" <<'PY'
import sys, tomllib
from pathlib import Path

lock_path = Path(sys.argv[1])
org = sys.argv[2]
with lock_path.open("rb") as handle:
    document = tomllib.load(handle)
versions = {
    f"{item['org']}/{item['name']}": item['version']
    for item in document.get("package", [])
}
expected = {
    f"{org}/left": "1.0.0",
    f"{org}/right": "1.0.0",
    f"{org}/shared": "1.5.0",
}
if versions != expected:
    raise SystemExit(f"unexpected overlap lock: expected={expected!r} actual={versions!r}")
PY
}

publish_package shared 1.5.0
publish_package shared 1.9.0
publish_package left 1.0.0 "$org/shared=^1"
publish_package right 1.0.0 "$org/shared=<=1.5.0"

initial="$work_root/initial"
write_consumer "$initial"
install_project "$initial" "$work_root/initial-home"
assert_selected_shared "$initial/.zpkg.lock"
test -d "$initial/zed_modules/$org/shared"

metadata="$registry/packages/$org/shared/versions/1.5.0.json"
test -f "$metadata"
shared_sha="$(python3 - "$metadata" <<'PY'
import json, sys
from pathlib import Path

path = Path(sys.argv[1])
document = json.loads(path.read_text())
sha = document["sha256"]
document["yanked"] = True
path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
print(sha)
PY
)"
[[ "$shared_sha" =~ ^[0-9a-f]{64}$ ]]

# Existing locks remain authoritative after withdrawal. A cold home must replay
# the exact selected graph and preserve lock bytes.
frozen="$work_root/frozen"
mkdir -p "$frozen"
cp "$initial/.zpkg.toml" "$frozen/.zpkg.toml"
cp "$initial/.zpkg.lock" "$frozen/.zpkg.lock"
cp "$frozen/.zpkg.lock" "$work_root/frozen.lock.before"
install_project "$frozen" "$work_root/frozen-home" --frozen
cmp "$work_root/frozen.lock.before" "$frozen/.zpkg.lock"
assert_selected_shared "$frozen/.zpkg.lock"
test -d "$frozen/zed_modules/$org/shared"

# Fresh solving must reject the yanked candidate before artifact acquisition.
# The current product regression downloads/extracts it and checks `yanked`
# afterward; this assertion makes that ordering observable and permanent.
fresh="$work_root/fresh"
fresh_home="$work_root/fresh-home"
write_consumer "$fresh"
if install_project "$fresh" "$fresh_home" >"$work_root/fresh.log" 2>&1; then
  echo "fresh resolution unexpectedly selected a yanked-only common version" >&2
  exit 1
fi
grep -i 'yanked' "$work_root/fresh.log"
grep -F -- '--frozen' "$work_root/fresh.log"
test ! -e "$fresh/.zpkg.lock"
test ! -e "$fresh/zed_modules"
test ! -e "$fresh/.zed"
test ! -e "$fresh/.zpkg-staging"

find "$fresh_home" -print >"$work_root/fresh-home-paths.txt" 2>/dev/null || true
if grep -Fq "$shared_sha" "$work_root/fresh-home-paths.txt"; then
  echo "fresh resolution acquired yanked $org/shared@1.5.0 before rejecting it" >&2
  grep -F "$shared_sha" "$work_root/fresh-home-paths.txt" >&2
  exit 1
fi

printf 'DEN-1553 yanked-candidate hardening acceptance passed: %s\n' "$work_root"
