#!/usr/bin/env python3
"""Low-level bounded parsing, retry, and atomic-file helpers for DEN-2957."""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any, Iterable, Mapping

from github_inventory_types import *  # noqa: F401,F403
from github_inventory_identity import normalize_repo

def _metadata_full_name(item: Mapping[str, Any]) -> str:
    full_name = item.get("full_name")
    if not isinstance(full_name, str):
        owner = item.get("owner")
        name = item.get("name")
        if isinstance(owner, dict) and isinstance(owner.get("login"), str) and isinstance(name, str):
            full_name = f"{owner['login']}/{name}"
        else:
            raise ParseFailure("repository metadata is missing full_name")
    return normalize_repo(full_name)


def _fixture_bytes(value: Any) -> bytes:
    if isinstance(value, str):
        return value.encode("utf-8")
    if isinstance(value, dict) and isinstance(value.get("base64"), str):
        try:
            return base64.b64decode(value["base64"], validate=True)
        except ValueError as error:
            raise InputError("fixture base64 content is invalid") from error
    raise InputError("fixture file content must be a UTF-8 string or {base64: ...}")


def _json_response(
    status: int,
    value: Any,
    headers: Mapping[str, str] | None = None,
) -> ApiResponse:
    body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    response_headers = {"Content-Type": "application/json", **dict(headers or {})}
    return ApiResponse(status=status, headers=response_headers, body=body)


def _query_positive_int(query: Mapping[str, list[str]], key: str, default: int) -> int:
    values = query.get(key)
    if not values:
        return default
    return _positive_int(values[-1], key)


def _positive_int(value: Any, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise InputError(f"{label} must be an integer") from error
    if result <= 0:
        raise InputError(f"{label} must be greater than zero")
    return result


def _valid_bearer_header(value: str | None) -> bool:
    if not value or not value.startswith("Bearer "):
        return False
    return bool(value.removeprefix("Bearer ").strip())


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _is_loopback_host(host: str | None) -> bool:
    if host is None:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _next_link(header: str | None) -> str | None:
    if not header:
        return None
    for part in header.split(","):
        match = re.fullmatch(r'\s*<([^>]+)>\s*;\s*rel="([^"]+)"\s*', part)
        if match and "next" in match.group(2).split():
            return match.group(1)
    return None


def _bounded_retry_after(value: str | None) -> float:
    if value is None:
        return 0.0
    try:
        number = float(value)
    except ValueError:
        return 0.0
    return max(0.0, min(number, 1.0))


def _is_transient_response(response: ApiResponse) -> bool:
    if response.status in TRANSIENT_STATUSES:
        return True
    return (
        response.status == 403
        and response.header("X-RateLimit-Remaining") == "0"
    )


def _bounded_text(value: str, label: str, max_bytes: int) -> str:
    if "\x00" in value:
        raise InputError(f"{label} contains a NUL byte")
    size = len(value.encode("utf-8"))
    if size > max_bytes:
        raise LimitError(f"{label} exceeds {max_bytes} bytes")
    return value


def _validate_repo_path(path: str) -> None:
    if len(path.encode("utf-8")) > MAX_REPOSITORY_PATH_BYTES:
        raise InputError(f"repository path exceeds {MAX_REPOSITORY_PATH_BYTES} bytes")
    if not path or path.startswith(("/", "\\")) or "\\" in path or "\x00" in path:
        raise InputError(f"unsafe repository path {path!r}")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise InputError(f"unsafe repository path {path!r}")


def _utf8(blob: SourceBlob) -> str:
    try:
        return blob.data.decode("utf-8")
    except UnicodeError as error:
        raise ParseFailure(f"{blob.path} is not UTF-8") from error


def _find_toml_line(text: str, key: str) -> int | None:
    quoted = json.dumps(key)
    for index, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith(f"{key} ") or stripped.startswith(f"{quoted} "):
            return index
        if stripped.startswith(f"{key}=") or stripped.startswith(f"{quoted}="):
            return index
    return None


def _find_json_line(text: str, value: str) -> int | None:
    needle = json.dumps(value)
    for index, line in enumerate(text.splitlines(), start=1):
        if needle in line:
            return index
    return None


def _json_pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _walk_json(value: Any, max_depth: int) -> Iterable[tuple[str, Any]]:
    stack: list[tuple[str, Any, int]] = [("", value, 0)]
    while stack:
        pointer, current, depth = stack.pop()
        if depth > max_depth:
            raise LimitError(f"JSON source nesting exceeded {max_depth}")
        yield pointer or "/", current
        if isinstance(current, dict):
            for key in sorted(current, reverse=True):
                child = current[key]
                stack.append((pointer + "/" + _json_pointer(str(key)), child, depth + 1))
        elif isinstance(current, list):
            for index in range(len(current) - 1, -1, -1):
                stack.append((pointer + f"/{index}", current[index], depth + 1))


def _edge_sort_key(edge: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        edge.get("source", ""),
        edge.get("target", ""),
        edge.get("kind", ""),
        edge.get("requirement", ""),
        edge.get("selected_version", ""),
        edge.get("selected_commit", ""),
        edge.get("artifact_sha256", ""),
        edge.get("source_path", ""),
        edge.get("input_name", ""),
    )


def _provenance_sort_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        item.get("repository", ""),
        item.get("repository_commit", ""),
        item.get("path", ""),
        item.get("line_start", -1),
        item.get("json_pointer", ""),
        item.get("blob_sha", ""),
    )


def _error_code(error: BaseException) -> str:
    if isinstance(error, ApiError):
        return error.code
    if isinstance(error, LimitError):
        return "limit_exceeded"
    if isinstance(error, InputError):
        return "invalid_input"
    if isinstance(error, ParseFailure):
        return "parse_failure"
    return "inventory_failure"


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
