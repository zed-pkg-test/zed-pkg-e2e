#!/usr/bin/env python3
"""Credential-free regression tests for the v0 R2 synchronization boundary."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILDER = ROOT / "build_static_registry.py"
SYNC = ROOT / "sync_to_r2.sh"


class R2SyncTests(unittest.TestCase):
    def fixture_tree(self, root: Path) -> Path:
        tree = root / "tree"
        environment = os.environ.copy()
        environment["SOURCE_DATE_EPOCH"] = "0"
        result = subprocess.run(
            [
                sys.executable,
                str(BUILDER),
                "--fixtures",
                str(ROOT / "fixtures"),
                "--out",
                str(tree),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return tree

    def fake_aws(self, root: Path) -> tuple[Path, Path]:
        bin_dir = root / "bin"
        bin_dir.mkdir(exist_ok=True)
        log = root / "aws-calls.jsonl"
        program = bin_dir / "aws"
        program.write_text(
            """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
log = Path(os.environ["FAKE_AWS_LOG"])
with log.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\\n")

if args[:2] == ["s3api", "head-object"]:
    raise SystemExit(0 if os.environ.get("FAKE_CHECKPOINT_EXISTS") == "true" else 1)
if args[:2] == ["s3api", "put-object"]:
    body = Path(args[args.index("--body") + 1])
    if not body.is_file():
        raise SystemExit(f"put-object body is not a file: {body}")
    print('"fake-etag"')
    raise SystemExit(0)
raise SystemExit(f"unexpected fake aws invocation: {args}")
""",
            encoding="utf-8",
        )
        program.chmod(program.stat().st_mode | stat.S_IXUSR)
        return bin_dir, log

    def run_sync(
        self,
        root: Path,
        tree: Path,
        *,
        checkpoint_exists: bool,
        allow_replace: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
        bin_dir, log = self.fake_aws(root)
        if log.exists():
            log.unlink()
        environment = os.environ.copy()
        environment.update(
            {
                "PATH": f"{bin_dir}{os.pathsep}{environment['PATH']}",
                "FAKE_AWS_LOG": str(log),
                "FAKE_CHECKPOINT_EXISTS": "true" if checkpoint_exists else "false",
                "ZPKG_R2_ACCESS_KEY_ID": "test-access-key",
                "ZPKG_R2_SECRET_ACCESS_KEY": "test-secret-key",
                "ZPKG_R2_ENDPOINT": "https://example.invalid",
            }
        )
        if allow_replace:
            environment["ZPKG_ALLOW_V0_REPLACE"] = "true"
        else:
            environment.pop("ZPKG_ALLOW_V0_REPLACE", None)

        result = subprocess.run(
            ["bash", str(SYNC), str(tree), "test-bucket"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            check=False,
        )
        calls = []
        if log.exists():
            calls = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertNotIn("test-secret-key", json.dumps(calls))
        return result, calls

    @staticmethod
    def put_calls(calls: list[list[str]]) -> list[list[str]]:
        return [call for call in calls if call[:2] == ["s3api", "put-object"]]

    @staticmethod
    def head_calls(calls: list[list[str]]) -> list[list[str]]:
        return [call for call in calls if call[:2] == ["s3api", "head-object"]]

    @staticmethod
    def option(call: list[str], name: str) -> str:
        return call[call.index(name) + 1]

    def assert_upload_contract(self, calls: list[list[str]]) -> None:
        puts = self.put_calls(calls)
        expected_keys = [
            ".well-known/zpkg-registry.json",
            "index/zpkg-e2e/dep-user",
            "index/zpkg-e2e/hello-zed",
            "pkgs/zpkg-e2e/dep-user/0.1.0.tar.zst",
            "pkgs/zpkg-e2e/hello-zed/1.0.0.tar.zst",
            "pkgs/zpkg-e2e/hello-zed/1.1.0.tar.zst",
            "pkgs/zpkg-e2e/hello-zed/1.2.0.tar.zst",
            "checkpoint.json",
        ]
        self.assertEqual(
            [self.option(call, "--key") for call in puts],
            expected_keys,
        )
        self.assertEqual(len(self.head_calls(calls)), 1)
        self.assertEqual(self.option(puts[-1], "--key"), "checkpoint.json")
        self.assertEqual(self.option(puts[-1], "--cache-control"), "no-cache")

        by_key = {self.option(call, "--key"): call for call in puts}
        self.assertEqual(
            self.option(
                by_key["pkgs/zpkg-e2e/hello-zed/1.0.0.tar.zst"],
                "--content-type",
            ),
            "application/zstd",
        )
        self.assertEqual(
            self.option(
                by_key["pkgs/zpkg-e2e/hello-zed/1.0.0.tar.zst"],
                "--cache-control",
            ),
            "public, max-age=31536000, immutable",
        )
        self.assertEqual(
            self.option(by_key["index/zpkg-e2e/hello-zed"], "--content-type"),
            "application/x-ndjson",
        )
        self.assertEqual(
            self.option(by_key["index/zpkg-e2e/hello-zed"], "--cache-control"),
            "no-cache",
        )
        for call in puts:
            self.assertEqual(self.option(call, "--bucket"), "test-bucket")
            self.assertTrue(Path(self.option(call, "--body")).is_file())

    def test_fresh_publication_writes_checkpoint_last_with_reviewed_cache_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tree = self.fixture_tree(root)
            result, calls = self.run_sync(root, tree, checkpoint_exists=False)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("uploaded 8 objects", result.stdout)
            self.assertIn("checkpoint last", result.stdout)
            self.assert_upload_contract(calls)

    def test_existing_v0_checkpoint_fails_closed_without_explicit_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tree = self.fixture_tree(root)
            result, calls = self.run_sync(root, tree, checkpoint_exists=True)

            self.assertEqual(result.returncode, 3)
            self.assertIn("refusing to replace an existing v0 checkpoint", result.stderr)
            self.assertEqual(self.put_calls(calls), [])
            self.assertEqual(len(self.head_calls(calls)), 1)

    def test_disposable_override_still_writes_checkpoint_last(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tree = self.fixture_tree(root)
            result, calls = self.run_sync(
                root,
                tree,
                checkpoint_exists=True,
                allow_replace=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_upload_contract(calls)

    def test_missing_checkpoint_fails_before_aws(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tree = self.fixture_tree(root)
            (tree / "checkpoint.json").unlink()

            result, calls = self.run_sync(root, tree, checkpoint_exists=False)

            self.assertEqual(result.returncode, 2)
            self.assertIn("missing", result.stderr)
            self.assertEqual(calls, [])

    def test_tampered_checkpointed_object_fails_before_aws(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tree = self.fixture_tree(root)
            artifact = tree / "pkgs/zpkg-e2e/hello-zed/1.0.0.tar.zst"
            artifact.write_bytes(artifact.read_bytes() + b"tamper")

            result, calls = self.run_sync(root, tree, checkpoint_exists=False)

            self.assertEqual(result.returncode, 2)
            self.assertIn("fails local conformance", result.stderr)
            self.assertEqual(calls, [])

    def test_uncheckpointed_object_fails_before_aws(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tree = self.fixture_tree(root)
            extra = tree / "index/zpkg-e2e/uncheckpointed"
            extra.write_text("{}\n", encoding="utf-8")

            result, calls = self.run_sync(root, tree, checkpoint_exists=False)

            self.assertEqual(result.returncode, 2)
            self.assertIn("fails local conformance", result.stderr)
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
