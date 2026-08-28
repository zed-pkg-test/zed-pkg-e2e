#!/usr/bin/env python3
"""Bounded types, limits, and error taxonomy for DEN-2957."""

from __future__ import annotations

import argparse
import base64
import configparser
import dataclasses
import hashlib
import html
import io
import ipaddress
import json
import os
import re
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

INVENTORY_SCHEMA = "zpkg/github-dependency-inventory/v1"
FIXTURE_SCHEMA = "zpkg/github-api-fixture/v1"
DEFAULT_API_BASE = "https://api.github.com"
ALLOWED_INCLUDES = ("git-submodule", "nix", "zed")
EXPECTED_MANIFESTS = {
    "zed": (".zpkg.toml", ".zpkg.lock"),
    "git-submodule": (".gitmodules",),
    "nix": ("flake.nix", "flake.lock", "nix/sources.json", "npins/sources.json"),
}
TRANSIENT_STATUSES = frozenset({429, 502, 503, 504})
HTTP_MAX_ERROR_BODY = 4096
MAX_REPOSITORY_PATH_BYTES = 4096

class InventoryError(Exception):
    """Base class for controlled command failures."""


class InputError(InventoryError):
    """Invalid caller or fixture input."""


class LimitError(InventoryError):
    """A hard resource limit was exceeded; no partial output may be published."""


class ApiError(InventoryError):
    def __init__(self, status: int, path: str, code: str = "github_http_error") -> None:
        self.status = status
        self.path = path
        self.code = code
        super().__init__(f"GitHub API request failed with HTTP {status} at {path}")


class ParseFailure(InventoryError):
    """One repository source could not be parsed."""


@dataclasses.dataclass(frozen=True)
class Limits:
    max_repositories: int = 1_000
    max_nodes: int = 20_000
    max_edges: int = 40_000
    max_requests: int = 20_000
    max_response_bytes: int = 4 * 1024 * 1024
    max_total_response_bytes: int = 128 * 1024 * 1024
    max_manifest_bytes: int = 2 * 1024 * 1024
    max_field_bytes: int = 16 * 1024
    max_tree_entries: int = 50_000
    max_json_depth: int = 64
    max_seconds: float = 120.0
    max_retries: int = 2

    def validate(self) -> None:
        for field in dataclasses.fields(self):
            value = getattr(self, field.name)
            if isinstance(value, bool):
                raise InputError(f"{field.name} must be numeric")
            if field.name == "max_seconds":
                if not isinstance(value, (int, float)) or value <= 0:
                    raise InputError("max_seconds must be greater than zero")
                continue
            if not isinstance(value, int):
                raise InputError(f"{field.name} must be an integer")
            if field.name == "max_retries":
                if value < 0:
                    raise InputError("max_retries must not be negative")
            elif value <= 0:
                raise InputError(f"{field.name} must be greater than zero")
        if self.max_retries > 8:
            raise InputError("max_retries must not exceed 8")

    def as_dict(self) -> dict[str, int | float]:
        return dataclasses.asdict(self)


class Budget:
    def __init__(self, limits: Limits, clock: Callable[[], float] = time.monotonic) -> None:
        limits.validate()
        self.limits = limits
        self.clock = clock
        self.started = clock()
        self.requests = 0
        self.response_bytes = 0

    def check_time(self) -> None:
        elapsed = self.clock() - self.started
        if elapsed > self.limits.max_seconds:
            raise LimitError(
                f"inventory time limit exceeded ({elapsed:.3f}s > {self.limits.max_seconds:.3f}s)"
            )

    def remaining_seconds(self) -> float:
        self.check_time()
        remaining = self.limits.max_seconds - (self.clock() - self.started)
        return max(0.1, remaining)

    def begin_request(self) -> None:
        self.check_time()
        self.requests += 1
        if self.requests > self.limits.max_requests:
            raise LimitError(
                f"GitHub request limit exceeded ({self.requests} > {self.limits.max_requests})"
            )

    def consume_response(self, size: int) -> None:
        self.check_time()
        if size > self.limits.max_response_bytes:
            raise LimitError(
                f"one GitHub response exceeded {self.limits.max_response_bytes} bytes"
            )
        self.response_bytes += size
        if self.response_bytes > self.limits.max_total_response_bytes:
            raise LimitError(
                "total GitHub response byte limit exceeded "
                f"({self.response_bytes} > {self.limits.max_total_response_bytes})"
            )


@dataclasses.dataclass(frozen=True)
class SourceBlob:
    repository: str
    repository_commit: str
    kind: str
    path: str
    blob_sha: str
    data: bytes


@dataclasses.dataclass(frozen=True)
class ApiResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes

    def header(self, name: str) -> str | None:
        wanted = name.lower()
        for key, value in self.headers.items():
            if key.lower() == wanted:
                return value
        return None
