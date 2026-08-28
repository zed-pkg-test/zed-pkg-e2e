#!/usr/bin/env python3
from __future__ import annotations
import hashlib, os, sys, tempfile, time
from pathlib import Path
REPO = Path(sys.argv[1]).resolve(); sys.path.insert(0, str(REPO))
from zed_pkg_insights.analyzer import Analyzer
from zed_pkg_insights.project import discover_zed_roots

def digest_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file(): digest.update(path.relative_to(root).as_posix().encode()); digest.update(path.read_bytes())
    return digest.hexdigest()

def write_cli(path: Path, correct: bool = True) -> None:
    marker = "Zed universal package manager" if correct else "Zed collaborative code editor"
    path.write_text("#!/bin/sh\ncase \"$1\" in\n  --version) echo 'zed 0.1.0' ;;\n  --help) echo '" + marker + "' ;;\n  *) echo 'unexpected mutation attempt' >&2; exit 91 ;;\nesac\n"); path.chmod(0o755)

def analyze(root: Path, zed: Path):
    before = digest_tree(root); snapshot = Analyzer().analyze(root, zed_binary=str(zed), probe_cli=True); after = digest_tree(root)
    assert before == after, f"analysis mutated {root}"; return {item.code for item in snapshot.diagnostics}

def main() -> int:
    with tempfile.TemporaryDirectory(prefix="zed-ide-sandbox-") as directory:
        sandbox = Path(directory); home = sandbox / "home"; home.mkdir(); os.environ["HOME"] = str(home)
        cli = sandbox / "zed"; wrong_cli = sandbox / "zed-editor"; write_cli(cli); write_cli(wrong_cli, False)
        unmanaged = sandbox / "unmanaged"; unmanaged.mkdir(); assert "ZED001" in analyze(unmanaged, cli)
        lock_only = sandbox / "lock-only"; lock_only.mkdir(); (lock_only / ".zpkg.lock").write_text("version = 1\n"); assert "ZED002" in analyze(lock_only, cli)
        manifest_only = sandbox / "manifest-only"; manifest_only.mkdir(); (manifest_only / ".zpkg.toml").write_text('[package]\norg = "acme"\nname = "widget"\nversion = "1.0.0"\n[dependencies]\n"zed-pkg/zed-interfaces" = "^0.1.0"\n'); assert "ZED003" in analyze(manifest_only, cli)
        stale = sandbox / "stale"; stale.mkdir(); manifest = stale / ".zpkg.toml"; lock = stale / ".zpkg.lock"; lock.write_text("version = 1\n"); time.sleep(1.1); manifest.write_text('[package]\norg = "acme"\nname = "widget"\nversion = "1.0.0"\n[dependencies]\n"zed-pkg/zed-interfaces" = "^0.1.0"\n'); assert {"ZED004", "ZED006"}.issubset(analyze(stale, cli))
        staging = sandbox / "staging"; staging.mkdir(); (staging / ".zpkg.toml").write_text('[package]\norg = "acme"\nname = "widget"\nversion = "1.0.0"\n'); (staging / ".zpkg.lock").write_text("version = 1\n"); transaction = staging / ".zpkg-staging"; transaction.mkdir(); (transaction / "journal.json").write_text("{}"); assert "ZED007" in analyze(staging, cli)
        invalid = sandbox / "invalid"; invalid.mkdir(); (invalid / ".zpkg.toml").write_text("[package\n"); assert "ZED014" in analyze(invalid, cli)
        wrong = sandbox / "wrong-cli"; wrong.mkdir(); assert "ZED011" in analyze(wrong, wrong_cli)
        assert {manifest_only.resolve(), stale.resolve(), staging.resolve()} == set(discover_zed_roots([manifest_only, stale, staging]))
        assert not any(home.iterdir()), "sandbox analysis wrote to HOME"
    print("sandboxed Sublime diagnostics and multi-root discovery passed"); return 0
if __name__ == "__main__": raise SystemExit(main())
