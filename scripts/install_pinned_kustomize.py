#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path


VERSION = "5.8.1"
BASE_URL = (
    "https://github.com/kubernetes-sigs/kustomize/releases/download/"
    f"kustomize/v{VERSION}"
)
ASSETS: dict[tuple[str, str], tuple[str, str]] = {
    ("linux", "x86_64"): (
        f"kustomize_v{VERSION}_linux_amd64.tar.gz",
        "029a7f0f4e1932c52a0476cf02a0fd855c0bb85694b82c338fc648dcb53a819d",
    ),
    ("linux", "aarch64"): (
        f"kustomize_v{VERSION}_linux_arm64.tar.gz",
        "0953ea3e476f66d6ddfcd911d750f5167b9365aa9491b2326398e289fef2c142",
    ),
    ("darwin", "x86_64"): (
        f"kustomize_v{VERSION}_darwin_amd64.tar.gz",
        "ee7cf0c1e3592aa7bb66ba82b359933a95e7f2e0b36e5f53ed0a4535b017f2f8",
    ),
    ("darwin", "arm64"): (
        f"kustomize_v{VERSION}_darwin_arm64.tar.gz",
        "8886f8a78474e608cc81234f729fda188a9767da23e28925802f00ece2bab288",
    ),
    ("win32", "amd64"): (
        f"kustomize_v{VERSION}_windows_amd64.zip",
        "8ec7f5e815e526d4622c06df0a7793d8cfb6eb1c74f816b46166097fef8b26c6",
    ),
    ("win32", "x86_64"): (
        f"kustomize_v{VERSION}_windows_amd64.zip",
        "8ec7f5e815e526d4622c06df0a7793d8cfb6eb1c74f816b46166097fef8b26c6",
    ),
    ("win32", "arm64"): (
        f"kustomize_v{VERSION}_windows_arm64.zip",
        "f6f5090c373965760a30a59af84c81853b80d2c7dbb3236424f440e47eed3f5a",
    ),
}


class InstallError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_extract_zip(archive: Path, destination: Path) -> None:
    root = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (destination / member.filename).resolve()
            if target != root and root not in target.parents:
                raise InstallError(f"unsafe zip member: {member.filename}")
        bundle.extractall(destination)


def safe_extract_tar(archive: Path, destination: Path) -> None:
    root = destination.resolve()
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            target = (destination / member.name).resolve()
            if target != root and root not in target.parents:
                raise InstallError(f"unsafe tar member: {member.name}")
            if member.issym() or member.islnk():
                raise InstallError(f"archive links are not allowed: {member.name}")
        bundle.extractall(destination, filter="data")


def normalized_platform() -> tuple[str, str]:
    machine = platform.machine().lower()
    aliases = {
        "amd64": "amd64",
        "x86_64": "x86_64",
        "aarch64": "aarch64",
        "arm64": "arm64",
    }
    return sys.platform, aliases.get(machine, machine)


def install(destination: Path, metadata_path: Path) -> Path:
    key = normalized_platform()
    asset = ASSETS.get(key)
    if asset is None:
        raise InstallError(f"unsupported runner platform: {key[0]}/{key[1]}")
    asset_name, expected_digest = asset
    url = f"{BASE_URL}/{asset_name}"

    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kustomize-install-") as temporary:
        temporary_root = Path(temporary)
        archive = temporary_root / asset_name
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "zed-pkg-test-direct-source-render/1"},
        )
        with urllib.request.urlopen(request, timeout=90) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
        actual_digest = sha256(archive)
        if actual_digest != expected_digest:
            raise InstallError(
                f"Kustomize asset digest mismatch: expected {expected_digest}, got {actual_digest}"
            )

        extracted = temporary_root / "extracted"
        extracted.mkdir()
        if asset_name.endswith(".zip"):
            safe_extract_zip(archive, extracted)
        else:
            safe_extract_tar(archive, extracted)

        executable_name = "kustomize.exe" if os.name == "nt" else "kustomize"
        matches = [path for path in extracted.rglob(executable_name) if path.is_file()]
        if len(matches) != 1:
            raise InstallError(
                f"expected exactly one {executable_name} in {asset_name}, found {len(matches)}"
            )
        target = destination / executable_name
        shutil.copy2(matches[0], target)
        if os.name != "nt":
            target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(
            {
                "$schema": "zed-pkg-test/pinned-kustomize-install/v1",
                "version": VERSION,
                "platform": {"system": key[0], "machine": key[1]},
                "asset": asset_name,
                "assetUrl": url,
                "assetSha256": expected_digest,
                "executable": str(target),
                "executableSha256": sha256(target),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return target


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        executable = install(args.destination.resolve(), args.metadata.resolve())
    except (InstallError, OSError, urllib.error.URLError, tarfile.TarError, zipfile.BadZipFile) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(executable)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
