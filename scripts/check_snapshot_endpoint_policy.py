#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse


class ContractError(ValueError):
    pass


def literal_environment_value(source: str, name: str) -> str:
    lines = source.splitlines()
    name_pattern = re.compile(rf"\bname:\s*{re.escape(name)}\b")
    value_pattern = re.compile(r"\bvalue:\s*[\"']?([^\"'\s,}}]+)")
    for index, line in enumerate(lines):
        if not name_pattern.search(line):
            continue
        for candidate in lines[index : index + 4]:
            match = value_pattern.search(candidate)
            if match:
                return match.group(1)
        raise ContractError(f"{name} is declared without a literal value")
    raise ContractError(f"Deployment does not declare {name}")


def endpoint_port(source: str, name: str) -> int:
    value = literal_environment_value(source, name)
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.hostname or parsed.port is None:
        raise ContractError(f"{name} must be an absolute URL with an explicit port: {value}")
    return parsed.port


def network_policy_tcp_ports(source: str) -> set[int]:
    ports = {
        int(value)
        for value in re.findall(
            r"(?m)^\s*-?\s*\{?\s*protocol:\s*TCP,\s*port:\s*(\d+)",
            source,
        )
    }
    ports.update(
        int(value)
        for value in re.findall(r"(?m)^\s*port:\s*(\d+)\s*$", source)
    )
    if not ports:
        raise ContractError("NetworkPolicy does not declare any TCP ports")
    return ports


def validate(deployment: str, network_policy: str) -> dict[str, object]:
    endpoints = {
        "NATS_URL": endpoint_port(deployment, "NATS_URL"),
        "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint_port(
            deployment, "OTEL_EXPORTER_OTLP_ENDPOINT"
        ),
    }
    allowed = network_policy_tcp_ports(network_policy)
    missing = {name: port for name, port in endpoints.items() if port not in allowed}
    if missing:
        rendered = ", ".join(f"{name}={port}" for name, port in sorted(missing.items()))
        raise ContractError(
            f"NetworkPolicy does not permit declared endpoint port(s): {rendered}; "
            f"allowed TCP ports are {sorted(allowed)}"
        )
    return {"endpointPorts": endpoints, "allowedTcpPorts": sorted(allowed)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deployment", type=Path, required=True)
    parser.add_argument("--network-policy", type=Path, required=True)
    parser.add_argument("--evidence", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = validate(
            args.deployment.read_text(encoding="utf-8"),
            args.network_policy.read_text(encoding="utf-8"),
        )
    except (ContractError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if args.evidence is not None:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(
            json.dumps(
                {
                    "$schema": "zed-pkg-test/snapshot-endpoint-policy/v1",
                    "result": "passed",
                    **result,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    print(
        "snapshot endpoint/egress contract valid: "
        f"endpoints={result['endpointPorts']}; allowed={result['allowedTcpPorts']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
