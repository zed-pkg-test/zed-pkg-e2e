#!/usr/bin/env python3
"""Redirect-safe, bounded GitHub HTTP transport for DEN-2957."""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping

from github_inventory_types import *  # noqa: F401,F403
from github_inventory_util import _is_loopback_host


def _unsafe_url_path(path: str) -> bool:
    """Reject path syntax whose routing meaning can change after decoding."""

    if not path.startswith("/") or "\\" in path or "\x00" in path:
        return True
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in path):
        return True
    decoded = urllib.parse.unquote(path)
    if "\\" in decoded or "\x00" in decoded or "//" in decoded:
        return True
    return any(segment in {".", ".."} for segment in decoded.split("/"))


class Transport:
    base_url: str
    _base_path: str

    def request(
        self,
        path: str,
        headers: Mapping[str, str],
        timeout: float,
        max_bytes: int,
    ) -> ApiResponse:
        raise NotImplementedError

    def token_destination_allowed(self) -> bool:
        return True

    def normalize_request_path(self, path: str) -> str:
        parsed = urllib.parse.urlsplit(path)
        if parsed.scheme or parsed.netloc or parsed.username or parsed.password or parsed.fragment:
            raise ApiError(400, "request-path", "unsafe_github_api_path")
        request_path = parsed.path or "/"
        if _unsafe_url_path(request_path):
            raise ApiError(400, "request-path", "unsafe_github_api_path")
        if parsed.query:
            request_path += "?" + parsed.query
        return request_path

    def absolute_request_url(self, path: str) -> str:
        return self.base_url + self.normalize_request_path(path)

    def relative_from_link(self, link: str, *, current_path: str = "/") -> str:
        base = urllib.parse.urlsplit(self.base_url)
        current_url = self.absolute_request_url(current_path)
        parsed = urllib.parse.urlsplit(urllib.parse.urljoin(current_url, link))
        if (parsed.scheme.lower(), parsed.netloc.lower()) != (
            base.scheme.lower(),
            base.netloc.lower(),
        ):
            raise ApiError(400, "pagination-link", "cross_origin_pagination_link")
        if parsed.username or parsed.password or parsed.fragment:
            raise ApiError(400, "pagination-link", "unsafe_pagination_link")
        path = parsed.path or "/"
        if _unsafe_url_path(path):
            raise ApiError(400, "pagination-link", "unsafe_pagination_link")
        base_path = getattr(self, "_base_path", base.path.rstrip("/"))
        if base_path:
            if path == base_path:
                relative = "/"
            elif path.startswith(base_path + "/"):
                relative = path[len(base_path) :]
            else:
                raise ApiError(400, "pagination-link", "pagination_link_outside_api_base")
        else:
            relative = path
        if parsed.query:
            relative += "?" + parsed.query
        return self.normalize_request_path(relative)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


class HttpTransport(Transport):
    def __init__(
        self,
        base_url: str = DEFAULT_API_BASE,
        *,
        allow_token_to_custom_origin: bool = False,
    ) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise InputError("GitHub API base must be an absolute http(s) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise InputError("GitHub API base must not contain credentials, query, or fragment")
        raw_path = parsed.path or ""
        candidate_path = raw_path.rstrip("/")
        if candidate_path == "/":
            candidate_path = ""
        if candidate_path and _unsafe_url_path(candidate_path):
            raise InputError("GitHub API base contains ambiguous path syntax")
        if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname):
            raise InputError("plain HTTP GitHub API bases are allowed only on loopback")
        self._base_path = candidate_path
        self.base_url = urllib.parse.urlunsplit(
            (parsed.scheme.lower(), parsed.netloc, self._base_path, "", "")
        )
        self._host = parsed.hostname.lower() if parsed.hostname else ""
        self._allow_token_to_custom_origin = allow_token_to_custom_origin
        self._opener = urllib.request.build_opener(_NoRedirect())

    def token_destination_allowed(self) -> bool:
        return (
            self._host == "api.github.com"
            or _is_loopback_host(self._host)
            or self._allow_token_to_custom_origin
        )

    def request(
        self,
        path: str,
        headers: Mapping[str, str],
        timeout: float,
        max_bytes: int,
    ) -> ApiResponse:
        relative = self.normalize_request_path(path)
        url = self.base_url + relative
        request = urllib.request.Request(url, method="GET", headers=dict(headers))
        try:
            with self._opener.open(request, timeout=timeout) as response:
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise LimitError(f"one GitHub response exceeded {max_bytes} bytes")
                return ApiResponse(
                    status=int(response.status),
                    headers={str(k): str(v) for k, v in response.headers.items()},
                    body=body,
                )
        except urllib.error.HTTPError as error:
            # Read only a bounded diagnostic body. It is never surfaced in an
            # exception, inventory, or retained evidence.
            read_limit = min(max_bytes, HTTP_MAX_ERROR_BODY)
            body = error.read(read_limit + 1)[:read_limit]
            return ApiResponse(
                status=int(error.code),
                headers={str(k): str(v) for k, v in error.headers.items()},
                body=body,
            )
        except (OSError, urllib.error.URLError) as error:
            # Avoid retaining raw transport text because it can include proxy
            # URLs or injected credential-bearing diagnostics.
            raise ApiError(599, relative, "github_transport_error") from error
