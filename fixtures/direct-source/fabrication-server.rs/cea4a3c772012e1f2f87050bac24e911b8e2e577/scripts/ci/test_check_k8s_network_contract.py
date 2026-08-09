#!/usr/bin/env python3
from __future__ import annotations

import unittest

from check_k8s_network_contract import ContractError, validate


DEPLOYMENT = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: example
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

NETWORK_POLICY = """\
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: example
  namespace: daedalus
spec:
  egress:
    - to: []
      ports:
        - { protocol: TCP, port: 4222 }
        - { protocol: TCP, port: 4318 }
"""


class KubernetesNetworkContractTests(unittest.TestCase):
    def test_declared_endpoint_ports_are_permitted(self) -> None:
        result = validate(DEPLOYMENT, NETWORK_POLICY)
        self.assertEqual("daedalus", result["namespace"])
        self.assertEqual(
            {"NATS_URL": 4222, "OTEL_EXPORTER_OTLP_ENDPOINT": 4318},
            result["endpointPorts"],
        )

    def test_otlp_port_mismatch_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            ContractError,
            r"OTEL_EXPORTER_OTLP_ENDPOINT=4318",
        ):
            validate(DEPLOYMENT, NETWORK_POLICY.replace("port: 4318", "port: 4317"))

    def test_namespace_mismatch_fails_closed(self) -> None:
        with self.assertRaisesRegex(ContractError, r"namespaces differ"):
            validate(DEPLOYMENT, NETWORK_POLICY.replace("namespace: daedalus", "namespace: other"))

    def test_endpoint_requires_an_explicit_port(self) -> None:
        deployment = DEPLOYMENT.replace(
            "http://otel.observability.svc:4318",
            "http://otel.observability.svc",
        )
        with self.assertRaisesRegex(ContractError, r"explicit port"):
            validate(deployment, NETWORK_POLICY)


if __name__ == "__main__":
    unittest.main()
