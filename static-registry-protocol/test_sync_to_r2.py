#!/usr/bin/env python3
"""Credential-free regression tests for the v0 R2 synchronization boundary."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SYNC = ROOT / "sync_to_r2.sh"


class R2SyncTests(unittest.TestCase):
    def fixture_tree(self, root: Path) -> Path:
        tree = root / "tree"
        files = {
            ".well-known/zpkg-registry.json": b"{}\n",
            "checkpoint.json": b"{}\n",
            "index/acme/demo": b"{}\n",
            "pkgs/acme/demo/1.0.0.tar.zst": b"fixture archive",
        }
        for relative, data in files.items():
            path = tree / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return tree

    def fake_aws(self, root: Path, *, checkpoint_exists: bool) -> tuple[Path, Path]:
        bin_dir = root / "bin"
        bin_dir.mkdir()
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
        bin_dir, log = self.fake_aws(root, checkpoint_exists=checkpoint_exists)
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
        return result, calls

    @staticmethod
    def put_calls(calls: list[list[str]]) -> list[list[str]]:
        return [call for call in calls if call[:2] == ["s3api", "put-object"]]

    @staticmethod
    def option(call: list[str], name: str) -> str:
        return call[call.index(name) + 1]

    def test_fresh_publication_writes_checkpoint_last_with_reviewed_cache_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tree = self.fixture_tree(root)
            result, calls = self.run_sync(root, tree, checkpoint_exists=False)

            self.assertEqual(result.returncode, 0, result.stderr)
            puts = self.put_calls(calls)
            self.assertEqual(self.option(puts[-1], "--key"), "checkpoint.json")
            self.assertEqual(self.option(puts[-1], "--cache-control"), "no-cache")

            by_key = {self.option(call, "--key"): call for call in puts}
            self.assertEqual(
                self.option(
                    by_key["pkgs/acme/demo/1.0.0.tar.zst"], "--cache-control"
                ),
                "public, max-age=31536000, immutable",
            )
            self.assertEqual(
                self.option(by_key["index/acme/demo"], "--cache-control"),
                "no-cache",
            )
            self.assertIn("checkpoint last", result.stdout)

    def test_existing_v0_checkpoint_fails_closed_without_explicit_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tree = self.fixture_tree(root)
            result, calls = self.run_sync(root, tree, checkpoint_exists=True)

            self.assertEqual(result.returncode, 3)
            self.assertIn("refusing to replace an existing v0 checkpoint", result.stderr)
            self.assertEqual(self.put_calls(calls), [])

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
            puts = self.put_calls(calls)
            self.assertEqual(self.option(puts[-1], "--key"), "checkpoint.json")


if __name__ == "__main__":
    unittest.main()
