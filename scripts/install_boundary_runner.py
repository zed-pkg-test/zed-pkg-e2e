#!/usr/bin/env python3
"""Run DEN-591 canaries with the host's exact-version Skopeo package.

The core harness keeps containerized Zed and application execution isolated.
This launcher deliberately routes only OCI archive transport through the
runner-installed, version-checked Ubuntu package. That avoids relying on Quay
retaining an old image manifest while preserving an independent OCI tool.
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
canaries.Harness.run_all = run_all_without_remote_skopeo_image


if __name__ == "__main__":
    canaries.main()
