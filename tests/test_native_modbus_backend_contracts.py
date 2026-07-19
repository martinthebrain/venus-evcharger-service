# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.backend.native_modbus_backend import (
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

    def test_native_modbus_client_rejects_invalid_cache_with_precise_type(self) -> None:
        with self.assertRaisesRegex(TypeError, "Native Modbus client cache must hold ModbusClient, got object"):
            native_modbus_client(SimpleNamespace(_client_cache=object()))


if __name__ == "__main__":
    unittest.main()
