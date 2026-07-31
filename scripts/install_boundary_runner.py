#!/usr/bin/env python3
"""Run DEN-591 canaries with host Skopeo and hermetic package checks.

The core harness keeps containerized Zed and application execution isolated.
OCI archive transport uses the runner's exact-version Skopeo package. Rust
formatting remains owned by each fixture repository's CI; this boundary suite
keeps offline compilation/tests without requiring an optional rustup component
inside the pinned runtime image.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence

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
canaries.Harness.run_all = run_all_without_remote_skopeo_image


if __name__ == "__main__":
    canaries.main()
