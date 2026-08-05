#!/usr/bin/env python3
"""Independent black-box certification for `zed task` and `zed-task` parity."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import textwrap
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Sequence


@dataclass
class CaseResult:
    name: str
    status: str
    duration_ms: int
    detail: str = ""


class CertificationFailure(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", required=True, type=Path)
    parser.add_argument("--zed-task", required=True, type=Path)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--candidate", required=True)
    return parser.parse_args()


def runtime_env(work: Path) -> dict[str, str]:
    keep = [
        "PATH",
        "SystemRoot",
        "SYSTEMROOT",
        "ComSpec",
        "COMSPEC",
        "PATHEXT",
        "WINDIR",
        "TEMP",
        "TMP",
        "LANG",
        "LC_ALL",
    ]
    env = {name: os.environ[name] for name in keep if name in os.environ}
    home = work / "home"
    home.mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "ZED_PKG_HOME": str(home / ".zed-pkg"),
            "ZED_PKG_UPDATE_CHECK": "false",
            "NO_COLOR": "1",
            "CLICOLOR": "0",
            "RUST_BACKTRACE": "0",
        }
    )
    return env


def run(
    binary: Path,
    args: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    expect_success: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [str(binary), *args],
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    succeeded = result.returncode == 0
    if succeeded != expect_success:
        expectation = "success" if expect_success else "failure"
        raise CertificationFailure(
            f"expected {expectation} from {binary.name} {list(args)!r}; "
            f"exit={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def main_args(args: Sequence[str]) -> list[str]:
    return ["task", *args]


def write_plan(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "input.txt").write_text("one\n", encoding="utf-8")
    (root / "zed-env.toml").write_text(
        textwrap.dedent(
            r'''
            schema = 2

            [env]
            MESSAGE = "hello"

            [tasks.prepare]
            description = "prepare inputs"
            run = ["printf 'prepare\\n' > order.txt"]
            run_windows = ["echo prepare>order.txt"]

            [tasks.build]
            description = "build project"
            aliases = ["b"]
            depends = ["prepare"]
            run = ["printf '%s\\n' \"$MESSAGE\" >> order.txt"]
            run_windows = ["echo %MESSAGE%>>order.txt"]

            [tasks.args]
            run = ["printf '%s' \"$ZED_TASK_ARG_0\" > arg.txt"]
            run_windows = ["echo %ZED_TASK_ARG_0%>arg.txt"]

            [tasks.copy]
            cache = true
            sources = ["input.txt"]
            outputs = ["output.txt"]
            run = ["cat input.txt > output.txt"]
            run_windows = ["type input.txt > output.txt"]

            [tasks.release]
            confirm = "publish release?"
            run = ["printf release > release.txt"]
            run_windows = ["echo release>release.txt"]
            '''
        ).lstrip(),
        encoding="utf-8",
    )


def assert_parity(
    zed: Path,
    staged: Path,
    root: Path,
    env: dict[str, str],
    args: Sequence[str],
) -> None:
    canonical = run(zed, main_args(args), cwd=root, env=env)
    compatibility = run(staged, args, cwd=root, env=env)
    if canonical.stdout != compatibility.stdout:
        raise CertificationFailure(
            f"stdout differs for {list(args)!r}\ncanonical:\n{canonical.stdout}\n"
            f"staged:\n{compatibility.stdout}"
        )
    if canonical.stderr != compatibility.stderr:
        raise CertificationFailure(
            f"stderr differs for {list(args)!r}\ncanonical:\n{canonical.stderr}\n"
            f"staged:\n{compatibility.stderr}"
        )


def case_json_parity(zed: Path, staged: Path, root: Path, env: dict[str, str]) -> None:
    for args in [
        ["--json", "list"],
        ["--json", "info", "b"],
        ["--json", "graph", "b"],
        ["--json", "run", "b", "--dry-run"],
    ]:
        assert_parity(zed, staged, root, env, args)

    listed = run(zed, main_args(["--json", "list"]), cwd=root, env=env)
    tasks = json.loads(listed.stdout)
    names = [task["name"] for task in tasks]
    if names != sorted(names) or "build" not in names:
        raise CertificationFailure(f"unexpected deterministic task list: {names!r}")


def case_execution(zed: Path, _staged: Path, root: Path, env: dict[str, str]) -> None:
    output = run(zed, main_args(["run", "b", "--jobs", "2"]), cwd=root, env=env)
    if "[done] build" not in output.stderr:
        raise CertificationFailure(f"missing completion event: {output.stderr}")
    lines = [line.strip() for line in (root / "order.txt").read_text().splitlines()]
    if lines != ["prepare", "hello"]:
        raise CertificationFailure(f"dependency/environment order drift: {lines!r}")


def case_arguments(zed: Path, staged: Path, root: Path, env: dict[str, str]) -> None:
    for binary, args in [
        (zed, main_args(["run", "args", "--", "hello world"])),
        (staged, ["run", "args", "--", "hello world"]),
    ]:
        (root / "arg.txt").unlink(missing_ok=True)
        run(binary, args, cwd=root, env=env)
        value = (root / "arg.txt").read_text(encoding="utf-8").strip()
        if value != "hello world":
            raise CertificationFailure(f"argument transport drift for {binary.name}: {value!r}")


def case_content_cache(zed: Path, _staged: Path, root: Path, env: dict[str, str]) -> None:
    first = run(zed, main_args(["run", "copy"]), cwd=root, env=env)
    if "[done] copy" not in first.stderr:
        raise CertificationFailure("first cacheable execution did not run")
    if (root / "output.txt").read_text(encoding="utf-8") != "one\n":
        raise CertificationFailure("first cached output content is wrong")

    second = run(zed, main_args(["run", "copy"]), cwd=root, env=env)
    if "incremental cache hit" not in second.stderr:
        raise CertificationFailure(f"second execution did not hit cache: {second.stderr}")

    (root / "input.txt").write_text("two\n", encoding="utf-8")
    third = run(zed, main_args(["run", "copy"]), cwd=root, env=env)
    if "[done] copy" not in third.stderr:
        raise CertificationFailure("source drift did not rerun task")
    if (root / "output.txt").read_text(encoding="utf-8") != "two\n":
        raise CertificationFailure("source drift output content is wrong")

    records = sorted((root / ".zed" / "task-cache" / "v1").glob("*.json"))
    if not records:
        raise CertificationFailure("task cache did not persist a content-identity record")
    for record in records:
        data = json.loads(record.read_text(encoding="utf-8"))
        if set(data) != {"schema", "task", "input_sha256", "output_sha256", "outputs"}:
            raise CertificationFailure(f"unexpected cache record fields: {sorted(data)}")


def case_fail_closed(zed: Path, staged: Path, root: Path, env: dict[str, str]) -> None:
    for binary, prefix in [(zed, ["task"]), (staged, [])]:
        zero = run(
            binary,
            [*prefix, "run", "build", "--jobs", "0"],
            cwd=root,
            env=env,
            expect_success=False,
        )
        if "at least one" not in zero.stderr:
            raise CertificationFailure(f"zero-jobs diagnostic drift for {binary.name}: {zero.stderr}")

        live_json = run(
            binary,
            [*prefix, "--json", "run", "build"],
            cwd=root,
            env=env,
            expect_success=False,
        )
        if "requires `--dry-run`" not in live_json.stderr:
            raise CertificationFailure(
                f"live JSON boundary drift for {binary.name}: {live_json.stderr}"
            )

        confirmation = run(
            binary,
            [*prefix, "run", "release"],
            cwd=root,
            env=env,
            expect_success=False,
        )
        if "requires confirmation" not in confirmation.stderr:
            raise CertificationFailure(
                f"confirmation boundary drift for {binary.name}: {confirmation.stderr}"
            )

    if (root / "release.txt").exists():
        raise CertificationFailure("rejected confirmation mutated the project")
    run(zed, main_args(["run", "release", "--yes"]), cwd=root, env=env)
    if not (root / "release.txt").is_file():
        raise CertificationFailure("approved confirmation did not execute")


def main() -> int:
    args = parse_args()
    candidate = args.candidate.lower()
    if len(candidate) != 40 or any(character not in "0123456789abcdef" for character in candidate):
        raise SystemExit("--candidate must be a full 40-character hexadecimal commit")

    zed = args.zed.resolve()
    staged = args.zed_task.resolve()
    if not zed.is_file() or not staged.is_file():
        raise SystemExit(f"missing binaries: zed={zed}, zed-task={staged}")

    work = args.work.resolve()
    if work.exists():
        shutil.rmtree(work)
    project = work / "project"
    write_plan(project)
    env = runtime_env(work)

    versions = {
        "zed": run(zed, ["--version"], cwd=project, env=env).stdout.strip(),
        "zed_task": run(staged, ["--version"], cwd=project, env=env).stdout.strip(),
    }

    cases: list[tuple[str, Callable[[Path, Path, Path, dict[str, str]], None]]] = [
        ("json-parity", case_json_parity),
        ("dependency-execution", case_execution),
        ("argument-isolation", case_arguments),
        ("content-cache", case_content_cache),
        ("fail-closed-boundaries", case_fail_closed),
    ]
    results: list[CaseResult] = []
    failed = False
    for name, case in cases:
        started = time.monotonic()
        try:
            case(zed, staged, project, env)
            results.append(
                CaseResult(
                    name=name,
                    status="passed",
                    duration_ms=round((time.monotonic() - started) * 1000),
                )
            )
            print(f"PASS {name}")
        except Exception as error:  # produce complete evidence after a failure
            failed = True
            results.append(
                CaseResult(
                    name=name,
                    status="failed",
                    duration_ms=round((time.monotonic() - started) * 1000),
                    detail=str(error),
                )
            )
            print(f"FAIL {name}: {error}", file=sys.stderr)

    evidence = {
        "schema": "zed-pkg-test/canonical-task-canary/v1",
        "candidate": candidate,
        "status": "failed" if failed else "passed",
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "versions": versions,
        "claims": {
            "manager_execution": False,
            "network_required_at_runtime": False,
            "canonical_and_staged_shared_semantics": True,
        },
        "results": [asdict(result) for result in results],
    }
    evidence_path = args.evidence.resolve()
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(f"wrote {evidence_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
