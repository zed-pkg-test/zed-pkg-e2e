#!/usr/bin/env python3
"""Git-submodule and Nix provenance parser mixin for DEN-2957."""

from __future__ import annotations

from github_inventory_core import *  # noqa: F401,F403
import github_inventory_core as _core

_bounded_text = _core._bounded_text
_find_json_line = _core._find_json_line
_find_toml_line = _core._find_toml_line
_json_pointer = _core._json_pointer
_utf8 = _core._utf8
_validate_repo_path = _core._validate_repo_path
_walk_json = _core._walk_json

class SourceParsingMixin:
    def _parse_git_submodules(
        self,
        blob: SourceBlob | None,
        full_name: str,
        commit_sha: str,
        gitlinks: Mapping[str, str],
    ) -> None:
        source = repo_node_id(full_name)
        mapped_paths: set[str] = set()
        if blob is not None:
            text = _utf8(blob)
            try:
                modules = parse_gitmodules(text)
            except ParseFailure as error:
                self._source_failure(blob, "parse-gitmodules", "invalid_gitmodules", error)
                modules = []
            for module in modules:
                path = module["path"]
                url = module["url"]
                line = module["line"]
                mapped_paths.add(path)
                try:
                    _bounded_text(url, "submodule URL", self.limits.max_field_bytes)
                    target_repo = normalize_github_url(url)
                except InputError as error:
                    self._source_failure(blob, "parse-gitmodules", "unsupported_submodule_url", error)
                    continue
                selected_commit = gitlinks.get(path)
                target = repo_node_id(target_repo)
                self._ensure_repo_node(target_repo, scanned=target_repo in self.repository_records)
                self._add_edge(
                    source,
                    target,
                    "git-submodule",
                    selected_commit=selected_commit,
                    source_path=path,
                    provenance=[provenance(blob, line)],
                )
                if selected_commit is None:
                    self.contradictions.append(
                        {
                            "code": "submodule-missing-gitlink",
                            "source": source,
                            "target": target,
                            "path": path,
                        }
                    )
        for path, sha in sorted(gitlinks.items()):
            if path not in mapped_paths:
                self.contradictions.append(
                    {
                        "code": "unmapped-gitlink",
                        "source": source,
                        "path": path,
                        "selected_commit": sha,
                        "repository_commit": commit_sha,
                    }
                )

    def _parse_flake_lock(self, blob: SourceBlob) -> None:
        text = _utf8(blob)
        try:
            document = json.loads(text)
        except json.JSONDecodeError as error:
            self._source_failure(blob, "parse-flake-lock", "invalid_json", error)
            return
        if not isinstance(document, dict) or not isinstance(document.get("nodes"), dict):
            self._source_failure(
                blob,
                "parse-flake-lock",
                "invalid_flake_lock",
                ParseFailure("flake.lock must contain a nodes object"),
            )
            return
        source = repo_node_id(blob.repository)
        for node_name, node in sorted(document["nodes"].items(), key=lambda item: str(item[0])):
            if not isinstance(node, dict) or not isinstance(node.get("locked"), dict):
                continue
            locked = node["locked"]
            if locked.get("type") != "github":
                continue
            owner, repo, rev = locked.get("owner"), locked.get("repo"), locked.get("rev")
            if not all(isinstance(value, str) and value for value in (owner, repo, rev)):
                self._source_failure(
                    blob,
                    "parse-flake-lock",
                    "invalid_github_lock_node",
                    ParseFailure(f"flake node {node_name!r} is missing owner/repo/rev"),
                )
                continue
            try:
                _bounded_text(str(node_name), "flake input name", self.limits.max_field_bytes)
                if not GIT_SHA_RE.fullmatch(rev.lower()):
                    raise InputError(f"flake node {node_name!r} has invalid Git revision")
                rev = rev.lower()
                target_repo = normalize_repo(f"{owner}/{repo}")
            except InputError as error:
                self._source_failure(blob, "parse-flake-lock", "invalid_github_coordinate", error)
                continue
            target = repo_node_id(target_repo)
            self._ensure_repo_node(target_repo, scanned=target_repo in self.repository_records)
            self._add_edge(
                source,
                target,
                "nix-flake-lock",
                selected_commit=rev,
                input_name=str(node_name),
                provenance=[
                    provenance(
                        blob,
                        _find_json_line(text, str(node_name)),
                        json_pointer=f"/nodes/{_json_pointer(str(node_name))}/locked",
                    )
                ],
            )

    def _parse_flake_nix(self, blob: SourceBlob) -> None:
        text = _utf8(blob)
        source = repo_node_id(blob.repository)
        pattern = re.compile(
            r"github:([A-Za-z0-9_.-]{1,100})/([A-Za-z0-9_.-]{1,100})(?:/([^\"'\s;)}]+))?"
        )
        for match in pattern.finditer(text):
            target_repo = normalize_repo(f"{match.group(1)}/{match.group(2)}")
            requirement = match.group(3)
            if requirement is not None:
                try:
                    _bounded_text(requirement, "flake input reference", self.limits.max_field_bytes)
                except InputError as error:
                    self._source_failure(
                        blob,
                        "parse-flake-nix",
                        "flake_reference_too_large",
                        error,
                    )
                    continue
            target = repo_node_id(target_repo)
            self._ensure_repo_node(target_repo, scanned=target_repo in self.repository_records)
            line = text.count("\n", 0, match.start()) + 1
            self._add_edge(
                source,
                target,
                "nix-flake-declared",
                requirement=requirement,
                provenance=[provenance(blob, line)],
            )

    def _parse_nix_sources(self, blob: SourceBlob) -> None:
        text = _utf8(blob)
        try:
            document = json.loads(text)
        except json.JSONDecodeError as error:
            self._source_failure(blob, "parse-nix-sources", "invalid_json", error)
            return
        source = repo_node_id(blob.repository)
        for pointer, item in _walk_json(document, self.limits.max_json_depth):
            if not isinstance(item, dict):
                continue
            owner, repo = item.get("owner"), item.get("repo")
            url = item.get("url")
            target_repo: str | None = None
            if isinstance(owner, str) and isinstance(repo, str):
                try:
                    target_repo = normalize_repo(f"{owner}/{repo}")
                except InputError:
                    target_repo = None
            elif isinstance(url, str):
                try:
                    _bounded_text(url, "Nix source URL", self.limits.max_field_bytes)
                    target_repo = normalize_github_url(url)
                except InputError:
                    target_repo = None
            if target_repo is None:
                continue
            selected_commit = item.get("rev") if isinstance(item.get("rev"), str) else None
            if selected_commit is not None:
                if not GIT_SHA_RE.fullmatch(selected_commit.lower()):
                    self._source_failure(
                        blob,
                        "parse-nix-sources",
                        "invalid_nix_source_revision",
                        ParseFailure(f"Nix source {target_repo} has invalid Git revision"),
                    )
                    continue
                selected_commit = selected_commit.lower()
            target = repo_node_id(target_repo)
            self._ensure_repo_node(target_repo, scanned=target_repo in self.repository_records)
            self._add_edge(
                source,
                target,
                "nix-source-pin" if selected_commit else "nix-source-declared",
                selected_commit=selected_commit,
                provenance=[
                    provenance(
                        blob,
                        _find_json_line(text, target_repo.split("/", 1)[1]),
                        json_pointer=pointer,
                    )
                ],
            )
