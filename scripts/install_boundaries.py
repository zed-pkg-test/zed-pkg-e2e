#!/usr/bin/env python3
"""Cross-container ownership canaries for DEN-591.

The harness publishes exact Node and Rust fixtures into a private file registry,
then proves the difference between store-backed symlink installs and independent
copy installs. Copy installs cross separate host filesystems, a Docker build
context, a non-root/read-only runtime, and an OCI archive round-trip through
Skopeo. No registry credentials or moving source refs are used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, NoReturn, Sequence


@dataclass(frozen=True)
class Mount:
    source: Path
    target: str
    readonly: bool = False


@dataclass(frozen=True)
class Ecosystem:
    name: str
    image: str
    lib_source: Path
    app_source: Path
    package_name: str
    version: str
    package_relative: Path
    representative_relative: Path
    expected_output: str
    adapter_relative: Path | None = None


class Harness:
    def __init__(self, args: argparse.Namespace) -> None:
        self.zed = args.zed.resolve()
        self.zed_dir = self.zed.parent
        self.root = args.work_root.resolve()
        self.registry = self.root / "registry"
        self.diagnostics = self.root / "diagnostics"
        self.log_path = self.diagnostics / "install-boundaries.log"
        self.dockerfiles = self.root / "dockerfiles"
        self.oci = self.root / "oci"
        self.node_image = args.node_image
        self.rust_image = args.rust_image
        self.debian_image = args.debian_image
        self.skopeo_image = args.skopeo_image
        self.uid = os.getuid()
        self.gid = os.getgid()
        self.shm_paths: list[Path] = []
        self.images: list[str] = []

        if self.root.exists():
            raise RuntimeError(f"work root must be fresh: {self.root}")
        if not self.zed.is_file():
            raise RuntimeError(f"zed binary not found: {self.zed}")
        for directory in [self.registry, self.diagnostics, self.dockerfiles, self.oci]:
            directory.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("", encoding="utf-8")

        self.ecosystems = [
            Ecosystem(
                name="node",
                image=self.node_image,
                lib_source=args.node_lib.resolve(),
                app_source=args.node_app.resolve(),
                package_name="zed-pkg-test/node-lib",
                version="1.0.0",
                package_relative=Path(".vendor/.zed/zed-pkg-test/node-lib"),
                adapter_relative=Path("node_modules/@zed-pkg-test/node-lib"),
                representative_relative=Path("src/index.js"),
                expected_output="OK: zed-sourced dep resolved alongside npm",
            ),
            Ecosystem(
                name="rust",
                image=self.rust_image,
                lib_source=args.rust_lib.resolve(),
                app_source=args.rust_app.resolve(),
                package_name="zed-pkg-test/rust-lib",
                version="1.0.0",
                package_relative=Path(".vendor/.zed/zed-pkg-test/rust-lib"),
                representative_relative=Path("src/lib.rs"),
                expected_output="OK: zed-sourced crate resolved via cargo path dep",
            ),
        ]

    def log(self, message: str) -> None:
        print(message, flush=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(message + "\n")

    def run(
        self,
        command: Sequence[str | Path],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        should_fail: bool = False,
        label: str | None = None,
    ) -> str:
        argv = [str(value) for value in command]
        shown = " ".join(shlex.quote(value) for value in argv)
        heading = f"[{label}] " if label else ""
        self.log(f"\n{heading}$ (cd {cwd or Path.cwd()} && {shown})")
        environment = os.environ.copy()
        if env:
            environment.update(env)
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        output = completed.stdout or ""
        if output:
            print(output, end="" if output.endswith("\n") else "\n", flush=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(output)
                if not output.endswith("\n"):
                    handle.write("\n")
        if should_fail:
            if completed.returncode == 0:
                raise AssertionError(f"command unexpectedly succeeded: {shown}")
        elif completed.returncode != 0:
            raise RuntimeError(
                f"command failed with exit code {completed.returncode}: {shown}"
            )
        return output

    def container(
        self,
        image: str,
        *,
        mounts: Sequence[Mount],
        command: Sequence[str | Path],
        workdir: str | None = None,
        env: Mapping[str, str] | None = None,
        should_fail: bool = False,
        user: bool = True,
        read_only_root: bool = False,
        tmpfs: bool = False,
        label: str | None = None,
    ) -> str:
        argv: list[str | Path] = ["docker", "run", "--rm", "--network", "none"]
        if user:
            argv.extend(["--user", f"{self.uid}:{self.gid}"])
        if read_only_root:
            argv.append("--read-only")
        if tmpfs:
            argv.extend(["--tmpfs", "/tmp:rw,noexec,nosuid,size=64m"])
        if workdir:
            argv.extend(["--workdir", workdir])
        environment = {"CI": "true", "HOME": "/tmp"}
        if env:
            environment.update(env)
        for key, value in environment.items():
            argv.extend(["--env", f"{key}={value}"])
        for mount in mounts:
            source = mount.source.resolve()
            if not source.exists():
                raise RuntimeError(f"mount source does not exist: {source}")
            spec = f"type=bind,src={source},dst={mount.target}"
            if mount.readonly:
                spec += ",readonly"
            argv.extend(["--mount", spec])
        argv.append(image)
        argv.extend(command)
        return self.run(argv, should_fail=should_fail, label=label)

    def fresh_shm(self, name: str) -> Path:
        path = Path("/dev/shm") / f"zed-den591-{os.getpid()}-{name}"
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)
        self.shm_paths.append(path)
        return path

    def copy_source(self, source: Path, destination: Path) -> Path:
        if destination.exists():
            shutil.rmtree(destination)

        def ignore(_directory: str, names: list[str]) -> set[str]:
            ignored = {
                ".git",
                ".zed",
                ".zpkg.lock",
                ".vendor",
                "Cargo.lock",
                "node_modules",
                "target",
                "zed_modules",
            }
            return ignored.intersection(names)

        shutil.copytree(source, destination, symlinks=True, ignore=ignore)
        return destination

    def validate_metadata(self) -> None:
        self.log("\n== Validate fixture identity and native metadata ==")
        for ecosystem in self.ecosystems:
            for role, source in [("lib", ecosystem.lib_source), ("app", ecosystem.app_source)]:
                manifest = tomllib.loads((source / ".zpkg.toml").read_text(encoding="utf-8"))
                package = manifest["package"]
                full_name = f"{package['org']}/{package['name']}"
                expected_name = (
                    ecosystem.package_name
                    if role == "lib"
                    else f"zed-pkg-test/{ecosystem.name}-app"
                )
                expected_version = ecosystem.version if role == "lib" else "0.1.0"
                if full_name != expected_name or package["version"] != expected_version:
                    raise AssertionError(
                        f"unexpected Zed identity for {ecosystem.name} {role}: "
                        f"{full_name}@{package['version']}"
                    )
                if ecosystem.name == "node":
                    native = json.loads((source / "package.json").read_text(encoding="utf-8"))
                    native_name = native["name"].split("/")[-1]
                    if native_name != package["name"] or native["version"] != package["version"]:
                        raise AssertionError(
                            f"Node/Zed metadata mismatch in {source}: "
                            f"{native['name']}@{native['version']} vs "
                            f"{full_name}@{package['version']}"
                        )
                else:
                    native = tomllib.loads((source / "Cargo.toml").read_text(encoding="utf-8"))
                    native_package = native["package"]
                    if (
                        native_package["name"] != package["name"]
                        or native_package["version"] != package["version"]
                    ):
                        raise AssertionError(
                            f"Cargo/Zed metadata mismatch in {source}: "
                            f"{native_package['name']}@{native_package['version']} vs "
                            f"{full_name}@{package['version']}"
                        )

    def run_source_checks(self) -> None:
        self.log("\n== Run native syntax and unit checks before app canaries ==")
        node_lib = self.copy_source(
            self.ecosystems[0].lib_source, self.root / "checks/node-lib"
        )
        node_app = self.copy_source(
            self.ecosystems[0].app_source, self.root / "checks/node-app"
        )
        for source, commands in [
            (node_lib, [["node", "--check", "src/index.js"], ["node", "--test"]]),
            (node_app, [["node", "--check", "src/main.js"]]),
        ]:
            for command in commands:
                self.container(
                    self.node_image,
                    mounts=[Mount(source, "/src", readonly=True)],
                    command=command,
                    workdir="/src",
                    label=f"native check {source.name}",
                )

        rust_lib = self.copy_source(
            self.ecosystems[1].lib_source, self.root / "checks/rust-lib"
        )
        rust_app = self.copy_source(
            self.ecosystems[1].app_source, self.root / "checks/rust-app"
        )
        for source, run_tests in [(rust_lib, True), (rust_app, False)]:
            target = self.root / "checks/targets" / source.name
            target.mkdir(parents=True)
            self.container(
                self.rust_image,
                mounts=[Mount(source, "/src"), Mount(target, "/target")],
                command=["cargo", "+1.90.0", "fmt", "--check"],
                workdir="/src",
                env={"CARGO_TARGET_DIR": "/target"},
                label=f"rustfmt {source.name}",
            )
            if run_tests:
                self.container(
                    self.rust_image,
                    mounts=[Mount(source, "/src"), Mount(target, "/target")],
                    command=["cargo", "+1.90.0", "test", "--offline"],
                    workdir="/src",
                    env={"CARGO_TARGET_DIR": "/target"},
                    label=f"cargo test {source.name}",
                )

    def zed_container(
        self,
        ecosystem: Ecosystem,
        *,
        cwd: Path,
        home: Path,
        arguments: Sequence[str | Path],
        registry_readonly: bool,
        should_fail: bool = False,
        label: str | None = None,
    ) -> str:
        return self.container(
            ecosystem.image,
            mounts=[
                Mount(self.zed_dir, "/tooling", readonly=True),
                Mount(cwd, "/work"),
                Mount(self.registry, "/registry", readonly=registry_readonly),
                Mount(home, "/zed-home"),
            ],
            command=[
                "/tooling/zed",
                "--registry",
                "file:///registry",
                "--home",
                "/zed-home",
                *arguments,
            ],
            workdir="/work",
            env={"ZED_PKG_TOKEN": ""},
            should_fail=should_fail,
            label=label,
        )

    def publish_libraries(self) -> None:
        self.log("\n== Publish exact library fixtures into the private registry ==")
        for ecosystem in self.ecosystems:
            source = self.copy_source(
                ecosystem.lib_source, self.root / "publish" / f"{ecosystem.name}-lib"
            )
            home = self.root / "publish-homes" / ecosystem.name
            home.mkdir(parents=True)
            self.zed_container(
                ecosystem,
                cwd=source,
                home=home,
                arguments=["publish", "--skip-vcs-checks"],
                registry_readonly=False,
                label=f"publish {ecosystem.package_name}",
            )
        files = [path for path in self.registry.rglob("*") if path.is_file()]
        if not files:
            raise AssertionError("publishing produced an empty file registry")
        (self.diagnostics / "registry-files.txt").write_text(
            "\n".join(sorted(path.relative_to(self.registry).as_posix() for path in files))
            + "\n",
            encoding="utf-8",
        )

    def install(
        self,
        ecosystem: Ecosystem,
        app: Path,
        home: Path,
        mode: str,
        *,
        frozen: bool = False,
    ) -> None:
        arguments: list[str] = ["install"]
        if frozen:
            arguments.append("--frozen")
        arguments.extend(["--install-mode", mode])
        self.zed_container(
            ecosystem,
            cwd=app,
            home=home,
            arguments=arguments,
            registry_readonly=True,
            label=f"{ecosystem.name} {mode}{' frozen' if frozen else ''} install",
        )

    def symlink_control(self, ecosystem: Ecosystem) -> None:
        self.log(f"\n== {ecosystem.name}: store-backed symlink control ==")
        app = self.copy_source(
            ecosystem.app_source, self.root / ecosystem.name / "symlink-app"
        )
        home = self.fresh_shm(f"{ecosystem.name}-symlink-home")
        self.install(ecosystem, app, home, "symlink")

        package = app / ecosystem.package_relative
        if not package.is_symlink():
            raise AssertionError(f"expected store-backed package symlink: {package}")
        if ecosystem.adapter_relative:
            adapter = app / ecosystem.adapter_relative
            if not adapter.is_symlink():
                raise AssertionError(f"expected native adapter symlink: {adapter}")

        without_store = app.parent / "symlink-without-store"
        shutil.copytree(app, without_store, symlinks=True)

        if ecosystem.name == "node":
            command = ["node", "src/main.js"]
            environment: dict[str, str] = {}
            success_mount_readonly = True
        else:
            command = ["cargo", "+1.90.0", "run", "--quiet", "--offline"]
            environment = {"CARGO_TARGET_DIR": "/tmp/target"}
            success_mount_readonly = False

        success = self.container(
            ecosystem.image,
            mounts=[
                Mount(app, "/app", readonly=success_mount_readonly),
                Mount(home, "/zed-home", readonly=True),
            ],
            command=command,
            workdir="/app",
            env=environment,
            label=f"{ecosystem.name} symlink runtime with store",
        )
        if ecosystem.expected_output not in success:
            raise AssertionError(
                f"{ecosystem.name} symlink runtime missed expected output"
            )

        failure = self.container(
            ecosystem.image,
            mounts=[Mount(without_store, "/app", readonly=ecosystem.name == "node")],
            command=command,
            workdir="/app",
            env=environment,
            should_fail=True,
            label=f"{ecosystem.name} symlink runtime without store",
        )
        lowered = failure.lower()
        package_fragment = ecosystem.package_name.split("/")[-1]
        if package_fragment not in lowered and "no such file" not in lowered:
            raise AssertionError(
                f"{ecosystem.name} expected failure did not diagnose the missing dependency:\n{failure}"
            )

    def copy_contract(self, ecosystem: Ecosystem) -> Path:
        self.log(f"\n== {ecosystem.name}: independent cross-device copy contract ==")
        base = self.root / ecosystem.name
        seed = self.copy_source(ecosystem.app_source, base / "copy-seed")
        seed_home = self.fresh_shm(f"{ecosystem.name}-copy-seed-home")
        self.install(ecosystem, seed, seed_home, "copy")
        lock = (seed / ".zpkg.lock").read_bytes()

        copies: list[Path] = []
        homes: list[Path] = []
        for suffix in ["a", "b"]:
            app = self.copy_source(ecosystem.app_source, base / f"copy-{suffix}")
            (app / ".zpkg.lock").write_bytes(lock)
            home = self.fresh_shm(f"{ecosystem.name}-copy-{suffix}-home")
            self.install(ecosystem, app, home, "copy", frozen=True)
            if (app / ".zpkg.lock").read_bytes() != lock:
                raise AssertionError(
                    f"{ecosystem.name} frozen install rewrote the lockfile"
                )
            copies.append(app)
            homes.append(home)

        copy_a, copy_b = copies
        home_a, _home_b = homes
        roots_a = [copy_a / ecosystem.package_relative, copy_a / ".zed"]
        roots_b = [copy_b / ecosystem.package_relative, copy_b / ".zed"]
        if ecosystem.adapter_relative:
            roots_a.append(copy_a / ecosystem.adapter_relative)
            roots_b.append(copy_b / ecosystem.adapter_relative)
        for root in [*roots_a, *roots_b]:
            assert_no_symlinks(root)
        for left, right in zip(roots_a, roots_b, strict=True):
            if tree_manifest(left) != tree_manifest(right):
                raise AssertionError(
                    f"{ecosystem.name} independent copies differ: {left} vs {right}"
                )

        destination = copy_a / ecosystem.package_relative / ecosystem.representative_relative
        second_copy = copy_b / ecosystem.package_relative / ecosystem.representative_relative
        store_candidates = list(
            home_a.rglob(f"pkg/{ecosystem.representative_relative.as_posix()}")
        )
        if len(store_candidates) != 1:
            raise AssertionError(
                f"expected one {ecosystem.name} store source, found {store_candidates}"
            )
        store_source = store_candidates[0]
        original = store_source.read_bytes()
        if destination.read_bytes() != original or second_copy.read_bytes() != original:
            raise AssertionError(f"{ecosystem.name} copied bytes differ from the store")
        store_stat = store_source.stat()
        destination_stat = destination.stat()
        if store_stat.st_dev == destination_stat.st_dev:
            raise AssertionError(
                f"{ecosystem.name} store and destination did not cross filesystems: "
                f"device {store_stat.st_dev}"
            )
        if (store_stat.st_dev, store_stat.st_ino) == (
            destination_stat.st_dev,
            destination_stat.st_ino,
        ):
            raise AssertionError(f"{ecosystem.name} copy shares the store inode")

        adapter_original: bytes | None = None
        adapter_file: Path | None = None
        if ecosystem.adapter_relative:
            adapter_file = copy_a / ecosystem.adapter_relative / ecosystem.representative_relative
            adapter_original = adapter_file.read_bytes()
            if adapter_original != original:
                raise AssertionError(f"{ecosystem.name} adapter copy differs from package copy")

        destination.write_bytes(
            destination.read_bytes() + b"\n// DEN-591 consumer-owned mutation\n"
        )
        if store_source.read_bytes() != original:
            raise AssertionError(f"{ecosystem.name} mutation changed the store")
        if second_copy.read_bytes() != original:
            raise AssertionError(f"{ecosystem.name} mutation changed the second copy")
        if adapter_file and adapter_file.read_bytes() != adapter_original:
            raise AssertionError(f"{ecosystem.name} mutation changed the adapter copy")

        record = {
            "ecosystem": ecosystem.name,
            "store": str(store_source),
            "destination": str(destination),
            "store_device": store_stat.st_dev,
            "destination_device": destination_stat.st_dev,
            "store_inode": store_stat.st_ino,
            "destination_inode": destination_stat.st_ino,
            "lock_sha256": hashlib.sha256(lock).hexdigest(),
        }
        (self.diagnostics / f"{ecosystem.name}-copy-ownership.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return copy_a

    def remove_store_and_registry(self) -> None:
        self.log("\n== Remove all registry and store inputs before image builds ==")
        shutil.rmtree(self.registry)
        for path in self.shm_paths:
            if path.exists():
                shutil.rmtree(path)
        if self.registry.exists() or any(path.exists() for path in self.shm_paths):
            raise AssertionError("store or registry input survived the ownership boundary")

    def write_runtime_dockerfiles(self) -> tuple[Path, Path]:
        node = self.dockerfiles / "node.Dockerfile"
        node.write_text(
            """ARG NODE_IMAGE\n"
            "FROM ${NODE_IMAGE}\n"
            "WORKDIR /app\n"
            "COPY --chown=node:node . .\n"
            "USER node\n"
            "CMD [\"node\", \"src/main.js\"]\n""",
            encoding="utf-8",
        )
        rust = self.dockerfiles / "rust.Dockerfile"
        rust.write_text(
            """ARG RUST_IMAGE\n"
            "ARG DEBIAN_IMAGE\n"
            "FROM ${RUST_IMAGE} AS build\n"
            "WORKDIR /app\n"
            "COPY . .\n"
            "RUN cargo +1.90.0 build --release --offline\n"
            "FROM ${DEBIAN_IMAGE}\n"
            "WORKDIR /app\n"
            "COPY --from=build /app/target/release/rust-app /usr/local/bin/rust-app\n"
            "COPY --from=build --chown=65532:65532 /app/.vendor /app/.vendor\n"
            "COPY --from=build --chown=65532:65532 /app/.zed /app/.zed\n"
            "COPY --from=build --chown=65532:65532 /app/.zpkg.toml /app/.zpkg.toml\n"
            "COPY --from=build --chown=65532:65532 /app/.zpkg.lock /app/.zpkg.lock\n"
            "USER 65532:65532\n"
            "CMD [\"/usr/local/bin/rust-app\"]\n""",
            encoding="utf-8",
        )
        return node, rust

    def build_and_roundtrip_images(self, contexts: Mapping[str, Path]) -> None:
        self.log("\n== Cross Docker and OCI runtime boundaries ==")
        node_dockerfile, rust_dockerfile = self.write_runtime_dockerfiles()
        for context in contexts.values():
            (context / ".zed").mkdir(exist_ok=True)
            (context / ".dockerignore").write_text(
                ".git\ntarget\nDockerfile*\n", encoding="utf-8"
            )

        specifications = {
            "node": (
                contexts["node"],
                node_dockerfile,
                ["--build-arg", f"NODE_IMAGE={self.node_image}"],
                "zed-pkg-test/node-boundary:den-591",
                [
                    "sh",
                    "-euc",
                    'test "$(id -u)" -ne 0; exec node src/main.js',
                ],
                self.ecosystems[0].expected_output,
            ),
            "rust": (
                contexts["rust"],
                rust_dockerfile,
                [
                    "--build-arg",
                    f"RUST_IMAGE={self.rust_image}",
                    "--build-arg",
                    f"DEBIAN_IMAGE={self.debian_image}",
                ],
                "zed-pkg-test/rust-boundary:den-591",
                [
                    "sh",
                    "-euc",
                    'test "$(id -u)" -ne 0; exec /usr/local/bin/rust-app',
                ],
                self.ecosystems[1].expected_output,
            ),
        }

        for name, (context, dockerfile, build_args, tag, command, expected) in specifications.items():
            self.run(
                [
                    "docker",
                    "build",
                    "--pull=false",
                    "--network=none",
                    "--file",
                    dockerfile,
                    "--tag",
                    tag,
                    *build_args,
                    context,
                ],
                label=f"build {name} runtime image",
            )
            self.images.append(tag)
            self.capture_image_diagnostics(name, tag)
            direct = self.run_runtime(tag, command, label=f"{name} direct runtime")
            if expected not in direct:
                raise AssertionError(f"{name} direct runtime missed expected output")

            archive = self.oci / f"{name}.oci.tar"
            archive_ref = f"{name}-boundary"
            self.skopeo(
                [
                    "copy",
                    f"docker-daemon:{tag}",
                    f"oci-archive:/archives/{archive.name}:{archive_ref}",
                ],
                label=f"export {name} OCI archive",
            )
            if not archive.is_file() or archive.stat().st_size == 0:
                raise AssertionError(f"missing OCI archive: {archive}")
            self.skopeo(
                ["inspect", f"oci-archive:/archives/{archive.name}:{archive_ref}"],
                label=f"inspect {name} OCI archive",
            )

            self.run(["docker", "image", "rm", tag], label=f"remove {name} source image")
            imported = f"{tag}-oci"
            self.skopeo(
                [
                    "copy",
                    f"oci-archive:/archives/{archive.name}:{archive_ref}",
                    f"docker-daemon:{imported}",
                ],
                label=f"import {name} OCI archive",
            )
            self.images.append(imported)
            roundtrip = self.run_runtime(
                imported, command, label=f"{name} OCI-imported runtime"
            )
            if expected not in roundtrip:
                raise AssertionError(f"{name} OCI runtime missed expected output")

    def run_runtime(self, image: str, command: Sequence[str], *, label: str) -> str:
        return self.run(
            [
                "docker",
                "run",
                "--rm",
                "--read-only",
                "--network",
                "none",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=64m",
                "--entrypoint",
                command[0],
                image,
                *command[1:],
            ],
            label=label,
        )

    def skopeo(self, arguments: Sequence[str], *, label: str) -> str:
        return self.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--mount",
                "type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock",
                "--mount",
                f"type=bind,src={self.oci},dst=/archives",
                self.skopeo_image,
                *arguments,
            ],
            label=label,
        )

    def capture_image_diagnostics(self, name: str, image: str) -> None:
        inspect = self.run(
            ["docker", "image", "inspect", image],
            label=f"inspect {name} image",
        )
        history = self.run(
            ["docker", "history", "--no-trunc", image],
            label=f"history {name} image",
        )
        contents = self.run(
            [
                "docker",
                "run",
                "--rm",
                "--read-only",
                "--network",
                "none",
                "--entrypoint",
                "find",
                image,
                "/app",
                "-maxdepth",
                "8",
                "-printf",
                "%y %p -> %l\\n",
            ],
            label=f"list {name} image contents",
        )
        (self.diagnostics / f"{name}-image-inspect.json").write_text(
            inspect, encoding="utf-8"
        )
        (self.diagnostics / f"{name}-image-history.txt").write_text(
            history, encoding="utf-8"
        )
        (self.diagnostics / f"{name}-image-contents.txt").write_text(
            contents, encoding="utf-8"
        )
        lowered = contents.lower()
        for forbidden in ["zed-home", "/registry", "/src/zed-cli", "/src/zed-interfaces"]:
            if forbidden in lowered:
                raise AssertionError(
                    f"{name} runtime image contains forbidden boundary input {forbidden}"
                )

    def run_all(self) -> None:
        for image in [
            self.node_image,
            self.rust_image,
            self.debian_image,
            self.skopeo_image,
        ]:
            self.run(["docker", "pull", image], label="pull immutable image")
        self.validate_metadata()
        self.run_source_checks()
        self.publish_libraries()
        for ecosystem in self.ecosystems:
            self.symlink_control(ecosystem)
        contexts = {
            ecosystem.name: self.copy_contract(ecosystem)
            for ecosystem in self.ecosystems
        }
        self.remove_store_and_registry()
        self.build_and_roundtrip_images(contexts)

    def failure_diagnostics(self, error: BaseException) -> None:
        self.diagnostics.mkdir(parents=True, exist_ok=True)
        (self.diagnostics / "failure.txt").write_text(
            f"{type(error).__name__}: {error}\n", encoding="utf-8"
        )
        for name, command in [
            ("docker-images.txt", ["docker", "images", "--digests", "--no-trunc"]),
            ("docker-ps.txt", ["docker", "ps", "-a", "--no-trunc"]),
        ]:
            try:
                output = subprocess.run(
                    command,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                ).stdout
                (self.diagnostics / name).write_text(output or "", encoding="utf-8")
            except OSError:
                pass

    def cleanup(self) -> None:
        for path in self.shm_paths:
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)


def assert_no_symlinks(root: Path) -> None:
    if not root.exists():
        raise AssertionError(f"missing installed output: {root}")
    if root.is_symlink():
        raise AssertionError(f"installed root is a symlink: {root}")
    for current, directories, files in os.walk(root, followlinks=False):
        base = Path(current)
        for name in [*directories, *files]:
            candidate = base / name
            if candidate.is_symlink():
                raise AssertionError(f"copy tree contains a symlink: {candidate}")


def tree_manifest(root: Path) -> list[tuple[str, int, str]]:
    assert_no_symlinks(root)
    records: list[tuple[str, int, str]] = []
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        records.append(
            (
                path.relative_to(root).as_posix(),
                stat.S_IMODE(path.stat().st_mode),
                digest,
            )
        )
    return records


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", type=Path, required=True)
    parser.add_argument("--node-lib", type=Path, required=True)
    parser.add_argument("--node-app", type=Path, required=True)
    parser.add_argument("--rust-lib", type=Path, required=True)
    parser.add_argument("--rust-app", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--node-image", required=True)
    parser.add_argument("--rust-image", required=True)
    parser.add_argument("--debian-image", required=True)
    parser.add_argument("--skopeo-image", required=True)
    return parser.parse_args()


def main() -> None:
    harness = Harness(parse_args())
    try:
        harness.run_all()
    except BaseException as error:
        harness.failure_diagnostics(error)
        raise
    finally:
        harness.cleanup()


if __name__ == "__main__":
    main()
