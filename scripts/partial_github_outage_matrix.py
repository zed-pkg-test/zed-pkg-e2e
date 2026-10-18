#!/usr/bin/env python3
"""Isolated priority-policy fault harness for source fallback. No real CLI/network.

The follow-up gate must route these injected failures into a reviewed zed-cli.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile

CANDIDATE_SHA = "388161f681bac8ae704a22a593280ceed6cc4fa9"
ORDER = ("github_release", "ghcr", "tag_archive", "r2_mirror")
SENTINEL = "FAIL_IF_USED"


class SourceFailure(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def resolve(surfaces, *, r2_enabled=False):
    if set(surfaces) != set(ORDER) | {"rest_api"}:
        raise SourceFailure("invalid_fixture")
    attempts = []
    for source in ORDER:
        if source == "r2_mirror" and not r2_enabled:
            continue
        state = surfaces[source]
        if state == SENTINEL:
            raise SourceFailure("forbidden_lower_priority_used")
        if state == "available":
            return {"source": source, "attempted": attempts + [source], "outcome": "resolved"}
        if state not in ("unavailable", "degraded"):
            raise SourceFailure("invalid_fixture")
        attempts.append(source)
    raise SourceFailure("sources_exhausted")


def scenarios():
    blocked = lambda **kwargs: {
        "rest_api": "unavailable", **dict.fromkeys(ORDER, "unavailable"), **kwargs,
    }
    return {
        "rest_degraded_tag_works": (
            blocked(rest_api="degraded", github_release="unavailable", ghcr="unavailable",
                    tag_archive="available", r2_mirror=SENTINEL), "tag_archive", False),
        "release_unavailable_ghcr_works": (
            blocked(github_release="unavailable", ghcr="available",
                    tag_archive=SENTINEL, r2_mirror=SENTINEL), "ghcr", False),
        "release_ghcr_down_tag_works": (
            blocked(github_release="unavailable", ghcr="unavailable",
                    tag_archive="available", r2_mirror=SENTINEL), "tag_archive", False),
        "archive_down_release_works": (
            blocked(rest_api="available", github_release="available",
                    ghcr=SENTINEL, tag_archive="unavailable", r2_mirror=SENTINEL),
            "github_release", False),
        "github_down_opt_in_r2": (
            blocked(r2_mirror="available"), "r2_mirror", True),
        "all_sources_down": (
            blocked(), "sources_exhausted", True),
    }


def run_matrix():
    if any(os.environ.get(k) for k in (
        "GITHUB_TOKEN", "GH_TOKEN", "ZED_PKG_GITHUB_TOKEN",
        "AWS_ACCESS_KEY_ID", "CLOUDFLARE_API_TOKEN",
    )):
        raise SourceFailure("ambient_credential_detected")
    results = []
    for name, (surfaces, expected, r2_enabled) in scenarios().items():
        try:
            result = resolve(surfaces, r2_enabled=r2_enabled)
        except SourceFailure as failure:
            actual = failure.code
            attempted = []
        else:
            actual = result["source"]
            attempted = result["attempted"]
            assert len(attempted) <= 4
            assert attempted[-1] == actual
        if actual != expected:
            raise SourceFailure("unexpected_source")
        results.append({"scenario": name, "outcome": actual, "attempted": attempted})
    return {"candidate_sha": CANDIDATE_SHA, "kind": "isolated-policy-model",
            "credential_free": True, "matrix": results}


if __name__ == "__main__":
    print(json.dumps(run_matrix(), sort_keys=True, separators=(",", ":")))
