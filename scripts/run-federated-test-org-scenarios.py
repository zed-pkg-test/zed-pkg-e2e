from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import tarfile
from pathlib import Path

ZED_TOKEN = os.environ.get("ZED_BIN", "zed")
ZED = Path(shutil.which(ZED_TOKEN) or ZED_TOKEN).resolve()
ROOT = Path(os.environ.get("ZED_E2E_ROOT", "/tmp/zed-test-org-e2e")).resolve()
BINARY_ARCHIVE = os.environ.get("ZED_BINARY_ARCHIVE")
SOURCE_COMMIT = os.environ.get("ZED_SOURCE_COMMIT", "unknown")
REPORT_PATH = Path(os.environ.get("ZED_E2E_REPORT", str(ROOT / "report.json"))).resolve()
REPORT = {"binary": {}, "suites": {}}


class ScenarioFailure(RuntimeError):
    pass


def run(args, cwd: Path, *, env=None, ok=True):
    merged = os.environ.copy()
    merged.update({"ZED_PKG_INTERACTIVE": "0", "RUST_BACKTRACE": "1"})
    if env:
        merged.update({str(k): str(v) for k, v in env.items()})
    process = subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        env=merged,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if ok is True and process.returncode != 0:
        raise ScenarioFailure(
            f"command failed ({process.returncode}): {' '.join(map(str, args))}\n"
            f"stdout:\n{process.stdout}\nstderr:\n{process.stderr}"
        )
    if ok is False and process.returncode == 0:
        raise ScenarioFailure(
            f"command unexpectedly succeeded: {' '.join(map(str, args))}\n"
            f"stdout:\n{process.stdout}"
        )
    return process


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(path: Path, name: str, version: str, dependencies=None, extra=""):
    dependencies = dependencies or {}
    dependency_lines = "\n".join(
        f'"{coordinate}" = "{requirement}"'
        for coordinate, requirement in dependencies.items()
    )
    path.write_text(
        textwrap.dedent(
            f'''\
            [package]
            org = "zed-pkg-test"
            name = "{name}"
            version = "{version}"
            description = "test-org executable fixture"
            license = "MIT"

            [package.repository]
            vcs = "git"
            url = "https://github.com/zed-pkg-test/{name}"

            [dependencies]
            {dependency_lines}

            [publish]
            exclude = []
            {extra}
            '''
        ),
        encoding="utf-8",
    )


def publish(registry: Path, home: Path, name: str, version: str, *, dependencies=None, files=None, extra=""):
    package = registry.parent / "sources" / f"{name}-{version}"
    package.mkdir(parents=True, exist_ok=True)
    write_manifest(package / ".zpkg.toml", name, version, dependencies, extra)
    for relative, content in (files or {"payload.txt": f"{name}@{version}\n"}).items():
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    run(
        [
            ZED,
            "--registry",
            f"file://{registry}",
            "--home",
            home,
            "publish",
            "--skip-vcs-checks",
        ],
        package,
    )
    return package


def lock_packages(lock: Path):
    import tomllib

    return tomllib.loads(lock.read_text(encoding="utf-8")).get("package", [])


def suite(name):
    REPORT["suites"][name] = {"checks": [], "status": "running"}
    return REPORT["suites"][name]


def check(target, name, detail=None):
    target["checks"].append(
        {"name": name, "status": "pass", **({"detail": detail} if detail else {})}
    )


def normalize_error(text: str, roots=()):
    for root in roots:
        text = text.replace(str(root), "<ROOT>")
    text = re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27,}", "<UUID>", text, flags=re.I)
    return "\n".join(line.rstrip() for line in text.splitlines() if line.strip())


def offline_cache_suite():
    target = suite("zed-pkg-test/offline-cache-e2e")
    base = ROOT / "offline-cache-e2e"
    registry, home, consumer = base / "registry", base / "home", base / "consumer-a"
    for directory in (registry, home, consumer):
        directory.mkdir(parents=True, exist_ok=True)
    publish(
        registry,
        home,
        "offline-cache-lib",
        "1.0.0",
        files={
            "package.json": '{"name":"@zed-pkg-test/offline-cache-lib","version":"1.0.0","type":"module","exports":"./index.js"}\n',
            "index.js": "export const answer = 42;\n",
        },
    )
    run(
        [
            ZED,
            "--registry",
            f"file://{registry}",
            "--home",
            home,
            "install",
            "zed-pkg-test/offline-cache-lib@^1",
            "--install-mode",
            "copy",
            "--adapter",
            "node",
        ],
        consumer,
    )
    run(
        [
            "node",
            "--input-type=module",
            "-e",
            "import('./node_modules/@zed-pkg-test/offline-cache-lib/index.js').then(m=>{if(m.answer!==42)process.exit(1)})",
        ],
        consumer,
    )
    packages = lock_packages(consumer / ".zpkg.lock")
    assert len(packages) == 1 and packages[0]["version"] == "1.0.0"
    digest = packages[0]["sha256"]
    cache = home / "cache" / f"{digest}.tar.gz"
    store = home / "store" / "v1" / digest[:2] / digest
    assert cache.is_file() and store.is_dir()
    check(target, "cold publish/install and Node consumer import", digest)

    manifest_bytes = (consumer / ".zpkg.toml").read_bytes()
    lock_bytes = (consumer / ".zpkg.lock").read_bytes()
    run([ZED, "--registry", f"file://{registry}", "--home", home, "uninstall"], consumer)
    offline_registry = base / "registry.offline"
    registry.rename(offline_registry)
    dead_network = {
        "http_proxy": "http://127.0.0.1:9",
        "https_proxy": "http://127.0.0.1:9",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "",
        "no_proxy": "",
    }
    frozen = [
        ZED,
        "--registry",
        f"file://{registry}",
        "--home",
        home,
        "install",
        "--frozen",
        "--install-mode",
        "copy",
        "--adapter",
        "node",
    ]
    run(frozen, consumer, env=dead_network)
    assert (consumer / ".zpkg.toml").read_bytes() == manifest_bytes
    assert (consumer / ".zpkg.lock").read_bytes() == lock_bytes
    check(target, "offline frozen reinstall with registry absent and proxies dead")

    consumer_b = base / "consumer-b"
    consumer_b.mkdir()
    (consumer_b / ".zpkg.toml").write_bytes(manifest_bytes)
    (consumer_b / ".zpkg.lock").write_bytes(lock_bytes)
    run(frozen, consumer_b, env=dead_network)
    assert (consumer_b / "zed_modules/zed-pkg-test/offline-cache-lib/index.js").is_file()
    check(target, "shared-home cache reuse in second checkout")

    run([ZED, "--registry", f"file://{registry}", "--home", home, "uninstall"], consumer_b)
    shutil.rmtree(store, ignore_errors=True)
    cache.write_bytes(b"corrupt-cache-entry")
    failed = run(frozen, consumer_b, env=dead_network, ok=False)
    assert not (consumer_b / "zed_modules/zed-pkg-test/offline-cache-lib").exists()
    assert any(word in failed.stderr.lower() for word in ("sha256", "integrity", "registry"))
    check(target, "corrupt cache + offline registry rejected without partial materialization")

    offline_registry.rename(registry)
    run(frozen, consumer_b)
    assert sha256(cache) == digest
    check(target, "corrupt cache recovers from immutable registry artifact")
    target["status"] = "pass"


def version_solver_suite():
    target = suite("zed-pkg-test/version-solver-e2e")
    base, registry, home = ROOT / "version-solver-e2e", ROOT / "version-solver-e2e/registry", ROOT / "version-solver-e2e/home"
    registry.mkdir(parents=True)
    home.mkdir()
    for version in ("1.0.0", "1.1.0", "2.0.0"):
        publish(registry, home, "solver-shared", version)
    publish(registry, home, "solver-alpha", "1.0.0", dependencies={"zed-pkg-test/solver-shared": "^1"})
    publish(registry, home, "solver-beta", "1.0.0", dependencies={"zed-pkg-test/solver-shared": "^2"})

    ranged = base / "range"
    ranged.mkdir()
    run([ZED, "--registry", f"file://{registry}", "--home", home, "install", "zed-pkg-test/solver-shared@^1", "--adapter", "none", "--install-mode", "copy"], ranged)
    assert [(item["name"], item["version"]) for item in lock_packages(ranged / ".zpkg.lock")] == [("solver-shared", "1.1.0")]
    check(target, "compatible range selects highest matching version", "1.1.0")

    exact = base / "exact"
    exact.mkdir()
    run([ZED, "--registry", f"file://{registry}", "--home", home, "install", "zed-pkg-test/solver-shared@=1.0.0", "--adapter", "none", "--install-mode", "copy"], exact)
    assert lock_packages(exact / ".zpkg.lock")[0]["version"] == "1.0.0"
    check(target, "exact version remains exact")

    errors = []
    for index in (1, 2):
        consumer = base / f"conflict-{index}"
        consumer.mkdir()
        write_manifest(consumer / ".zpkg.toml", f"conflict-{index}", "0.0.0", {"zed-pkg-test/solver-alpha": "^1", "zed-pkg-test/solver-beta": "^1"})
        output = run([ZED, "--registry", f"file://{registry}", "--home", home, "install", "--adapter", "none", "--install-mode", "copy"], consumer, ok=False)
        assert not (consumer / "zed_modules").exists()
        errors.append(normalize_error(output.stderr, (consumer, base)))
    cores = [re.sub(r"conflict-[12]", "conflict-N", error) for error in errors]
    assert cores[0] == cores[1]
    assert "solver-shared" in cores[0] and any(word in cores[0].lower() for word in ("conflict", "satisf"))
    check(target, "incompatible transitive constraints fail deterministically", cores[0].splitlines()[-1][:240])

    for version in ("1.0.0+aaa", "1.0.0+bbb"):
        publish(registry, home, "solver-metadata", version)
    outcomes = []
    for index in (1, 2):
        consumer = base / f"metadata-{index}"
        consumer.mkdir()
        output = run([ZED, "--registry", f"file://{registry}", "--home", home, "install", "zed-pkg-test/solver-metadata@=1.0.0", "--adapter", "none", "--install-mode", "copy"], consumer, ok=None)
        outcomes.append(("resolved", lock_packages(consumer / ".zpkg.lock")[0]["version"]) if output.returncode == 0 else ("rejected", normalize_error(output.stderr, (consumer, base))))
    assert outcomes[0] == outcomes[1]
    check(target, "SemVer build-metadata ambiguity has a stable outcome", repr(outcomes[0])[:240])
    target["status"] = "pass"


def security_suite():
    target = suite("zed-pkg-test/security-adversarial-e2e")
    base, registry, home = ROOT / "security-adversarial-e2e", ROOT / "security-adversarial-e2e/registry", ROOT / "security-adversarial-e2e/home"
    registry.mkdir(parents=True)
    home.mkdir()

    package = base / "symlink"
    package.mkdir()
    write_manifest(package / ".zpkg.toml", "escape-symlink", "1.0.0")
    (package / "payload.txt").write_text("safe\n")
    (package / "escape").symlink_to("/etc/passwd")
    output = run([ZED, "--home", home, "pack"], package, ok=None)
    if output.returncode == 0:
        archives = list((package / ".zed/pack").glob("*.tar.gz"))
        assert len(archives) == 1
        with tarfile.open(archives[0], "r:gz") as archive:
            members = archive.getmembers()
            assert all(member.isfile() for member in members)
            assert all(member.name != "pkg/escape" for member in members)
            assert all(not member.name.startswith("/") and ".." not in member.name.split("/") for member in members)
        check(target, "absolute symlink omitted from deterministic artifact without external bytes")
    else:
        assert any(word in output.stderr.lower() for word in ("symlink", "absolute", "escape"))
        check(target, "absolute symlink rejected during deterministic pack")

    traversal = base / "target-traversal"
    traversal.mkdir()
    write_manifest(traversal / ".zpkg.toml", "target-traversal", "1.0.0", extra='''
[targets.nodejs]
dir = "../outside"
adapter = "node"
''')
    (traversal / "payload.txt").write_text("safe\n")
    output = run([ZED, "--home", home, "pack"], traversal, ok=False)
    assert ".." in output.stderr or any(word in output.stderr.lower() for word in ("target", "relative"))
    check(target, "target directory traversal rejected")

    publish(registry, home, "hook-fixture", "1.0.0", extra='''
[build]
command = "sh -c 'echo consented > build-marker.txt'"
''')
    no_consent = base / "hook-no-consent"
    no_consent.mkdir()
    run([ZED, "--registry", f"file://{registry}", "--home", home, "install", "zed-pkg-test/hook-fixture@1.0.0", "--adapter", "none", "--install-mode", "copy"], no_consent)
    assert not any(no_consent.rglob("build-marker.txt"))
    check(target, "package build hook does not execute without explicit consent")

    consent = base / "hook-consent"
    consent.mkdir()
    run([ZED, "--registry", f"file://{registry}", "--home", home, "install", "zed-pkg-test/hook-fixture@1.0.0", "--adapter", "none", "--install-mode", "copy", "--allow-build"], consent)
    markers = list(consent.rglob("build-marker.txt"))
    assert markers
    check(target, "explicit --allow-build executes declared hook", str(markers[0].relative_to(consent)))

    publish(registry, home, "tamper-fixture", "1.0.0", files={"package.json": '{"name":"@zed-pkg-test/tamper-fixture","version":"1.0.0"}\n'})
    consumer = base / "tamper-consumer"
    consumer.mkdir()
    run([ZED, "--registry", f"file://{registry}", "--home", home, "install", "zed-pkg-test/tamper-fixture@1.0.0", "--adapter", "none", "--install-mode", "copy"], consumer)
    run([ZED, "--registry", f"file://{registry}", "--home", home, "uninstall"], consumer)
    lock = consumer / ".zpkg.lock"
    text, count = re.subn(r'^sha256 = "[0-9a-f]{64}"$', 'sha256 = "' + "0" * 64 + '"', lock.read_text(), count=1, flags=re.M)
    assert count == 1
    lock.write_text(text)
    output = run([ZED, "--registry", f"file://{registry}", "--home", home, "install", "--frozen", "--adapter", "none", "--install-mode", "copy"], consumer, ok=False)
    assert not (consumer / "zed_modules/zed-pkg-test/tamper-fixture").exists()
    assert any(word in output.stderr.lower() for word in ("sha256", "integrity"))
    check(target, "tampered frozen lock rejected without partial install")
    target["status"] = "pass"


def manager_suite():
    target = suite("zed-pkg-test/manager-interop-e2e")
    base, home = ROOT / "manager-interop-e2e", ROOT / "manager-interop-e2e/home"
    empty = home / "empty-path"
    home.mkdir(parents=True)
    empty.mkdir()
    isolated = {"HOME": home, "USERPROFILE": home, "XDG_CONFIG_HOME": home / ".config", "ZED_PKG_HOME": home / ".zed-pkg", "PATH": empty}

    mise = base / "mise"
    mise.mkdir(parents=True)
    (mise / "mise.toml").write_text('[settings]\nlockfile = true\n\n[tools]\nnode = "22"\n')
    (mise / "mise.lock").write_text('[[tools.node]]\nversion = "22.4.0"\nbackend = "core:node"\n\n[tools.node.platforms.linux-x64]\nchecksum = "sha256:' + "a" * 64 + '"\nsize = 123\nurl = "https://example.invalid/node.tar.xz"\n')
    before = (sha256(mise / "mise.toml"), sha256(mise / "mise.lock"))
    verified = json.loads(run([ZED, "env", "verify", "mise", "--config", "mise.toml", "--lock", "mise.lock", "--frozen", "--json"], mise, env=isolated).stdout)
    imported = json.loads(run([ZED, "env", "import", "mise", "--config", "mise.toml", "--lock", "mise.lock", "--frozen", "--json"], mise, env=isolated).stdout)
    assert verified["verified"] is True and verified["tools"] == 1
    assert imported["tools"]["node"]["resolved"] == "22.4.0"
    assert before == (sha256(mise / "mise.toml"), sha256(mise / "mise.lock"))
    check(target, "mise frozen import/verify is parser-only, deterministic, and read-only", verified["environment_plan_sha256"])

    (mise / ".mise.toml").write_text('[tools]\npython = "3.12"\n')
    assert "multiple project-local" in run([ZED, "env", "verify", "mise", "--frozen", "--json"], mise, env=isolated, ok=False).stderr
    (mise / ".mise.toml").unlink()
    unlocked = base / "mise-unlocked"
    unlocked.mkdir()
    (unlocked / "mise.toml").write_text('[tools]\nnode = "22"\n')
    assert "requires a project-local lockfile" in run([ZED, "env", "verify", "mise", "--config", "mise.toml", "--frozen", "--json"], unlocked, env=isolated, ok=False).stderr
    check(target, "mise ambiguity and incomplete frozen state fail closed")

    asdf = base / "asdf"
    (asdf / ".zed").mkdir(parents=True)
    (asdf / ".tool-versions").write_text("nodejs 22.11.0\npython 3.12.4\n")
    (asdf / ".zed/asdf.lock.toml").write_text('''schema = 1

[plugins.nodejs]
version = "22.11.0"
url = "https://github.com/asdf-vm/asdf-nodejs.git"
revision = "1111111111111111111111111111111111111111"
sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
platforms = ["x86_64-linux"]

[plugins.python]
version = "3.12.4"
url = "https://github.com/danhper/asdf-python.git"
revision = "2222222222222222222222222222222222222222"
sha256 = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
''')
    before = (sha256(asdf / ".tool-versions"), sha256(asdf / ".zed/asdf.lock.toml"))
    verified = json.loads(run([ZED, "env", "verify", "asdf", "--config", ".tool-versions", "--lock", ".zed/asdf.lock.toml", "--frozen", "--json"], asdf, env=isolated).stdout)
    imported = json.loads(run([ZED, "env", "import", "asdf", "--config", ".tool-versions", "--lock", ".zed/asdf.lock.toml", "--frozen", "--json"], asdf, env=isolated).stdout)
    assert verified["verified"] is True and verified["tools"] == 2
    assert imported["tools"]["nodejs"]["resolved"] == "22.11.0"
    assert before == (sha256(asdf / ".tool-versions"), sha256(asdf / ".zed/asdf.lock.toml"))
    check(target, "asdf frozen import/verify validates immutable plugin/artifact provenance read-only", verified["environment_plan_sha256"])

    moving = base / "asdf-moving"
    (moving / ".zed").mkdir(parents=True)
    (moving / ".tool-versions").write_text("nodejs ref:main\n")
    (moving / ".zed/asdf.lock.toml").write_text('''schema = 1
[plugins.nodejs]
version = "ref:main"
url = "https://github.com/asdf-vm/asdf-nodejs.git"
revision = "1111111111111111111111111111111111111111"
sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
''')
    assert "moving selector" in run([ZED, "env", "verify", "asdf", "--config", ".tool-versions", "--lock", ".zed/asdf.lock.toml", "--frozen"], moving, env=isolated, ok=False).stderr
    check(target, "asdf moving selector rejected in frozen mode")

    unsupported = []
    for manager in ("flox", "devbox", "nix"):
        output = run([ZED, "env", "import", manager], base, env=isolated, ok=False)
        assert "possible values" in output.stderr and "mise" in output.stderr and "asdf" in output.stderr
        unsupported.append(manager)
    check(target, "unsupported manager labels fail at typed CLI boundary", ",".join(unsupported))
    target["status"] = "pass"


def main():
    if ROOT.exists():
        shutil.rmtree(ROOT)
    ROOT.mkdir(parents=True)
    REPORT["binary"] = {
        "version": run([ZED, "--version"], ROOT).stdout.strip(),
        "archive_sha256": sha256(Path(BINARY_ARCHIVE)) if BINARY_ARCHIVE else None,
        "source_commit": SOURCE_COMMIT,
    }
    for function in (offline_cache_suite, version_solver_suite, security_suite, manager_suite):
        try:
            function()
        except Exception as error:
            for value in REPORT["suites"].values():
                if value.get("status") == "running":
                    value["status"] = "fail"
                    value["error"] = str(error)
                    break
            print(json.dumps(REPORT, indent=2), file=sys.stderr)
            raise
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(REPORT, indent=2) + "\n")
    print(json.dumps(REPORT, indent=2))


if __name__ == "__main__":
    main()
