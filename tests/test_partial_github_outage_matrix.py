import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from partial_github_outage_matrix import (
    CANDIDATE_SHA, SENTINEL, SourceFailure, resolve, run_matrix, scenarios,
)


class FallbackFaultMatrixTests(unittest.TestCase):
    def test_each_outage_selects_exactly_expected_source_or_bounded_failure(self):
        result = run_matrix()
        self.assertEqual(result["candidate_sha"], CANDIDATE_SHA)
        self.assertEqual(len(result["matrix"]), 6)
        for case in result["matrix"]:
            self.assertLessEqual(len(case["attempted"]), 4)

    def test_fail_if_used_sentinel_guards_priority(self):
        for name, (surfaces, expected, opt_in) in scenarios().items():
            if expected == "sources_exhausted":
                with self.assertRaisesRegex(SourceFailure, expected):
                    resolve(surfaces, r2_enabled=opt_in)
                continue
            self.assertEqual(resolve(surfaces, r2_enabled=opt_in)["source"], expected)
        all_up = dict.fromkeys(("rest_api", "github_release", "ghcr", "tag_archive", "r2_mirror"), "available")
        all_up["ghcr"] = SENTINEL
        all_up["tag_archive"] = SENTINEL
        self.assertEqual(resolve(all_up)["source"], "github_release")
        all_up["github_release"] = "degraded"
        with self.assertRaisesRegex(SourceFailure, "forbidden_lower_priority_used"):
            resolve(all_up)

    def test_no_r2_without_opt_in_even_if_configured(self):
        inputs = dict.fromkeys(("rest_api", "github_release", "ghcr", "tag_archive"), "unavailable")
        inputs["r2_mirror"] = "available"
        with self.assertRaisesRegex(SourceFailure, "sources_exhausted"):
            resolve(inputs, r2_enabled=False)

    def test_subprocess_has_clean_home_store_and_no_ambient_tokens(self):
        path = Path(__file__).resolve().parents[1] / "scripts/partial_github_outage_matrix.py"
        with TemporaryDirectory(prefix="zed-fallback-") as temp:
            home = Path(temp) / "home"
            store = Path(temp) / "store"
            home.mkdir()
            store.mkdir()
            safe_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                        "HOME": str(home), "ZED_PKG_STORE": str(store)}
            proc = subprocess.run([sys.executable, str(path)], env=safe_env,
                                  check=True, capture_output=True, text=True, timeout=10)
            obj = json.loads(proc.stdout)
            self.assertEqual(obj["candidate_sha"], CANDIDATE_SHA)
            self.assertNotIn("token", proc.stdout.lower())
            self.assertTrue(obj["credential_free"])


if __name__ == "__main__":
    unittest.main()
