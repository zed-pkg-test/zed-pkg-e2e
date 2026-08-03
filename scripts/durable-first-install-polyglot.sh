#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: durable-first-install-polyglot.sh ZED_BIN ECOSYSTEM LIB_SOURCE APP_SOURCE WORK_ROOT

ECOSYSTEM must be one of: go, python, rust.
EOF
  exit 64
}

[[ $# -eq 5 ]] || usage
zed="$1"
ecosystem="$2"
lib_source="$3"
app_source="$4"
work_root="$5"

case "$ecosystem" in
  go|python|rust) ;;
  *) usage ;;
esac

[[ -x "$zed" ]] || { echo "zed binary is not executable: $zed" >&2; exit 66; }
[[ -f "$lib_source/.zpkg.toml" ]] || { echo "missing library fixture: $lib_source" >&2; exit 66; }
[[ -f "$app_source/.zpkg.toml" ]] || { echo "missing application fixture: $app_source" >&2; exit 66; }
[[ ! -e "$work_root" ]] || { echo "work root must be fresh: $work_root" >&2; exit 73; }

zed="$(cd "$(dirname "$zed")" && pwd)/$(basename "$zed")"
lib_source="$(cd "$lib_source" && pwd)"
app_source="$(cd "$app_source" && pwd)"
mkdir -p "$work_root"
work_root="$(cd "$work_root" && pwd)"

registry="$work_root/registry"
home="$work_root/zed-home"
file_registry="file://$registry"
package="zed-pkg-test/${ecosystem}-lib"
mkdir -p "$registry" "$home"

# Do not inherit a developer/runner registry, token, compatibility switch, or
# interactive preference. The canary is credential-free and fully local.
unset ZED_PKG_TOKEN ZED_PKG_REGISTRY ZED_PKG_HOME ZED_PKG_INTERACTIVE \
  ZED_PKG_ALLOW_NO_MANIFEST ZED_PKG_DO_NOT_WRITE_NEW_MANIFEST

copy_tree() {
  local source="$1"
  local destination="$2"
  mkdir -p "$destination"
  cp -R "$source/." "$destination/"
  rm -rf "$destination/.git"
}

prepare_consumer() {
  local destination="$1"
  copy_tree "$app_source" "$destination"
  rm -rf \
    "$destination/.zpkg.toml" \
    "$destination/.zpkg.lock" \
    "$destination/.vendor" \
    "$destination/.zed" \
    "$destination/.zpkg-staging" \
    "$destination/zed_modules" \
    "$destination/node_modules" \
    "$destination/target" \
    "$destination/__pycache__"
  mkdir -p "$destination/deep/nested"
}

python_adapter_path() {
  local root="$1"
  local candidate
  for candidate in "$root/.zed/pythonpath" "$root/.zed/python_path"; do
    if [[ -f "$candidate" ]]; then
      cat "$candidate"
      return 0
    fi
  done
  echo "python adapter did not write .zed/pythonpath or .zed/python_path" >&2
  return 1
}

run_native_consumer() {
  local root="$1"
  local log="$2"
  case "$ecosystem" in
    go)
      (cd "$root" && go run .) | tee "$log"
      grep -Fq 'OK: zed-sourced module resolved' "$log"
      ;;
    python)
      local python_path
      python_path="$(python_adapter_path "$root")"
      (cd "$root" && PYTHONPATH="$python_path" python main.py) | tee "$log"
      grep -Fq 'OK: zed-sourced dep resolved' "$log"
      ;;
    rust)
      (cd "$root" && CARGO_TARGET_DIR="$work_root/cargo-target" cargo run --quiet) \
        | tee "$log"
      ;;
  esac
}

assert_package_shape() {
  local root="$1"
  local installed="$root/.vendor/.zed/$package"
  [[ -d "$installed" ]]
  case "$ecosystem" in
    go)
      [[ -f "$installed/go.mod" ]]
      [[ -f "$installed/greet.go" ]]
      ;;
    python)
      [[ -f "$installed/pyproject.toml" ]]
      [[ -f "$installed/python_lib/__init__.py" ]]
      ;;
    rust)
      [[ -f "$installed/Cargo.toml" ]]
      [[ -f "$installed/src/lib.rs" ]]
      ;;
  esac
  if find "$root/.vendor/.zed" -type l -print -quit | grep -q .; then
    echo "copy-mode install contains a symlink: $root" >&2
    find "$root/.vendor/.zed" -type l -print >&2
    exit 1
  fi
}

publish_source="$work_root/publish/${ecosystem}-lib"
copy_tree "$lib_source" "$publish_source"
(
  cd "$publish_source"
  "$zed" \
    --registry "$file_registry" \
    --home "$home/publisher" \
    publish --skip-vcs-checks
)

test -f "$registry/packages/zed-pkg-test/${ecosystem}-lib/package.json"
test -f "$registry/packages/zed-pkg-test/${ecosystem}-lib/versions/1.0.0.json"
test -n "$(find "$registry/artifacts" -maxdepth 1 -type f -name '*.tar.gz' -print -quit)"

install_consumer() {
  local root="$1"
  local local_home="$2"
  local spec="$3"
  local label="$4"
  prepare_consumer "$root"

  # The invocation is deliberately below the native project root. Zed must
  # climb to go.mod, main.py/project structure, or Cargo.toml and write only at
  # that root.
  (
    cd "$root/deep/nested"
    "$zed" \
      --registry "$file_registry" \
      --home "$local_home" \
      install "$spec" \
      --install-mode copy
  ) 2>&1 | tee "$work_root/${label}-install.log"

  test -f "$root/.zpkg.toml"
  test -f "$root/.zpkg.lock"
  test ! -e "$root/deep/.zpkg.toml"
  test ! -e "$root/deep/nested/.zpkg.toml"
  grep -F 'org = "zed-local"' "$root/.zpkg.toml"
  grep -F 'name = "consumer"' "$root/.zpkg.toml"
  grep -F 'version = "0.0.0"' "$root/.zpkg.toml"
  grep -F 'zed-generated-consumer' "$root/.zpkg.toml"
  grep -F "\"$package\" = \"^1.0.0\"" "$root/.zpkg.toml"
  grep -F "adapter = \"$ecosystem\"" "$root/.zpkg.toml"
  grep -F "target = \"$ecosystem\"" "$root/.zpkg.toml"
  if grep -Fq "$work_root" "$root/.zpkg.toml" "$root/.zpkg.lock"; then
    echo "generated project state leaked an absolute working path" >&2
    exit 1
  fi

  assert_package_shape "$root"
  run_native_consumer "$root" "$work_root/${label}-native.log"
}

explicit="$work_root/explicit/consumer"
latest="$work_root/latest/consumer"
install_consumer "$explicit" "$home/explicit" "$package@^1.0.0" explicit
install_consumer "$latest" "$home/latest" "$package" latest

# Resolving an unversioned request through the pinned local registry must record
# the same durable direct requirement and produce identical project state as an
# explicit semver request.
cmp "$explicit/.zpkg.toml" "$latest/.zpkg.toml"
cmp "$explicit/.zpkg.lock" "$latest/.zpkg.lock"

# Managed state remains stable across uninstall and a frozen reinstall from a
# nested directory. The package is rematerialized without re-resolving or
# rewriting the manifest/lockfile.
cp "$explicit/.zpkg.toml" "$work_root/before-reinstall.toml"
cp "$explicit/.zpkg.lock" "$work_root/before-reinstall.lock"
(
  cd "$explicit/deep/nested"
  "$zed" --home "$home/explicit" uninstall
)
test -f "$explicit/.zpkg.toml"
test -f "$explicit/.zpkg.lock"
test ! -e "$explicit/.vendor/.zed/$package"
(
  cd "$explicit/deep/nested"
  "$zed" \
    --registry "$file_registry" \
    --home "$home/explicit" \
    install --frozen --install-mode copy
)
cmp "$work_root/before-reinstall.toml" "$explicit/.zpkg.toml"
cmp "$work_root/before-reinstall.lock" "$explicit/.zpkg.lock"
assert_package_shape "$explicit"
run_native_consumer "$explicit" "$work_root/frozen-native.log"

printf 'DEN-1413 %s durable first-install acceptance passed: %s\n' \
  "$ecosystem" "$work_root"
