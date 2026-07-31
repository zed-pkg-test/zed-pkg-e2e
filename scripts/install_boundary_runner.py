#!/usr/bin/env python3
"""Run DEN-591 canaries with host Skopeo and semantic OCI checks.

The core harness keeps containerized Zed and application execution isolated.
OCI archive transport uses the runner's exact-version Skopeo package. Rust
formatting remains owned by each fixture repository's CI; this boundary suite
keeps offline compilation/tests without requiring an optional rustup component
inside the pinned runtime image.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Mapping, Sequence

import install_boundary_canaries as canaries


def host_skopeo(
    self: canaries.Harness,
    args: Sequence[str],
    *,
    label: str,
) -> str:
    translated: list[str] = []
    archive_prefix = "oci-archive:/archives/"
    for argument in args:
        if argument.startswith(archive_prefix):
            argument = f"oci-archive:{self.oci_dir}/{argument[len(archive_prefix):]}"
        translated.append(argument)
    return self.run(
        ["skopeo", "--insecure-policy", *translated],
        env={
            "REGISTRY_AUTH_FILE": os.devnull,
            "XDG_RUNTIME_DIR": str(self.root / "skopeo-runtime"),
        },
        label=label,
    )


def native_checks_without_optional_rustfmt(self: canaries.Harness) -> None:
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
                mounts=[canaries.Mount(source, "/src", readonly=True)],
                command=command,
                workdir="/src",
                label=f"native Node check {source.name}",
            )

    rust_lib = self.copy_source(
        self.ecosystems[1].lib_source, self.root / "checks/rust-lib"
    )
    target = self.root / "checks/targets/rust-lib"
    target.mkdir(parents=True)
    self.container(
        self.rust_image,
        mounts=[
            canaries.Mount(rust_lib, "/src"),
            canaries.Mount(target, "/target"),
        ],
        command=["cargo", "+1.90.0", "test", "--offline"],
        workdir="/src",
        env={"CARGO_TARGET_DIR": "/target"},
        label="cargo test rust-lib",
    )


def semantic_image_contract(raw: str) -> dict[str, object]:
    image = json.loads(raw)[0]
    config = image["Config"]
    return {
        "architecture": image["Architecture"],
        "os": image["Os"],
        "user": config.get("User") or "",
        "working_dir": config.get("WorkingDir") or "",
        "entrypoint": config.get("Entrypoint") or [],
        "cmd": config.get("Cmd") or [],
        "env": sorted(config.get("Env") or []),
    }


def build_and_roundtrip_semantically(
    self: canaries.Harness, contexts: Mapping[str, Path]
) -> None:
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
        source_inspect = self.run(
            ["docker", "image", "inspect", tag], label=f"inspect source {name} image"
        )
        source_contract = semantic_image_contract(source_inspect)
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
        imported_inspect = self.run(
            ["docker", "image", "inspect", imported],
            label=f"inspect imported {name} image",
        )
        imported_contract = semantic_image_contract(imported_inspect)
        if imported_contract != source_contract:
            raise AssertionError(
                f"{name} OCI round-trip changed runtime configuration: "
                f"{source_contract!r} != {imported_contract!r}"
            )
        self.image_diagnostics(f"{name}-oci", imported)
        roundtrip = self.runtime(imported, command, label=f"{name} OCI-imported runtime")
        if expected not in roundtrip:
            raise AssertionError(f"{name} OCI runtime output mismatch")


def run_all_without_remote_skopeo_image(self: canaries.Harness) -> None:
    (self.root / "skopeo-runtime").mkdir(parents=True, exist_ok=True)
    for image in [self.node_image, self.rust_image, self.debian_image]:
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


canaries.Harness.skopeo = host_skopeo
canaries.Harness.native_checks = native_checks_without_optional_rustfmt
canaries.Harness.build_and_roundtrip = build_and_roundtrip_semantically
canaries.Harness.run_all = run_all_without_remote_skopeo_image


if __name__ == "__main__":
    canaries.main()
