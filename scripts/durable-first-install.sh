#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: durable-first-install.sh ZED_BIN NODE_LIB_DIR NODE_APP_DIR WORK_ROOT
EOF
  exit 64
}

[[ $# -eq 4 ]] || usage
zed="$1"
node_lib_source="$2"
node_app_source="$3"
work_root="$4"

[[ -x "$zed" ]] || { echo "zed binary is not executable: $zed" >&2; exit 66; }
[[ -f "$node_lib_source/.zpkg.toml" ]] || { echo "missing node-lib fixture" >&2; exit 66; }
[[ -f "$node_app_source/package.json" ]] || { echo "missing node-app fixture" >&2; exit 66; }
[[ ! -e "$work_root" ]] || { echo "work root must be fresh: $work_root" >&2; exit 73; }

zed="$(cd "$(dirname "$zed")" && pwd)/$(basename "$zed")"
node_lib_source="$(cd "$node_lib_source" && pwd)"
node_app_source="$(cd "$node_app_source" && pwd)"
mkdir -p "$work_root"
work_root="$(cd "$work_root" && pwd)"

registry="$work_root/registry"
home="$work_root/zed-home"
mkdir -p "$registry" "$home"

# Never let a developer or runner credential, registry, or compatibility flag
# affect this credential-free local-registry acceptance test.
unset ZED_PKG_TOKEN ZED_PKG_REGISTRY ZED_PKG_HOME ZED_PKG_ALLOW_NO_MANIFEST \
  ZED_PKG_DO_NOT_WRITE_NEW_MANIFEST ZED_PKG_INTERACTIVE

file_registry="file://$registry"

copy_tree() {
  local source="$1"
  local destination="$2"
  mkdir -p "$destination"
  cp -R "$source/." "$destination/"
  rm -rf "$destination/.git"
}

prepare_consumer() {
  local destination="$1"
  copy_tree "$node_app_source" "$destination"
  rm -rf \
    "$destination/.zpkg.toml" \
    "$destination/.zpkg.lock" \
    "$destination/zed_modules" \
    "$destination/.vendor" \
    "$destination/.zed" \
    "$destination/node_modules" \
    "$destination/.zpkg-staging"
  mkdir -p "$destination/src/nested"
}

assert_no_symlinks() {
  local root="$1"
  if find "$root/zed_modules" "$root/node_modules" -type l -print -quit \
    | grep -q .; then
    echo "copy-mode consumer contains a symlink: $root" >&2
    find "$root/zed_modules" "$root/node_modules" -type l -print >&2
    exit 1
  fi
}

run_consumer() {
  local root="$1"
  (
    cd "$root"
    npm run check
    npm start
  )
}

publish_source="$work_root/publish/node-lib"
copy_tree "$node_lib_source" "$publish_source"
(
  cd "$publish_source"
  "$zed" \
    --registry "$file_registry" \
    --home "$home/publisher" \
    publish --skip-vcs-checks
)

test -f "$registry/packages/zed-pkg-test/node-lib/package.json"
test -f "$registry/packages/zed-pkg-test/node-lib/versions/1.0.0.json"
test -n "$(find "$registry/artifacts" -maxdepth 1 -type f -name '*.tar.gz' -print -quit)"

install_durable() {
  local root="$1"
  local local_home="$2"
  prepare_consumer "$root"
  (
    cd "$root/src/nested"
    "$zed" \
      --registry "$file_registry" \
      --home "$local_home" \
      install zed-pkg-test/node-lib@^1.0.0 \
      --install-mode copy
  )

  test -f "$root/.zpkg.toml"
  test -f "$root/.zpkg.lock"
  test ! -e "$root/src/.zpkg.toml"
  test ! -e "$root/src/nested/.zpkg.toml"
  grep -F 'org = "zed-local"' "$root/.zpkg.toml"
  grep -F 'name = "consumer"' "$root/.zpkg.toml"
  grep -F 'version = "0.0.0"' "$root/.zpkg.toml"
  grep -F 'zed-generated-consumer' "$root/.zpkg.toml"
  grep -F '"zed-pkg-test/node-lib" = "^1.0.0"' "$root/.zpkg.toml"
  grep -F 'adapter = "node"' "$root/.zpkg.toml"
  grep -F 'target = "node"' "$root/.zpkg.toml"
  test -d "$root/zed_modules/zed-pkg-test/node-lib"
  test -d "$root/node_modules/@zed-pkg-test/node-lib"
  assert_no_symlinks "$root"
  run_consumer "$root"
}

# Two clean projects with the same basename and inputs must receive identical
# manifest and lock bytes; timestamps, absolute project paths, and random IDs
# are not permitted in durable first-install state.
durable_a="$work_root/durable-a/consumer"
durable_b="$work_root/durable-b/consumer"
install_durable "$durable_a" "$home/durable-a"
install_durable "$durable_b" "$home/durable-b"
cmp "$durable_a/.zpkg.toml" "$durable_b/.zpkg.toml"
cmp "$durable_a/.zpkg.lock" "$durable_b/.zpkg.lock"

# Generated local identity must fail closed at publication before VCS policy or
# upload behavior can make the inferred placeholder look authoritative.
if (
  cd "$durable_a"
  "$zed" \
    --registry "$file_registry" \
    --home "$home/generated-publish" \
    publish --dry-run --skip-vcs-checks
) >"$work_root/generated-publish.log" 2>&1; then
  echo "generated consumer manifest unexpectedly passed publication" >&2
  exit 1
fi
grep -F 'auto-generated local consumer manifest' "$work_root/generated-publish.log"

# The canonical flag is only a no-new-file escape hatch. On an already managed
# project it is an informational no-op, and frozen installation remains normal.
cp "$durable_a/.zpkg.toml" "$work_root/generated-before.toml"
cp "$durable_a/.zpkg.lock" "$work_root/generated-before.lock"
(
  cd "$durable_a/src"
  "$zed" \
    --registry "$file_registry" \
    --home "$home/durable-a" \
    install --frozen --do-not-write-new-manifest --install-mode copy
) >"$work_root/existing-generated.log" 2>&1
cmp "$work_root/generated-before.toml" "$durable_a/.zpkg.toml"
cmp "$work_root/generated-before.lock" "$durable_a/.zpkg.lock"
grep -F 'has no effect because .zpkg.toml already exists' \
  "$work_root/existing-generated.log"
run_consumer "$durable_a"

# An authored manifest is also never converted into ephemeral mode. The
# existing bytes remain untouched while the ordinary dependency graph installs.
authored="$work_root/authored/node-app"
copy_tree "$node_app_source" "$authored"
rm -rf "$authored/.zpkg.lock" "$authored/.vendor" "$authored/.zed" \
  "$authored/node_modules" "$authored/.zpkg-staging"
cp "$authored/.zpkg.toml" "$work_root/authored-before.toml"
(
  cd "$authored/src"
  "$zed" \
    --registry "$file_registry" \
    --home "$home/authored" \
    install --do-not-write-new-manifest --install-mode copy
) >"$work_root/authored.log" 2>&1
cmp "$work_root/authored-before.toml" "$authored/.zpkg.toml"
test -f "$authored/.zpkg.lock"
grep -F 'has no effect because .zpkg.toml already exists' "$work_root/authored.log"
run_consumer "$authored"

# The canonical CLI escape hatch preserves the established synthetic consumer
# path: lock and package outputs are allowed, but no .zpkg.toml is written.
ephemeral="$work_root/ephemeral/consumer"
prepare_consumer "$ephemeral"
(
  cd "$ephemeral/src/nested"
  "$zed" \
    --registry "$file_registry" \
    --home "$home/ephemeral" \
    install zed-pkg-test/node-lib@^1.0.0 \
    --do-not-write-new-manifest \
    --install-mode copy
)
test ! -e "$ephemeral/.zpkg.toml"
test -f "$ephemeral/.zpkg.lock"
test -d "$ephemeral/zed_modules/zed-pkg-test/node-lib"
test -d "$ephemeral/node_modules/@zed-pkg-test/node-lib"
assert_no_symlinks "$ephemeral"
run_consumer "$ephemeral"

# The canonical environment variable is equivalent to the canonical flag.
env_ephemeral="$work_root/env-ephemeral/consumer"
prepare_consumer "$env_ephemeral"
(
  cd "$env_ephemeral"
  ZED_PKG_DO_NOT_WRITE_NEW_MANIFEST=1 \
    "$zed" \
      --registry "$file_registry" \
      --home "$home/env-ephemeral" \
      install zed-pkg-test/node-lib@^1.0.0 \
      --install-mode copy
)
test ! -e "$env_ephemeral/.zpkg.toml"
test -f "$env_ephemeral/.zpkg.lock"
run_consumer "$env_ephemeral"

# Legacy spellings remain functional during the compatibility window but make
# the migration path explicit in diagnostics.
legacy="$work_root/legacy/consumer"
prepare_consumer "$legacy"
(
  cd "$legacy"
  "$zed" \
    --registry "$file_registry" \
    --home "$home/legacy" \
    install zed-pkg-test/node-lib@^1.0.0 \
    --skip-manifest \
    --install-mode copy
) >"$work_root/legacy.log" 2>&1
test ! -e "$legacy/.zpkg.toml"
test -f "$legacy/.zpkg.lock"
grep -F -- '--skip-manifest is deprecated' "$work_root/legacy.log"
run_consumer "$legacy"

# A failed graph resolution must remove the exact generated manifest and leave
# no lockfile, materialized dependency, adapter output, or transaction debris.
failed="$work_root/failed/consumer"
prepare_consumer "$failed"
if (
  cd "$failed"
  "$zed" \
    --registry "$file_registry" \
    --home "$home/failed" \
    install zed-pkg-test/node-lib@=9.9.9 \
    --install-mode copy
) >"$work_root/failed.log" 2>&1; then
  echo "an unpublished dependency version unexpectedly installed" >&2
  exit 1
fi
test ! -e "$failed/.zpkg.toml"
test ! -e "$failed/.zpkg.lock"
test ! -e "$failed/zed_modules"
test ! -e "$failed/node_modules/@zed-pkg-test/node-lib"
test ! -e "$failed/.zed"
test ! -e "$failed/.zpkg-staging"

# A lockfile alone cannot truthfully reconstruct direct dependency intent. The
# default fails rather than treating every transitive package as direct; the
# explicit ephemeral mode replays the lock byte-for-byte.
restore="$work_root/restore/consumer"
prepare_consumer "$restore"
cp "$ephemeral/.zpkg.lock" "$restore/.zpkg.lock"
if (
  cd "$restore"
  "$zed" \
    --registry "$file_registry" \
    --home "$home/ephemeral" \
    install --frozen --install-mode copy
) >"$work_root/implicit-restore.log" 2>&1; then
  echo "manifestless lock-only restore unexpectedly inferred a manifest" >&2
  exit 1
fi
test ! -e "$restore/.zpkg.toml"
grep -F 'lockfile cannot identify which packages were direct dependencies' \
  "$work_root/implicit-restore.log"
(
  cd "$restore"
  "$zed" \
    --registry "$file_registry" \
    --home "$home/ephemeral" \
    install --frozen --do-not-write-new-manifest --install-mode copy
)
test ! -e "$restore/.zpkg.toml"
cmp "$ephemeral/.zpkg.lock" "$restore/.zpkg.lock"
run_consumer "$restore"

printf 'DEN-1413 durable first-install acceptance passed: %s\n' "$work_root"
