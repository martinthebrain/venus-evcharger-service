# SPDX-License-Identifier: GPL-3.0-or-later
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.backend.modbus_transport import create_modbus_transport
from venus_evcharger.backend.native_modbus_backend import (
    _native_modbus_transport_factory,
    initialize_native_modbus_backend,
    native_modbus_client,
)


class _FakeTransport:
    def exchange(self, request: object, *, timeout_seconds: float) -> bytes:
        return b""


class _FakeClient:
    def __init__(self, transport: object, unit_id: object, timeout_seconds: object) -> None:
        self.transport = transport
        self.unit_id = unit_id
        self.timeout_seconds = timeout_seconds


class TestNativeModbusBackendContracts(unittest.TestCase):
    def test_initialize_native_modbus_backend_sets_exact_runtime_fields(self) -> None:
        backend = SimpleNamespace(_transport="old", _client_cache="old")
        service = object()
        calls: list[tuple[object, str]] = []
        settings = SimpleNamespace(name="settings")

        def loader(loader_service: object, config_path: str) -> object:
            calls.append((loader_service, config_path))
            return settings

        initialize_native_modbus_backend(backend, service, " /tmp/wallbox.ini ", loader)

        self.assertIs(backend.service, service)
        self.assertEqual(backend.config_path, "/tmp/wallbox.ini")
        self.assertIs(backend.settings, settings)
        self.assertIsNone(backend._transport)
        self.assertIsNone(backend._client_cache)
        self.assertEqual(calls, [(service, "/tmp/wallbox.ini")])

    def test_native_modbus_client_creates_transport_and_client_once(self) -> None:
        transport_settings = SimpleNamespace(unit_id=7, timeout_seconds=3.5)
        backend = SimpleNamespace(_client_cache=None, _transport=None, settings=SimpleNamespace(transport_settings=transport_settings))
        transport = _FakeTransport()

        with patch("venus_evcharger.backend.native_modbus_backend.create_modbus_transport", return_value=transport) as factory:
            with patch("venus_evcharger.backend.native_modbus_backend.ModbusClient", _FakeClient):
                first = native_modbus_client(backend)
                second = native_modbus_client(backend)

        self.assertIs(first, second)
        self.assertIs(backend._transport, transport)
        self.assertIs(first.transport, transport)
        self.assertEqual(first.unit_id, 7)
        self.assertEqual(first.timeout_seconds, 3.5)
        factory.assert_called_once_with(transport_settings)

    def test_native_modbus_client_reuses_existing_transport(self) -> None:
        transport_settings = SimpleNamespace(unit_id=9, timeout_seconds=4.5)
        transport = _FakeTransport()
        backend = SimpleNamespace(
            _client_cache=None,
            _transport=transport,
            settings=SimpleNamespace(transport_settings=transport_settings),
        )

        with patch("venus_evcharger.backend.native_modbus_backend.create_modbus_transport") as factory:
            with patch("venus_evcharger.backend.native_modbus_backend.ModbusClient", _FakeClient):
                client = native_modbus_client(backend)

        self.assertIs(client.transport, transport)
        self.assertEqual(client.unit_id, 9)
        self.assertEqual(client.timeout_seconds, 4.5)
        factory.assert_not_called()

    def test_native_modbus_client_uses_backend_module_transport_patchpoint(self) -> None:
        module_name = "_native_modbus_backend_client_contract_module"
        module = types.ModuleType(module_name)
        transport_settings = SimpleNamespace(unit_id=11, timeout_seconds=5.5)
        transport = _FakeTransport()
        calls: list[object] = []

        def custom_factory(factory_settings: object) -> _FakeTransport:
            calls.append(factory_settings)
            return transport

        module.create_modbus_transport = custom_factory
        sys.modules[module_name] = module
        try:
            backend_type = type("ClientBackendWithPatchpoint", (), {"__module__": module_name})
            backend = backend_type()
            backend._client_cache = None
            backend._transport = None
            backend.settings = SimpleNamespace(transport_settings=transport_settings)
            with patch("venus_evcharger.backend.native_modbus_backend.ModbusClient", _FakeClient):
                client = native_modbus_client(backend)
        finally:
            sys.modules.pop(module_name, None)

        self.assertEqual(calls, [transport_settings])
        self.assertIs(backend._transport, transport)
        self.assertIs(client.transport, transport)
        self.assertEqual(client.unit_id, 11)
        self.assertEqual(client.timeout_seconds, 5.5)

    def test_native_modbus_client_rejects_invalid_cache_with_precise_type(self) -> None:
        with self.assertRaisesRegex(TypeError, "Native Modbus client cache must hold ModbusClient, got object"):
            native_modbus_client(SimpleNamespace(_client_cache=object()))

    def test_transport_factory_uses_backend_module_patchpoint(self) -> None:
        module_name = "_native_modbus_backend_contract_module"
        module = types.ModuleType(module_name)
        transport = _FakeTransport()
        settings = object()
        calls: list[object] = []

        def custom_factory(factory_settings: object) -> _FakeTransport:
            calls.append(factory_settings)
            return transport

        module.create_modbus_transport = custom_factory
        sys.modules[module_name] = module
        try:
            backend_type = type("BackendWithPatchpoint", (), {"__module__": module_name})
            factory = _native_modbus_transport_factory(backend_type())
            self.assertIs(factory(settings), transport)
        finally:
            sys.modules.pop(module_name, None)

        self.assertEqual(calls, [settings])

    def test_transport_factory_falls_back_and_validates_exchange_method(self) -> None:
        backend = SimpleNamespace()
        with patch("types.create_modbus_transport", object(), create=True):
            self.assertIs(_native_modbus_transport_factory(backend), create_modbus_transport)

        with patch("types.create_modbus_transport", return_value=object(), create=True):
            factory = _native_modbus_transport_factory(backend)
            with self.assertRaisesRegex(TypeError, "Modbus transport factory returned object"):
                factory(SimpleNamespace())


if __name__ == "__main__":
    unittest.main()
