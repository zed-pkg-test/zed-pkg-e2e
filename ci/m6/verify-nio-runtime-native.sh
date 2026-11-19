#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
expected="${ATTO_EXPECTED_HEAD_SHA:-}"
if [[ ! "$expected" =~ ^[0-9a-f]{40}$ ]] || [[ "$(git rev-parse HEAD)" != "$expected" ]]; then
  echo 'M6_NOT_EXECUTED: exact Git source SHA mismatch' >&2; exit 2
fi
git diff --quiet HEAD -- || { echo 'M6_NOT_EXECUTED: tracked files modified' >&2; exit 2; }
test -z "$(git ls-files --others --exclude-standard)" || {
  echo 'M6_NOT_EXECUTED: unexpected untracked files' >&2; exit 2;
}
test -x "${JAVA_HOME:-}/bin/javac" && test -x "$JAVA_HOME/bin/native-image" || {
  echo 'M6_NOT_EXECUTED: GraalVM JDK25 Native Image unavailable' >&2; exit 2;
}
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/classes"
"$JAVA_HOME/bin/javac" -d "$work/classes" src/m6/AttoM6Runtime.java tests/m6/M6NioRuntimeProbe.java \
  tests/m6/M6SelectorLifecycleAuditProbe.java \
  tests/m6/M6ControlBudgetStressProbe.java
"$JAVA_HOME/bin/java" -version
if ! timeout 90 "$JAVA_HOME/bin/java" -cp "$work/classes" M6NioRuntimeProbe > "$work/jvm" 2> "$work/jvm.err"; then
  echo 'M6_JVM_EXECUTION_FAILED' >&2; cat "$work/jvm.err" >&2; exit 1
fi
(
  cd "$work"
  "$JAVA_HOME/bin/native-image" --no-fallback --gc=epsilon -H:Name=m6-nio-host \
    -cp "$work/classes" M6NioRuntimeProbe
)
test -x "$work/m6-nio-host"
file "$work/m6-nio-host" | grep -Eq 'ELF|Mach-O' || { echo 'M6_NO_REAL_NATIVE_BINARY' >&2; exit 1; }
echo "M6_NATIVE_SHA256=$(sha256sum "$work/m6-nio-host" | cut -d ' ' -f 1)"
if ! timeout 90 "$work/m6-nio-host" > "$work/native" 2> "$work/native.err"; then
  echo 'M6_NATIVE_EXECUTION_FAILED' >&2; cat "$work/native.err" >&2; exit 1
fi
cat "$work/native"
test ! -s "$work/native.err" || { cat "$work/native.err" >&2; exit 1; }
for marker in \
  M6_NIO_READ_ONE_SHOT_BACKPRESSURE=PASS \
  M6_NIO_LOOPBACK_SOCKET_EOF=PASS \
  M6_STRICT_CONTROL_BEFORE_DATA=PASS \
  M6_SUPERVISOR_CONTROL_PANIC_FENCING=PASS \
  M6_POOLED_SERIAL_CARRIER_MPSC=1000 \
  M6_POOLED_NEIGHBOR_PROGRESS=100 \
  M6_SERIAL_MAX_CONCURRENT_RECEIVES=1 \
  M6_GLOBAL_CLOSE_SPAWN_FENCE=PASS \
  M6_AUDIT_BUDGETED_SELECTED_KEYS_96=PASS \
  M6_AUDIT_EXTERNAL_CHANNEL_CLOSE_SURVIVES=PASS \
  M6_AUDIT_POST_CLOSE_SELECTOR_FAST_FAIL=PASS \
  M6_BUDGET_WATCHDOG_LIVE_NOTICE=PASS \
  M6_CONTROL_DUPLICATE_STOP_2000_COALESCED=PASS \
  M6_BUDGET_NEIGHBOR_PROGRESS=PASS \
  M6_CONTROL_STOP_TO_PANIC_ESCALATION=PASS \
  M6_STRESS_MPSC_ACCEPTED_SETTLED=100000 \
  M6_STRESS_NEIGHBOR_ACCEPTED_SETTLED=2000 \
  M6_STRESS_SERIAL_MAX_RECEIVES=1 \
  M6_STRESS_READINESS_STOP_RACES=48 \
  M6_NIO_SCHEDULER_SUPERVISOR_NATIVE_HOST=PASS \
  M6_SCOPE=HOST_SUBSET_NOT_ATTO_GUEST_OR_HARD_REAL_TIME_PROOF; do
  grep -Fx "$marker" "$work/native" >/dev/null || { echo "M6_MISSING_NATIVE=$marker" >&2; exit 1; }
  grep -Fx "$marker" "$work/jvm" >/dev/null || { echo "M6_MISSING_JVM=$marker" >&2; exit 1; }
done
cmp "$work/jvm" "$work/native" || { echo 'M6_JVM_NATIVE_MARKERS_DIVERGE' >&2; exit 1; }
echo 'M6_NIO_CARRIER_CONTROL_GRAALVM_NATIVE=PASS'
echo 'M6_EVIDENCE_SCOPE=HOST_JAVA_NIO_NATIVE_IMAGE_NOT_COMPILED_ATTO_OR_PRODUCTION_HARD_RT'
