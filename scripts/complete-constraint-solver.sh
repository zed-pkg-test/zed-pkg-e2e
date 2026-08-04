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
home="$work_root/home"
publisher_home="$home/publisher"
mkdir -p "$registry" "$publisher_home"
file_registry="file://$registry"

# This is a credential-free, isolated file-registry contract. Ambient state
# must not choose a registry, home, target, adapter, concurrency, or legacy
# manifest mode for the candidate under test.
unset ZED_PKG_TOKEN ZED_PKG_REGISTRY ZED_PKG_HOME ZED_PKG_TARGET \
  ZED_PKG_ADAPTER ZED_PKG_INSTALL_CONCURRENCY ZED_PKG_ALLOW_NO_MANIFEST \
  ZED_PKG_DO_NOT_WRITE_NEW_MANIFEST ZED_PKG_INTERACTIVE

publish_package() {
  local name="$1"
  local version="$2"
  shift 2
  local source="$work_root/publish/${name}-${version//[^A-Za-z0-9._-]/-}"
  mkdir -p "$source"
  {
    cat <<EOF
[package]
org = "solver-e2e"
name = "$name"
version = "$version"
description = "DEN-1553 immutable solver fixture"

[package.repository]
vcs = "git"
url = "https://example.invalid/solver-e2e/$name"
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
  printf '%s@%s\n' "$name" "$version" >"$source/payload.txt"
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
  local name="$2"
  shift 2
  mkdir -p "$root"
  {
    cat <<EOF
[package]
org = "solver-consumer"
name = "$name"
version = "0.1.0"

[package.repository]
vcs = "git"
url = "https://example.invalid/solver-consumer/$name"
EOF
    printf '\n[dependencies]\n'
    local spec key requirement
    for spec in "$@"; do
      key="${spec%%=*}"
      requirement="${spec#*=}"
      printf '"%s" = "%s"\n' "$key" "$requirement"
    done
  } >"$root/.zpkg.toml"
}

install_project() {
  local root="$1"
  local install_home="$2"
  shift 2
  (
    cd "$root"
    "$zed" \
      --registry "$file_registry" \
      --home "$install_home" \
      install --install-mode copy --adapter none "$@"
  )
}

assert_lock_versions() {
  local lockfile="$1"
  shift
  python3 - "$lockfile" "$@" <<'PY'
import sys, tomllib
from pathlib import Path

lock_path = Path(sys.argv[1])
expected = dict(item.split("=", 1) for item in sys.argv[2:])
with lock_path.open("rb") as handle:
    lock = tomllib.load(handle)
actual = {
    f"{package['org']}/{package['name']}": package['version']
    for package in lock.get("package", [])
}
if actual != expected:
    raise SystemExit(f"unexpected lock graph: expected={expected!r} actual={actual!r}")
PY
}

assert_no_project_mutation() {
  local root="$1"
  test ! -e "$root/.zpkg.lock"
  test ! -e "$root/zed_modules"
  test ! -e "$root/.zed"
  test ! -e "$root/.zpkg-staging"
}

# ---------------------------------------------------------------------------
# Publish the overlap graph that the former first-seen greedy walk rejected.

publish_package shared 1.5.0
publish_package shared 1.9.0
publish_package overlap-left 1.0.0 "solver-e2e/shared=^1"
publish_package overlap-right 1.0.0 "solver-e2e/shared=<=1.5.0"

overlap="$work_root/consumers/overlap"
write_consumer \
  "$overlap" overlap \
  "solver-e2e/overlap-left==1.0.0" \
  "solver-e2e/overlap-right==1.0.0"
install_project "$overlap" "$home/overlap"
assert_lock_versions \
  "$overlap/.zpkg.lock" \
  "solver-e2e/overlap-left=1.0.0" \
  "solver-e2e/overlap-right=1.0.0" \
  "solver-e2e/shared=1.5.0"
test -d "$overlap/zed_modules/solver-e2e/shared"
test ! -e "$overlap/zed_modules/solver-e2e/shared-1.9.0"

# Frozen replay is lock-authoritative. Yank the selected version after writing
# the lock, clear project output, and prove a cold home still installs the exact
# locked artifact without resolving a different graph or rewriting the lock.
shared_metadata="$registry/packages/solver-e2e/shared/versions/1.5.0.json"
shared_sha="$(python3 - "$shared_metadata" <<'PY'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
data = json.loads(path.read_text())
print(data["sha256"])
data["yanked"] = True
path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
PY
)"
cp "$overlap/.zpkg.lock" "$work_root/overlap.lock.before"
rm -rf "$overlap/zed_modules" "$overlap/.zed" "$overlap/.zpkg-staging"
install_project "$overlap" "$home/frozen" --frozen
cmp "$work_root/overlap.lock.before" "$overlap/.zpkg.lock"
test -d "$overlap/zed_modules/solver-e2e/shared"

# Fresh resolution must skip a yanked candidate before artifact acquisition.
# This is both a user-visible policy assertion and a hardening boundary: a
# withdrawn archive must not be downloaded or extracted merely to learn that
# its immutable metadata is yanked.
yanked="$work_root/consumers/yanked-fresh"
write_consumer \
  "$yanked" overlap \
  "solver-e2e/overlap-left==1.0.0" \
  "solver-e2e/overlap-right==1.0.0"
if install_project "$yanked" "$home/yanked-fresh" \
  >"$work_root/yanked-fresh.log" 2>&1; then
  echo "fresh resolution unexpectedly selected a yanked-only common version" >&2
  exit 1
fi
grep -i 'yanked' "$work_root/yanked-fresh.log"
grep -F -- '--frozen' "$work_root/yanked-fresh.log"
assert_no_project_mutation "$yanked"
find "$home/yanked-fresh" -print >"$work_root/yanked-home-paths.txt"
if grep -Fq "$shared_sha" "$work_root/yanked-home-paths.txt"; then
  echo "fresh resolution acquired the yanked shared@1.5.0 artifact" >&2
  grep -F "$shared_sha" "$work_root/yanked-home-paths.txt" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Backtracking must cross more than one package coordinate.

publish_package core 1.0.0
publish_package core 2.0.0
publish_package route-b 1.0.0 "solver-e2e/core=^1"
publish_package route-b 2.0.0 "solver-e2e/core=^2"
publish_package route-a 1.0.0 "solver-e2e/route-b=^1"
publish_package route-a 2.0.0 "solver-e2e/route-b=^2"
publish_package policy 1.0.0 "solver-e2e/core=^1"

backtrack="$work_root/consumers/backtrack"
write_consumer \
  "$backtrack" backtrack \
  "solver-e2e/route-a=>=1" \
  "solver-e2e/policy==1.0.0"
install_project "$backtrack" "$home/backtrack"
assert_lock_versions \
  "$backtrack/.zpkg.lock" \
  "solver-e2e/core=1.0.0" \
  "solver-e2e/policy=1.0.0" \
  "solver-e2e/route-a=1.0.0" \
  "solver-e2e/route-b=1.0.0"

# ---------------------------------------------------------------------------
# Unsatisfiable diagnostics must retain both provenance paths, be byte-stable
# under reversed declaration order, and leave the project entirely untouched.

publish_package conflict-leaf 1.0.0
publish_package conflict-leaf 2.0.0
publish_package conflict-left 1.0.0 "solver-e2e/conflict-leaf=^1"
publish_package conflict-right 1.0.0 "solver-e2e/conflict-leaf=^2"

conflict_a="$work_root/consumers/conflict-a"
conflict_b="$work_root/consumers/conflict-b"
write_consumer \
  "$conflict_a" conflict \
  "solver-e2e/conflict-left==1.0.0" \
  "solver-e2e/conflict-right==1.0.0"
write_consumer \
  "$conflict_b" conflict \
  "solver-e2e/conflict-right==1.0.0" \
  "solver-e2e/conflict-left==1.0.0"
cp "$conflict_a/.zpkg.toml" "$work_root/conflict-a.toml.before"
cp "$conflict_b/.zpkg.toml" "$work_root/conflict-b.toml.before"

if install_project "$conflict_a" "$home/conflict-a" \
  >"$work_root/conflict-a.log" 2>&1; then
  echo "unsatisfiable conflict graph unexpectedly installed" >&2
  exit 1
fi
if install_project "$conflict_b" "$home/conflict-b" \
  >"$work_root/conflict-b.log" 2>&1; then
  echo "reversed unsatisfiable conflict graph unexpectedly installed" >&2
  exit 1
fi

grep -F 'version conflict for solver-e2e/conflict-leaf' "$work_root/conflict-a.log"
grep -F '`^1` via solver-consumer/conflict@0.1.0 -> solver-e2e/conflict-left@1.0.0 -> solver-e2e/conflict-leaf' \
  "$work_root/conflict-a.log"
grep -F '`^2` via solver-consumer/conflict@0.1.0 -> solver-e2e/conflict-right@1.0.0 -> solver-e2e/conflict-leaf' \
  "$work_root/conflict-a.log"
cmp "$work_root/conflict-a.log" "$work_root/conflict-b.log"
cmp "$work_root/conflict-a.toml.before" "$conflict_a/.zpkg.toml"
cmp "$work_root/conflict-b.toml.before" "$conflict_b/.zpkg.toml"
assert_no_project_mutation "$conflict_a"
assert_no_project_mutation "$conflict_b"

printf 'DEN-1553 complete constraint-solver acceptance passed: %s\n' "$work_root"
