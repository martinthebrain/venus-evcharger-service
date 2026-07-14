# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact contracts for semantic gateway-backed input reads."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.dbus_gateway import GRID_POWER_READ_KEY
from venus_evcharger.inputs.gateway_read import GatewayInputReader, numeric_gateway_value


class GatewayInputReaderContractTests(unittest.TestCase):
    def _reader(self, service: object | None = None) -> GatewayInputReader:
        reader = GatewayInputReader()
        reader.service = service or SimpleNamespace()
        return reader

    def test_numeric_conversion_and_direct_dbus_guard_are_exact(self) -> None:
        for value, expected in ((True, None), (False, None), (1, 1), (1.5, 1.5), (" 2.5 ", 2.5), ("bad", None), (None, None)):
            with self.subTest(value=value):
                self.assertEqual(numeric_gateway_value(value), expected)
        with patch("venus_evcharger.inputs.gateway_read.coerce_dbus_numeric", return_value=True):
            self.assertIsNone(numeric_gateway_value("value"))
        with self.assertRaises(RuntimeError) as raised:
            self._reader()._dbus_module()
        self.assertEqual(
            str(raised.exception),
            "Direct DBus access is disabled; use the DBus gateway adapter",
        )

    def test_successful_and_missing_reads_have_exact_lifecycle(self) -> None:
        service = SimpleNamespace(mark_recovery=MagicMock(), mark_failure=MagicMock())
        reader = self._reader(service)
        with patch.object(reader, "_gateway_snapshot", return_value={"snapshot": True}), patch(
            "venus_evcharger.inputs.gateway_read.gateway_read_value",
            return_value=-42.5,
        ) as read_value, patch("venus_evcharger.inputs.gateway_read.time.time", return_value=123.0):
            self.assertEqual(reader.get_gateway_read_value(GRID_POWER_READ_KEY, reason="grid"), -42.5)
        read_value.assert_called_once_with({"snapshot": True}, GRID_POWER_READ_KEY, max_age_seconds=10.0)
        self.assertEqual(service._last_dbus_ok_at, 123.0)
        service.mark_recovery.assert_called_once_with("dbus", "DBus reads recovered")
        service.mark_failure.assert_not_called()

        client = SimpleNamespace(request_read_key=MagicMock())
        with patch.object(reader, "_gateway_snapshot", return_value={}), patch(
            "venus_evcharger.inputs.gateway_read.gateway_read_value",
            return_value=None,
        ), patch.object(reader, "_gateway_client", return_value=client):
            self.assertIsNone(reader.get_gateway_read_value(GRID_POWER_READ_KEY, reason="missing grid"))
        client.request_read_key.assert_called_once_with(
            GRID_POWER_READ_KEY,
            priority="read",
            reason="missing grid",
            source="evcharger-inputs",
        )
        service.mark_failure.assert_called_once_with("dbus")

    def test_failed_refresh_and_gateway_configuration_fallbacks_are_exact(self) -> None:
        owner = SimpleNamespace(
            dbus_gateway_cache_path=" cache.json ",
            dbus_gateway_run_dir=" run-dir ",
            dbus_gateway_max_age_seconds=-2.5,
            mark_failure=MagicMock(),
        )
        port = SimpleNamespace(_service=owner, mark_failure=MagicMock())
        reader = self._reader(port)
        self.assertIs(reader._gateway_owner(), owner)
        plain_reader = self._reader()
        self.assertIs(plain_reader._gateway_owner(), plain_reader.service)
        self.assertEqual(reader._gateway_cache_max_age_seconds(), 0.0)
        with patch("venus_evcharger.inputs.gateway_read.DbusCacheStore.load_snapshot", return_value={"ok": True}) as load:
            self.assertEqual(reader._gateway_snapshot(), {"ok": True})
        load.assert_called_once_with("cache.json")
        with patch("venus_evcharger.inputs.gateway_read.gateway_paths") as paths:
            paths.return_value = SimpleNamespace(cache_path="fallback.json", run_dir="normalized-run")
            del owner.dbus_gateway_cache_path
            self.assertEqual(reader._gateway_snapshot(), {})
            paths.assert_called_once_with("run-dir")

        empty_owner = SimpleNamespace()
        empty_reader = self._reader(empty_owner)
        with patch("venus_evcharger.inputs.gateway_read.gateway_paths") as paths, patch(
            "venus_evcharger.inputs.gateway_read.DbusCacheStore.load_snapshot",
            return_value={"fallback": True},
        ) as load:
            paths.return_value = SimpleNamespace(cache_path="default-cache.json")
            self.assertEqual(empty_reader._gateway_snapshot(), {"fallback": True})
        paths.assert_called_once_with(None)
        load.assert_called_once_with("default-cache.json")

        owner.dbus_gateway_max_age_seconds = None
        self.assertEqual(reader._gateway_cache_max_age_seconds(), 10.0)
        client_sentinel = object()
        with patch("venus_evcharger.inputs.gateway_read.gateway_paths") as paths, patch(
            "venus_evcharger.inputs.gateway_read.GatewayClient",
            return_value=client_sentinel,
        ) as client_factory:
            gateway_paths = SimpleNamespace(run_dir="normalized-run")
            paths.return_value = gateway_paths
            client = reader._gateway_client()
        self.assertIs(client, client_sentinel)
        paths.assert_called_once_with("run-dir")
        client_factory.assert_called_once_with(gateway_paths)

        with patch("venus_evcharger.inputs.gateway_read.gateway_paths") as paths, patch(
            "venus_evcharger.inputs.gateway_read.GatewayClient",
            return_value=client_sentinel,
        ) as client_factory:
            default_paths = SimpleNamespace(run_dir="default-run")
            paths.return_value = default_paths
            self.assertIs(empty_reader._gateway_client(), client_sentinel)
        paths.assert_called_once_with(None)
        client_factory.assert_called_once_with(default_paths)

        failing_client = SimpleNamespace(request_read_key=MagicMock(side_effect=OSError("offline")))
        with patch.object(reader, "_gateway_snapshot", return_value={}), patch(
            "venus_evcharger.inputs.gateway_read.gateway_read_value",
            return_value=None,
        ), patch.object(reader, "_gateway_client", return_value=failing_client):
            self.assertIsNone(reader.get_gateway_read_value(GRID_POWER_READ_KEY, reason="offline"))
        port.mark_failure.assert_called_once_with("dbus")


if __name__ == "__main__":
    unittest.main()
