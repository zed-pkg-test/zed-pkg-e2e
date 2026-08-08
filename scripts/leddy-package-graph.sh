#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: leddy-package-graph.sh ZED_BIN INTERFACES_DIR LIB_DIR CLIENTS_DIR MONOREPO_DIR WORK_ROOT
EOF
  exit 64
}

[[ $# -eq 6 ]] || usage
zed="$1"
interfaces_source="$2"
lib_source="$3"
clients_source="$4"
monorepo_source="$5"
work_root="$6"

[[ -x "$zed" ]] || { echo "zed binary is not executable: $zed" >&2; exit 66; }
for fixture in "$interfaces_source" "$lib_source" "$clients_source" "$monorepo_source"; do
  [[ -f "$fixture/.zpkg.toml" ]] || { echo "missing .zpkg.toml in $fixture" >&2; exit 66; }
done
[[ ! -e "$work_root" ]] || { echo "work root must be fresh: $work_root" >&2; exit 73; }

zed="$(cd "$(dirname "$zed")" && pwd)/$(basename "$zed")"
mkdir -p "$work_root"
work_root="$(cd "$work_root" && pwd)"
registry="$work_root/registry"
home="$work_root/home"
mkdir -p "$registry" "$home"
file_registry="file://$registry"

unset ZED_PKG_TOKEN ZED_PKG_REGISTRY ZED_PKG_HOME ZED_PKG_ALLOW_NO_MANIFEST \
  ZED_PKG_DO_NOT_WRITE_NEW_MANIFEST ZED_PKG_INTERACTIVE

copy_tree() {
  local source="$1"
  local destination="$2"
  mkdir -p "$destination"
  cp -R "$source/." "$destination/"
  rm -rf "$destination/.git" "$destination/target" "$destination/.vendor" \
    "$destination/.zed" "$destination/.zpkg.lock" "$destination/.zpkg-staging"
}

assert_manifest_contract() {
  local root="$1"
  local package="$2"
  grep -F 'org = "led-dynamo"' "$root/.zpkg.toml"
  grep -F "name = \"$package\"" "$root/.zpkg.toml"
  grep -F 'version = "0.1.0"' "$root/.zpkg.toml"
  grep -F 'dir = ".vendor/.zed"' "$root/.zpkg.toml"
}

publish_fixture() {
  local label="$1"
  local source="$2"
  local package="$3"
  local destination="$work_root/publish/$label"
  copy_tree "$source" "$destination"
  assert_manifest_contract "$destination" "$package"
  (
    cd "$destination"
    "$zed" --registry "$file_registry" --home "$home/publisher-$label" \
      publish --skip-vcs-checks
  )
  test -f "$registry/packages/led-dynamo/$package/package.json"
  test -f "$registry/packages/led-dynamo/$package/versions/0.1.0.json"
}

# Publish in dependency order into a fresh credential-free registry. This proves
# the authored Leddy graph resolves through the real package manager rather than
# only through grep/static repository CI.
publish_fixture interfaces "$interfaces_source" leddy-interfaces
publish_fixture lib "$lib_source" leddy-lib
publish_fixture clients "$clients_source" leddy-clients
publish_fixture monorepo "$monorepo_source" leddy-monorepo

# Assert the canonical dependency edges before exercising resolver closure.
grep -F '"led-dynamo/leddy-interfaces" = "^0.1.0"' "$lib_source/.zpkg.toml"
grep -F '"led-dynamo/leddy-interfaces" = "^0.1.0"' "$clients_source/.zpkg.toml"
grep -F '"led-dynamo/leddy-lib" = "^0.1.0"' "$clients_source/.zpkg.toml"
for dependency in leddy-interfaces leddy-lib leddy-clients; do
  grep -F "\"led-dynamo/$dependency\" = \"^0.1.0\"" "$monorepo_source/.zpkg.toml"
done

# The multi-target client package is a deliberately broad stress case. Check a
# representative set spanning native, managed, scripting, mobile and WASM.
for target in c cpp zig gleamlang erlang elixir dart rust java golang python3 ruby php \
  typescript-nodejs typescript-deno typescript-bun typescript-edge kotlin swift rust-wasm nodejs; do
  grep -F "[targets.$target]" "$clients_source/.zpkg.toml"
done

install_consumer() {
  local name="$1"
  local consumer="$work_root/$name"
  local consumer_home="$home/$name"
  mkdir -p "$consumer"
  (
    cd "$consumer"
    "$zed" --registry "$file_registry" --home "$consumer_home" \
      install led-dynamo/leddy-monorepo@^0.1.0 \
      --do-not-write-new-manifest \
      --install-mode copy
  )

  test ! -e "$consumer/.zpkg.toml"
  test -f "$consumer/.zpkg.lock"
  for package in leddy-monorepo leddy-clients leddy-lib leddy-interfaces; do
    test -d "$consumer/zed_modules/led-dynamo/$package"
  done
  if find "$consumer/zed_modules" -type l -print -quit | grep -q .; then
    echo "copy-mode Leddy graph unexpectedly contains symlinks" >&2
    find "$consumer/zed_modules" -type l -print >&2
    exit 1
  fi
}

install_consumer consumer-a
install_consumer consumer-b
cmp "$work_root/consumer-a/.zpkg.lock" "$work_root/consumer-b/.zpkg.lock"

# Frozen reinstall must reconstruct the exact graph from lock state with no
# manifest creation and no registry-selection drift.
rm -rf "$work_root/consumer-a/zed_modules" "$work_root/consumer-a/.vendor" "$work_root/consumer-a/.zed"
(
  cd "$work_root/consumer-a"
  "$zed" --registry "$file_registry" --home "$home/consumer-a" \
    install --frozen --do-not-write-new-manifest --install-mode copy
)
for package in leddy-monorepo leddy-clients leddy-lib leddy-interfaces; do
  test -d "$work_root/consumer-a/zed_modules/led-dynamo/$package"
done
cmp "$work_root/consumer-a/.zpkg.lock" "$work_root/consumer-b/.zpkg.lock"

# The test registry is the only publication surface used by this canary.
test -d "$registry/packages/led-dynamo"
printf 'Leddy Zed graph canary passed: interfaces -> lib -> clients -> monorepo\n'
