#!/usr/bin/env python3
"""Bounded GitHub API client and retry policy for DEN-2957."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Mapping

from github_inventory_support import *  # noqa: F401,F403
from github_inventory_fixture import FixtureBackend
import github_inventory_support as _support

_bounded_retry_after = _support._bounded_retry_after
_is_transient_response = _support._is_transient_response
_metadata_full_name = _support._metadata_full_name
_next_link = _support._next_link
_quote = _support._quote


class FixtureTransport(Transport):
    def __init__(self, backend: FixtureBackend) -> None:
        self.backend = backend
        self.base_url = backend.base_url
        self._base_path = urllib.parse.urlsplit(self.base_url).path.rstrip("/")
        self.requests: list[str] = []

    def request(
        self,
        path: str,
        headers: Mapping[str, str],
        timeout: float,
        max_bytes: int,
    ) -> ApiResponse:
        del timeout, max_bytes
        relative = self.normalize_request_path(path)
        self.requests.append(relative)
        return self.backend.handle(relative, headers.get("Authorization"))


class GitHubClient:
    def __init__(
        self,
        transport: Transport,
        budget: Budget,
        token: str | None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.transport = transport
        self.budget = budget
        if token and not transport.token_destination_allowed():
            raise InputError(
                "refusing to send a GitHub token to a custom API origin without "
                "ZED_PKG_GITHUB_ALLOW_TOKEN_TO_API_BASE=1"
            )
        self.token = token
        self.sleeper = sleeper

    def list_org_repositories(self, org: str) -> list[dict[str, Any]]:
        encoded = urllib.parse.quote(org, safe="")
        path = (
            f"/orgs/{encoded}/repos?direction=asc&page=1&per_page=100&sort=full_name&type=all"
        )
        repositories: list[dict[str, Any]] = []
        seen_pages: set[str] = set()
        while path:
            if path in seen_pages:
                raise ApiError(400, path, "pagination_cycle")
            seen_pages.add(path)
            current_path = path
            response, value = self._get_json(current_path)
            if not isinstance(value, list):
                raise ApiError(502, current_path, "invalid_github_json_shape")
            for item in value:
                if not isinstance(item, dict):
                    raise ApiError(502, current_path, "invalid_github_json_shape")
                repositories.append(item)
            next_link = _next_link(response.header("Link"))
            path = (
                self.transport.relative_from_link(next_link, current_path=current_path)
                if next_link
                else ""
            )
        return repositories

    def get_repository(self, full_name: str) -> dict[str, Any]:
        owner, name = full_name.split("/", 1)
        path = f"/repos/{_quote(owner)}/{_quote(name)}"
        _, value = self._get_json(path)
        if not isinstance(value, dict):
            raise ApiError(502, path, "invalid_github_json_shape")
        return value

    def pin_default_branch(self, full_name: str, default_branch: str) -> str:
        owner, name = full_name.split("/", 1)
        path = f"/repos/{_quote(owner)}/{_quote(name)}/commits/{_quote(default_branch)}"
        _, value = self._get_json(path)
        if not isinstance(value, dict) or not isinstance(value.get("sha"), str):
            raise ApiError(502, path, "invalid_github_json_shape")
        sha = value["sha"].lower()
        if not GIT_SHA_RE.fullmatch(sha):
            raise ApiError(502, path, "invalid_git_sha")
        return sha

    def get_tree(self, full_name: str, commit_sha: str) -> list[dict[str, Any]]:
        owner, name = full_name.split("/", 1)
        path = f"/repos/{_quote(owner)}/{_quote(name)}/git/trees/{commit_sha}?recursive=1"
        _, value = self._get_json(path)
        if not isinstance(value, dict) or not isinstance(value.get("tree"), list):
            raise ApiError(502, path, "invalid_github_json_shape")
        if value.get("truncated") is True:
            raise ApiError(422, path, "github_tree_truncated")
        tree = value["tree"]
        if len(tree) > self.budget.limits.max_tree_entries:
            raise LimitError(
                f"repository tree exceeded {self.budget.limits.max_tree_entries} entries"
            )
        result: list[dict[str, Any]] = []
        for entry in tree:
            if not isinstance(entry, dict):
                raise ApiError(502, path, "invalid_github_json_shape")
            result.append(entry)
        return result

    def get_blob(self, full_name: str, blob_sha: str, declared_size: int | None) -> bytes:
        owner, name = full_name.split("/", 1)
        path = f"/repos/{_quote(owner)}/{_quote(name)}/git/blobs/{blob_sha}"
        _, value = self._get_json(path)
        if not isinstance(value, dict):
            raise ApiError(502, path, "invalid_github_json_shape")
        if value.get("encoding") != "base64" or not isinstance(value.get("content"), str):
            raise ApiError(502, path, "unsupported_blob_encoding")
        try:
            encoded = "".join(value["content"].split())
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as error:
            raise ApiError(502, path, "invalid_blob_base64") from error

        response_size = value.get("size")
        if not isinstance(response_size, int) or response_size < 0 or len(data) != response_size:
            raise ApiError(502, path, "blob_size_mismatch")
        if declared_size is not None and len(data) != declared_size:
            raise ApiError(502, path, "blob_size_mismatch")
        if len(data) > self.budget.limits.max_manifest_bytes:
            raise LimitError(
                f"manifest blob exceeded {self.budget.limits.max_manifest_bytes} bytes"
            )

        response_sha = value.get("sha")
        if not isinstance(response_sha, str):
            raise ApiError(502, path, "blob_sha_mismatch")
        response_sha = response_sha.lower()
        if response_sha != blob_sha or not GIT_SHA_RE.fullmatch(response_sha):
            raise ApiError(502, path, "blob_sha_mismatch")

        header = f"blob {len(data)}\0".encode("ascii")
        if len(blob_sha) == 40:
            computed_sha = hashlib.sha1(header + data, usedforsecurity=False).hexdigest()
        elif len(blob_sha) == 64:
            computed_sha = hashlib.sha256(header + data).hexdigest()
        else:
            raise ApiError(502, path, "invalid_git_sha")
        if computed_sha != blob_sha:
            raise ApiError(502, path, "blob_object_id_mismatch")
        return data

    def _get_json(self, path: str) -> tuple[ApiResponse, Any]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "zed-pkg-den-2957-conformance/1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        attempts = self.budget.limits.max_retries + 1
        response: ApiResponse | None = None
        for attempt in range(attempts):
            self.budget.begin_request()
            timeout = min(30.0, self.budget.remaining_seconds())
            response = self.transport.request(
                path,
                headers,
                timeout,
                self.budget.limits.max_response_bytes,
            )
            self.budget.consume_response(len(response.body))
            if not _is_transient_response(response) or attempt + 1 >= attempts:
                break
            retry_after = _bounded_retry_after(response.header("Retry-After"))
            self.sleeper(retry_after)
        assert response is not None
        if response.status != 200:
            code = (
                "github_rate_limited"
                if response.status in {429, 403}
                and response.header("X-RateLimit-Remaining") == "0"
                else "github_http_error"
            )
            raise ApiError(response.status, path, code)
        try:
            value = json.loads(response.body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ApiError(502, path, "invalid_github_json") from error
        return response, value
