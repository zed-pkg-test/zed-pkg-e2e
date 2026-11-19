#!/usr/bin/env bash
set -euo pipefail
repo="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$repo"
expected="${ATTO_EXPECTED_HEAD_SHA:-}"
if [[ ! "$expected" =~ ^[0-9a-f]{40}$ ]] || [[ "$(git rev-parse HEAD)" != "$expected" ]]; then
  echo 'M5_NOT_EXECUTED: exact Git source commit mismatch' >&2; exit 2
fi
git diff --quiet HEAD -- || { echo 'M5_NOT_EXECUTED: tracked source modified' >&2; exit 2; }
test -z "$(git ls-files --others --exclude-standard)" || {
  echo 'M5_NOT_EXECUTED: unexpected untracked source' >&2; exit 2
}
test -x "${JAVA_HOME:-}/bin/native-image" && test -x "$JAVA_HOME/bin/javac" || {
  echo 'M5_NOT_EXECUTED: real GraalVM Native Image JDK25 unavailable' >&2; exit 2
}
python3 -m unittest discover -s tests -p test_m5_checked_actor_ir.py -v
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/classes"
"$JAVA_HOME/bin/javac" -d "$work/classes" \
  src/m5/M5BoundedActorMailbox.java tests/m5/M5MailboxNativeProbe.java
(
  cd "$work"
  "$JAVA_HOME/bin/native-image" --no-fallback --gc=epsilon -H:+SharedArenaSupport \
    -H:Name=m5-mailbox-guest -cp "$work/classes" M5MailboxNativeProbe
)
test -x "$work/m5-mailbox-guest" && test -s "$work/m5-mailbox-guest" || {
  echo 'M5_NOT_EXECUTED: no native executable built' >&2; exit 1
}
file "$work/m5-mailbox-guest" | grep -Eq 'ELF|Mach-O' || {
  echo 'M5_NOT_EXECUTED: output is not native machine code' >&2; exit 1
}
echo "M5_MAILBOX_NATIVE_ARTIFACT_SHA256=$(sha256sum "$work/m5-mailbox-guest" | cut -d ' ' -f 1)"
if ! timeout 120 "$work/m5-mailbox-guest" > "$work/output" 2> "$work/stderr"; then
  echo 'M5_WORKER_NATIVE_EXECUTION_FAILED' >&2
  cat "$work/output" >&2
  cat "$work/stderr" >&2
  exit 1
fi
cat "$work/output"
test ! -s "$work/stderr" || {
  echo 'M5_WORKER_UNEXPECTED_STDERR' >&2
  cat "$work/stderr" >&2
  exit 1
}
for required in \
  M5_MAILBOX_CAPACITY_REFUSAL=PASS \
  M5_MAILBOX_POST_CLOSE_SNAPSHOT=PASS \
  M5_MAILBOX_CONCURRENT_ACCEPTED=100000 \
  M5_MAILBOX_NEIGHBOR_ACCEPTED=1000 \
  M5_MAILBOX_ACTOR_STATE=100000 \
  M5_MAILBOX_NEIGHBOR_STATE=2050 \
  M5_MAILBOX_INDEPENDENT_WORKER_THREADS=PASS \
  M5_MAILBOX_SUPERVISOR_CONTROL_NOTICE=1 \
  M5_MAILBOX_FAILED_TURN_ACCOUNTED=1 \
  M5_MAILBOX_ABANDONED_MAIL_ACCOUNTED=1 \
  M5_MAILBOX_RUNTIME_GC_DELTA=0 \
  M5_GRAALVM_WORKER_MAILBOX_NATIVE_AOT=PASS; do
  grep -Fx "$required" "$work/output" >/dev/null || {
    echo "M5_WORKER_MISSING_EXECUTED_GATE=$required" >&2; exit 1;
  }
done
echo 'M5_GRAALVM_EXPERIMENTAL_BOUNDED_ACTOR_WORKERS=PASSED'
echo 'EVIDENCE_SCOPE=EXPERIMENTAL_NATIVE_JAVA_HOST_ACTORS_NOT_ACTUAL_COMPILED_ATTO_GUEST'
