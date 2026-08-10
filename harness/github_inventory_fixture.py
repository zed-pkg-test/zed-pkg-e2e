#!/usr/bin/env python3
"""Compact fake-GitHub fixture backend for DEN-2957."""

from __future__ import annotations

import base64
import json
import urllib.parse
from pathlib import Path
from typing import Any, Mapping

from github_inventory_support import *  # noqa: F401,F403
import github_inventory_support as _support

_fixture_bytes = _support._fixture_bytes
_json_response = _support._json_response
_positive_int = _support._positive_int
_query_positive_int = _support._query_positive_int
_quote = _support._quote
_valid_bearer_header = _support._valid_bearer_header
_validate_repo_path = _support._validate_repo_path

class FixtureBackend:
    """Synthesizes bounded GitHub REST responses from a compact checked-in fixture."""

    def __init__(self, document: Mapping[str, Any], base_url: str = "https://api.github.test") -> None:
        if document.get("schema") != FIXTURE_SCHEMA:
            raise InputError(f"fixture schema must be {FIXTURE_SCHEMA}")
        self.document = json.loads(json.dumps(document))
        self.base_url = base_url.rstrip("/")
        self.page_size = _positive_int(document.get("page_size", 100), "fixture page_size")
        self.require_token = bool(document.get("require_token", False))
        organizations = document.get("organizations", {})
        repositories = document.get("repositories", {})
        if not isinstance(organizations, dict) or not isinstance(repositories, dict):
            raise InputError("fixture organizations and repositories must be objects")
        self.organizations: dict[str, list[str]] = {}
        for org, values in organizations.items():
            normalized_org = normalize_org(str(org))
            if not isinstance(values, list):
                raise InputError(f"fixture organization {org!r} must be a list")
            self.organizations[normalized_org] = [normalize_repo(str(value)) for value in values]
        self.repositories: dict[str, dict[str, Any]] = {}
        for full_name, value in repositories.items():
            normalized = normalize_repo(str(full_name))
            if not isinstance(value, dict):
                raise InputError(f"fixture repository {full_name!r} must be an object")
            repository = dict(value)
            repository.setdefault("default_branch", "main")
            repository.setdefault("archived", False)
            repository.setdefault("private", False)
            repository.setdefault("disabled", False)
            repository.setdefault("files", {})
            repository.setdefault("gitlinks", {})
            repository.setdefault("failures", {})
            sha = str(repository.get("commit_sha", ""))
            if not GIT_SHA_RE.fullmatch(sha):
                raise InputError(f"fixture repository {normalized} has invalid commit_sha")
            if not isinstance(repository["files"], dict) or not isinstance(repository["gitlinks"], dict):
                raise InputError(f"fixture repository {normalized} files/gitlinks must be objects")
            self.repositories[normalized] = repository
        self._blob_index: dict[tuple[str, str], bytes] = {}
        for full_name, repository in self.repositories.items():
            for path, content in repository["files"].items():
                _validate_repo_path(str(path))
                data = _fixture_bytes(content)
                self._blob_index[(full_name, git_blob_sha(data))] = data
            for path, sha in repository["gitlinks"].items():
                _validate_repo_path(str(path))
                if not GIT_SHA_RE.fullmatch(str(sha)):
                    raise InputError(f"fixture gitlink {full_name}:{path} has invalid SHA")

    @classmethod
    def from_path(cls, path: Path) -> "FixtureBackend":
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise InputError(f"could not read fixture {path}") from error
        if not isinstance(document, dict):
            raise InputError("fixture root must be an object")
        return cls(document)

    def handle(self, raw_path: str, authorization: str | None) -> ApiResponse:
        if self.require_token and not _valid_bearer_header(authorization):
            return _json_response(401, {"message": "fixture authorization required"})

        parsed = urllib.parse.urlsplit(raw_path)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        path = parsed.path

        org_match = re.fullmatch(r"/orgs/([^/]+)/repos", path)
        if org_match:
            org = normalize_org(urllib.parse.unquote(org_match.group(1)))
            if org not in self.organizations:
                return _json_response(404, {"message": "organization not found"})
            page = _query_positive_int(query, "page", 1)
            requested_page_size = _query_positive_int(query, "per_page", 100)
            page_size = min(requested_page_size, self.page_size)
            values = self.organizations[org]
            start = (page - 1) * page_size
            selected = values[start : start + page_size]
            body = [self._metadata(full_name) for full_name in selected]
            headers: dict[str, str] = {}
            if start + page_size < len(values):
                next_query = dict(query)
                next_query["page"] = [str(page + 1)]
                next_query["per_page"] = [str(requested_page_size)]
                encoded = urllib.parse.urlencode(
                    [(key, value) for key in sorted(next_query) for value in next_query[key]]
                )
                next_url = f"{self.base_url}{path}?{encoded}"
                headers["Link"] = f'<{next_url}>; rel="next"'
            return _json_response(200, body, headers)

        repo_match = re.fullmatch(r"/repos/([^/]+)/([^/]+)", path)
        if repo_match:
            full_name = normalize_repo(
                f"{urllib.parse.unquote(repo_match.group(1))}/{urllib.parse.unquote(repo_match.group(2))}"
            )
            failure = self._failure(full_name, "metadata")
            if failure:
                return failure
            if full_name not in self.repositories:
                return _json_response(404, {"message": "repository not found"})
            return _json_response(200, self._metadata(full_name))

        commit_match = re.fullmatch(r"/repos/([^/]+)/([^/]+)/commits/(.+)", path)
        if commit_match:
            full_name = normalize_repo(
                f"{urllib.parse.unquote(commit_match.group(1))}/{urllib.parse.unquote(commit_match.group(2))}"
            )
            failure = self._failure(full_name, "commit")
            if failure:
                return failure
            repository = self.repositories.get(full_name)
            if repository is None:
                return _json_response(404, {"message": "repository not found"})
            ref = urllib.parse.unquote(commit_match.group(3))
            if ref != repository["default_branch"] and ref != repository["commit_sha"]:
                return _json_response(404, {"message": "commit not found"})
            return _json_response(200, {"sha": repository["commit_sha"]})

        tree_match = re.fullmatch(r"/repos/([^/]+)/([^/]+)/git/trees/([0-9a-f]+)", path)
        if tree_match:
            full_name = normalize_repo(
                f"{urllib.parse.unquote(tree_match.group(1))}/{urllib.parse.unquote(tree_match.group(2))}"
            )
            failure = self._failure(full_name, "tree")
            if failure:
                return failure
            repository = self.repositories.get(full_name)
            if repository is None or tree_match.group(3) != repository["commit_sha"]:
                return _json_response(404, {"message": "tree not found"})
            entries: list[dict[str, Any]] = []
            for file_path, content in sorted(repository["files"].items()):
                data = _fixture_bytes(content)
                entries.append(
                    {
                        "path": file_path,
                        "mode": "100644",
                        "type": "blob",
                        "sha": git_blob_sha(data),
                        "size": len(data),
                    }
                )
            for link_path, sha in sorted(repository["gitlinks"].items()):
                entries.append(
                    {
                        "path": link_path,
                        "mode": "160000",
                        "type": "commit",
                        "sha": sha,
                    }
                )
            return _json_response(
                200,
                {
                    "sha": repository["commit_sha"],
                    "truncated": bool(repository.get("tree_truncated", False)),
                    "tree": entries,
                },
            )

        blob_match = re.fullmatch(r"/repos/([^/]+)/([^/]+)/git/blobs/([0-9a-f]+)", path)
        if blob_match:
            full_name = normalize_repo(
                f"{urllib.parse.unquote(blob_match.group(1))}/{urllib.parse.unquote(blob_match.group(2))}"
            )
            failure = self._failure(full_name, "blob")
            if failure:
                return failure
            sha = blob_match.group(3)
            data = self._blob_index.get((full_name, sha))
            if data is None:
                return _json_response(404, {"message": "blob not found"})
            return _json_response(
                200,
                {
                    "sha": sha,
                    "size": len(data),
                    "encoding": "base64",
                    "content": base64.b64encode(data).decode("ascii"),
                },
            )

        return _json_response(404, {"message": "fixture route not found"})

    def _metadata(self, full_name: str) -> dict[str, Any]:
        repository = self.repositories.get(full_name)
        if repository is None:
            owner, name = full_name.split("/", 1)
            return {
                "full_name": full_name,
                "name": name,
                "owner": {"login": owner},
                "default_branch": "main",
                "archived": False,
                "private": False,
                "disabled": False,
            }
        owner, name = full_name.split("/", 1)
        return {
            "full_name": full_name,
            "name": name,
            "owner": {"login": owner},
            "default_branch": repository["default_branch"],
            "archived": bool(repository["archived"]),
            "private": bool(repository["private"]),
            "disabled": bool(repository["disabled"]),
        }

    def _failure(self, full_name: str, stage: str) -> ApiResponse | None:
        repository = self.repositories.get(full_name)
        if repository is None:
            return None
        failures = repository.get("failures", {})
        value = failures.get(stage) if isinstance(failures, dict) else None
        if value is None:
            return None
        if isinstance(value, int):
            return _json_response(value, {"message": "synthetic failure"})
        if not isinstance(value, dict):
            raise InputError(f"fixture failure {full_name}:{stage} must be an integer or object")
        status = int(value.get("status", 500))
        body = value.get("body", {"message": "synthetic failure"})
        return _json_response(status, body)
