#!/usr/bin/env python3
"""Identity normalization, provenance, and graph-label escaping for DEN-2957."""

from __future__ import annotations

import configparser
import hashlib
import html
import io
import json
import re
import urllib.parse
from typing import Any

from github_inventory_types import *  # noqa: F401,F403

SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")

def _validate_repo_path(path: str) -> None:
    if len(path.encode("utf-8")) > MAX_REPOSITORY_PATH_BYTES:
        raise InputError(f"repository path exceeds {MAX_REPOSITORY_PATH_BYTES} bytes")
    if not path or path.startswith(("/", "\\")) or "\\" in path or "\x00" in path:
        raise InputError(f"unsafe repository path {path!r}")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise InputError(f"unsafe repository path {path!r}")


def normalize_org(value: str) -> str:
    value = value.strip()
    if not SEGMENT_RE.fullmatch(value) or value in {".", ".."}:
        raise InputError(f"invalid GitHub organization {value!r}")
    return value.lower()


def normalize_repo(value: str) -> str:
    value = value.strip()
    if value.count("/") != 1:
        raise InputError(f"invalid GitHub repository {value!r}; expected owner/name")
    owner, name = value.split("/", 1)
    if (
        not SEGMENT_RE.fullmatch(owner)
        or not SEGMENT_RE.fullmatch(name)
        or owner in {".", ".."}
        or name in {".", ".."}
    ):
        raise InputError(f"invalid GitHub repository {value!r}")
    return f"{owner.lower()}/{name.lower()}"


def normalize_includes(values: Sequence[str]) -> list[str]:
    selected: set[str] = set()
    for value in values:
        for part in value.split(","):
            part = part.strip().lower()
            if not part:
                continue
            if part not in ALLOWED_INCLUDES:
                raise InputError(
                    f"unsupported include {part!r}; expected {','.join(ALLOWED_INCLUDES)}"
                )
            selected.add(part)
    return sorted(selected or set(ALLOWED_INCLUDES))


def normalize_github_url(url: str) -> str:
    value = url.strip()
    patterns = (
        re.compile(r"^https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", re.IGNORECASE),
        re.compile(r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$", re.IGNORECASE),
        re.compile(r"^ssh://git@github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", re.IGNORECASE),
        re.compile(r"^git://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", re.IGNORECASE),
    )
    for pattern in patterns:
        match = pattern.fullmatch(value)
        if match:
            return normalize_repo(f"{match.group(1)}/{match.group(2)}")
    raise InputError("submodule/source URL is not an explicit github.com repository URL")


def repo_node_id(full_name: str) -> str:
    return "github-repo:" + normalize_repo(full_name)


def package_node_id(coordinate: str, version: str | None = None) -> str:
    base = "zpkg-package:" + normalize_repo(coordinate)
    return f"{base}@{version}" if version else base


def manifest_kind(path: str) -> str:
    if path == ".zpkg.toml":
        return "zed-manifest"
    if path == ".zpkg.lock":
        return "zed-lock"
    if path == ".gitmodules":
        return "gitmodules"
    if path == "flake.nix":
        return "nix-flake"
    if path == "flake.lock":
        return "nix-flake-lock"
    if path.endswith("sources.json"):
        return "nix-sources"
    return "source"


def provenance(
    blob: SourceBlob,
    line: int | None = None,
    *,
    json_pointer: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "repository": blob.repository,
        "repository_commit": blob.repository_commit,
        "manifest_kind": blob.kind,
        "path": blob.path,
        "blob_sha": blob.blob_sha,
    }
    if line is not None:
        item["line_start"] = line
        item["line_end"] = line
    if json_pointer is not None:
        item["json_pointer"] = json_pointer
    return item


def parse_gitmodules(text: str) -> list[dict[str, Any]]:
    parser = configparser.RawConfigParser(interpolation=None, strict=True)
    parser.optionxform = str.lower
    try:
        parser.read_file(io.StringIO(text))
    except configparser.Error as error:
        raise ParseFailure("invalid .gitmodules syntax") from error
    result: list[dict[str, Any]] = []
    lines = text.splitlines()
    for section in parser.sections():
        if not re.fullmatch(r'submodule "[^"\r\n]+"', section):
            raise ParseFailure(".gitmodules contains a non-submodule section")
        if not parser.has_option(section, "path") or not parser.has_option(section, "url"):
            raise ParseFailure(f"{section} must contain path and url")
        path = parser.get(section, "path").strip()
        url = parser.get(section, "url").strip()
        _validate_repo_path(path)
        line = next(
            (
                index
                for index, value in enumerate(lines, start=1)
                if value.strip().lower().startswith("url") and url in value
            ),
            None,
        )
        result.append({"section": section, "path": path, "url": url, "line": line})
    result.sort(key=lambda item: (item["path"], item["url"], item["section"]))
    return result


def git_blob_sha(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


def render_node_id(node_id: str) -> str:
    return "n_" + hashlib.sha256(node_id.encode("utf-8")).hexdigest()[:20]


def dot_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )


def mermaid_escape(value: str) -> str:
    # html.escape handles ampersands first and therefore cannot accidentally
    # produce a raw quote or tag delimiter in a Mermaid label.
    escaped = html.escape(value, quote=True)
    return (
        escaped.replace("\r", "&#13;")
        .replace("\n", "&#10;")
        .replace("[", "&#91;")
        .replace("]", "&#93;")
        .replace("{", "&#123;")
        .replace("}", "&#125;")
        .replace("|", "&#124;")
    )


def redact_text(value: str, token: str | None) -> str:
    result = value
    if token:
        result = result.replace(token, "[REDACTED]")
    result = re.sub(r"(?i)(authorization:\s*bearer\s+)[^\s]+", r"\1[REDACTED]", result)
    result = re.sub(r"(?i)(access_token=)[^&\s]+", r"\1[REDACTED]", result)
    return result
