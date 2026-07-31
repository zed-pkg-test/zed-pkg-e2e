#!/usr/bin/env python3
"""Pinned Node/Rust Docker and OCI materialization canaries for DEN-591."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


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
    package_version: str
    package_path: Path
    representative: Path
    expected_output: str
    adapter_path: Path | None = None


class Harness:
    def __init__(self, args: argparse.Namespace) -> None:
        self.zed = args.zed.resolve()
        self.zed_dir = self.zed.parent
        self.root = args.work_root.resolve()
        self.registry = self.root / "registry"
        self.diagnostics = self.root / "diagnostics"
        self.log_file = self.diagnostics / "install-boundaries.log"
        self.oci_dir = self.root / "oci"
        self.dockerfiles = self.root / "dockerfiles"
        self.node_image = args.node_image
        self.rust_image = args.rust_image
        self.debian_image = args.debian_image
        self.skopeo_image = args.skopeo_image
        self.uid = os.getuid()
        self.gid = os.getgid()
        self.shm_homes: list[Path] = []

        if self.root.exists():
            raise RuntimeError(f"work root must be fresh: {self.root}")
        if not self.zed.is_file():
            raise RuntimeError(f"zed binary not found: {self.zed}")
        for directory in [self.registry, self.diagnostics, self.oci_dir, self.dockerfiles]:
            directory.mkdir(parents=True, exist_ok=True)
        self.log_file.write_text("", encoding="utf-8")

        self.ecosystems = [
            Ecosystem(
                name="node",
                image=self.node_image,
                lib_source=args.node_lib.resolve(),
                app_source=args.node_app.resolve(),
                package_name="zed-pkg-test/node-lib",
                package_version="1.0.0",
                package_path=Path(".vendor/.zed/zed-pkg-test/node-lib"),
                adapter_path=Path("node_modules/@zed-pkg-test/node-lib"),
                representative=Path("src/index.js"),
                expected_output="OK: zed-sourced dep resolved alongside npm",
            ),
            Ecosystem(
                name="rust",
                image=self.rust_image,
                lib_source=args.rust_lib.resolve(),
                app_source=args.rust_app.resolve(),
                package_name="zed-pkg-test/rust-lib",
                package_version="1.0.0",
                package_path=Path(".vendor/.zed/zed-pkg-test/rust-lib"),
                representative=Path("src/lib.rs"),
                expected_output="OK: zed-sourced crate resolved via cargo path dep",
            ),
        ]

    def log(self, message: str) -> None:
        print(message, flush=True)
        with self.log_file.open("a", encoding="utf-8") as handle:
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
        argv = [str(item) for item in command]
        rendered = " ".join(shlex.quote(item) for item in argv)
        prefix = f"[{label}] " if label else ""
        self.log(f"\n{prefix}$ (cd {cwd or Path.cwd()} && {rendered})")
        process_env = os.environ.copy()
        if env:
            process_env.update(env)
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=process_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        output = completed.stdout or ""
        if output:
            print(output, end="" if output.endswith("\n") else "\n", flush=True)
            with self.log_file.open("a", encoding="utf-8") as handle:
                handle.write(output)
                if not output.endswith("\n"):
                    handle.write("\n")
        if should_fail:
            if completed.returncode == 0:
                raise AssertionError(f"command unexpectedly succeeded: {rendered}")
        elif completed.returncode != 0:
            raise RuntimeError(
                f"command failed with exit code {completed.returncode}: {rendered}"
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
        read_only: bool = False,
        tmpfs: bool = False,
        entrypoint: str | None = None,
        label: str | None = None,
    ) -> str:
        argv: list[str | Path] = ["docker", "run", "--rm", "--network", "none"]
        if user:
            argv.extend(["--user", f"{self.uid}:{self.gid}"])
        if read_only:
            argv.append("--read-only")
        if tmpfs:
            argv.extend(["--tmpfs", "/tmp:rw,noexec,nosuid,size=64m"])
        if entrypoint:
            argv.extend(["--entrypoint", entrypoint])
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

    def copy_source(self, source: Path, destination: Path) -> Path:
        if destination.exists():
            shutil.rmtree(destination)

        def ignored(_directory: str, names: list[str]) -> set[str]:
            generated = {
                ".git",
                ".vendor",
                ".zed",
                ".zpkg.lock",
                "Cargo.lock",
                "node_modules",
                "target",
                "zed_modules",
            }
            return generated.intersection(names)

        shutil.copytree(source, destination, symlinks=True, ignore=ignored)
        return destination

    def fresh_home(self, name: str) -> Path:
        home = Path("/dev/shm") / f"zed-den591-{os.getpid()}-{name}"
        if home.exists():
            shutil.rmtree(home)
        home.mkdir(parents=True)
        self.shm_homes.append(home)
        return home

    def zed_command(
        self,
        ecosystem: Ecosystem,
        *,
        cwd: Path,
        home: Path,
        args: Sequence[str | Path],
        registry_readonly: bool,
        should_fail: bool = False,
        label: str,
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
                *args,
            ],
            workdir="/work",
            env={"ZED_PKG_TOKEN": ""},
            should_fail=should_fail,
            label=label,
        )

    def validate_metadata(self) -> None:
        self.log("\n== Validate Zed/native identities and fixture dependency graph ==")
        for ecosystem in self.ecosystems:
            for role, source in [("lib", ecosystem.lib_source), ("app", ecosystem.app_source)]:
                zed_manifest = tomllib.loads(
                    (source / ".zpkg.toml").read_text(encoding="utf-8")
                )
                package = zed_manifest["package"]
                full_name = f"{package['org']}/{package['name']}"
                expected_name = (
                    ecosystem.package_name
                    if role == "lib"
                    else f"zed-pkg-test/{ecosystem.name}-app"
                )
                expected_version = ecosystem.package_version if role == "lib" else "0.1.0"
                if full_name != expected_name or package["version"] != expected_version:
                    raise AssertionError(
                        f"unexpected {ecosystem.name} {role} identity: "
                        f"{full_name}@{package['version']}"
                    )
                if role == "app":
                    dependencies = zed_manifest.get("dependencies") or {}
                    if set(dependencies) != {ecosystem.package_name}:
                        raise AssertionError(
                            f"unexpected {ecosystem.name} app dependency graph: {dependencies}"
                        )
                if ecosystem.name == "node":
                    native = json.loads(
                        (source / "package.json").read_text(encoding="utf-8")
                    )
                    if (
                        native["name"].split("/")[-1] != package["name"]
                        or native["version"] != package["version"]
                    ):
                        raise AssertionError(f"Node/Zed metadata mismatch in {source}")
                else:
                    native = tomllib.loads(
                        (source / "Cargo.toml").read_text(encoding="utf-8")
                    )["package"]
                    if (
                        native["name"] != package["name"]
                        or native["version"] != package["version"]
                    ):
                        raise AssertionError(f"Cargo/Zed metadata mismatch in {source}")

    def native_checks(self) -> None:
        self.log("\n== Run package syntax/unit checks before downstream canaries ==")
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
                    label=f"native Node check {source.name}",
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

    def publish_libraries(self) -> None:
        self.log("\n== Publish exact libraries into a private file registry ==")
        for ecosystem in self.ecosystems:
            source = self.copy_source(
                ecosystem.lib_source, self.root / "publish" / f"{ecosystem.name}-lib"
            )
            home = self.root / "publish-homes" / ecosystem.name
            home.mkdir(parents=True)
            self.zed_command(
                ecosystem,
                cwd=source,
                home=home,
                args=["publish", "--skip-vcs-checks"],
                registry_readonly=False,
                label=f"publish {ecosystem.package_name}",
            )
        registry_files = sorted(
            path.relative_to(self.registry).as_posix()
            for path in self.registry.rglob("*")
            if path.is_file()
        )
        if not registry_files:
            raise AssertionError("publishing produced an empty registry")
        (self.diagnostics / "registry-files.txt").write_text(
            "\n".join(registry_files) + "\n", encoding="utf-8"
        )

    def install(
        self,
        ecosystem: Ecosystem,
        *,
        app: Path,
        home: Path,
        mode: str,
        frozen: bool = False,
    ) -> None:
        args: list[str] = ["install"]
        if frozen:
            args.append("--frozen")
        args.extend(["--install-mode", mode])
        self.zed_command(
            ecosystem,
            cwd=app,
            home=home,
            args=args,
            registry_readonly=True,
            label=f"{ecosystem.name} {mode}{' frozen' if frozen else ''} install",
        )

    def runtime_command(self, ecosystem: Ecosystem) -> tuple[list[str], dict[str, str]]:
        if ecosystem.name == "node":
            return ["node", "src/main.js"], {}
        return ["cargo", "+1.90.0", "run", "--quiet", "--offline"], {
            "CARGO_TARGET_DIR": "/tmp/target"
        }

    def symlink_control(self, ecosystem: Ecosystem) -> None:
        self.log(f"\n== {ecosystem.name}: store-backed symlink control ==")
        app = self.copy_source(
            ecosystem.app_source, self.root / ecosystem.name / "symlink-app"
        )
        home = self.fresh_home(f"{ecosystem.name}-symlink")
        self.install(ecosystem, app=app, home=home, mode="symlink")

        package = app / ecosystem.package_path
        if not package.is_symlink():
            raise AssertionError(f"expected package symlink: {package}")
        if ecosystem.adapter_path:
            adapter = app / ecosystem.adapter_path
            if not adapter.is_symlink():
                raise AssertionError(f"expected adapter symlink: {adapter}")

        without_store = app.parent / "symlink-without-store"
        shutil.copytree(app, without_store, symlinks=True)
        command, environment = self.runtime_command(ecosystem)
        success = self.container(
            ecosystem.image,
            mounts=[
                Mount(app, "/app", readonly=ecosystem.name == "node"),
                Mount(home, "/zed-home", readonly=True),
            ],
            command=command,
            workdir="/app",
            env=environment,
            label=f"{ecosystem.name} symlink runtime with store",
        )
        if ecosystem.expected_output not in success:
            raise AssertionError(f"{ecosystem.name} mounted-store runtime output mismatch")

        failure = self.container(
            ecosystem.image,
            mounts=[
                Mount(without_store, "/app", readonly=ecosystem.name == "node")
            ],
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
                f"{ecosystem.name} missing-store failure was not diagnostic:\n{failure}"
            )

    def copy_contract(self, ecosystem: Ecosystem) -> Path:
        self.log(f"\n== {ecosystem.name}: independent cross-device copy contract ==")
        base = self.root / ecosystem.name
        seed = self.copy_source(ecosystem.app_source, base / "copy-seed")
        seed_home = self.fresh_home(f"{ecosystem.name}-seed")
        self.install(ecosystem, app=seed, home=seed_home, mode="copy")
        lock_bytes = (seed / ".zpkg.lock").read_bytes()

        copies: list[Path] = []
        homes: list[Path] = []
        for suffix in ["a", "b"]:
            app = self.copy_source(ecosystem.app_source, base / f"copy-{suffix}")
            (app / ".zpkg.lock").write_bytes(lock_bytes)
            home = self.fresh_home(f"{ecosystem.name}-{suffix}")
            self.install(ecosystem, app=app, home=home, mode="copy", frozen=True)
            if (app / ".zpkg.lock").read_bytes() != lock_bytes:
                raise AssertionError(f"{ecosystem.name} frozen install rewrote lockfile")
            copies.append(app)
            homes.append(home)

        copy_a, copy_b = copies
        home_a = homes[0]
        for app in copies:
            assert_no_symlinks(app / ecosystem.package_path)
            assert_no_symlinks(app / ".zed")
            bin_dir = app / ".vendor/.zed/.bin"
            if bin_dir.exists():
                assert_no_symlinks(bin_dir)
            if ecosystem.adapter_path:
                assert_no_symlinks(app / ecosystem.adapter_path)

        if tree_manifest(copy_a / ecosystem.package_path) != tree_manifest(
            copy_b / ecosystem.package_path
        ):
            raise AssertionError(f"{ecosystem.name} package copies are not deterministic")
        if ecosystem.adapter_path and tree_manifest(
            copy_a / ecosystem.adapter_path
        ) != tree_manifest(copy_b / ecosystem.adapter_path):
            raise AssertionError(f"{ecosystem.name} adapter copies are not deterministic")

        destination = copy_a / ecosystem.package_path / ecosystem.representative
        second = copy_b / ecosystem.package_path / ecosystem.representative
        store_sources = list(
            home_a.glob(f"store/v1/*/*/pkg/{ecosystem.representative.as_posix()}")
        )
        if len(store_sources) != 1:
            raise AssertionError(
                f"expected one {ecosystem.name} store source, found {store_sources}"
            )
        store_source = store_sources[0]
        original = store_source.read_bytes()
        if destination.read_bytes() != original or second.read_bytes() != original:
            raise AssertionError(f"{ecosystem.name} copied bytes differ from store")

        store_stat = store_source.stat()
        destination_stat = destination.stat()
        if store_stat.st_dev == destination_stat.st_dev:
            raise AssertionError(
                f"{ecosystem.name} copy did not cross filesystems: {store_stat.st_dev}"
            )
        if (store_stat.st_dev, store_stat.st_ino) == (
            destination_stat.st_dev,
            destination_stat.st_ino,
        ):
            raise AssertionError(f"{ecosystem.name} copy shares store inode")

        adapter_file: Path | None = None
        adapter_original: bytes | None = None
        if ecosystem.adapter_path:
            adapter_file = copy_a / ecosystem.adapter_path / ecosystem.representative
            adapter_original = adapter_file.read_bytes()
            if adapter_original != original:
                raise AssertionError("Node adapter bytes differ from package copy")

        destination.write_bytes(
            destination.read_bytes() + b"\n// DEN-591 consumer-owned mutation\n"
        )
        if store_source.read_bytes() != original:
            raise AssertionError(f"{ecosystem.name} mutation changed store")
        if second.read_bytes() != original:
            raise AssertionError(f"{ecosystem.name} mutation changed second copy")
        if adapter_file and adapter_file.read_bytes() != adapter_original:
            raise AssertionError("Node package mutation changed adapter copy")

        record = {
            "ecosystem": ecosystem.name,
            "store": str(store_source),
            "destination": str(destination),
            "store_device": store_stat.st_dev,
            "destination_device": destination_stat.st_dev,
            "store_inode": store_stat.st_ino,
            "destination_inode": destination_stat.st_ino,
            "lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
        }
        (self.diagnostics / f"{ecosystem.name}-ownership.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return copy_a

    def remove_boundary_inputs(self) -> None:
        self.log("\n== Remove registry and every Zed home before image construction ==")
        shutil.rmtree(self.registry)
        publish_homes = self.root / "publish-homes"
        if publish_homes.exists():
            shutil.rmtree(publish_homes)
        for home in self.shm_homes:
            if home.exists():
                shutil.rmtree(home)
        if (
            self.registry.exists()
            or publish_homes.exists()
            or any(home.exists() for home in self.shm_homes)
        ):
            raise AssertionError("registry or store input survived boundary removal")

    def runtime_dockerfiles(self) -> tuple[Path, Path]:
        node = self.dockerfiles / "node.Dockerfile"
        node.write_text(
            "ARG NODE_IMAGE\n"
            "FROM ${NODE_IMAGE}\n"
            "WORKDIR /app\n"
            "COPY --chown=node:node . .\n"
            "USER node\n"
            'CMD ["node", "src/main.js"]\n',
            encoding="utf-8",
        )
        rust = self.dockerfiles / "rust.Dockerfile"
        rust.write_text(
            "ARG RUST_IMAGE\n"
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
            'CMD ["/usr/local/bin/rust-app"]\n',
            encoding="utf-8",
        )
        return node, rust

    def runtime(self, image: str, command: Sequence[str], *, label: str) -> str:
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

    def skopeo(self, args: Sequence[str], *, label: str) -> str:
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
                f"type=bind,src={self.oci_dir},dst=/archives",
                self.skopeo_image,
                *args,
            ],
            label=label,
        )

    def image_diagnostics(self, name: str, image: str) -> None:
        inspect = self.run(["docker", "image", "inspect", image], label=f"inspect {name}")
        history = self.run(
            ["docker", "history", "--no-trunc", image], label=f"history {name}"
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
            label=f"contents {name}",
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
                raise AssertionError(f"{name} image contains forbidden input {forbidden}")

    def build_and_roundtrip(self, contexts: Mapping[str, Path]) -> None:
        self.log("\n== Build fresh runtimes and round-trip through OCI archives ==")
        node_dockerfile, rust_dockerfile = self.runtime_dockerfiles()
        for context in contexts.values():
            (context / ".zed").mkdir(exist_ok=True)
            (context / ".dockerignore").write_text(
                ".git\ntarget\nDockerfile*\n", encoding="utf-8"
            )

        specs = {
            "node": {
                "context": contexts["node"],
                "dockerfile": node_dockerfile,
                "args": ["--build-arg", f"NODE_IMAGE={self.node_image}"],
                "tag": "zed-pkg-test/node-boundary:den-591",
                "command": ["sh", "-euc", 'test "$(id -u)" -ne 0; exec node src/main.js'],
                "expected": self.ecosystems[0].expected_output,
            },
            "rust": {
                "context": contexts["rust"],
                "dockerfile": rust_dockerfile,
                "args": [
                    "--build-arg",
                    f"RUST_IMAGE={self.rust_image}",
                    "--build-arg",
                    f"DEBIAN_IMAGE={self.debian_image}",
                ],
                "tag": "zed-pkg-test/rust-boundary:den-591",
                "command": [
                    "sh",
                    "-euc",
                    'test "$(id -u)" -ne 0; exec /usr/local/bin/rust-app',
                ],
                "expected": self.ecosystems[1].expected_output,
            },
        }

        for name, raw in specs.items():
            context = raw["context"]
            dockerfile = raw["dockerfile"]
            build_args = raw["args"]
            tag = str(raw["tag"])
            command = raw["command"]
            expected = str(raw["expected"])
            assert isinstance(context, Path)
            assert isinstance(dockerfile, Path)
            assert isinstance(build_args, list)
            assert isinstance(command, list)

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
                label=f"build {name} image",
            )
            source_id = self.run(
                ["docker", "image", "inspect", "--format", "{{.Id}}", tag],
                label=f"identify {name} image",
            ).strip()
            self.image_diagnostics(name, tag)
            direct = self.runtime(tag, command, label=f"{name} direct runtime")
            if expected not in direct:
                raise AssertionError(f"{name} direct runtime output mismatch")

            archive = self.oci_dir / f"{name}.oci.tar"
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
            imported_id = self.run(
                ["docker", "image", "inspect", "--format", "{{.Id}}", imported],
                label=f"identify imported {name} image",
            ).strip()
            if imported_id != source_id:
                raise AssertionError(
                    f"{name} OCI round-trip changed image identity: "
                    f"{source_id} != {imported_id}"
                )
            roundtrip = self.runtime(
                imported, command, label=f"{name} OCI-imported runtime"
            )
            if expected not in roundtrip:
                raise AssertionError(f"{name} OCI runtime output mismatch")

    def run_all(self) -> None:
        for image in [
            self.node_image,
            self.rust_image,
            self.debian_image,
            self.skopeo_image,
        ]:
            self.run(["docker", "pull", image], label="pull pinned image")
        self.validate_metadata()
        self.native_checks()
        self.publish_libraries()
        for ecosystem in self.ecosystems:
            self.symlink_control(ecosystem)
        contexts = {
            ecosystem.name: self.copy_contract(ecosystem)
            for ecosystem in self.ecosystems
        }
        self.remove_boundary_inputs()
        self.build_and_roundtrip(contexts)

    def capture_failure(self, error: BaseException) -> None:
        self.diagnostics.mkdir(parents=True, exist_ok=True)
        (self.diagnostics / "failure.txt").write_text(
            f"{type(error).__name__}: {error}\n", encoding="utf-8"
        )
        for filename, command in [
            ("docker-images.txt", ["docker", "images", "--digests", "--no-trunc"]),
            ("docker-containers.txt", ["docker", "ps", "-a", "--no-trunc"]),
        ]:
            output = subprocess.run(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            ).stdout
            (self.diagnostics / filename).write_text(output or "", encoding="utf-8")

    def cleanup(self) -> None:
        for home in self.shm_homes:
            if home.exists():
                shutil.rmtree(home, ignore_errors=True)


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
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        records.append(
            (
                path.relative_to(root).as_posix(),
                stat.S_IMODE(path.stat().st_mode),
                hashlib.sha256(path.read_bytes()).hexdigest(),
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
        harness.capture_failure(error)
        raise
    finally:
        harness.cleanup()


if __name__ == "__main__":
    main()
