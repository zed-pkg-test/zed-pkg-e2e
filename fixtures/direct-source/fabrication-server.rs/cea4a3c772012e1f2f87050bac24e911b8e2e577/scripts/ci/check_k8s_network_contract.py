#!/usr/bin/env python3
"""Validate app endpoint ports against the namespace-scoped NetworkPolicy.

The check intentionally uses only the Python standard library so it can run in
an isolated source checkout without Kubernetes, Helm, or private path
dependencies.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlparse


class ContractError(ValueError):
    """Raised when the checked-in Kubernetes source violates the app contract."""


def _value_for_environment_variable(source: str, name: str) -> str:
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
    raise ContractError(f"deployment does not declare {name}")


def _url_port(source: str, name: str) -> int:
    value = _value_for_environment_variable(source, name)
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.hostname or parsed.port is None:
        raise ContractError(f"{name} must be an absolute URL with an explicit port: {value}")
    return parsed.port


def _network_policy_ports(source: str) -> set[int]:
    ports = {int(value) for value in re.findall(r"(?m)^\s*-?\s*\{?\s*protocol:\s*TCP,\s*port:\s*(\d+)", source)}
    ports.update(int(value) for value in re.findall(r"(?m)^\s*port:\s*(\d+)\s*$", source))
    if not ports:
        raise ContractError("NetworkPolicy does not declare any TCP egress ports")
    return ports


def _metadata_namespace(source: str, resource: str) -> str:
    metadata = re.search(r"(?ms)^metadata:\s*$\n(?P<body>(?:^[ \t].*\n?)*)", source)
    if metadata is None:
        raise ContractError(f"{resource} does not contain metadata")
    namespace = re.search(r"(?m)^\s+namespace:\s*([^\s#]+)\s*$", metadata.group("body"))
    if namespace is None:
        raise ContractError(f"{resource} metadata does not declare a namespace")
    return namespace.group(1)


def validate(deployment: str, network_policy: str) -> dict[str, object]:
    deployment_namespace = _metadata_namespace(deployment, "Deployment")
    policy_namespace = _metadata_namespace(network_policy, "NetworkPolicy")
    if deployment_namespace != policy_namespace:
        raise ContractError(
            "Deployment and NetworkPolicy namespaces differ: "
            f"{deployment_namespace} != {policy_namespace}"
        )

    endpoint_ports = {
        "NATS_URL": _url_port(deployment, "NATS_URL"),
        "OTEL_EXPORTER_OTLP_ENDPOINT": _url_port(
            deployment, "OTEL_EXPORTER_OTLP_ENDPOINT"
        ),
    }
    allowed_ports = _network_policy_ports(network_policy)
    missing = {
        name: port for name, port in endpoint_ports.items() if port not in allowed_ports
    }
    if missing:
        rendered = ", ".join(f"{name}={port}" for name, port in sorted(missing.items()))
        raise ContractError(
            f"NetworkPolicy egress does not permit declared endpoint port(s): {rendered}; "
            f"allowed TCP ports are {sorted(allowed_ports)}"
        )

    return {
        "namespace": deployment_namespace,
        "endpointPorts": endpoint_ports,
        "allowedTcpPorts": sorted(allowed_ports),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deployment", type=Path, default=Path("k8s/deployment.yaml"))
    parser.add_argument(
        "--network-policy", type=Path, default=Path("k8s/networkpolicy.yaml")
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        evidence = validate(
            args.deployment.read_text(encoding="utf-8"),
            args.network_policy.read_text(encoding="utf-8"),
        )
    except (ContractError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    endpoints = ", ".join(
        f"{name}={port}" for name, port in evidence["endpointPorts"].items()
    )
    print(
        f"Kubernetes endpoint/egress contract valid in {evidence['namespace']}: "
        f"{endpoints}; allowed TCP ports={evidence['allowedTcpPorts']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
