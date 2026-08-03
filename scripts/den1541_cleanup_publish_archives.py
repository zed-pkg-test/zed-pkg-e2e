#!/usr/bin/env python3
"""Apply precise cleanup for untracked archives emitted by `zed publish`."""

from pathlib import Path

path = Path("scripts/lifecycle.py")
source = path.read_text(encoding="utf-8")

class_marker = "\n\nclass Harness:\n"
helper = r'''

def publish_archive_name(package: PackageRef) -> str:
    """Return the deterministic archive name emitted by `zed publish`."""
    return f"{package.full_name.replace('/', '-')}-{package.version}.tar.gz"


def remove_transient_pack_outputs(root: Path, packages: Sequence[PackageRef]) -> None:
    """Remove only verified untracked archives produced by lifecycle publishes.

    The source-cleanliness invariant remains fail closed: tracked modifications,
    symlinks, directories, and unexpected files under `.zed/pack` are rejected.
    Repositories that already ignore these archives produce no status entries and
    require no cleanup.
    """

    expected = {
        f".zed/pack/{publish_archive_name(package)}" for package in packages
    }
    if not expected:
        return

    completed = subprocess.run(
        [
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            ".zed/pack",
        ],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"failed to inspect transient pack outputs: {detail}")

    entries: list[str] = []
    for line in completed.stdout.splitlines():
        if len(line) < 4:
            raise AssertionError(f"malformed git status entry for pack output: {line!r}")
        status = line[:2]
        relative = line[3:]
        if status != "??":
            raise AssertionError(
                f"refusing to clean tracked or modified pack output: {line}"
            )
        if relative not in expected:
            raise AssertionError(
                f"refusing to clean unexpected pack output: {relative}"
            )
        entries.append(relative)

    if not entries:
        return

    zed_dir = root / ".zed"
    pack_dir = zed_dir / "pack"
    if zed_dir.is_symlink() or pack_dir.is_symlink():
        raise AssertionError("refusing to clean pack outputs through a symlink")

    for relative in entries:
        archive = root / relative
        if archive.is_symlink() or not archive.is_file():
            raise AssertionError(
                f"refusing to clean non-regular pack output: {relative}"
            )
        if archive.parent != pack_dir:
            raise AssertionError(
                f"refusing to clean pack output outside .zed/pack: {relative}"
            )
        archive.unlink()

    for directory in (pack_dir, zed_dir):
        try:
            directory.rmdir()
        except OSError:
            pass
'''
if class_marker not in source:
    raise SystemExit("Harness class insertion marker not found")
if "def remove_transient_pack_outputs(" in source:
    raise SystemExit("transient pack cleanup already exists")
source = source.replace(class_marker, helper + class_marker, 1)

old_prerequisite = '''        self.published_sources.add(key)
        for output in expected_packages(manifest):
            assert_registry_version(self.registry, output)
        self.assert_git_clean(self.source_root(repo), f"seed source {repo}")
'''
new_prerequisite = '''        self.published_sources.add(key)
        outputs = expected_packages(manifest)
        for output in outputs:
            assert_registry_version(self.registry, output)
        remove_transient_pack_outputs(source, outputs)
        self.assert_git_clean(self.source_root(repo), f"seed source {repo}")
'''
if old_prerequisite not in source:
    raise SystemExit("prerequisite publish block not found")
source = source.replace(old_prerequisite, new_prerequisite, 1)

old_unit = '''        for output_index, output in enumerate(outputs):
            self.exercise_consumer(output, index, output_index)
        self.assert_git_clean(self.fixture, self.repo)
'''
new_unit = '''        for output_index, output in enumerate(outputs):
            self.exercise_consumer(output, index, output_index)
        remove_transient_pack_outputs(unit, outputs)
        self.assert_git_clean(self.fixture, self.repo)
'''
if old_unit not in source:
    raise SystemExit("unit final-clean block not found")
source = source.replace(old_unit, new_unit, 1)

path.write_text(source, encoding="utf-8")
