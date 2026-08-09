#!/usr/bin/env python3
from __future__ import annotations

import unittest

from check_snapshot_endpoint_policy import ContractError, validate


DEPLOYMENT = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app
  namespace: daedalus
spec:
  template:
    spec:
      containers:
        - name: app
          env:
            - { name: NATS_URL, value: "nats://nats.messaging.svc:4222" }
            - { name: OTEL_EXPORTER_OTLP_ENDPOINT, value: "http://otel.observability.svc:4318" }
"""

POLICY = """\
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: app
  namespace: daedalus
spec:
  egress:
    - to: []
      ports:
        - { protocol: TCP, port: 4222 }
        - { protocol: TCP, port: 4318 }
"""


class SnapshotEndpointPolicyTests(unittest.TestCase):
    def test_declared_endpoints_are_permitted(self) -> None:
        result = validate(DEPLOYMENT, POLICY)
        self.assertEqual(
            {"NATS_URL": 4222, "OTEL_EXPORTER_OTLP_ENDPOINT": 4318},
            result["endpointPorts"],
        )

    def test_otlp_grpc_port_does_not_cover_http_export(self) -> None:
        with self.assertRaisesRegex(
            ContractError,
            r"OTEL_EXPORTER_OTLP_ENDPOINT=4318",
        ):
            validate(DEPLOYMENT, POLICY.replace("port: 4318", "port: 4317"))

    def test_missing_explicit_endpoint_port_fails_closed(self) -> None:
        deployment = DEPLOYMENT.replace(
            "http://otel.observability.svc:4318",
            "http://otel.observability.svc",
        )
        with self.assertRaisesRegex(ContractError, r"explicit port"):
            validate(deployment, POLICY)


if __name__ == "__main__":
    unittest.main()
