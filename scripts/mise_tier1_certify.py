#!/usr/bin/env python3
"""Certify zed-cli's tier-1 mise project adapter.

The suite intentionally treats imported hooks, plugins, tasks, and templates as
untrusted data. Adapter operations must preserve those fields without running
project code or fetching plugin sources.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence


@dataclass
class CaseResult:
    case: str
    status: str
    duration_ms: int
    detail: str = ""


class CertificationFailure(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(
    command: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path,
    expect_success: bool = True,
) -> subprocess.CompletedProcess[str]:
    rendered = [os.fspath(part) for part in command]
    result = subprocess.run(
        rendered,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env={**os.environ, "NO_COLOR": "1", "CLICOLOR": "0"},
    )
    succeeded = result.returncode == 0
    if succeeded != expect_success:
        expectation = "success" if expect_success else "failure"
        raise CertificationFailure(
            f"expected {expectation} from {rendered!r}, got exit "
            f"{result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def copy_fixture(fixtures: Path, relative: str) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    temporary = tempfile.TemporaryDirectory(prefix="zed-mise-tier1-")
    root = Path(temporary.name) / "project"
    shutil.copytree(fixtures / relative, root)
    return temporary, root


def assert_no_adapter_execution(root: Path) -> None:
    forbidden = [
        "ADAPTER_EXECUTED_HOOK",
        "ADAPTER_EXECUTED_TASK",
        "ADAPTER_EXECUTED_PLUGIN",
    ]
    found = [name for name in forbidden if (root / name).exists()]
    if found:
        raise CertificationFailure(
            "mise adapter executed imported project code: " + ", ".join(found)
        )


def assert_adapter_outputs(root: Path) -> None:
    expected = [root / "zed-dev.toml", root / ".zed" / "mise.lock"]
    missing = [path.relative_to(root).as_posix() for path in expected if not path.is_file()]
    if missing:
        raise CertificationFailure("missing adapter output(s): " + ", ".join(missing))


def import_verify_export(zed: Path, root: Path) -> None:
    source = root / ".mise.toml"
    if not source.is_file():
        source = root / "mise.toml"
    before = sha256(source)

    run([zed, "mise", "import"], cwd=root)
    assert_adapter_outputs(root)
    assert_no_adapter_execution(root)
    run([zed, "mise", "verify"], cwd=root)
    assert_no_adapter_execution(root)

    # The adapter owns the exact output convention. Calling export without a
    # destination exercises its documented project default while the source
    # hash check proves losslessness regardless of whether it rewrites or
    # validates the existing file.
    run([zed, "mise", "export"], cwd=root)
    assert_no_adapter_execution(root)
    after = sha256(source)
    if before != after:
        raise CertificationFailure(
            f"lossless export changed {source.name}: {before} -> {after}"
        )
    run([zed, "mise", "verify"], cwd=root)


def supported_basic(zed: Path, fixtures: Path) -> None:
    temporary, root = copy_fixture(fixtures, "supported/basic")
    try:
        import_verify_export(zed, root)
        plan = (root / "zed-dev.toml").read_text(encoding="utf-8")
        for marker in ["node", "22.4.0", "python", "3.12.4", "mise.vars"]:
            if marker not in plan:
                raise CertificationFailure(f"normalized plan omitted {marker!r}")
    finally:
        temporary.cleanup()


def supported_multi_version(zed: Path, fixtures: Path) -> None:
    temporary, root = copy_fixture(fixtures, "supported/multi-version")
    try:
        import_verify_export(zed, root)
        plan = (root / "zed-dev.toml").read_text(encoding="utf-8")
        first = plan.find("22.4.0")
        second = plan.find("20.15.1")
        if first < 0 or second < 0 or first >= second:
            raise CertificationFailure(
                "normalized multi-version state did not preserve declared order"
            )
    finally:
        temporary.cleanup()


def supported_typed_state(zed: Path, fixtures: Path) -> None:
    temporary, root = copy_fixture(fixtures, "supported/typed-state")
    try:
        import_verify_export(zed, root)
        plan = (root / "zed-dev.toml").read_text(encoding="utf-8")
        for marker in [
            "STRING_VALUE",
            "INTEGER_VALUE",
            "FLOAT_VALUE",
            "BOOLEAN_VALUE",
            "ARRAY_VALUE",
            "TABLE_VALUE",
            "mise.vars",
            "mise.tasks",
        ]:
            if marker not in plan:
                raise CertificationFailure(f"typed adapter state omitted {marker!r}")
        assert_no_adapter_execution(root)
    finally:
        temporary.cleanup()


def preserved_inert_sections(zed: Path, fixtures: Path) -> None:
    temporary, root = copy_fixture(fixtures, "preserved/inert-hooks-plugins")
    try:
        source_before = (root / ".mise.toml").read_bytes()
        import_verify_export(zed, root)
        source_after = (root / ".mise.toml").read_bytes()
        if source_before != source_after:
            raise CertificationFailure("unknown mise sections did not round-trip exactly")
        plan = (root / "zed-dev.toml").read_text(encoding="utf-8")
        for marker in ["mise.unknown_top_level", "postinstall", "plugins"]:
            if marker not in plan:
                raise CertificationFailure(
                    f"preserved-but-inert section omitted marker {marker!r}"
                )
        assert_no_adapter_execution(root)
    finally:
        temporary.cleanup()


def rejects_conflicting_config(zed: Path, fixtures: Path) -> None:
    temporary, root = copy_fixture(fixtures, "negative/conflicting-config")
    try:
        result = run([zed, "mise", "import"], cwd=root, expect_success=False)
        diagnostic = (result.stdout + "\n" + result.stderr).lower()
        if ".mise.toml" not in diagnostic or "mise.toml" not in diagnostic:
            raise CertificationFailure(
                "conflicting-config diagnostic did not identify both project files"
            )
        if (root / "zed-dev.toml").exists() or (root / ".zed" / "mise.lock").exists():
            raise CertificationFailure("conflicting configs produced partial adapter state")
    finally:
        temporary.cleanup()


def rejects_tool_versions_only(zed: Path, fixtures: Path) -> None:
    temporary, root = copy_fixture(fixtures, "negative/tool-versions-only")
    try:
        run([zed, "mise", "import"], cwd=root, expect_success=False)
        if (root / "zed-dev.toml").exists() or (root / ".zed" / "mise.lock").exists():
            raise CertificationFailure(
                "unsupported .tool-versions input silently produced an empty plan"
            )
    finally:
        temporary.cleanup()


def rejects_source_tamper(zed: Path, fixtures: Path) -> None:
    temporary, root = copy_fixture(fixtures, "supported/basic")
    try:
        run([zed, "mise", "import"], cwd=root)
        source = root / ".mise.toml"
        source.write_text(
            source.read_text(encoding="utf-8") + "\n# certification tamper\n",
            encoding="utf-8",
        )
        run([zed, "mise", "verify"], cwd=root, expect_success=False)
    finally:
        temporary.cleanup()


def rejects_plan_tamper(zed: Path, fixtures: Path) -> None:
    temporary, root = copy_fixture(fixtures, "supported/basic")
    try:
        run([zed, "mise", "import"], cwd=root)
        plan = root / "zed-dev.toml"
        plan.write_text(
            plan.read_text(encoding="utf-8")
            + "\n[certification_tamper]\nenabled = true\n",
            encoding="utf-8",
        )
        run([zed, "mise", "verify"], cwd=root, expect_success=False)
    finally:
        temporary.cleanup()


def certification_cases() -> Iterable[tuple[str, object]]:
    return [
        ("project-config-basic", supported_basic),
        ("multi-version-order", supported_multi_version),
        ("typed-env-vars-tasks", supported_typed_state),
        ("unknown-hooks-plugins", preserved_inert_sections),
        ("conflicting-config-files", rejects_conflicting_config),
        ("tool-versions-only", rejects_tool_versions_only),
        ("source-tamper", rejects_source_tamper),
        ("plan-tamper", rejects_plan_tamper),
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", required=True, type=Path)
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    zed = args.zed.resolve()
    fixtures = args.fixtures.resolve()
    report_path = args.report.resolve()

    if not zed.is_file():
        raise SystemExit(f"zed binary does not exist: {zed}")
    if not fixtures.is_dir():
        raise SystemExit(f"fixture directory does not exist: {fixtures}")

    version = run([zed, "--version"], cwd=fixtures).stdout.strip()
    results: list[CaseResult] = []
    failed = False

    for name, case in certification_cases():
        started = time.monotonic()
        try:
            case(zed, fixtures)  # type: ignore[misc,operator]
            results.append(
                CaseResult(
                    case=name,
                    status="passed",
                    duration_ms=round((time.monotonic() - started) * 1000),
                )
            )
            print(f"PASS {name}")
        except Exception as error:  # keep the complete report after one failure
            failed = True
            results.append(
                CaseResult(
                    case=name,
                    status="failed",
                    duration_ms=round((time.monotonic() - started) * 1000),
                    detail=str(error),
                )
            )
            print(f"FAIL {name}: {error}", file=sys.stderr)

    report = {
        "schema_version": 1,
        "compatibility_tier": 1,
        "suite": "mise-lossless-project-adapter",
        "status": "failed" if failed else "passed",
        "zed_version": version,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "results": [asdict(result) for result in results],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"wrote {report_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
