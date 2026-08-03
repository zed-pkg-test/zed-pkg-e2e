from pathlib import Path


path = Path("scripts/org_app_installs.py")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    text = text.replace(old, new, 1)


replace_once(
    '''PREFETCH_RE = re.compile(
    r"recursive install prefetch: (?P<resolved>\\d+) package\\(s\\), "
    r"up to (?P<concurrency>\\d+) concurrent, (?P<downloaded>\\d+) downloaded"
)
''',
    '''PREFETCH_RE = re.compile(
    r"recursive install prefetch: (?P<resolved>\\d+) package\\(s\\), "
    r"up to (?P<concurrency>\\d+) concurrent, (?P<downloaded>\\d+) downloaded"
)
GENERATED_WIRING_FILES = (
    "paths.json",
    "node_path",
    "classpath",
    "go.work",
    "pythonpath",
    "cargo-paths.toml",
    "pub-deps.yaml",
)
''',
    "add generated wiring contract",
)

replace_once(
    '''def assert_no_staging(project: Path) -> None:
    staging = project / ".zpkg-staging"
    if staging.exists():
        raise AssertionError(f"transaction staging leaked in {project}: {list(staging.iterdir())}")
''',
    '''def assert_generated_wiring_absent(project: Path) -> None:
    leaked = [
        project / ".zed" / name
        for name in GENERATED_WIRING_FILES
        if (project / ".zed" / name).exists()
        or (project / ".zed" / name).is_symlink()
    ]
    if leaked:
        raise AssertionError(f"uninstall left generated adapter wiring: {leaked}")


def assert_no_staging(project: Path) -> None:
    staging = project / ".zpkg-staging"
    if staging.exists():
        raise AssertionError(f"transaction staging leaked in {project}: {list(staging.iterdir())}")
''',
    "add generated wiring absence assertion",
)

replace_once(
    '''    require_success(uninstalled)
    assert_not_materialized(project, install_dir, packages)
    if (project / ".zpkg.lock").read_bytes() != lock_bytes:
''',
    '''    require_success(uninstalled)
    assert_not_materialized(project, install_dir, packages)
    assert_generated_wiring_absent(project)
    if (project / ".zpkg.lock").read_bytes() != lock_bytes:
''',
    "assert registry app wiring cleanup",
)

replace_once(
    '''    assert_projection(symlink_repo, symlink_project, "symlink")
    manifest_path.write_text(manifest_before, encoding="utf-8")

    copy_repo = workspace_root / "copy"
''',
    '''    assert_projection(symlink_repo, symlink_project, "symlink")
    manifest_path.write_text(manifest_before, encoding="utf-8")

    symlink_uninstall = run(
        zed_command(zed, registry, home, "uninstall"),
        cwd=symlink_project,
        env=environment,
    )
    require_success(symlink_uninstall)
    for projection in [
        symlink_project / "zed_modules" / "zedtest" / "ws-utils",
        symlink_project / "zed_modules" / "zedtest" / "ws-core",
        symlink_project / "node_modules" / "@zedtest" / "ws-utils",
        symlink_project / "node_modules" / "@zedtest" / "ws-core",
    ]:
        if projection.exists() or projection.is_symlink():
            raise AssertionError(f"workspace uninstall left projection: {projection}")
    assert_generated_wiring_absent(symlink_project)
    assert_no_staging(symlink_project)
    if (symlink_project / ".zpkg.lock").read_bytes() != symlink_lock:
        raise AssertionError("workspace uninstall rewrote the retained lock")

    symlink_restore = run(
        zed_command(zed, registry, home, "install", "--frozen"),
        cwd=symlink_project,
        env=environment,
    )
    require_success(symlink_restore)
    if PREFETCH_RE.search(symlink_restore.output):
        raise AssertionError("workspace post-uninstall restore accessed registry artifacts")
    if (symlink_project / ".zpkg.lock").read_bytes() != symlink_lock:
        raise AssertionError("workspace post-uninstall restore rewrote the lock")
    assert_projection(symlink_repo, symlink_project, "symlink")
    assert_no_staging(symlink_project)

    copy_repo = workspace_root / "copy"
''',
    "add symlink workspace uninstall and frozen restore",
)

replace_once(
    '''    if leaked:
        raise AssertionError(f"workspace copy mode leaked symlinks: {leaked}")


def main() -> int:
''',
    '''    if leaked:
        raise AssertionError(f"workspace copy mode leaked symlinks: {leaked}")

    copy_uninstall = run(
        zed_command(zed, registry, home, "uninstall"),
        cwd=copy_project,
        env=environment,
    )
    require_success(copy_uninstall)
    for projection in [
        copy_project / "zed_modules" / "zedtest" / "ws-utils",
        copy_project / "zed_modules" / "zedtest" / "ws-core",
        copy_project / "node_modules" / "@zedtest" / "ws-utils",
        copy_project / "node_modules" / "@zedtest" / "ws-core",
    ]:
        if projection.exists() or projection.is_symlink():
            raise AssertionError(f"workspace copy uninstall left projection: {projection}")
    assert_generated_wiring_absent(copy_project)
    assert_no_staging(copy_project)
    if (copy_project / ".zpkg.lock").read_bytes() != copy_lock:
        raise AssertionError("workspace copy uninstall rewrote the retained lock")

    copy_restore = run(
        zed_command(
            zed,
            registry,
            home,
            "install",
            "--frozen",
            "--install-mode",
            "copy",
        ),
        cwd=copy_project,
        env=environment,
    )
    require_success(copy_restore)
    if PREFETCH_RE.search(copy_restore.output):
        raise AssertionError("workspace copy post-uninstall restore accessed registry artifacts")
    if (copy_project / ".zpkg.lock").read_bytes() != copy_lock:
        raise AssertionError("workspace copy post-uninstall restore rewrote the lock")
    assert_projection(copy_repo, copy_project, "copy")
    assert_no_staging(copy_project)
    leaked = [
        candidate
        for root in (copy_project / "zed_modules", copy_project / "node_modules")
        for candidate in root.rglob("*")
        if candidate.is_symlink()
    ]
    if leaked:
        raise AssertionError(f"workspace restored copy mode leaked symlinks: {leaked}")


def main() -> int:
''',
    "add copied workspace uninstall and frozen restore",
)

path.write_text(text, encoding="utf-8")
