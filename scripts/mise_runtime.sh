#!/usr/bin/env bash
# Black-box certification for zed-pkg's runtime mise composition.
set -euo pipefail

usage() {
  echo "usage: $0 --zed /absolute/path/to/zed --work-root /fresh/path" >&2
  exit 64
}

zed=''
work_root=''
while (($#)); do
  case "$1" in
    --zed)
      (($# >= 2)) || usage
      zed=$2
      shift 2
      ;;
    --work-root)
      (($# >= 2)) || usage
      work_root=$2
      shift 2
      ;;
    *) usage ;;
  esac
done

[[ -n "$zed" && -n "$work_root" ]] || usage
[[ -x "$zed" ]] || { echo "zed executable is missing: $zed" >&2; exit 1; }
[[ ! -e "$work_root" ]] || { echo "work root already exists: $work_root" >&2; exit 1; }
mkdir -p "$work_root/logs" "$work_root/stub-bin" "$work_root/mise-tool-bin"
work_root=$(cd "$work_root" && pwd -P)
zed=$(cd "$(dirname "$zed")" && printf '%s/%s\n' "$(pwd -P)" "$(basename "$zed")")
shell_bin=/bin/bash
original_path=${PATH:-}
unset MISE_STUB_ACTIVE ZED_DEV_MISE_ACTIVE __MISE_DIFF || true
passed=0

fail() {
  echo "mise runtime certification failed: $*" >&2
  exit 1
}

pass() {
  passed=$((passed + 1))
  printf 'ok %02d - %s\n' "$passed" "$1"
}

write_project() {
  local project=$1
  mkdir -p "$project/.git" "$project/zed_modules/.bin"
  printf '%s\n' '{}' > "$project/package.json"
  cat > "$project/zed_modules/.bin/zed-tool" <<'TOOL'
#!/bin/sh
printf zed-tool
TOOL
  chmod +x "$project/zed_modules/.bin/zed-tool"
}

cat > "$work_root/mise-tool-bin/mise-tool" <<'TOOL'
#!/bin/sh
printf mise-tool
TOOL
chmod +x "$work_root/mise-tool-bin/mise-tool"

cat > "$work_root/stub-bin/mise" <<'MISE'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == '--version' ]]; then
  printf '%s\n' 'mise 2099.0.0-test'
  exit 0
fi
{
  printf 'argv=%q ' "$@"
  printf '\n'
  printf 'cwd=%s\n' "$PWD"
  printf 'MISE_CEILING_PATHS=%s\n' "${MISE_CEILING_PATHS:-}"
  printf 'MISE_CONFIG_DIR=%s\n' "${MISE_CONFIG_DIR:-}"
  printf 'MISE_LOCKED=%s\n' "${MISE_LOCKED:-}"
  printf 'MISE_OVERRIDE_CONFIG_FILENAMES=%s\n' "${MISE_OVERRIDE_CONFIG_FILENAMES:-}"
  printf 'MISE_OVERRIDE_TOOL_VERSIONS_FILENAMES=%s\n' "${MISE_OVERRIDE_TOOL_VERSIONS_FILENAMES:-}"
  printf 'MISE_SYSTEM_CONFIG_DIR=%s\n' "${MISE_SYSTEM_CONFIG_DIR:-}"
  printf 'ZED_DEV_MISE_ACTIVE=%s\n' "${ZED_DEV_MISE_ACTIVE:-}"
  printf '%s\n' '---'
} >> "$MISE_STUB_LOG"
[[ "${1:-}" == exec ]] || exit 64
shift
[[ "${1:-}" == -- ]] || exit 64
shift
[[ $# -gt 0 ]] || exit 64
export MISE_STUB_ACTIVE=1
export PATH="$MISE_STUB_TOOL_DIR:${PATH:-}"
exec "$@"
MISE
chmod +x "$work_root/stub-bin/mise"

base_env=(
  "PATH=$work_root/stub-bin:$original_path"
  "MISE_STUB_TOOL_DIR=$work_root/mise-tool-bin"
  "ZED_PKG_HOME=$work_root/zed-home"
)

run_capture() {
  local expected=$1
  local stdout=$2
  local stderr=$3
  shift 3
  set +e
  "$@" >"$stdout" 2>"$stderr"
  local status=$?
  set -e
  if [[ $status -ne $expected ]]; then
    cat "$stdout" >&2 || true
    cat "$stderr" >&2 || true
    fail "expected status $expected, got $status: $*"
  fi
}

project="$work_root/required"
write_project "$project"
printf '%s\n' '[tools]' 'node = "22.4.0"' > "$project/mise.toml"
log="$work_root/logs/required.log"
: > "$log"
(
  cd "$project"
  env "${base_env[@]}" "MISE_STUB_LOG=$log" "$zed" dev \
    --no-install --nix never --mise required --python-venv never \
    --shell "$shell_bin" \
    -c 'test "$MISE_STUB_ACTIVE" = 1; test "$(mise-tool)" = mise-tool; test "$(zed-tool)" = zed-tool; case "$PATH" in "$PWD/zed_modules/.bin":*"$MISE_STUB_TOOL_DIR"*) ;; *) exit 46 ;; esac; printf required-ok'
) | grep -q required-ok
[[ $(grep -c '^argv=' "$log") -eq 1 ]] || fail 'required mode did not enter mise exactly once'
pass 'required activation and PATH coexistence'

project="$work_root/env-required"
write_project "$project"
printf '%s\n' '[tools]' 'python = "3.12.4"' > "$project/.mise.toml"
log="$work_root/logs/env-required.log"
: > "$log"
(
  cd "$project"
  env "${base_env[@]}" "MISE_STUB_LOG=$log" ZED_DEV_MISE=required "$zed" dev \
    --no-install --nix never --python-venv never --shell "$shell_bin" \
    -c 'test "$MISE_STUB_ACTIVE" = 1; printf env-required-ok'
) | grep -q env-required-ok
[[ $(grep -c '^argv=' "$log") -eq 1 ]] || fail 'ZED_DEV_MISE did not select required mode'
pass 'flags-to-environment fallback'

project="$work_root/cli-never"
write_project "$project"
log="$work_root/logs/cli-never.log"
: > "$log"
(
  cd "$project"
  env "${base_env[@]}" "MISE_STUB_LOG=$log" ZED_DEV_MISE=required "$zed" dev \
    --no-install --nix never --mise never --python-venv never --shell "$shell_bin" \
    -c 'test -z "${MISE_STUB_ACTIVE+x}"; printf cli-never-ok'
) | grep -q cli-never-ok
[[ ! -s "$log" ]] || fail 'CLI --mise never did not override ZED_DEV_MISE'
pass 'CLI precedence over environment'

project="$work_root/auto-native"
write_project "$project"
log="$work_root/logs/auto-native.log"
: > "$log"
(
  cd "$project"
  env "${base_env[@]}" "MISE_STUB_LOG=$log" "$zed" dev \
    --no-install --nix never --mise auto --python-venv never --shell "$shell_bin" \
    -c 'test -z "${MISE_STUB_ACTIVE+x}"; printf auto-native-ok'
) | grep -q auto-native-ok
[[ ! -s "$log" ]] || fail 'auto mode probed mise without project configuration'
pass 'auto fallback without configuration'

project="$work_root/required-missing-config"
write_project "$project"
log="$work_root/logs/required-missing-config.log"
: > "$log"
(
  cd "$project"
  run_capture 1 "$work_root/required-missing-config.out" "$work_root/required-missing-config.err" \
    env "${base_env[@]}" "MISE_STUB_LOG=$log" "$zed" dev \
      --no-install --nix never --mise required --python-venv never --shell "$shell_bin" -c true
)
grep -q 'no project-local `mise.toml` or `.mise.toml`' "$work_root/required-missing-config.err"
[[ ! -s "$log" ]] || fail 'missing configuration should fail before probing mise'
pass 'required failure without configuration'

project="$work_root/required-missing-binary"
write_project "$project"
printf '%s\n' '[tools]' 'node = "22.4.0"' > "$project/mise.toml"
(
  cd "$project"
  run_capture 1 "$work_root/required-missing-binary.out" "$work_root/required-missing-binary.err" \
    env PATH= "ZED_PKG_HOME=$work_root/zed-home-missing-binary" "$zed" dev \
      --no-install --nix never --mise required --python-venv never --shell "$shell_bin" -c true
)
grep -q '`mise` is not available on PATH' "$work_root/required-missing-binary.err"
pass 'required failure without executable'

project="$work_root/frozen"
write_project "$project"
printf '%s\n' '[tools]' 'node = "22.4.0"' > "$project/mise.toml"
printf '%s\n' '[tools]' 'node = "999.0.0"' > "$project/.mise.toml"
printf '%s\n' 'nodejs latest' > "$project/.tool-versions"
log="$work_root/logs/frozen.log"
: > "$log"
(
  cd "$project"
  run_capture 1 "$work_root/frozen-missing.out" "$work_root/frozen-missing.err" \
    env "${base_env[@]}" "MISE_STUB_LOG=$log" "$zed" dev \
      --no-install --frozen --nix never --mise required --python-venv never --shell "$shell_bin" -c true
)
grep -q 'requires the adjacent mise lockfile' "$work_root/frozen-missing.err"
[[ ! -s "$log" ]] || fail 'frozen missing-lock failure unexpectedly entered mise'
printf '%s\n' '{}' > "$project/mise.lock"
: > "$log"
(
  cd "$project"
  env "${base_env[@]}" "MISE_STUB_LOG=$log" "$zed" dev \
    --no-install --frozen --nix never --mise required --python-venv never --shell "$shell_bin" \
    -c 'test "$MISE_STUB_ACTIVE" = 1; test "$MISE_LOCKED" = 1; printf frozen-ok'
) | grep -q frozen-ok
grep -qx "MISE_LOCKED=1" "$log"
grep -qx "MISE_OVERRIDE_CONFIG_FILENAMES=mise.toml" "$log"
grep -qx "MISE_OVERRIDE_TOOL_VERSIONS_FILENAMES=none" "$log"
grep -qx "MISE_CONFIG_DIR=$project/.zed/dev/mise/config" "$log"
grep -qx "MISE_SYSTEM_CONFIG_DIR=$project/.zed/dev/mise/system-config" "$log"
grep -qx "MISE_CEILING_PATHS=$work_root" "$log"
grep -qx "ZED_DEV_MISE_ACTIVE=1" "$log"
pass 'frozen lock enforcement, isolation, and config precedence'

project="$work_root/dot-config"
write_project "$project"
printf '%s\n' '[tools]' 'python = "3.12.4"' > "$project/.mise.toml"
printf '%s\n' '{}' > "$project/mise.lock"
log="$work_root/logs/dot-config.log"
: > "$log"
(
  cd "$project"
  env "${base_env[@]}" "MISE_STUB_LOG=$log" "$zed" dev \
    --no-install --frozen --nix never --mise required --python-venv never --shell "$shell_bin" -c true
)
grep -qx 'MISE_OVERRIDE_CONFIG_FILENAMES=.mise.toml' "$log"
pass 'adjacent lock support for .mise.toml'

outer="$work_root/parent-boundary"
project="$outer/checkout"
mkdir -p "$outer"
printf '%s\n' '[tools]' 'node = "22.4.0"' > "$outer/mise.toml"
write_project "$project"
log="$work_root/logs/parent-boundary.log"
: > "$log"
(
  cd "$project"
  run_capture 1 "$work_root/parent-boundary.out" "$work_root/parent-boundary.err" \
    env "${base_env[@]}" "MISE_STUB_LOG=$log" "$zed" dev \
      --no-install --nix never --mise required --python-venv never --shell "$shell_bin" -c true
)
grep -q 'no project-local `mise.toml` or `.mise.toml`' "$work_root/parent-boundary.err"
[[ ! -s "$log" ]] || fail 'parent configuration escaped the owning checkout'
pass 'owning-checkout configuration boundary'

project="$work_root/zed-recursion-guard"
write_project "$project"
printf '%s\n' '[tools]' 'node = "22.4.0"' > "$project/mise.toml"
log="$work_root/logs/zed-recursion-guard.log"
: > "$log"
(
  cd "$project"
  env "${base_env[@]}" "MISE_STUB_LOG=$log" ZED_DEV_MISE_ACTIVE=1 "$zed" dev \
    --no-install --nix never --mise required --python-venv never --shell "$shell_bin" \
    -c 'test -z "${MISE_STUB_ACTIVE+x}"; printf zed-guard-ok'
) | grep -q zed-guard-ok
[[ ! -s "$log" ]] || fail 'ZED recursion guard still invoked mise'
pass 'ZED re-entry recursion guard'

project="$work_root/mise-recursion-guard"
write_project "$project"
printf '%s\n' '[tools]' 'node = "22.4.0"' > "$project/mise.toml"
log="$work_root/logs/mise-recursion-guard.log"
: > "$log"
(
  cd "$project"
  env "${base_env[@]}" "MISE_STUB_LOG=$log" __MISE_DIFF=test-active "$zed" dev \
    --no-install --nix never --mise required --python-venv never --shell "$shell_bin" \
    -c 'test -z "${MISE_STUB_ACTIVE+x}"; printf mise-guard-ok'
) | grep -q mise-guard-ok
[[ ! -s "$log" ]] || fail 'mise-native recursion guard still invoked mise'
pass 'mise-native recursion guard'

project="$work_root/exit-status"
write_project "$project"
printf '%s\n' '[tools]' 'node = "22.4.0"' > "$project/mise.toml"
log="$work_root/logs/exit-status.log"
: > "$log"
(
  cd "$project"
  run_capture 37 "$work_root/exit-status.out" "$work_root/exit-status.err" \
    env "${base_env[@]}" "MISE_STUB_LOG=$log" "$zed" dev \
      --no-install --nix never --mise required --python-venv never --shell "$shell_bin" -c 'exit 37'
)
[[ $(grep -c '^argv=' "$log") -eq 1 ]] || fail 'exit-status case did not enter mise exactly once'
pass 'exact child exit status'

printf '{"schema":"zed-pkg.mise-runtime-certification.v1","passed":%d,"platform":"%s"}\n' \
  "$passed" "$(uname -s)"
