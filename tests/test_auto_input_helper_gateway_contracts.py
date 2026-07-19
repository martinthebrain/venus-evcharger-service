# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from venus_evcharger.dbus_gateway import BATTERY_SOC_READ_KEY, dbus_path_key
from venus_evcharger.inputs.helper.sources_dbus_gateway import GatewayCacheReader
from tests.support.auto_input_helper import FakeGatewayClient, helper_settings


class AutoInputHelperGatewayContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FakeGatewayClient()
        self.reader = GatewayCacheReader(helper_settings(), self.client)

    def test_fresh_raw_value_is_returned_without_request(self) -> None:
        snapshot = {"values": {dbus_path_key("svc", "/Value"): {"status": "fresh", "value": 12.5}}}
        with patch.object(self.reader, "cache_snapshot", return_value=snapshot):
            self.assertEqual(self.reader.cached_value("svc", "/Value"), 12.5)
        self.assertEqual(self.client.commands, [])

    def test_known_unusable_path_requests_introspection_without_raw_refresh(self) -> None:
        with patch.object(self.reader, "_introspection_says_skip", return_value=True), patch.object(
            self.reader, "request_introspection"
        ) as request:
            self.assertIsNone(self.reader.cached_value("svc", "/Missing"))
        request.assert_called_once()

    def test_cache_miss_requests_value_and_recent_error_is_suppressed(self) -> None:
        with patch.object(self.reader, "cache_snapshot", return_value={"values": {}}):
            self.assertIsNone(self.reader.cached_value("svc", "/Value"))
        self.assertEqual(len(self.client.commands), 1)
        self.client.commands.clear()
        error = {"values": {dbus_path_key("svc", "/Value"): {"status": "error", "error_at": 100.0}}}
        with patch.object(self.reader, "cache_snapshot", return_value=error), patch(
            "venus_evcharger.inputs.helper.sources_dbus_gateway.time.time", return_value=101.0
        ):
            self.assertIsNone(self.reader.cached_value("svc", "/Value"))
        self.assertEqual(self.client.commands, [])

    def test_semantic_read_returns_fresh_value_or_requests_refresh(self) -> None:
        snapshot = {"values": {BATTERY_SOC_READ_KEY: {"status": "fresh", "value": 65.0, "age_s": 0.0}}}
        with patch.object(self.reader, "cache_snapshot", return_value=snapshot):
            self.assertEqual(self.reader.semantic_value(BATTERY_SOC_READ_KEY, reason="test"), 65.0)
        with patch.object(self.reader, "cache_snapshot", return_value={}):
            self.assertIsNone(self.reader.semantic_value(BATTERY_SOC_READ_KEY, reason="test"))
        self.assertEqual(len(self.client.read_requests), 1)

    def test_semantic_recent_error_is_not_requeued(self) -> None:
        snapshot = {
            "values": {BATTERY_SOC_READ_KEY: {"status": "error", "error_at": 100.0, "value": None, "age_s": 0.0}}
        }
        with patch.object(self.reader, "cache_snapshot", return_value=snapshot), patch(
            "venus_evcharger.inputs.helper.sources_dbus_gateway.time.time", return_value=101.0
        ):
            self.assertIsNone(self.reader.semantic_value(BATTERY_SOC_READ_KEY, reason="test"))
        self.assertEqual(self.client.read_requests, [])

    def test_service_discovery_uses_cache_and_backoff(self) -> None:
        with patch.object(self.reader, "cache_snapshot", return_value={"services": {"svc.a": {}, "svc.b": {}}}):
            self.assertEqual(self.reader.service_names(), ["svc.a", "svc.b"])
        with patch.object(self.reader, "cache_snapshot", return_value={}), patch(
            "venus_evcharger.inputs.helper.sources_dbus_gateway.time.time", return_value=100.0
        ):
            self.assertEqual(self.reader.service_names(), [])
        self.assertGreater(self.reader._list_backoff_until, 100.0)
        with patch("venus_evcharger.inputs.helper.sources_dbus_gateway.time.time", return_value=101.0):
            self.assertEqual(self.reader.service_names(), [])
        uncapped = GatewayCacheReader(replace(helper_settings(), auto_dbus_backoff_max_seconds=0.0), self.client)
        with patch.object(uncapped, "cache_snapshot", return_value={}), patch(
            "venus_evcharger.inputs.helper.sources_dbus_gateway.time.time", return_value=200.0
        ):
            self.assertEqual(uncapped.service_names(), [])

    def test_retry_window_is_owned_by_gateway_reader(self) -> None:
        with patch("venus_evcharger.inputs.helper.sources_dbus_gateway.time.time", return_value=10.0):
            self.reader.delay_source_retry("pv")
            self.assertFalse(self.reader.source_retry_ready("pv"))
        with patch("venus_evcharger.inputs.helper.sources_dbus_gateway.time.time", return_value=20.0):
            self.assertTrue(self.reader.source_retry_ready("pv"))

    def test_client_cache_path_and_service_available_are_explicit(self) -> None:
        reader = GatewayCacheReader(replace(helper_settings(), dbus_gateway_cache_path=""))
        client = FakeGatewayClient("/run/fallback.json")
        with patch("venus_evcharger.inputs.helper.sources_dbus_gateway.GatewayClient", return_value=client) as factory, patch(
            "venus_evcharger.inputs.helper.sources_dbus_gateway.DbusCacheStore.load_snapshot", return_value={}
        ) as load:
            reader.cache_snapshot()
            reader.cache_snapshot()
        factory.assert_called_once()
        self.assertEqual(load.call_count, 2)
        load.assert_called_with("/run/fallback.json", max_age_seconds=10.0)
        with patch.object(self.reader, "service_names", return_value=["svc"]):
            self.assertTrue(self.reader.service_available("svc"))
            self.assertFalse(self.reader.service_available(""))

    def test_introspection_children_are_cached_or_requested(self) -> None:
        fresh = {
            "schema_version": 1,
            "captured_at": 100.0,
            "heartbeat_at": 100.0,
            "services": {"svc": {"paths": {"/": {"status": "fresh", "children": ["A"]}}}},
        }
        with patch.object(self.reader, "_fresh_introspection_snapshot", return_value=fresh):
            self.assertEqual(self.reader.child_nodes("svc", "/"), ["A"])
        with patch.object(self.reader, "_fresh_introspection_snapshot", return_value={}), patch.object(
            self.reader, "request_introspection"
        ) as request:
            self.assertEqual(self.reader.child_nodes("svc", "/"), [])
        request.assert_called_once()

    def test_introspection_snapshot_reloads_at_most_every_five_seconds(self) -> None:
        with patch("venus_evcharger.inputs.helper.sources_dbus_gateway.time.time", side_effect=[10.0, 12.0]), patch(
            "venus_evcharger.inputs.helper.sources_dbus_gateway.load_introspection_snapshot", return_value={"fresh": True}
        ) as load:
            self.assertEqual(self.reader._fresh_introspection_snapshot(), {"fresh": True})
            self.assertEqual(self.reader._fresh_introspection_snapshot(), {"fresh": True})
        load.assert_called_once()

    def test_introspection_skip_logs_reason(self) -> None:
        with patch.object(self.reader, "_fresh_introspection_snapshot", return_value={}), patch(
            "venus_evcharger.inputs.helper.sources_dbus_gateway.path_unusable_until",
            return_value=(True, "known-missing"),
        ), patch("venus_evcharger.inputs.helper.sources_dbus_gateway.logging.debug") as debug:
            self.assertTrue(self.reader._introspection_says_skip("svc", "/P"))
        debug.assert_called_once()

    def test_gateway_request_failures_remain_best_effort(self) -> None:
        self.client.enqueue_error = OSError("socket gone")
        self.reader.request_value("svc", "/P", priority=80, reason="test")
        self.reader.request_introspection("svc", "/", priority=60, reason="test")
        self.assertFalse(self.reader.request_service_refresh())
        self.client.read_error = OSError("socket gone")
        self.reader.request_read_key(BATTERY_SOC_READ_KEY, reason="test")


if __name__ == "__main__":
    unittest.main()
