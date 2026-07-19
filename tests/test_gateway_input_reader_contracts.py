# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway-cache scenarios for semantic DBus input reads."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.support.dbus_inputs import DbusInputServiceFake
from venus_evcharger.dbus_gateway import (
    GRID_POWER_READ_KEY,
    GatewayClient,
    dbus_path_key,
    gateway_paths,
    write_json_file,
)
from venus_evcharger.inputs.gateway_read import GatewayInputReader, InputSourceHealth, numeric_gateway_value
from venus_evcharger.ports.dbus import DbusInputPort, DbusInputReaderPort


def cache_payload(values: dict[str, object], services: object = None) -> dict[str, object]:
    return {
        "schema_version": 1,
        "captured_at": time.time(),
        "services": [] if services is None else services,
        "values": values,
    }


class GatewayInputReaderContractTests(unittest.TestCase):
    def test_numeric_gateway_value_rejects_boolean_and_unusable_values(self) -> None:
        self.assertEqual(numeric_gateway_value("12.5"), 12.5)
        self.assertEqual(numeric_gateway_value(4), 4)
        for value in (True, None, object(), "bad"):
            self.assertIsNone(numeric_gateway_value(value))

    def test_semantic_and_raw_cache_hits_record_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = gateway_paths(temp_dir)
            raw_key = dbus_path_key("service.a", "/Value")
            write_json_file(
                paths.cache_path,
                cache_payload(
                    {
                        GRID_POWER_READ_KEY: {"value": -42.5, "status": "fresh", "updated_at": time.time()},
                        raw_key: {"value": 12, "status": "fresh", "updated_at": time.time()},
                    }
                ),
            )
            service = DbusInputServiceFake(dbus_gateway_cache_path=paths.cache_path)
            reader = GatewayInputReader(DbusInputPort(service))
            self.assertEqual(reader.read_semantic_value(GRID_POWER_READ_KEY, reason="grid"), -42.5)
            self.assertEqual(reader.get_dbus_value("service.a", "/Value"), 12)
        self.assertEqual(
            service.runtime.recoveries,
            [
                ("dbus", "DBus reads recovered", ()),
                ("dbus", "DBus reads recovered", ()),
            ],
        )
        self.assertGreater(service._last_dbus_ok_at, 0.0)

    def test_semantic_and_raw_reads_reject_dynamically_stale_entries_and_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = gateway_paths(temp_dir)
            raw_key = dbus_path_key("service.a", "/Value")
            write_json_file(
                paths.cache_path,
                {
                    **cache_payload(
                        {
                            GRID_POWER_READ_KEY: {
                                "value": -42.5,
                                "status": "fresh",
                                "updated_at": 94.9,
                                "age_s": 0.0,
                            },
                            raw_key: {
                                "value": 12,
                                "status": "fresh",
                                "updated_at": 94.9,
                                "age_s": 0.0,
                            },
                        }
                    ),
                    "captured_at": 99.0,
                },
            )
            service = DbusInputServiceFake(
                dbus_gateway_cache_path=paths.cache_path,
                dbus_gateway_run_dir=temp_dir,
                dbus_gateway_max_age_seconds=5.0,
            )
            reader = GatewayInputReader(DbusInputPort(service))

            with patch("venus_evcharger.inputs.gateway_read.time.time", return_value=100.0):
                self.assertIsNone(reader.read_semantic_value(GRID_POWER_READ_KEY, reason="stale grid"))
                self.assertIsNone(reader.get_dbus_value("service.a", "/Value"))

            commands = list(Path(paths.command_dir).glob("*.json"))
            self.assertEqual(len(commands), 2)
        self.assertEqual(service.runtime.failures, ["dbus", "dbus"])

    def test_semantic_and_raw_reads_reject_a_dynamically_stale_snapshot_and_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = gateway_paths(temp_dir)
            raw_key = dbus_path_key("service.a", "/Value")
            write_json_file(
                paths.cache_path,
                {
                    **cache_payload(
                        {
                            GRID_POWER_READ_KEY: {
                                "value": -42.5,
                                "status": "fresh",
                                "updated_at": 100.0,
                                "age_s": 0.0,
                            },
                            raw_key: {
                                "value": 12,
                                "status": "fresh",
                                "updated_at": 100.0,
                                "age_s": 0.0,
                            },
                        }
                    ),
                    "captured_at": 94.9,
                },
            )
            service = DbusInputServiceFake(
                dbus_gateway_cache_path=paths.cache_path,
                dbus_gateway_run_dir=temp_dir,
                dbus_gateway_max_age_seconds=5.0,
            )
            reader = GatewayInputReader(DbusInputPort(service))

            with patch("venus_evcharger.inputs.gateway_read.time.time", return_value=100.0):
                self.assertIsNone(reader.read_semantic_value(GRID_POWER_READ_KEY, reason="stale snapshot"))
                self.assertIsNone(reader.get_dbus_value("service.a", "/Value"))

            self.assertEqual(len(list(Path(paths.command_dir).glob("*.json"))), 2)
        self.assertEqual(service.runtime.failures, ["dbus", "dbus"])

    def test_semantic_and_raw_reads_share_dynamic_boundary_and_ignore_frozen_age(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = gateway_paths(temp_dir)
            raw_key = dbus_path_key("service.a", "/Value")
            write_json_file(
                paths.cache_path,
                {
                    **cache_payload(
                        {
                            GRID_POWER_READ_KEY: {
                                "value": -42.5,
                                "status": "stale",
                                "updated_at": 95.0,
                                "age_s": 999.0,
                            },
                            raw_key: {
                                "value": "ready",
                                "status": "stale",
                                "updated_at": 95.0,
                                "age_s": 999.0,
                            },
                        }
                    ),
                    "captured_at": 95.0,
                },
            )
            service = DbusInputServiceFake(
                dbus_gateway_cache_path=paths.cache_path,
                dbus_gateway_run_dir=temp_dir,
                dbus_gateway_max_age_seconds=5.0,
            )
            reader = GatewayInputReader(DbusInputPort(service))

            with patch("venus_evcharger.inputs.gateway_read.time.time", return_value=100.0):
                self.assertEqual(reader.read_semantic_value(GRID_POWER_READ_KEY, reason="grid"), -42.5)
                self.assertEqual(reader.get_dbus_value("service.a", "/Value"), "ready")

            self.assertEqual(list(Path(paths.command_dir).glob("*.json")), [])
        self.assertEqual(service.runtime.failures, [])

    def test_reads_use_one_current_time_for_snapshot_and_zero_age_entry_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = gateway_paths(temp_dir)
            raw_key = dbus_path_key("service.a", "/Value")
            write_json_file(
                paths.cache_path,
                {
                    **cache_payload(
                        {
                            GRID_POWER_READ_KEY: {
                                "value": -42.5,
                                "status": "fresh",
                                "updated_at": 100.0,
                            },
                            raw_key: {
                                "value": 12,
                                "status": "fresh",
                                "updated_at": 100.0,
                            },
                        }
                    ),
                    "captured_at": 95.0,
                },
            )
            service = DbusInputServiceFake(
                dbus_gateway_cache_path=paths.cache_path,
                dbus_gateway_run_dir=temp_dir,
                dbus_gateway_max_age_seconds=5.0,
            )
            reader = GatewayInputReader(DbusInputPort(service))

            with patch("venus_evcharger.inputs.gateway_read.time.time", side_effect=(100.0, 101.0)):
                self.assertEqual(reader.read_semantic_value(GRID_POWER_READ_KEY, reason="grid"), -42.5)
            with patch("venus_evcharger.inputs.gateway_read.time.time", side_effect=(100.0, 101.0)):
                self.assertEqual(reader.get_dbus_value("service.a", "/Value"), 12)

            self.assertEqual(list(Path(paths.command_dir).glob("*.json")), [])
        self.assertEqual(service.runtime.failures, [])

    def test_snapshot_loader_uses_configured_freshness_and_current_read_time(self) -> None:
        service = DbusInputServiceFake(
            dbus_gateway_cache_path="cache.json",
            dbus_gateway_max_age_seconds=4.5,
        )
        reader = GatewayInputReader(DbusInputPort(service))

        with patch(
            "venus_evcharger.inputs.gateway_read.DbusCacheStore.load_snapshot",
            return_value={"captured_at": 100.0},
        ) as load_snapshot:
            self.assertEqual(reader._gateway_snapshot(now=104.5), {"captured_at": 100.0})

        load_snapshot.assert_called_once_with(
            "cache.json",
            max_age_seconds=4.5,
            now=104.5,
        )

    def test_missing_future_and_error_entries_are_consistent_refresh_misses(self) -> None:
        raw_key = dbus_path_key("service.a", "/Value")
        service = DbusInputServiceFake(dbus_gateway_max_age_seconds=5.0)
        reader = GatewayInputReader(DbusInputPort(service))
        client = MagicMock(spec=GatewayClient)
        snapshots = (
            cache_payload(
                {
                    GRID_POWER_READ_KEY: {"value": 1.0, "status": "fresh"},
                    raw_key: {"value": 2.0, "status": "fresh"},
                }
            ),
            cache_payload(
                {
                    GRID_POWER_READ_KEY: {"value": 1.0, "status": "fresh", "updated_at": 101.0},
                    raw_key: {"value": 2.0, "status": "fresh", "updated_at": 101.0},
                }
            ),
            cache_payload(
                {
                    GRID_POWER_READ_KEY: {"value": 1.0, "status": "error", "updated_at": 100.0},
                    raw_key: {"value": 2.0, "status": "error", "updated_at": 100.0},
                }
            ),
        )

        with (
            patch("venus_evcharger.inputs.gateway_read.time.time", return_value=100.0),
            patch.object(reader, "_gateway_client", return_value=client),
        ):
            for snapshot in snapshots:
                with self.subTest(snapshot=snapshot):
                    with patch.object(reader, "_gateway_snapshot", return_value=snapshot):
                        self.assertIsNone(reader.read_semantic_value(GRID_POWER_READ_KEY, reason="grid"))
                        self.assertIsNone(reader.get_dbus_value("service.a", "/Value"))

        self.assertEqual(client.request_read_key.call_count, 3)
        self.assertEqual(client.request_raw_value.call_count, 3)
        self.assertEqual(service.runtime.failures, ["dbus"] * 6)

    def test_semantic_cache_miss_preserves_refresh_contract(self) -> None:
        service = DbusInputServiceFake()
        port = DbusInputPort(service)
        reader = GatewayInputReader(port)
        client = MagicMock(spec=GatewayClient)

        with (
            patch.object(reader, "_gateway_snapshot", return_value={}),
            patch.object(reader, "_gateway_client", return_value=client),
        ):
            self.assertIsNone(reader.read_semantic_value(GRID_POWER_READ_KEY, reason="stale grid"))

        client.request_read_key.assert_called_once_with(
            GRID_POWER_READ_KEY,
            reason="stale grid",
            source="evcharger-inputs",
        )
        self.assertEqual(service.runtime.failures, ["dbus"])

    def test_raw_cache_miss_preserves_refresh_contract(self) -> None:
        service = DbusInputServiceFake()
        port = DbusInputPort(service)
        reader = GatewayInputReader(port)
        client = MagicMock(spec=GatewayClient)

        with (
            patch.object(reader, "_gateway_snapshot", return_value={}),
            patch.object(reader, "_gateway_client", return_value=client),
        ):
            self.assertIsNone(reader.get_dbus_value("service.a", "/Value"))

        client.request_raw_value.assert_called_once_with(
            "service.a",
            "/Value",
            reason="main input cache miss",
            source="evcharger-inputs",
        )
        self.assertEqual(service.runtime.failures, ["dbus"])

    def test_raw_refresh_tolerates_unavailable_command_inbox(self) -> None:
        service = DbusInputServiceFake()
        port = DbusInputPort(service)
        reader = GatewayInputReader(port)
        client = MagicMock(spec=GatewayClient)
        client.request_raw_value.side_effect = OSError("command inbox unavailable")

        with (
            patch.object(reader, "_gateway_snapshot", return_value={}),
            patch.object(reader, "_gateway_client", return_value=client),
        ):
            self.assertIsNone(reader.get_dbus_value("service.a", "/Value"))

        client.request_raw_value.assert_called_once_with(
            "service.a",
            "/Value",
            reason="main input cache miss",
            source="evcharger-inputs",
        )
        self.assertEqual(service.runtime.failures, ["dbus"])

    def test_cache_misses_enqueue_refresh_and_mark_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = gateway_paths(temp_dir)
            write_json_file(paths.cache_path, cache_payload({}))
            service = DbusInputServiceFake(dbus_gateway_run_dir=temp_dir)
            reader = GatewayInputReader(DbusInputPort(service))
            self.assertIsNone(reader.read_semantic_value(GRID_POWER_READ_KEY, reason="grid"))
            self.assertIsNone(reader.get_dbus_value("service.a", "/Value"))
            commands = list(Path(paths.command_dir).glob("*.json"))
            self.assertGreaterEqual(len(commands), 2)
        self.assertEqual(service.runtime.failures, ["dbus", "dbus"])

    def test_semantic_refresh_tolerates_unwritable_command_boundary(self) -> None:
        service = DbusInputServiceFake(dbus_gateway_run_dir="/proc/venus-evcharger-test")
        reader = GatewayInputReader(DbusInputPort(service))
        self.assertIsNone(reader.read_semantic_value(GRID_POWER_READ_KEY, reason="grid"))
        self.assertEqual(service.runtime.failures, ["dbus"])

    def test_service_listing_accepts_list_and_mapping_then_applies_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = gateway_paths(temp_dir)
            service = DbusInputServiceFake(dbus_gateway_run_dir=temp_dir)
            reader = GatewayInputReader(DbusInputPort(service))
            cases: tuple[tuple[object, list[str]], ...] = (
                (["b", "a"], ["b", "a"]),
                ({"b": {}, "a": {}}, ["a", "b"]),
            )
            for services, expected in cases:
                write_json_file(paths.cache_path, cache_payload({}, services))
                service._dbus_list_failures = 3
                service._dbus_list_backoff_until = -1.0
                self.assertEqual(reader.list_dbus_services(), expected)
                self.assertEqual(service._dbus_list_failures, 0)
                self.assertEqual(service._dbus_list_backoff_until, 0.0)

            write_json_file(paths.cache_path, cache_payload({}, "invalid"))
            with patch("venus_evcharger.inputs.gateway_read.time.time", return_value=100.0):
                self.assertEqual(reader.list_dbus_services(), [])
            self.assertEqual(service._dbus_list_failures, 1)
            self.assertEqual(service._dbus_list_backoff_until, 105.0)
            with patch("venus_evcharger.inputs.gateway_read.time.time", return_value=104.0):
                with self.assertRaisesRegex(RuntimeError, "backoff"):
                    reader.list_dbus_services()

            service._dbus_list_failures = 4
            service._dbus_list_backoff_until = 0.0
            service.auto_dbus_backoff_base_seconds = 20.0
            service.auto_dbus_backoff_max_seconds = 30.0
            with patch("venus_evcharger.inputs.gateway_read.time.time", return_value=200.0):
                self.assertEqual(reader.list_dbus_services(), [])
            self.assertEqual(service._dbus_list_backoff_until, 230.0)

    def test_service_listing_backoff_boundary_and_message_are_exact(self) -> None:
        service = DbusInputServiceFake(_dbus_list_backoff_until=100.0)
        reader = GatewayInputReader(DbusInputPort(service))

        with patch("venus_evcharger.inputs.gateway_read.time.time", return_value=99.0):
            with self.assertRaises(RuntimeError) as raised:
                reader.list_dbus_services()
        self.assertEqual(str(raised.exception), "DBus list backoff active")

        with (
            patch("venus_evcharger.inputs.gateway_read.time.time", return_value=100.0),
            patch.object(reader, "_list_dbus_names", return_value=["service.a"]),
        ):
            self.assertEqual(reader.list_dbus_services(), ["service.a"])

    def test_empty_service_listing_preserves_refresh_and_failure_contracts(self) -> None:
        service = DbusInputServiceFake()
        port = DbusInputPort(service)
        reader = GatewayInputReader(port)
        client = MagicMock(spec=GatewayClient)

        with (
            patch("venus_evcharger.inputs.gateway_read.time.time", return_value=100.0),
            patch.object(reader, "_list_dbus_names", return_value=[]),
            patch.object(reader, "_gateway_client", return_value=client),
        ):
            self.assertEqual(reader.list_dbus_services(), [])

        client.enqueue_command.assert_called_once_with(
            {"kind": "refresh_services", "source": "evcharger-inputs", "priority": "read"}
        )
        self.assertEqual(service.runtime.failures, ["dbus"])
        self.assertEqual(service._dbus_list_failures, 1)
        self.assertEqual(service._dbus_list_backoff_until, 105.0)

    def test_service_listing_uses_binary_exponential_backoff_before_cap(self) -> None:
        service = DbusInputServiceFake(
            auto_dbus_backoff_base_seconds=5.0,
            auto_dbus_backoff_max_seconds=100.0,
            _dbus_list_failures=3,
        )
        reader = GatewayInputReader(DbusInputPort(service))

        self.assertEqual(reader._dbus_list_backoff_delay(), 20.0)


class InputSourceHealthContractTests(unittest.TestCase):
    def test_retry_ready_forwards_source_and_time_and_returns_result(self) -> None:
        port = MagicMock(spec=DbusInputReaderPort)
        port.source_retry_ready.return_value = False
        health = InputSourceHealth(port)

        self.assertFalse(health.retry_ready("pv", 123.5))

        port.source_retry_ready.assert_called_once_with("pv", 123.5)

    def test_recovered_forwards_complete_diagnostic_context(self) -> None:
        port = MagicMock(spec=DbusInputReaderPort)
        health = InputSourceHealth(port)

        health.recovered("battery", "Battery recovered: %s", "battery.a")

        port.mark_recovery.assert_called_once_with(
            "battery",
            "Battery recovered: %s",
            "battery.a",
        )

    def test_failed_applies_failure_retry_and_warning_in_order(self) -> None:
        port = MagicMock(spec=DbusInputReaderPort)
        calls: list[tuple[str, tuple[object, ...]]] = []

        def record_failure(source_key: str) -> None:
            calls.append(("failure", (source_key,)))

        def record_delay(source_key: str, now: float) -> None:
            calls.append(("delay", (source_key, now)))

        def record_warning(
            warning_key: str,
            warning_interval: float,
            warning_message: str,
            *args: object,
        ) -> None:
            calls.append(("warning", (warning_key, warning_interval, warning_message, *args)))

        port.mark_failure.side_effect = record_failure
        port.delay_source_retry.side_effect = record_delay
        port.warning_throttled.side_effect = record_warning
        health = InputSourceHealth(port)

        health.failed(
            "grid",
            456.25,
            "grid-offline",
            30.0,
            "Grid read failed: %s",
            "timeout",
        )

        self.assertEqual(
            calls,
            [
                ("failure", ("grid",)),
                ("delay", ("grid", 456.25)),
                ("warning", ("grid-offline", 30.0, "Grid read failed: %s", "timeout")),
            ],
        )


if __name__ == "__main__":
    unittest.main()
