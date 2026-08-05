#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
import os
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

BUNDLE = Path("portfolio-bundle")
MANIFEST = BUNDLE / "manifest.json"


def normalize(entry: dict) -> None:
    archive = BUNDLE / entry["archive"]
    repository_name = entry["repo"].split("/", 1)[1]
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        roots = {PurePosixPath(member.name).parts[0] for member in members if PurePosixPath(member.name).parts}
        if len(roots) != 1:
            raise ValueError(f"{entry['repo']}#{entry['number']}: expected one archive root, got {sorted(roots)}")
        old_root = next(iter(roots))
        fd, temporary_name = tempfile.mkstemp(prefix="normalized-", suffix=".tar.gz", dir=archive.parent)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            with tarfile.open(temporary, "w:gz") as output:
                for member in members:
                    path = PurePosixPath(member.name)
                    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != old_root:
                        raise ValueError(f"unsafe archive path: {member.name}")
                    if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                        raise ValueError(f"unsupported archive entry: {member.name}")
                    rewritten = copy.copy(member)
                    rewritten.name = str(PurePosixPath(repository_name, *path.parts[1:]))
                    fileobj = source.extractfile(member) if member.isfile() else None
                    output.addfile(rewritten, fileobj)
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)

    data = archive.read_bytes()
    entry["archive_sha256"] = hashlib.sha256(data).hexdigest()
    entry["archive_bytes"] = len(data)


def main() -> None:
    manifest = json.loads(MANIFEST.read_text())
    for entry in manifest.get("entries", []):
        normalize(entry)
    manifest["archives_normalized_to_repository_name"] = True
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"NORMALIZED_PORTFOLIO_ARCHIVES count={len(manifest.get('entries', []))}")


if __name__ == "__main__":
    main()
