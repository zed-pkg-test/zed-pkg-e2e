#!/usr/bin/env python3
"""Public internal facade for the DEN-2957 conformance implementation."""

from github_inventory_support import *  # noqa: F401,F403
from github_inventory_fixture import *  # noqa: F401,F403
from github_inventory_client import *  # noqa: F401,F403
import github_inventory_support as _support

_bounded_retry_after = _support._bounded_retry_after
_bounded_text = _support._bounded_text
_edge_sort_key = _support._edge_sort_key
_error_code = _support._error_code
_find_json_line = _support._find_json_line
_find_toml_line = _support._find_toml_line
_fixture_bytes = _support._fixture_bytes
_fsync_directory = _support._fsync_directory
_is_loopback_host = _support._is_loopback_host
_is_transient_response = _support._is_transient_response
_json_pointer = _support._json_pointer
_metadata_full_name = _support._metadata_full_name
_next_link = _support._next_link
_positive_int = _support._positive_int
_provenance_sort_key = _support._provenance_sort_key
_query_positive_int = _support._query_positive_int
_quote = _support._quote
_utf8 = _support._utf8
_valid_bearer_header = _support._valid_bearer_header
_validate_repo_path = _support._validate_repo_path
_walk_json = _support._walk_json


def normalize_package_org(value: str) -> str:
    """Normalize a package organization with the canonical identity grammar."""

    return normalize_org(value)


def normalize_package_name(value: str) -> str:
    """Normalize one package-name segment without inventing a second grammar."""

    return normalize_repo(f"package-owner/{value}").split("/", 1)[1]


def normalize_exact_version(value: str, field: str) -> str:
    """Validate a bounded exact package version used in an inventory identity."""

    normalized = value.strip()
    if not normalized:
        raise InputError(f"{field} must be non-empty")
    if normalized != value or any(character.isspace() for character in normalized):
        raise InputError(f"{field} must not contain whitespace")
    _bounded_text(normalized, field, Limits().max_field_bytes)
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+_-]*", normalized):
        raise InputError(f"{field} contains unsupported characters")
    return normalized
