# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for the shared service-port forwarding boundary."""

import unittest
from types import SimpleNamespace

from venus_evcharger.ports.base import _BaseServicePort, _ControllerBoundPort


class _Port(_ControllerBoundPort):
    _ALLOWED_ATTRS = {"readable"}
    _MUTABLE_ATTRS = {"mutable"}
    _ALLOWED_METHODS = {"operation"}

    @property
    def normalized(self) -> int:
        return int(self._service.normalized)

    @normalized.setter
    def normalized(self, value: object) -> None:
        self._service.normalized = int(value)


class PortsBaseContractTests(unittest.TestCase):
    def test_base_port_forwards_only_the_declared_surface(self) -> None:
        service = SimpleNamespace(readable=1, mutable=2, operation=lambda: "done", normalized=3)
        port = _Port(service)

        self.assertEqual(port.__dict__, {"_service": service, "_controller": None})
        self.assertEqual(port.readable, 1)
        self.assertEqual(port.operation(), "done")
        self.assertEqual(port.normalized, 3)
        port.mutable = 4
        port.normalized = "5"
        self.assertEqual(service.mutable, 4)
        self.assertEqual(service.normalized, 5)

        with self.assertRaisesRegex(AttributeError, "^forbidden$"):
            getattr(port, "forbidden")
        with self.assertRaisesRegex(AttributeError, "^readable$"):
            port.readable = 9

    def test_controller_resolution_prefers_service_override_then_bound_controller(self) -> None:
        service = SimpleNamespace(override=lambda: "service")
        port = _Port(service)
        self.assertEqual(port._controller_or_override("override", "public")(), "service")

        with self.assertRaisesRegex(AttributeError, "^missing$"):
            port._controller_or_override("missing", "public")

        controller = SimpleNamespace(public=lambda: "controller")
        port.bind_controller(controller)
        self.assertIs(port.__dict__["_controller"], controller)
        self.assertEqual(port._controller_or_override("missing", "public")(), "controller")

    def test_plain_base_port_initializes_only_service_reference(self) -> None:
        service = object()
        port = _BaseServicePort(service)
        self.assertEqual(port.__dict__, {"_service": service})


if __name__ == "__main__":
    unittest.main()
