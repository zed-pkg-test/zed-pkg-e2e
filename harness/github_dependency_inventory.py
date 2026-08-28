#!/usr/bin/env python3
"""Deterministic GitHub repository dependency inventory reference CLI.

This is the first-certification implementation for Linear DEN-2957. It emits
repository/source inventory pinned to exact commits, never a universal current
resolution. The implementation uses only the Python standard library.
"""

from __future__ import annotations

from github_inventory_core import *  # noqa: F401,F403 - public conformance facade
from github_inventory_builder import *  # noqa: F401,F403 - public conformance facade
from github_inventory_graph import *  # noqa: F401,F403 - public conformance facade

def run_inventory(
    *,
    repositories: Sequence[str],
    organizations: Sequence[str],
    includes: Sequence[str],
    limits: Limits,
    transport: Transport,
    token: str | None,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    normalized_repositories = sorted({normalize_repo(value) for value in repositories})
    normalized_organizations = sorted({normalize_org(value) for value in organizations})
    normalized_includes = normalize_includes(includes)
    if not normalized_repositories and not normalized_organizations:
        raise InputError("at least one --repo or --org is required")
    budget = Budget(limits)
    client = GitHubClient(transport, budget, token, sleeper=sleeper)
    builder = InventoryBuilder(
        client,
        limits,
        normalized_repositories,
        normalized_organizations,
        normalized_includes,
        token,
    )
    return builder.build()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="github_dependency_inventory.py",
        description=(
            "Emit a deterministic, provenance-rich GitHub repository dependency inventory. "
            "Credentials are environment-only."
        ),
    )
    parser.add_argument("--repo", action="append", default=[], metavar="OWNER/NAME")
    parser.add_argument("--org", action="append", default=[], metavar="OWNER")
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="KINDS",
        help="comma-separated subset of zed,git-submodule,nix; default is all",
    )
    parser.add_argument("--format", choices=("json", "dot", "mermaid"), default="json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument(
        "--api-base",
        default=os.environ.get("ZED_PKG_GITHUB_API_BASE", DEFAULT_API_BASE),
        help="GitHub API origin; plain HTTP is accepted only on loopback",
    )
    parser.add_argument("--max-repositories", type=int, default=Limits.max_repositories)
    parser.add_argument("--max-nodes", type=int, default=Limits.max_nodes)
    parser.add_argument("--max-edges", type=int, default=Limits.max_edges)
    parser.add_argument("--max-requests", type=int, default=Limits.max_requests)
    parser.add_argument("--max-response-bytes", type=int, default=Limits.max_response_bytes)
    parser.add_argument(
        "--max-total-response-bytes", type=int, default=Limits.max_total_response_bytes
    )
    parser.add_argument("--max-manifest-bytes", type=int, default=Limits.max_manifest_bytes)
    parser.add_argument("--max-field-bytes", type=int, default=Limits.max_field_bytes)
    parser.add_argument("--max-tree-entries", type=int, default=Limits.max_tree_entries)
    parser.add_argument("--max-json-depth", type=int, default=Limits.max_json_depth)
    parser.add_argument("--max-seconds", type=float, default=Limits.max_seconds)
    parser.add_argument("--max-retries", type=int, default=Limits.max_retries)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    token = os.environ.get("ZED_PKG_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    try:
        limits = Limits(
            max_repositories=args.max_repositories,
            max_nodes=args.max_nodes,
            max_edges=args.max_edges,
            max_requests=args.max_requests,
            max_response_bytes=args.max_response_bytes,
            max_total_response_bytes=args.max_total_response_bytes,
            max_manifest_bytes=args.max_manifest_bytes,
            max_field_bytes=args.max_field_bytes,
            max_tree_entries=args.max_tree_entries,
            max_json_depth=args.max_json_depth,
            max_seconds=args.max_seconds,
            max_retries=args.max_retries,
        )
        if args.fixture:
            transport: Transport = FixtureTransport(FixtureBackend.from_path(args.fixture))
        else:
            allow_custom_token = os.environ.get(
                "ZED_PKG_GITHUB_ALLOW_TOKEN_TO_API_BASE", ""
            ).strip().lower() in {"1", "true", "yes", "on"}
            transport = HttpTransport(
                args.api_base,
                allow_token_to_custom_origin=allow_custom_token,
            )
        inventory = run_inventory(
            repositories=args.repo,
            organizations=args.org,
            includes=args.include,
            limits=limits,
            transport=transport,
            token=token,
        )
        rendered = render_inventory(inventory, args.format)
        if args.output:
            write_atomic(args.output, rendered)
        else:
            sys.stdout.write(rendered)
        return 1 if inventory["completeness"]["inventory"] == "partial" else 0
    except LimitError as error:
        print(f"error: {redact_text(str(error), token)}", file=sys.stderr)
        return 3
    except InventoryError as error:
        print(f"error: {redact_text(str(error), token)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
