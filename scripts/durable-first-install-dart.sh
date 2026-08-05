#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: durable-first-install-dart.sh ZED_BIN DART_LIB_SOURCE DART_APP_SOURCE WORK_ROOT
EOF
  exit 64
}

[[ $# -eq 4 ]] || usage
zed="$1"
lib_source="$2"
app_source="$3"
work_root="$4"

[[ -x "$zed" ]] || { echo "zed binary is not executable: $zed" >&2; exit 66; }
[[ -f "$lib_source/.zpkg.toml" ]] || { echo "missing Dart library fixture" >&2; exit 66; }
[[ -f "$lib_source/pubspec.yaml" ]] || { echo "missing Dart library pubspec" >&2; exit 66; }
[[ -f "$app_source/.zpkg.toml" ]] || { echo "missing Dart application fixture" >&2; exit 66; }
[[ -f "$app_source/pubspec.yaml" ]] || { echo "missing Dart application pubspec" >&2; exit 66; }
[[ ! -e "$work_root" ]] || { echo "work root must be fresh: $work_root" >&2; exit 73; }

zed="$(cd "$(dirname "$zed")" && pwd)/$(basename "$zed")"
lib_source="$(cd "$lib_source" && pwd)"
app_source="$(cd "$app_source" && pwd)"
mkdir -p "$work_root"
work_root="$(cd "$work_root" && pwd)"

registry="$work_root/registry"
home="$work_root/zed-home"
file_registry="file://$registry"
package="zed-pkg-test/dart-lib"
native_name="zed_pkg_test_dart_lib"
installed_relative="zed_modules/zed-pkg-test/dart-lib"
mkdir -p "$registry" "$home"

# Keep the test credential-free and independent of developer or runner state.
unset ZED_PKG_TOKEN ZED_PKG_REGISTRY ZED_PKG_HOME ZED_PKG_INTERACTIVE \
  ZED_PKG_ALLOW_NO_MANIFEST ZED_PKG_DO_NOT_WRITE_NEW_MANIFEST \
  PUB_CACHE FLUTTER_ROOT

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
    "$destination/.dart_tool" \
    "$destination/pubspec.lock" \
    "$destination/build"

  # The authored fixture deliberately uses [install].dir = `.vendor/.zed`.
  # A generated consumer manifest intentionally defaults to `zed_modules`, so
  # only the disposable native consumer copy is adjusted to that path.
  perl -0pi -e \
    's@\.vendor/\.zed/zed-pkg-test/dart-lib@zed_modules/zed-pkg-test/dart-lib@g' \
    "$destination/pubspec.yaml"
  mkdir -p "$destination/tool/deep"
}

assert_no_symlinks() {
  local root="$1"
  if find "$root/zed_modules" -type l -print -quit | grep -q .; then
    echo "copy-mode Dart install contains a symlink: $root" >&2
    find "$root/zed_modules" -type l -print >&2
    exit 1
  fi
}

assert_dart_wiring() {
  local root="$1"
  local wiring="$root/.zed/pub-deps.yaml"
  local expected="$work_root/expected-pub-deps.yaml"

  test -f "$root/.zed/paths.json"
  grep -Fq "$installed_relative" "$root/.zed/paths.json"
  test -f "$wiring"
  cat >"$expected" <<EOF
dependencies:
  $native_name:
    path: "$installed_relative"
EOF
  cmp "$expected" "$wiring"
  if grep -Eq '^  dart-lib:$' "$wiring"; then
    echo "Dart wiring used the Zed directory basename instead of pubspec identity" >&2
    exit 1
  fi
}

run_native_consumer() {
  local root="$1"
  local label="$2"
  local log="$work_root/${label}-dart.log"

  grep -Fq "$native_name:" "$root/pubspec.yaml"
  grep -Fq "path: $installed_relative" "$root/pubspec.yaml"
  (
    cd "$root"
    dart pub get --offline
    dart run bin/dart_app.dart
  ) 2>&1 | tee "$log"
  grep -Fq 'OK: zed-sourced dep resolved alongside pub' "$log"
}

assert_installed() {
  local root="$1"
  local installed="$root/$installed_relative"
  test -f "$installed/.zpkg.toml"
  test -f "$installed/pubspec.yaml"
  test -f "$installed/lib/dart_lib.dart"
  grep -Eq '^name:[[:space:]]+zed_pkg_test_dart_lib([[:space:]]|$)' \
    "$installed/pubspec.yaml"
  assert_no_symlinks "$root"
  assert_dart_wiring "$root"
}

publish_source="$work_root/publish/dart-lib"
copy_tree "$lib_source" "$publish_source"
(
  cd "$publish_source"
  "$zed" \
    --registry "$file_registry" \
    --home "$home/publisher" \
    publish --skip-vcs-checks
)

test -f "$registry/packages/zed-pkg-test/dart-lib/package.json"
test -f "$registry/packages/zed-pkg-test/dart-lib/versions/1.0.0.json"
test -n "$(find "$registry/artifacts" -maxdepth 1 -type f -name '*.tar.gz' -print -quit)"

install_consumer() {
  local root="$1"
  local local_home="$2"
  local spec="$3"
  local label="$4"
  prepare_consumer "$root"

  # Invoke below the Dart project root to certify conservative pubspec-based
  # root inference and root-only manifest creation.
  (
    cd "$root/tool/deep"
    "$zed" \
      --registry "$file_registry" \
      --home "$local_home" \
      install "$spec" \
      --install-mode copy
  ) 2>&1 | tee "$work_root/${label}-install.log"

  test -f "$root/.zpkg.toml"
  test -f "$root/.zpkg.lock"
  test ! -e "$root/tool/.zpkg.toml"
  test ! -e "$root/tool/deep/.zpkg.toml"
  grep -F 'org = "zed-local"' "$root/.zpkg.toml"
  grep -F 'name = "consumer"' "$root/.zpkg.toml"
  grep -F 'version = "0.0.0"' "$root/.zpkg.toml"
  grep -F 'zed-generated-consumer' "$root/.zpkg.toml"
  grep -F '"zed-pkg-test/dart-lib" = "^1.0.0"' "$root/.zpkg.toml"
  grep -F 'adapter = "dart"' "$root/.zpkg.toml"
  grep -F 'target = "dart"' "$root/.zpkg.toml"

  if grep -Fq "$work_root" "$root/.zpkg.toml"; then
    echo "generated Dart manifest leaked an absolute working path" >&2
    exit 1
  fi
  grep -Fq "$file_registry" "$root/.zpkg.lock"

  assert_installed "$root"
  run_native_consumer "$root" "$label"
}

explicit="$work_root/explicit/consumer"
latest="$work_root/latest/consumer"
install_consumer "$explicit" "$home/explicit" "$package@^1.0.0" explicit
install_consumer "$latest" "$home/latest" "$package" latest

# Explicit semver and registry-latest resolution must converge on the same
# durable direct requirement and immutable lock bytes.
cmp "$explicit/.zpkg.toml" "$latest/.zpkg.toml"
cmp "$explicit/.zpkg.lock" "$latest/.zpkg.lock"

# Uninstall materialized content from the managed root, then prove a frozen
# nested-directory reinstall restores package and native wiring without
# changing durable project state.
cp "$explicit/.zpkg.toml" "$work_root/before-reinstall.toml"
cp "$explicit/.zpkg.lock" "$work_root/before-reinstall.lock"
(
  cd "$explicit"
  "$zed" --home "$home/explicit" uninstall
)
test -f "$explicit/.zpkg.toml"
test -f "$explicit/.zpkg.lock"
test ! -e "$explicit/$installed_relative"
(
  cd "$explicit/tool/deep"
  "$zed" \
    --registry "$file_registry" \
    --home "$home/explicit" \
    install --frozen --install-mode copy
)
cmp "$work_root/before-reinstall.toml" "$explicit/.zpkg.toml"
cmp "$work_root/before-reinstall.lock" "$explicit/.zpkg.lock"
assert_installed "$explicit"
run_native_consumer "$explicit" frozen

printf 'DEN-1579 Dart durable first-install acceptance passed: %s\n' "$work_root"
