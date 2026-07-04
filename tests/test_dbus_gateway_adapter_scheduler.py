# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import builtins
import configparser
import json
import logging
import tempfile
import time
import unittest
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

from venus_evcharger.dbus_gateway import DbusCacheStore, DbusCommandInbox, dbus_path_key, gateway_paths, read_json_file


def _install_mock(target: object, name: str, mock: MagicMock) -> MagicMock:
    setattr(target, name, mock)
    return mock


fake_vedbus = ModuleType("vedbus")


class _FakeVeDbusService(dict):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__()
        self.args = args
        self.kwargs = kwargs
        self.registered = False
        self.added_paths: dict[str, dict[str, object]] = {}

    def register(self) -> None:
        self.registered = True

    def add_path(self, path: str, value: object, **kwargs: object) -> None:
        self.added_paths[path] = {"value": value, **kwargs}
        self[path] = value


setattr(fake_vedbus, "VeDbusService", _FakeVeDbusService)
fake_dbus_mainloop = ModuleType("dbus.mainloop.glib")
setattr(fake_dbus_mainloop, "DBusGMainLoop", MagicMock())

with patch.dict("sys.modules", {"vedbus": fake_vedbus, "dbus.mainloop.glib": fake_dbus_mainloop}):
    import venus_evcharger.dbus_adapter_components_rate as rate_module
    import venus_evcharger.dbus_adapter_components_resource as resource_module
    import venus_evcharger.dbus_adapter_health_backpressure as health_backpressure_module
    import venus_evcharger.dbus_adapter_health_freshness as health_freshness_module
    import venus_evcharger.dbus_adapter_health_history as health_history_module
    import venus_evcharger.dbus_adapter_health_queue as health_queue_module
    import venus_evcharger.dbus_adapter_health_slo as health_slo_module
    import venus_evcharger.dbus_adapter_process_config as process_config_module
    import venus_evcharger.dbus_adapter_process_health as process_health_module
    import venus_evcharger.dbus_adapter_process_introspection as introspection_module
    import venus_evcharger.dbus_adapter_process_io as process_io_module
    import venus_evcharger.dbus_adapter_process_loop as process_loop_module
    import venus_evcharger.dbus_adapter_process_runtime as runtime_module
    import venus_evcharger.dbus_adapter_process_socket as process_socket_module
    import venus_evcharger.dbus_adapter_read as read_module
    import venus_evcharger.dbus_adapter_read_aggregate as read_aggregate_module
    import venus_evcharger.dbus_adapter_read_pv as read_pv_module
    import venus_evcharger.dbus_adapter_read_targets as read_targets_module
    import venus_evcharger.dbus_adapter_write_core as write_core_module
    import venus_evcharger.dbus_adapter_write_health as write_health_module
    import venus_evcharger.dbus_adapter_write_publish as write_publish_module
    import venus_evcharger.dbus_adapter_write_support as write_support_module
    import venus_evcharger.dbus_gateway_core as gateway_core_module
    import venus_evcharger_dbus_adapter as adapter_module
    from venus_evcharger.dbus_gateway_command_types import CommandFileList, CommandMapping
    from venus_evcharger.dbus_adapter_components import (
        AtomicJsonWriter,
        DbusCircuitBreaker,
        DbusConnectionManager,
        DbusDiscoveryManager,
        DbusOperationDeferred,
        DbusRateLimiter,
        DbusReadScheduler,
        ResourceMonitor,
        TickHealth,
    )
    from venus_evcharger.dbus_adapter_read_types import read_spec_from_mapping
    from venus_evcharger_dbus_adapter import DbusAdapter
    from venus_evcharger_dbus_adapter import main as adapter_main


class DbusGatewayAdapterSchedulerTests(unittest.TestCase):
    def test_cache_freshness_helpers_report_status_age_and_path_contracts(self) -> None:
        values = {
            "grid_power_w": {"age_s": "1.25", "status": "fresh"},
            "pv_power_w": {"age_s": 2.5, "status": "error"},
            "battery_soc": {"updated_at": 95.0},
            dbus_path_key("svc", "/Fresh"): {"updated_at": 90.0, "value": "12.5"},
            dbus_path_key("svc", "/Future"): {"updated_at": 110.0, "value": object()},
            "unknown_status": {},
        }

        self.assertEqual(
            health_freshness_module.status_counts(values),
            {"fresh": 1, "error": 1, "unknown": 4},
        )
        self.assertEqual(
            health_freshness_module.important_freshness(values),
            {
                "grid_power_w_age_s": 1.25,
                "pv_power_w_age_s": 2.5,
                "battery_soc_age_s": 0.0,
                "grid_power_w_status": "fresh",
                "pv_power_w_status": "error",
                "battery_soc_status": "missing",
            },
        )
        self.assertEqual(health_freshness_module.cached_entry_age(values[dbus_path_key("svc", "/Fresh")], 100.0), 10.0)
        self.assertEqual(health_freshness_module.cached_entry_age(values[dbus_path_key("svc", "/Future")], 100.0), 0.0)
        self.assertEqual(health_freshness_module.cached_entry_age({"updated_at": 0.5}, 1.0), 0.5)
        self.assertEqual(health_freshness_module.cached_entry_age(object(), 100.0), 0.0)
        self.assertEqual(
            health_freshness_module.max_cached_path_age(values, "svc", {"/Fresh", "/Future", "/Missing"}, 100.0),
            10.0,
        )
        self.assertEqual(health_freshness_module.missing_cached_path_count(values, "svc", {"/Fresh", "/Missing"}), 1.0)
        self.assertEqual(health_freshness_module.cached_entry_float(values[dbus_path_key("svc", "/Fresh")]), 12.5)
        self.assertEqual(health_freshness_module.cached_entry_float(values[dbus_path_key("svc", "/Future")]), 0.0)
        self.assertEqual(health_freshness_module.cached_entry_float(object()), 0.0)

        cache = DbusCacheStore(stale_after_seconds=10.0)
        cache.update_value("grid_power_w", 123.0, source="grid", status="fresh", now=99.0)
        cache.update_value("pv_power_w", 45.0, source="pv", status="cached", now=98.0)
        cache.mark_error("pv_power_w", source="pv", error="no reply", now=100.0)
        snapshot = health_freshness_module.cache_freshness(cache, 100.0)
        self.assertEqual(snapshot["value_count"], 2)
        self.assertEqual(snapshot["status_counts"], {"fresh": 1, "error": 1})
        self.assertEqual(snapshot["grid_power_w_age_s"], 1.0)
        self.assertEqual(snapshot["pv_power_w_status"], "error")
        self.assertEqual(snapshot["battery_soc_status"], "missing")

    def test_queue_health_helpers_report_classes_ages_and_drain_contracts(self) -> None:
        pending = [
            ("slow-old.json", {"queue_class": "read-slow", "created_at": 90.0}),
            ("slow-updated.json", {"queue_class": "read-slow", "created_at": 1.0, "updated_at": 97.0}),
            ("remote.json", {"queue_class": "remote-write", "created_at": 95.0}),
            ("fast-fallback.json", {"kind": "refresh_value", "key": "grid_power_w", "created_at": 96.0}),
            ("gui-fallback.json", {"kind": "publish_value", "path": "/Mode", "created_at": 98.0}),
        ]
        core_pending = [
            ("core.json", {"created_at": 80.0}),
            ("core-updated.json", {"created_at": 1.0, "updated_at": 99.0}),
        ]

        self.assertEqual(health_queue_module.command_activity_at({"created_at": 1.0, "updated_at": 99.0}, 100.0), 99.0)
        self.assertEqual(health_queue_module.command_activity_at({"created_at": "bad"}, 100.0), 100.0)
        self.assertEqual(health_queue_module.command_activity_at({"updated_at": 101.0}, 100.0), 101.0)
        self.assertEqual(health_queue_module.command_activity_at({"updated_at": 0.5}, 1.0), 0.5)
        self.assertEqual(health_queue_module.oldest_command_age(pending, 100.0), 10.0)
        self.assertEqual(health_queue_module.oldest_command_age([("future.json", {"created_at": 101.0})], 100.0), 0.0)
        self.assertEqual(health_queue_module.oldest_command_age([], 100.0), 0.0)
        self.assertEqual(health_queue_module.physical_command_count_from_pending(pending, None), len(pending))
        self.assertEqual(health_queue_module.physical_command_count_from_pending(pending, 7), 7)

        self.assertEqual(
            health_queue_module.queue_class_health([("future.json", {"queue_class": "diagnostic", "created_at": 101.0})], 100.0),
            {"diagnostic": {"pending": 1, "oldest_age_s": 0.0}},
        )
        self.assertEqual(
            health_queue_module.queue_class_health([("tiny.json", {"queue_class": "diagnostic", "created_at": 99.5})], 100.0),
            {"diagnostic": {"pending": 1, "oldest_age_s": 0.5}},
        )
        self.assertEqual(
            health_queue_module.queue_class_health([("bad.json", {"queue_class": "diagnostic", "created_at": "bad"})], 100.0),
            {"diagnostic": {"pending": 1, "oldest_age_s": 0.0}},
        )
        self.assertEqual(
            health_queue_module.queue_class_health(pending, 100.0),
            {
                "gui-critical-publish": {"pending": 1, "oldest_age_s": 2.0},
                "read-fast": {"pending": 1, "oldest_age_s": 4.0},
                "read-slow": {"pending": 2, "oldest_age_s": 10.0},
                "remote-write": {"pending": 1, "oldest_age_s": 5.0},
            },
        )
        self.assertEqual(
            health_queue_module.queue_health(
                pending,
                core_pending,
                100.0,
                physical_count=7,
                write_scheduler_health={"processed_commands_60s": "30", "last_processed_at": "88.5"},
            ),
            {
                "pending_command_count": 5,
                "physical_command_count": 7,
                "oldest_command_age_s": 10.0,
                "core_command_count": 2,
                "oldest_core_command_age_s": 20.0,
                "processed_commands_60s": 30,
                "queue_drain_rate_per_s": 0.5,
                "last_processed_at": 88.5,
            },
        )
        self.assertEqual(
            health_queue_module.queue_health(pending, [], 100.0)["physical_command_count"],
            len(pending),
        )

    def test_slo_helpers_report_exact_targets_boundaries_and_burst_contracts(self) -> None:
        thresholds = health_slo_module.SloThresholds(
            gui_max_age_seconds=2.0,
            core_read_max_age_seconds=5.0,
            queue_max_age_seconds=7.0,
            mainloop_gap_max_ms=100.0,
            tick_seconds=0.2,
            max_tick_seconds=0.6,
        )
        observed_at_limit = {
            "gui_max_age_s": 10.0,
            "gui_measurement_max_age_s": 10.0,
            "gui_control_max_age_s": 10.0,
            "gui_session_max_age_s": 10.0,
            "core_read_max_age_s": 5.0,
            "queue_oldest_age_s": 7.0,
            "mainloop_max_gap_ms_60s": 1500.0,
        }

        self.assertEqual(health_slo_module.effective_gui_max_age_seconds(thresholds), 10.0)
        self.assertEqual(health_slo_module.effective_mainloop_gap_max_ms(thresholds), 1500.0)
        self.assertEqual(
            health_slo_module.slo_targets(thresholds),
            {
                "gui_max_age_s": 10.0,
                "gui_measurement_max_age_s": 10.0,
                "gui_control_max_age_s": 10.0,
                "gui_session_max_age_s": 10.0,
                "configured_gui_max_age_s": 2.0,
                "core_read_max_age_s": 5.0,
                "queue_max_age_s": 7.0,
                "mainloop_gap_max_ms": 1500.0,
            },
        )
        self.assertEqual(
            health_slo_module.slo_checks_from_observed(observed_at_limit, thresholds),
            {
                "gui_fresh": True,
                "gui_measurements_fresh": True,
                "gui_controls_fresh": True,
                "gui_session_fresh": True,
                "core_reads_fresh": True,
                "queue_age_ok": True,
                "mainloop_gap_ok": True,
            },
        )
        violated_observed = {
            "gui_max_age_s": 10.1,
            "gui_measurement_max_age_s": 10.2,
            "gui_control_max_age_s": 10.3,
            "gui_session_max_age_s": 10.4,
            "core_read_max_age_s": 5.1,
            "queue_oldest_age_s": 7.1,
            "mainloop_max_gap_ms_60s": 1500.1,
        }
        violated_checks = health_slo_module.slo_checks_from_observed(violated_observed, thresholds)
        self.assertEqual(
            violated_checks,
            {
                "gui_fresh": False,
                "gui_measurements_fresh": False,
                "gui_controls_fresh": False,
                "gui_session_fresh": False,
                "core_reads_fresh": False,
                "queue_age_ok": False,
                "mainloop_gap_ok": False,
            },
        )
        self.assertEqual(
            health_slo_module.slo_payload(
                violated_checks,
                health_slo_module.slo_targets(thresholds),
                violated_observed,
            ),
            {
                "state": "violated",
                "violated": [
                    "gui_fresh",
                    "gui_measurements_fresh",
                    "gui_controls_fresh",
                    "gui_session_fresh",
                    "core_reads_fresh",
                    "queue_age_ok",
                    "mainloop_gap_ok",
                ],
                "checks": violated_checks,
                "targets": health_slo_module.slo_targets(thresholds),
                "observed": violated_observed,
            },
        )
        self.assertEqual(
            health_slo_module.slo_payload({"gui_fresh": True}, {"gui_max_age_s": 10.0}, {}),
            {
                "state": "ok",
                "violated": [],
                "checks": {"gui_fresh": True},
                "targets": {"gui_max_age_s": 10.0},
                "observed": {},
            },
        )
        self.assertEqual(health_slo_module.slo_checks_from_observed({}, thresholds)["gui_fresh"], True)
        tiny_thresholds = health_slo_module.SloThresholds(
            gui_max_age_seconds=0.1,
            core_read_max_age_seconds=0.1,
            queue_max_age_seconds=0.1,
            mainloop_gap_max_ms=0.1,
            tick_seconds=0.0001,
            max_tick_seconds=0.0001,
        )
        self.assertEqual(
            health_slo_module.slo_checks_from_observed({}, tiny_thresholds),
            {
                "gui_fresh": True,
                "gui_measurements_fresh": True,
                "gui_controls_fresh": True,
                "gui_session_fresh": True,
                "core_reads_fresh": True,
                "queue_age_ok": True,
                "mainloop_gap_ok": True,
            },
        )

        mainloop_configured = health_slo_module.SloThresholds(
            gui_max_age_seconds=12.0,
            core_read_max_age_seconds=1.0,
            queue_max_age_seconds=7.0,
            mainloop_gap_max_ms=3000.0,
            tick_seconds=0.2,
            max_tick_seconds=0.6,
        )
        self.assertEqual(health_slo_module.effective_gui_max_age_seconds(mainloop_configured), 12.0)
        self.assertEqual(health_slo_module.effective_mainloop_gap_max_ms(mainloop_configured), 3000.0)
        self.assertEqual(
            health_slo_module.max_core_read_age(
                {
                    "grid_power_w_age_s": "3.5",
                    "pv_power_w_age_s": 7.25,
                    "battery_soc_age_s": "bad",
                    "ignored_age_s": 99.0,
                }
            ),
            7.25,
        )
        self.assertEqual(health_slo_module.max_core_read_age({"grid_power_w_age_s": 1.5}), 1.5)
        self.assertEqual(health_slo_module.max_core_read_age({"pv_power_w_age_s": 2.5}), 2.5)
        self.assertEqual(health_slo_module.max_core_read_age({"battery_soc_age_s": 3.5}), 3.5)
        self.assertEqual(health_slo_module.max_core_read_age({"ignored_age_s": 99.0}), 0.0)
        self.assertEqual(
            health_slo_module.stale_core_read_keys(
                {
                    "grid_power_w_status": "fresh",
                    "grid_power_w_age_s": 4.9,
                    "pv_power_w_status": "stale",
                    "pv_power_w_age_s": 0.1,
                },
                ("grid_power_w", "pv_power_w", "battery_soc"),
                max_age_seconds=5.0,
            ),
            {"pv_power_w", "battery_soc"},
        )

        self.assertEqual(
            health_slo_module.regulated_publish_burst(
                queue_age=7.0,
                eventloop_gap_ms=1500.0,
                base_burst=4,
                thresholds=thresholds,
            ),
            4,
        )
        self.assertEqual(
            health_slo_module.regulated_publish_burst(
                queue_age=7.1,
                eventloop_gap_ms=1500.0,
                base_burst=4,
                thresholds=thresholds,
            ),
            12,
        )
        self.assertEqual(
            health_slo_module.regulated_publish_burst(
                queue_age=7.1,
                eventloop_gap_ms=1500.0,
                base_burst=1,
                thresholds=thresholds,
            ),
            5,
        )
        self.assertEqual(
            health_slo_module.regulated_publish_burst(
                queue_age=70.0,
                eventloop_gap_ms=1500.0,
                base_burst=20,
                thresholds=thresholds,
            ),
            50,
        )
        self.assertEqual(
            health_slo_module.regulated_publish_burst(
                queue_age=7.1,
                eventloop_gap_ms=1500.1,
                base_burst=4,
                thresholds=thresholds,
            ),
            2,
        )
        self.assertEqual(
            health_slo_module.regulated_publish_burst(
                queue_age=7.0,
                eventloop_gap_ms=1500.1,
                base_burst=5,
                thresholds=thresholds,
            ),
            2,
        )
        self.assertEqual(
            health_slo_module.regulated_publish_burst(
                queue_age=7.1,
                eventloop_gap_ms=1500.1,
                base_burst=1,
                thresholds=thresholds,
            ),
            1,
        )

    def test_read_target_contract_requires_service_and_absolute_path(self) -> None:
        target = read_targets_module.read_target(" svc ", " /Path ")
        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.service, "svc")
        self.assertEqual(target.path, "/Path")
        self.assertEqual(target.source, "svc/Path")
        self.assertEqual(target.cache_key, "path:svc/Path")
        self.assertIsNone(read_targets_module.read_target("", "/Path"))
        self.assertIsNone(read_targets_module.read_target("svc", "Path"))
        self.assertIsNone(read_targets_module.read_target("svc", ""))

    def test_aggregate_signature_members_accepts_only_matching_complete_signatures(self) -> None:
        self.assertIsNone(read_aggregate_module.aggregate_signature_members(None, "pv-total"))
        self.assertIsNone(read_aggregate_module.aggregate_signature_members(("pv-total", (), "extra"), "pv-total"))
        self.assertIsNone(read_aggregate_module.aggregate_signature_members(("sum", (("svc", "/Path"),)), "pv-total"))
        self.assertIsNone(read_aggregate_module.aggregate_signature_members(("pv-total", ["svc"]), "pv-total"))
        self.assertIsNone(read_aggregate_module.aggregate_signature_members(("pv-total", ("svc",)), "pv-total"))
        self.assertIsNone(read_aggregate_module.aggregate_signature_members(("pv-total", (("svc", "/Path", "x"),)), "pv-total"))
        self.assertEqual(
            read_aggregate_module.aggregate_signature_members(("pv-total", (("svc", "/Path"),)), "pv-total"),
            [("svc", "/Path")],
        )

    def test_aggregate_member_float_rejects_non_numeric_values(self) -> None:
        self.assertEqual(read_aggregate_module.aggregate_member_float(True), 1.0)
        self.assertEqual(read_aggregate_module.aggregate_member_float("2.5"), 2.5)
        self.assertEqual(read_aggregate_module.aggregate_member_float(b"3.5"), 3.5)
        with self.assertRaises(TypeError):
            read_aggregate_module.aggregate_member_float(object())

    def test_read_executor_drops_invalid_first_service_path_without_dbus_read(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.cache.update_services(["svc.first"])
            read_busitem = _install_mock(adapter.read_executor, "read_busitem", MagicMock(return_value=12.0))

            outcome = adapter.read_executor.poll_read_spec(
                "first_value",
                {"aggregate": "first-service", "prefix": "svc.", "path": "NotAbsolute"},
            )

            self.assertEqual(outcome, "dropped")
            read_busitem.assert_not_called()
            self.assertNotIn("first_value", adapter.cache.values)

    def test_read_executor_handles_invalid_aggregate_member_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            _install_mock(adapter.read_executor, "read_busitem", MagicMock(return_value=5.0))

            outcome = adapter.read_executor.poll_read_spec(
                "bad_sum",
                {"aggregate": "sum", "service": "svc.aggregate", "paths": ["NotAbsolute"]},
            )

            self.assertEqual(outcome, "applied")
            self.assertEqual(adapter.cache.values["bad_sum"]["value"], 5.0)
            self.assertNotIn("path:svc.aggregateNotAbsolute", adapter.cache.values)

    def test_read_executor_records_optional_invalid_aggregate_errors_only_on_semantic_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            _install_mock(
                adapter.read_executor,
                "read_busitem",
                MagicMock(side_effect=RuntimeError("no reply")),
            )

            outcome = adapter.read_executor.poll_read_spec(
                "pv_power_w",
                {
                    "aggregate": "pv-total",
                    "use_dc_pv": "yes",
                    "dc_service": "com.victronenergy.system",
                    "dc_path": "NotAbsolute",
                    "optional_zero_on_error": "yes",
                    "optional_confidence": 0.25,
                },
            )

            self.assertEqual(outcome, "applied")
            self.assertEqual(adapter.cache.values["pv_power_w"]["value"], 0.0)
            self.assertEqual(adapter.cache.values["pv_power_w"]["confidence"], 0.25)
            self.assertIn("No available AC or DC PV source candidates", adapter.cache.values["pv_power_w"]["last_error"])
            self.assertNotIn("path:com.victronenergy.systemNotAbsolute", adapter.cache.values)

    def test_read_executor_direct_path_key_updates_only_one_cache_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            _install_mock(adapter.read_executor, "read_busitem", MagicMock(return_value=11.0))

            outcome = adapter.read_executor.poll_read_spec(
                "path:svc.direct/Value",
                {"service": "svc.direct", "path": "/Value"},
            )

            self.assertEqual(outcome, "applied")
            self.assertEqual(adapter.cache.values["path:svc.direct/Value"]["value"], 11.0)
            self.assertEqual(list(adapter.cache.values), ["path:svc.direct/Value"])

    def test_read_executor_direct_refresh_and_error_contracts_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            self.assertIs(adapter.read_executor.adapter, adapter)
            self.assertFalse(adapter.read_executor.has_pending_aggregate())
            self.assertIs(adapter.read_executor.last_operation_performed, False)

            with patch.object(adapter.read_executor, "poll_read_spec", MagicMock(return_value="applied")) as poll_read_spec:
                self.assertEqual(adapter.read_executor.refresh_requested_value({"key": "grid_power_w"}), "applied")
            poll_read_spec.assert_called_once_with(
                "grid_power_w",
                adapter.read_scheduler.specs["grid_power_w"],
            )

            adapter.read_scheduler.specs["XXXX"] = {"service": "svc.unexpected", "path": "/Value"}
            with (
                patch.object(adapter.read_executor, "poll_read_spec", MagicMock(return_value="applied")) as poll_read_spec,
                patch.object(adapter.read_executor, "_refresh_direct_value", MagicMock(return_value="dropped")) as refresh_direct,
            ):
                self.assertEqual(adapter.read_executor.refresh_requested_value({"key": ""}), "dropped")
            poll_read_spec.assert_not_called()
            refresh_direct.assert_called_once_with({"key": ""})

            _install_mock(adapter.read_executor, "read_busitem", MagicMock(return_value=12.5))
            self.assertEqual(
                adapter.read_executor.refresh_requested_value({"service": " svc.direct ", "path": " /Value "}),
                "applied",
            )
            adapter.read_executor.read_busitem.assert_called_once_with("svc.direct", "/Value")
            entry = adapter.cache.values["path:svc.direct/Value"]
            self.assertEqual(entry["value"], 12.5)
            self.assertEqual(entry["source"], "svc.direct/Value")
            self.assertEqual(entry["status"], "fresh")

            adapter.read_executor.last_operation_performed = True
            self.assertEqual(
                adapter.read_executor.poll_read_spec("direct_value", {"service": "svc.direct", "path": "/Direct"}),
                "applied",
            )
            self.assertIs(adapter.read_executor.last_operation_performed, False)

            error = RuntimeError("offline")
            _install_mock(adapter.read_executor, "read_busitem", MagicMock(side_effect=error))
            with patch.object(read_module.logging, "debug") as log_debug:
                self.assertEqual(
                    adapter.read_executor.refresh_requested_value({"service": "svc.direct", "path": "/Broken"}),
                    "dropped",
                )
            failed = adapter.cache.values["path:svc.direct/Broken"]
            self.assertEqual(failed["source"], "svc.direct/Broken")
            self.assertEqual(failed["status"], "error")
            self.assertEqual(failed["last_error"], "offline")
            self.assertEqual(failed["confidence"], 0.0)
            log_debug.assert_called_once_with(
                "DBus adapter direct refresh failed key=%s: %s",
                "path:svc.direct/Broken",
                error,
            )

            _install_mock(
                adapter.read_executor,
                "read_busitem",
                MagicMock(side_effect=DbusOperationDeferred("wait")),
            )
            self.assertEqual(
                adapter.read_executor.refresh_requested_value({"service": "svc.direct", "path": "/Deferred"}),
                "deferred",
            )
            self.assertNotIn("path:svc.direct/Deferred", adapter.cache.values)

    def test_read_executor_optional_and_low_level_dbus_contracts_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            _install_mock(adapter.rate_limiter, "require_due", MagicMock())
            _install_mock(adapter.circuit, "record_success", MagicMock())
            _install_mock(adapter.read_executor, "read_busitem_now", MagicMock(return_value="8.5"))
            with patch.object(read_module.time, "monotonic", side_effect=[10.0, 10.25]):
                self.assertEqual(adapter.read_executor.read_optional_busitem("svc.optional", "/Power"), "8.5")
            adapter.rate_limiter.require_due.assert_called_once_with("read")
            adapter.read_executor.read_busitem_now.assert_called_once_with("svc.optional", "/Power")
            adapter.circuit.record_success.assert_called_once_with(250.0, kind="optional_read")

            low_level_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-low-level")))
            fake_iface = MagicMock()
            fake_iface.GetValue.return_value = "4.25"
            fake_obj = object()
            get_object = _install_mock(low_level_adapter.connection, "get_object", MagicMock(return_value=fake_obj))
            with patch.object(read_module.dbus, "Interface", return_value=fake_iface) as interface:
                self.assertEqual(low_level_adapter.read_executor.read_busitem_now("svc.low", "/P"), 4.25)
            get_object.assert_called_once_with("svc.low", "/P", introspect=False)
            interface.assert_called_once_with(fake_obj, "com.victronenergy.BusItem")
            fake_iface.GetValue.assert_called_once_with(timeout=1.0)

    def test_read_executor_aggregate_contracts_preserve_members_and_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            self.assertEqual(adapter.read_executor._services_for_sum({"service": "svc.explicit", "prefix": "ignored."}), ["svc.explicit"])
            adapter.cache.update_services(["svc.2", "other.1", "svc.1"])
            self.assertEqual(adapter.read_executor._services_for_sum({"prefix": "svc."}), ["svc.1", "svc.2"])

            _install_mock(adapter.read_executor, "read_busitem", MagicMock(side_effect=[2.0, 3.5]))
            spec = {"aggregate": "sum", "service": "svc.sum", "paths": ["/L1", "/L2"], "optional_confidence": 0.75}

            self.assertEqual(adapter.read_executor.poll_read_spec("sum_power", spec), "deferred")
            self.assertTrue(adapter.read_executor.last_operation_performed)
            self.assertTrue(adapter.read_executor.has_pending_aggregate())
            self.assertEqual(adapter.cache.values["path:svc.sum/L1"]["value"], 2.0)
            self.assertEqual(adapter.cache.values["path:svc.sum/L1"]["source"], "svc.sum/L1")

            self.assertEqual(adapter.read_executor.poll_read_spec("sum_power", spec), "applied")
            self.assertFalse(adapter.read_executor.has_pending_aggregate())
            adapter.read_executor.read_busitem.assert_has_calls([unittest.mock.call("svc.sum", "/L1"), unittest.mock.call("svc.sum", "/L2")])
            payload = adapter.cache.values["sum_power"]
            self.assertEqual(payload["value"], 5.5)
            self.assertEqual(payload["source"], "svc.sum/L1,svc.sum/L2")
            self.assertEqual(payload["confidence"], 1.0)
            self.assertEqual(payload["last_error"], "")

    def test_read_executor_error_contracts_keep_cache_source_logs_and_pending_state_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.read_executor._aggregates.state_for(
                "required_value",
                ("sum", (("svc.old", "/L1"),)),
                0.4,
            )
            error = RuntimeError("required offline")

            with patch.object(adapter.cache, "mark_error") as mark_error, patch.object(read_module.logging, "debug") as log_debug:
                adapter.read_executor._mark_read_error(
                    "required_value",
                    {"service": "svc.required", "path": "/Power"},
                    error,
                )

            self.assertFalse(adapter.read_executor.has_pending_aggregate())
            mark_error.assert_called_once_with(
                "required_value",
                source="svc.required",
                error=error,
            )
            log_debug.assert_called_once_with(
                "DBus adapter read failed key=%s: %s",
                "required_value",
                error,
            )

    def test_read_executor_optional_zero_contract_keeps_fallback_confidence_and_diagnostics_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.read_executor._aggregates.state_for(
                "optional_value",
                ("services-sum", "/Power", ("svc.old",)),
                0.4,
            )
            error = RuntimeError("optional offline")

            with patch.object(adapter.cache, "update_value") as update_value, patch.object(read_module.logging, "debug") as log_debug:
                adapter.read_executor._mark_optional_zero(
                    "optional_value",
                    {"optional_confidence": 0.55},
                    error,
                )

            self.assertFalse(adapter.read_executor.has_pending_aggregate())
            update_value.assert_called_once_with(
                "optional_value",
                0.0,
                source="optional_value",
                confidence=0.55,
                last_error="optional offline",
            )
            log_debug.assert_called_once_with(
                "DBus adapter optional read fell back to zero key=%s: %s",
                "optional_value",
                error,
            )

    def test_read_executor_optional_helpers_have_explicit_defaults_and_sources(self) -> None:
        self.assertEqual(read_module._spec_text({}, "service"), "")
        self.assertEqual(read_module._spec_text({"service": None}, "service"), "")
        self.assertEqual(read_module._spec_text({"service": 42}, "service"), "")
        self.assertEqual(read_module._spec_text({"service": ""}, "service"), "")
        self.assertEqual(read_module._spec_text({"service": "svc"}, "service"), "svc")
        self.assertTrue(read_module.DbusReadExecutor._optional_zero_on_error({"optional_zero_on_error": "TRUE"}))
        self.assertTrue(read_module.DbusReadExecutor._optional_zero_on_error({"optional_zero_on_error": " yes "}))
        self.assertTrue(read_module.DbusReadExecutor._optional_zero_on_error({"optional_zero_on_error": "on"}))
        self.assertFalse(read_module.DbusReadExecutor._optional_zero_on_error({}))
        self.assertFalse(read_module.DbusReadExecutor._optional_zero_on_error({"optional_zero_on_error": "TRUE " + "x"}))
        self.assertEqual(read_module.DbusReadExecutor._optional_confidence({}), 0.2)
        self.assertEqual(read_module.DbusReadExecutor._optional_confidence({"optional_confidence": None}), 0.2)
        self.assertEqual(read_module.DbusReadExecutor._optional_confidence({"optional_confidence": 0.0}), 0.2)
        self.assertEqual(read_module.DbusReadExecutor._optional_confidence({"optional_confidence": 0.45}), 0.45)
        self.assertEqual(read_module.DbusReadExecutor._spec_source({"service": "svc", "prefix": "ignored"}, fallback="fb"), "svc")
        self.assertEqual(read_module.DbusReadExecutor._spec_source({"service": "", "prefix": "pv."}, fallback="fb"), "pv.")
        self.assertEqual(read_module.DbusReadExecutor._spec_source({}, fallback="fb"), "fb")
        self.assertEqual(read_module.DbusReadExecutor._spec_source({}), "")

    def test_read_executor_aggregate_dispatch_contracts_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            aggregate = _install_mock(adapter.read_executor, "_poll_aggregate_step", MagicMock(return_value="deferred"))
            self.assertEqual(
                adapter.read_executor._poll_sum_step(
                    "sum_power",
                    {"aggregate": "sum", "service": "svc.sum", "paths": ["/L1", "/L2"]},
                ),
                "deferred",
            )
            aggregate.assert_called_once_with(
                "sum_power",
                ("sum", (("svc.sum", "/L1"), ("svc.sum", "/L2"))),
                [("svc.sum", "/L1"), ("svc.sum", "/L2")],
            )

            with patch.object(adapter.cache, "update_value") as update_value:
                self.assertEqual(
                    adapter.read_executor._poll_sum_step("empty_sum", {"aggregate": "sum", "service": "svc.empty"}),
                    "applied",
                )
                update_value.assert_called_once_with("empty_sum", 0.0, source="svc.empty")

            aggregate.reset_mock()
            with patch.object(adapter.cache, "update_value") as update_value:
                self.assertEqual(
                    adapter.read_executor._poll_sum_step(
                        "empty_path_sum",
                        {"aggregate": "sum", "service": "svc.empty", "paths": [""]},
                    ),
                    "applied",
                )
                aggregate.assert_not_called()
                update_value.assert_called_once_with("empty_path_sum", 0.0, source="svc.empty")

            adapter.cache.update_services(["pv.2", "other.1", "pv.1"])
            aggregate.reset_mock()
            self.assertEqual(
                adapter.read_executor._poll_services_sum_step(
                    "pv_sum",
                    {"aggregate": "services-sum", "prefix": "pv.", "path": "/Ac/Power"},
                ),
                "deferred",
            )
            aggregate.assert_called_once_with(
                "pv_sum",
                ("services-sum", "/Ac/Power", ("pv.1", "pv.2")),
                [("pv.1", "/Ac/Power"), ("pv.2", "/Ac/Power")],
            )

            with self.assertRaisesRegex(RuntimeError, "No cached services for prefix 'missing\\.'"):
                adapter.read_executor._poll_services_sum_step(
                    "missing_sum",
                    {"aggregate": "services-sum", "prefix": "missing.", "path": "/Ac/Power"},
                )
            with self.assertRaisesRegex(RuntimeError, "No cached services for prefix ''"):
                empty_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-empty")))
                empty_adapter.read_executor._poll_services_sum_step(
                    "missing_default_sum",
                    {"aggregate": "services-sum", "path": "/Ac/Power"},
                )

    def test_read_executor_pv_total_reuses_in_progress_members_without_rediscovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.read_executor._aggregates.state_for(
                "pv_power_w",
                (read_aggregate_module.PV_TOTAL_AGGREGATE, (("pv.cached", "/Ac/Power"),)),
                0.75,
            )
            aggregate = _install_mock(adapter.read_executor, "_poll_aggregate_step", MagicMock(return_value="deferred"))

            with patch.object(read_module, "pv_total_members") as discover:
                self.assertEqual(
                    adapter.read_executor._poll_pv_total_step(
                        "pv_power_w",
                        {
                            "aggregate": "pv-total",
                            "prefix": "pv.",
                            "path": "/Ac/Power",
                            "optional_confidence": 0.75,
                        },
                    ),
                    "deferred",
                )

            discover.assert_not_called()
            aggregate.assert_called_once_with(
                "pv_power_w",
                (read_aggregate_module.PV_TOTAL_AGGREGATE, (("pv.cached", "/Ac/Power"),)),
                [("pv.cached", "/Ac/Power")],
                ignore_member_errors=True,
                empty_confidence=0.75,
            )

    def test_read_executor_update_and_complete_cache_contracts_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            target = read_targets_module.read_target("svc.update", "/Value")
            self.assertIsNotNone(target)
            assert target is not None

            with patch.object(adapter.cache, "update_value") as update_value:
                adapter.read_executor._update_read_value("semantic_value", target, 42.0)
                update_value.assert_has_calls(
                    [
                        unittest.mock.call("path:svc.update/Value", 42.0, source="svc.update/Value"),
                        unittest.mock.call("semantic_value", 42.0, source="svc.update/Value"),
                    ]
                )
                self.assertEqual(update_value.call_count, 2)

                update_value.reset_mock()
                adapter.read_executor._update_read_value("path:svc.update/Value", target, 43.0)
                update_value.assert_called_once_with("path:svc.update/Value", 43.0, source="svc.update/Value")

            state = read_aggregate_module.AggregateState(("sum", (("svc.a", "/A"),)), empty_confidence=0.35)
            adapter.read_executor._record_aggregate_member(state, "svc.a", "/A", 2.5)
            state.record_error("svc.b", "/B", RuntimeError("offline"))
            adapter.read_executor._aggregates.state_for("aggregate_key", state.signature, 0.35)

            with patch.object(adapter.cache, "update_value") as update_value:
                update_value.reset_mock()
                adapter.read_executor._complete_aggregate("aggregate_key", state)
                update_value.assert_called_once_with(
                    "aggregate_key",
                    2.5,
                    source="svc.a/A",
                    confidence=1.0,
                    last_error="svc.b/B: offline",
                )

                empty_state = read_aggregate_module.AggregateState(("sum", (("svc.empty", "/A"),)), empty_confidence=0.35)
                update_value.reset_mock()
                adapter.read_executor._complete_aggregate("empty_aggregate", empty_state)
                update_value.assert_called_once_with(
                    "empty_aggregate",
                    0.0,
                    source="empty_aggregate",
                    confidence=0.35,
                    last_error="",
                )

    def test_read_executor_aggregate_default_confidence_and_prefix_defaults_are_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            _install_mock(adapter.read_executor, "read_optional_busitem", MagicMock(side_effect=RuntimeError("optional asleep")))

            self.assertEqual(
                adapter.read_executor._poll_aggregate_step(
                    "empty_optional",
                    ("pv-total", (("svc.optional", "/Power"),)),
                    [("svc.optional", "/Power")],
                    ignore_member_errors=True,
                ),
                "applied",
            )
            self.assertEqual(adapter.cache.values["empty_optional"]["confidence"], 1.0)
            self.assertEqual(adapter.cache.values["empty_optional"]["source"], "empty_optional")

            adapter.cache.update_services(["svc.z", "svc.a"])
            _install_mock(adapter.read_executor, "read_busitem", MagicMock(return_value=77.0))
            self.assertEqual(
                adapter.read_executor._poll_first_service("first_default", {"aggregate": "first-service", "path": "/Soc"}),
                "applied",
            )
            adapter.read_executor.read_busitem.assert_called_once_with("svc.a", "/Soc")
            self.assertEqual(adapter.cache.values["first_default"]["value"], 77.0)

            with self.assertRaisesRegex(RuntimeError, "No cached services for prefix ''"):
                empty_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-empty")))
                empty_adapter.read_executor._poll_first_service(
                    "first_missing_default",
                    {"aggregate": "first-service", "path": "/Soc"},
                )

    def test_read_executor_optional_aggregate_error_contract_is_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            state = read_aggregate_module.AggregateState(("pv-total", (("svc.optional", "/Power"),)), 0.2)
            error = RuntimeError("sleeping")

            with patch.object(adapter.cache, "mark_error") as mark_error, patch.object(read_module.logging, "debug") as log_debug:
                adapter.read_executor._record_optional_aggregate_error("svc.optional", "/Power", state, error)

            mark_error.assert_called_once_with(
                "path:svc.optional/Power",
                source="svc.optional/Power",
                error=error,
            )
            self.assertEqual(state.errors, ["svc.optional/Power: sleeping"])
            log_debug.assert_called_once_with(
                "DBus adapter optional aggregate member failed %s%s: %s",
                "svc.optional",
                "/Power",
                error,
            )

    def test_coalesced_commands_use_stable_filename_and_latest_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = DbusCommandInbox(str(Path(temp_dir) / "commands"))
            first = inbox.enqueue({"kind": "set_value", "value": 1, "created_at": 10.0, "coalesce_key": "ev:/Mode"})
            second = inbox.enqueue({"kind": "set_value", "value": 2, "created_at": 20.0, "coalesce_key": "ev:/Mode"})

            self.assertEqual(first, second)
            pending = inbox.load_pending()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0][1]["value"], 2)
            self.assertEqual(pending[0][1]["created_at"], 10.0)
            self.assertEqual(pending[0][1]["lifecycle_state"], "coalesced")
            self.assertGreater(pending[0][1]["updated_at"], 0.0)
            self.assertTrue(Path(first).name.startswith("coalesced-"))

    def test_coalesce_key_overrides_explicit_command_id_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = DbusCommandInbox(str(Path(temp_dir) / "commands"))
            first = inbox.enqueue({"id": "manual-a", "kind": "set_value", "value": 1, "coalesce_key": "ev:/Mode"})
            second = inbox.enqueue({"id": "manual-b", "kind": "set_value", "value": 2, "coalesce_key": "ev:/Mode"})

            self.assertEqual(first, second)
            self.assertTrue(Path(first).name.startswith("coalesced-"))
            self.assertFalse((Path(temp_dir) / "commands" / "manual-a.json").exists())
            self.assertFalse((Path(temp_dir) / "commands" / "manual-b.json").exists())

    def test_coalesced_physical_file_keeps_higher_priority_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = DbusCommandInbox(str(Path(temp_dir) / "commands"))
            path = inbox.enqueue(
                {"kind": "set_value", "value": "off", "priority": "safety", "coalesce_key": "relay:/StartStop"}
            )
            same_path = inbox.enqueue(
                {"kind": "set_value", "value": "on", "priority": "diagnostic", "coalesce_key": "relay:/StartStop"}
            )

            self.assertEqual(path, same_path)
            self.assertEqual(inbox.load_pending()[0][1]["value"], "off")

            inbox.enqueue({"kind": "set_value", "value": "manual", "priority": "user", "coalesce_key": "relay:/StartStop"})
            self.assertEqual(inbox.load_pending()[0][1]["value"], "off")

            inbox.enqueue(
                {"kind": "set_value", "value": "new-off", "priority": "safety", "coalesce_key": "relay:/StartStop"}
            )
            self.assertEqual(inbox.load_pending()[0][1]["value"], "new-off")

    def test_rate_limiter_defers_without_sleeping(self) -> None:
        default_limiter = DbusRateLimiter()
        self.assertEqual(default_limiter.intervals, {"read": 0.25, "write": 0.35, "introspection": 2.0})
        self.assertTrue(default_limiter.due("read", now=0.0))

        limiter = DbusRateLimiter(read_interval_seconds=10.0)
        self.assertEqual(limiter.intervals, {"read": 10.0, "write": 0.35, "introspection": 2.0})
        self.assertEqual(limiter.next_at, {"read": 0.0, "write": 0.0, "introspection": 0.0})
        limiter.require_due("read")
        with self.assertRaises(DbusOperationDeferred) as deferred:
            limiter.require_due("read")
        self.assertEqual(deferred.exception.args, ("read",))
        limiter.mark("read", now=1.0)
        self.assertFalse(limiter.due("read", now=2.0))
        self.assertTrue(limiter.due("read", now=11.0))
        limiter.mark("write", now=5.0)
        limiter.mark("introspection", now=5.0)
        self.assertEqual(limiter.next_at["write"], 5.35)
        self.assertEqual(limiter.next_at["introspection"], 7.0)

        clamped = DbusRateLimiter(
            read_interval_seconds=-1.0,
            write_interval_seconds=-2.0,
            introspection_interval_seconds=-3.0,
        )
        self.assertEqual(clamped.intervals, {"read": 0.0, "write": 0.0, "introspection": 0.0})

    def test_circuit_breaker_states_priorities_and_timeout_detection(self) -> None:
        class _CustomDbusError(Exception):
            pass

        with patch.object(rate_module.dbus, "DBusException", _CustomDbusError, create=True):
            self.assertIs(rate_module._dbus_exception_type(), _CustomDbusError)
        with patch.object(rate_module.dbus, "DBusException", object, create=True):
            self.assertIs(rate_module._dbus_exception_type(), RuntimeError)
        with patch.object(rate_module.dbus, "DBusException", "bad", create=True):
            self.assertIs(rate_module._dbus_exception_type(), RuntimeError)
        with patch.object(rate_module, "dbus", object()):
            self.assertIs(rate_module._dbus_exception_type(), RuntimeError)
        self.assertEqual(rate_module._normalized_kind(" read "), "read")
        self.assertEqual(rate_module._normalized_kind("  "), "dbus")
        self.assertEqual(rate_module._normalized_priority(" USER "), "user")
        self.assertEqual(rate_module._normalized_priority("  "), "diagnostic")

        breaker = DbusCircuitBreaker(degraded_seconds=30.0, protective_seconds=60.0)
        self.assertEqual(breaker.degraded_seconds, 30.0)
        self.assertEqual(breaker.protective_seconds, 60.0)
        self.assertEqual(breaker.last_success_at, 0.0)
        self.assertEqual(breaker.last_error, "")
        self.assertEqual(breaker.consecutive_failures, 0)
        self.assertEqual(breaker.state(now=100.0), "ok")
        self.assertTrue(breaker.allows_priority("diagnostic"))
        self.assertTrue(breaker.allows_priority(""))

        class _NamedTimeout(Exception):
            def get_dbus_name(self) -> str:
                return "org.freedesktop.DBus.Error.NoReply"

        class _BrokenName(Exception):
            def get_dbus_name(self) -> str:
                raise RuntimeError("name unavailable")

        with patch.object(rate_module.time, "time", return_value=1000.0):
            for _index in range(3):
                breaker.record_error(_NamedTimeout("slow"))
            self.assertEqual(breaker.state(now=1001.0), "degraded")
            self.assertFalse(breaker.allows_priority("discovery"))
            self.assertTrue(breaker.allows_priority("optional"))
            for _index in range(2):
                breaker.record_error(TimeoutError("timeout"))
            self.assertEqual(breaker.consecutive_failures, 5)
            self.assertEqual(breaker.state(now=1001.0), "degraded")
            for _index in range(3):
                breaker.record_error(TimeoutError("timeout"))
            self.assertEqual(breaker.state(now=1001.0), "protective")
            self.assertFalse(breaker.allows_priority("optional"))
            self.assertTrue(breaker.allows_priority("read"))

        with patch.object(rate_module.time, "time", return_value=1010.0):
            breaker.record_success(3.5, kind="write")
        self.assertEqual(breaker.last_success_at, 1010.0)
        self.assertEqual(breaker.last_error, "")
        self.assertEqual(breaker.consecutive_failures, 0)
        self.assertIn("write", breaker.latencies_by_kind)
        with patch.object(rate_module.time, "time", return_value=1011.0):
            breaker.record_success(4.5)
        self.assertIn("dbus", breaker.latencies_by_kind)
        breaker.record_error(_BrokenName("plain"))
        health = breaker.health()
        self.assertEqual(health["state"], breaker.state())
        self.assertEqual(health["last_success_at"], breaker.last_success_at)
        self.assertEqual(health["last_error"], "plain")
        self.assertIn("avg_latency_ms", health)
        self.assertIn("operations", health)
        self.assertIn("write", health["operations"])
        self.assertEqual(health["errors_60s"], 1)
        self.assertEqual(health["consecutive_failures"], 1)
        self.assertEqual(breaker.state(now=2000.0), "ok")
        with patch.object(rate_module.time, "time", return_value=3000.0):
            breaker.record_success(1.0, kind="")
            self.assertIn("dbus", breaker.latencies_by_kind)
            breaker.record_error(TimeoutError("timeout"))
            self.assertEqual(breaker.last_error, "timeout")
            self.assertIn(("dbus"), breaker.latencies_by_kind)
            self.assertGreaterEqual(breaker.health()["successes_60s"], 1)
        pruning_breaker = DbusCircuitBreaker()
        with patch.object(rate_module.time, "time", return_value=1.0):
            pruning_breaker.record_success(1.0)
            pruning_breaker.record_error(TimeoutError("timeout"))
        with patch.object(rate_module.time, "time", return_value=100.0):
            self.assertEqual(pruning_breaker.health()["successes_60s"], 0)
            self.assertEqual(pruning_breaker.health()["errors_60s"], 0)
        pruning_breaker = DbusCircuitBreaker()
        with patch.object(rate_module.time, "time", return_value=40.0):
            pruning_breaker.record_success(1.0)
            pruning_breaker.record_error(RuntimeError("plain"), kind=" write ")
        with patch.object(rate_module.time, "time", return_value=100.0):
            boundary_health = pruning_breaker.health()
        self.assertEqual(boundary_health["successes_60s"], 1)
        self.assertEqual(boundary_health["errors_60s"], 1)
        self.assertEqual(list(pruning_breaker._errors), [(40.0, "write")])
        self.assertEqual(list(pruning_breaker._successes), [(40.0, "dbus")])
        pruning_breaker = DbusCircuitBreaker()
        with patch.object(rate_module.time, "time", return_value=39.5):
            pruning_breaker.record_success(1.0)
            pruning_breaker.record_error(RuntimeError("plain"))
        with patch.object(rate_module.time, "time", return_value=40.0):
            pruning_breaker.record_success(1.0)
            pruning_breaker.record_error(RuntimeError("plain"))
        with patch.object(rate_module.time, "time", return_value=100.0):
            narrowed_health = pruning_breaker.health()
        self.assertEqual(narrowed_health["successes_60s"], 1)
        self.assertEqual(narrowed_health["errors_60s"], 1)
        self.assertEqual(list(pruning_breaker._successes), [(40.0, "dbus")])
        self.assertEqual(list(pruning_breaker._errors), [(40.0, "dbus")])
        clamped_breaker = DbusCircuitBreaker(degraded_seconds=0.0, protective_seconds=-1.0)
        self.assertEqual(clamped_breaker.degraded_seconds, 1.0)
        self.assertEqual(clamped_breaker.protective_seconds, 1.0)

    def test_circuit_breaker_timeout_name_and_priority_contracts(self) -> None:
        class _NamedDbusTimeout(Exception):
            def __init__(self, name: object) -> None:
                super().__init__("plain")
                self.name = name

            def get_dbus_name(self) -> object:
                return self.name

        self.assertTrue(DbusCircuitBreaker._looks_like_timeout(TimeoutError("timed out")))
        self.assertTrue(DbusCircuitBreaker._looks_like_timeout(RuntimeError("NoReply from dbus")))
        self.assertTrue(DbusCircuitBreaker._looks_like_timeout(RuntimeError("no_reply from dbus")))
        self.assertTrue(DbusCircuitBreaker._looks_like_timeout(_NamedDbusTimeout("org.freedesktop.DBus.Error.NoReply")))
        self.assertFalse(DbusCircuitBreaker._looks_like_timeout(RuntimeError("plain failure")))
        self.assertEqual(rate_module._dbus_error_name(_NamedDbusTimeout("Example.Error")), "example.error")
        self.assertEqual(rate_module._dbus_error_name(RuntimeError("plain")), "")
        self.assertEqual(rate_module._dbus_error_name(_NamedDbusTimeout(RuntimeError("bad"))), "bad")

        class _BrokenName(Exception):
            def get_dbus_name(self) -> str:
                raise RuntimeError("name unavailable")

        self.assertEqual(rate_module._dbus_error_name(_BrokenName("plain")), "")

        breaker = DbusCircuitBreaker()
        breaker.protective_until = 100.0
        breaker.degraded_until = 200.0
        self.assertEqual(breaker.state(now=50.0), "protective")
        self.assertEqual(breaker.state(now=100.0), "degraded")
        self.assertEqual(breaker.state(now=150.0), "degraded")
        self.assertEqual(breaker.state(now=200.0), "ok")
        self.assertEqual(breaker.state(now=250.0), "ok")
        self.assertIs(breaker._kind_window("read"), breaker._kind_window("read"))
        self.assertIs(breaker._kind_window(""), breaker._kind_window("dbus"))
        with patch.object(breaker, "state", return_value="protective"):
            self.assertTrue(breaker.allows_priority("safety"))
            self.assertTrue(breaker.allows_priority("USER"))
            self.assertTrue(breaker.allows_priority("read"))
            self.assertFalse(breaker.allows_priority("optional"))
            self.assertFalse(breaker.allows_priority("unknown"))
        with patch.object(breaker, "state", return_value="degraded"):
            self.assertTrue(breaker.allows_priority("optional"))
            self.assertFalse(breaker.allows_priority("discovery"))

    def test_circuit_breaker_default_kind_and_health_summary_contracts(self) -> None:
        default_breaker = DbusCircuitBreaker()
        self.assertEqual(default_breaker.degraded_seconds, 60.0)
        self.assertEqual(default_breaker.protective_seconds, 180.0)
        self.assertEqual(default_breaker.degraded_until, 0.0)
        self.assertEqual(default_breaker.protective_until, 0.0)

        breaker = DbusCircuitBreaker()
        latency_window = MagicMock()
        kind_window = MagicMock()
        breaker.latencies = latency_window
        with patch.object(breaker, "_kind_window", MagicMock(return_value=kind_window)) as kind_window_factory:
            with patch.object(rate_module.time, "time", return_value=42.0):
                breaker.record_success(7.5)
        latency_window.record_latency.assert_called_once_with(7.5, now=42.0)
        kind_window.record_latency.assert_called_once_with(7.5, now=42.0)
        kind_window_factory.assert_called_once_with("dbus")
        self.assertEqual(list(breaker._successes), [(42.0, "dbus")])

        breaker = DbusCircuitBreaker()
        latency_window = MagicMock()
        kind_window = MagicMock()
        latency_window.summary.return_value = {"timeouts_60s": 1}
        breaker.latencies = latency_window
        with patch.object(breaker, "_kind_window", MagicMock(return_value=kind_window)) as kind_window_factory:
            with patch.object(rate_module.time, "time", return_value=50.0):
                breaker.record_error(TimeoutError("timeout"))
        latency_window.record_timeout.assert_called_once_with(now=50.0)
        latency_window.summary.assert_called_once_with(now=50.0)
        kind_window.record_timeout.assert_called_once_with(now=50.0)
        kind_window_factory.assert_called_once_with("dbus")
        self.assertEqual(list(breaker._errors), [(50.0, "dbus")])

        breaker = DbusCircuitBreaker()
        breaker.degraded_until = 10.0
        breaker.protective_until = 20.0
        main_summary = {"avg_latency_ms": 1.5, "timeouts_60s": 2}
        read_summary = {"avg_latency_ms": 3.0, "timeouts_60s": 4}
        breaker.latencies = MagicMock()
        breaker.latencies.summary.return_value = main_summary
        read_window = MagicMock()
        read_window.summary.return_value = read_summary
        breaker.latencies_by_kind = {"read": read_window}
        with patch.object(rate_module.time, "time", return_value=77.0):
            health = breaker.health()

        breaker.latencies.summary.assert_called_once_with(now=77.0)
        read_window.summary.assert_called_once_with(now=77.0)
        self.assertEqual(health["degraded_until"], 20.0)
        self.assertEqual(health["operations"], {"read": read_summary})
        self.assertEqual(health["avg_latency_ms"], 1.5)
        self.assertEqual(health["timeouts_60s"], 2)

    def test_connection_manager_delegates_private_bus_and_resets_safely(self) -> None:
        manager = DbusConnectionManager()
        fake_bus = MagicMock()
        fake_bus.get_object.return_value = "object"
        with patch.object(rate_module.dbus, "SystemBus", MagicMock(return_value=fake_bus)) as system_bus:
            self.assertIs(manager.bus(), fake_bus)
            self.assertIs(manager.bus(), fake_bus)
            system_bus.assert_called_once_with(private=True)
            self.assertEqual(manager.get_object("svc", "/Path", introspect=True), "object")
            fake_bus.get_object.assert_called_once_with("svc", "/Path", introspect=True)
            fake_bus.get_object.reset_mock()
            self.assertEqual(manager.get_object("svc", "/Other"), "object")
            fake_bus.get_object.assert_called_once_with("svc", "/Other", introspect=False)

        manager.reset()
        fake_bus.close.assert_called_once_with()
        self.assertIsNone(manager._bus)

        manager._bus = object()
        with self.assertRaisesRegex(TypeError, "^DBus bus does not provide get_object$"):
            manager.get_object("svc", "/Path")

    def test_resource_monitor_reports_procfs_style_resource_health(self) -> None:
        monitor = ResourceMonitor(pid=123)
        _install_mock(monitor, "_read_system_cpu", MagicMock(side_effect=[(1000, 500), (1100, 540)]))
        _install_mock(monitor, "_read_process_cpu_seconds", MagicMock(side_effect=[1.0, 1.2]))
        _install_mock(
            monitor,
            "_read_meminfo",
            MagicMock(return_value={"MemTotal": 1024.0 * 1024.0, "MemAvailable": 512.0 * 1024.0}),
        )
        _install_mock(
            monitor,
            "_read_process_status",
            MagicMock(return_value={"VmRSS": 1234.0, "VmHWM": 2345.0, "Threads": 3.0, "FDSize": 64.0}),
        )
        _install_mock(monitor, "_open_fd_count", MagicMock(return_value=7))
        _install_mock(monitor, "_loadavg", MagicMock(return_value=(0.2, 0.3, 0.4)))

        with patch.object(resource_module.time, "monotonic", side_effect=[10.0, 11.0]):
            first = monitor.snapshot()
            second = monitor.snapshot()

        self.assertEqual(first["state"], "ok")
        self.assertEqual(first["process"]["open_fds"], 7)
        self.assertAlmostEqual(second["system_cpu_pct"], 60.0)
        self.assertAlmostEqual(second["process"]["cpu_pct_one_core"], 20.0)
        self.assertEqual(resource_module.resource_state(1.1, 10.0, 100000.0), "busy")
        self.assertEqual(resource_module.resource_state(0.1, 95.0, 100000.0), "constrained")
        self.assertEqual(resource_module.resource_state(0.1, 10.0, 1000.0), "constrained")

    def test_resource_monitor_procfs_failure_edges(self) -> None:
        monitor = ResourceMonitor(pid=123)
        with patch.object(resource_module.os, "getloadavg", side_effect=OSError("missing")):
            self.assertEqual(monitor._loadavg(), (0.0, 0.0, 0.0))
        with patch.object(builtins, "open", side_effect=OSError("missing")):
            self.assertEqual(monitor._read_system_cpu(), (0, 0))
            self.assertEqual(monitor._read_process_cpu_seconds(), 0.0)
            self.assertEqual(monitor._read_meminfo(), {})
            self.assertEqual(monitor._read_process_status(), {})
        read_data = "Name:\tpython\nThreads:\t3\nVmRSS:\tbad kB\n"
        with patch.object(builtins, "open", unittest.mock.mock_open(read_data=read_data)):
            self.assertEqual(monitor._read_process_status(), {"Threads": 3.0})
        with patch.object(resource_module.os, "listdir", side_effect=OSError("missing")):
            self.assertEqual(monitor._open_fd_count(), 0)

    def test_tick_health_rolls_recent_tick_durations(self) -> None:
        health = TickHealth(window_seconds=10.0)
        health.record(duration_ms=5.0, expected_interval_s=1.0, now=100.0)
        health.record(duration_ms=2500.0, expected_interval_s=1.0, now=101.0)
        snapshot = health.snapshot(now=102.0)
        self.assertEqual(snapshot["tick_count_60s"], 2)
        self.assertEqual(snapshot["late_ticks_60s"], 1)
        self.assertEqual(snapshot["max_tick_gap_ms_60s"], 1000.0)
        self.assertEqual(snapshot["late_tick_gap_count_60s"], 0)
        health.record(duration_ms=1.0, expected_interval_s=1.0, now=104.5)
        self.assertEqual(health.snapshot(now=104.5)["late_tick_gap_count_60s"], 1)
        health.record(duration_ms=1.0, expected_interval_s=1.0, now=120.0)
        self.assertEqual(health.snapshot(now=120.0)["tick_count_60s"], 1)

    def test_connection_manager_creates_private_bus_and_resets_best_effort(self) -> None:
        manager = DbusConnectionManager()
        fake_bus = MagicMock()
        with patch.object(rate_module.dbus, "SystemBus", return_value=fake_bus) as system_bus:
            self.assertIs(manager.bus(), fake_bus)
            self.assertIs(manager.bus(), fake_bus)
            system_bus.assert_called_once_with(private=True)
        manager.reset()
        fake_bus.close.assert_called_once()
        self.assertIsNone(manager._bus)

        bad_bus = MagicMock()
        bad_bus.close.side_effect = RuntimeError("already closed")
        manager._bus = bad_bus
        manager.reset()
        self.assertIsNone(manager._bus)

        manager._bus = object()
        manager.reset()
        self.assertIsNone(manager._bus)

    def test_read_scheduler_tracks_due_reads_and_degraded_intervals(self) -> None:
        scheduler = DbusReadScheduler({"grid": {"interval": 2.0, "priority": "read"}})

        due = scheduler.next_due(now=10.0, circuit_state="degraded", priority_allowed=lambda _priority: True)
        self.assertIsNotNone(due)
        assert due is not None
        key, _spec, interval = due
        self.assertEqual(key, "grid")
        self.assertEqual(interval, 6.0)

        scheduler.record_success(key, now=10.0, interval=interval)
        self.assertIsNone(scheduler.next_due(now=15.0, circuit_state="ok", priority_allowed=lambda _priority: True))
        self.assertIsNotNone(scheduler.next_due(now=16.0, circuit_state="ok", priority_allowed=lambda _priority: True))
        scheduler.record_error("grid", now=20.0, interval=2.0)
        self.assertIsNone(scheduler.next_due(now=21.0, circuit_state="ok", priority_allowed=lambda _priority: True))
        self.assertEqual(DbusReadScheduler.effective_interval({"interval": 2.0}, "protective"), 10.0)
        self.assertEqual(DbusReadScheduler.effective_interval({"interval": True}, "ok"), 2.0)
        self.assertEqual(DbusReadScheduler.effective_interval({"interval": "bad"}, "ok"), 2.0)
        self.assertEqual(DbusReadScheduler.effective_interval({"interval": object()}, "ok"), 2.0)
        blocked = DbusReadScheduler({"grid": {"interval": 1.0, "priority": "discovery"}})
        self.assertIsNone(blocked.next_due(now=1.0, circuit_state="ok", priority_allowed=lambda _priority: False))

        fair = DbusReadScheduler(
            {
                "grid": {"interval": 2.0, "priority": "read"},
                "pv": {"interval": 2.0, "priority": "read"},
                "battery": {"interval": 2.0, "priority": "read"},
            }
        )
        fair.next_read_at = {"grid": 100.0, "pv": 90.0, "battery": 0.0}
        due = fair.next_due(now=100.0, circuit_state="ok", priority_allowed=lambda _priority: True)
        self.assertIsNotNone(due)
        assert due is not None
        self.assertEqual(due[0], "battery")

    def test_read_spec_from_mapping_validates_known_fields(self) -> None:
        spec = read_spec_from_mapping(
            {
                "aggregate": "pv-total",
                "dc_path": "/Dc/Pv/Power",
                "dc_service": "com.victronenergy.system",
                "path": "/Ac/Power",
                "prefix": "com.victronenergy.pvinverter",
                "priority": "read",
                "service": "svc",
                "paths": ["/A", "/B"],
                "interval": 2,
                "use_dc_pv": True,
                "optional_zero_on_error": False,
                "optional_confidence": 0.2,
            }
        )
        self.assertEqual(spec["aggregate"], "pv-total")
        self.assertEqual(spec["dc_path"], "/Dc/Pv/Power")
        self.assertEqual(spec["dc_service"], "com.victronenergy.system")
        self.assertEqual(spec["path"], "/Ac/Power")
        self.assertEqual(spec["prefix"], "com.victronenergy.pvinverter")
        self.assertEqual(spec["priority"], "read")
        self.assertEqual(spec["service"], "svc")
        self.assertEqual(spec["paths"], ["/A", "/B"])
        self.assertEqual(spec["interval"], 2.0)
        self.assertEqual(spec["optional_confidence"], 0.2)
        self.assertFalse(spec["optional_zero_on_error"])
        self.assertTrue(spec["use_dc_pv"])

        with self.assertRaisesRegex(KeyError, "unknown read spec field"):
            read_spec_from_mapping({"unexpected": "value"})
        with self.assertRaisesRegex(TypeError, "service must be str"):
            read_spec_from_mapping({"service": object()})
        with self.assertRaisesRegex(TypeError, "interval must be float"):
            read_spec_from_mapping({"interval": True})
        with self.assertRaisesRegex(TypeError, "paths must be list\\[str\\]"):
            read_spec_from_mapping({"paths": ["/ok", 1]})
        with self.assertRaisesRegex(TypeError, "use_dc_pv must be bool"):
            read_spec_from_mapping({"use_dc_pv": 1})
        with self.assertRaisesRegex(TypeError, "optional_confidence must be float"):
            read_spec_from_mapping({"optional_confidence": True})
        for bad_field, bad_value, expected_type in (
            ("service", object(), "object"),
            ("interval", True, "bool"),
            ("use_dc_pv", 1, "int"),
            ("paths", ("/ok",), "tuple"),
        ):
            with self.subTest(field=bad_field):
                with self.assertRaises(TypeError) as caught:
                    read_spec_from_mapping({bad_field: bad_value})
                self.assertIn(f"got {expected_type}", str(caught.exception))

        true_spec = read_spec_from_mapping({"optional_zero_on_error": True})
        self.assertTrue(true_spec["optional_zero_on_error"])

    def test_discovery_manager_tracks_success_and_error_backoff(self) -> None:
        discovery = DbusDiscoveryManager(interval_seconds=900.0)

        self.assertTrue(discovery.due(now=100.0, priority_allowed=lambda _priority: True))
        discovery.record_success(now=100.0)
        self.assertEqual(discovery.last_success_at, 100.0)
        self.assertEqual(discovery.next_scan_at, 1000.0)
        self.assertFalse(discovery.due(now=999.0, priority_allowed=lambda _priority: True))

        discovery.record_error(RuntimeError("dbus down"), now=200.0)
        self.assertEqual(discovery.last_error, "dbus down")
        self.assertEqual(discovery.next_scan_at, 260.0)
        self.assertFalse(discovery.due(now=260.0, priority_allowed=lambda _priority: False))

    def test_read_scheduler_errors_back_off_and_success_resets_failure_count(self) -> None:
        scheduler = DbusReadScheduler({"pv": {"interval": 2.0, "priority": "read"}})
        scheduler.record_error("pv", now=100.0, interval=2.0)
        self.assertEqual(scheduler.failure_counts["pv"], 1)
        self.assertEqual(scheduler.next_read_at["pv"], 130.0)
        scheduler.record_error("pv", now=130.0, interval=2.0)
        self.assertEqual(scheduler.failure_counts["pv"], 2)
        self.assertEqual(scheduler.next_read_at["pv"], 190.0)
        scheduler.record_success("pv", now=200.0, interval=2.0)
        self.assertEqual(scheduler.failure_counts["pv"], 0)
        self.assertEqual(scheduler.next_read_at["pv"], 202.0)
        scheduler.force_due(["missing"])
        self.assertNotIn("missing", scheduler.next_read_at)

    def test_pv_member_backoff_ignores_non_numeric_error_timestamps(self) -> None:
        key = dbus_path_key("com.victronenergy.system", "/Dc/Pv/Power")
        for error_at in (True, "bad", object()):
            cached = {key: {"status": "error", "error_at": error_at}}
            self.assertFalse(
                read_pv_module.pv_member_recently_failed(
                    cached,
                    "com.victronenergy.system",
                    "/Dc/Pv/Power",
                    now=100.0,
                )
            )

    def test_adapter_static_config_helpers_cover_defaults_and_invalid_instance(self) -> None:
        parser = configparser.ConfigParser()
        parser["DEFAULT"] = {
            "ServiceName": "com.example.ev",
            "DeviceInstance": "bad",
            "AutoGridL2Path": "",
            "AutoPvService": "pv.fixed",
            "AutoBatteryService": "com.victronenergy.battery.example",
            "AutoBatteryServicePrefix": "com.victronenergy.battery",
            "AutoBatterySocPath": "/Soc",
        }
        self.assertEqual(process_config_module.evcharger_service_name(parser["DEFAULT"]), "com.example.ev.http_60")
        specs = process_config_module.configured_read_specs(parser["DEFAULT"])
        self.assertEqual(specs["grid_power_w"]["paths"], ["/Ac/Grid/L1/Power", "/Ac/Grid/L3/Power"])
        self.assertEqual(specs["pv_power_w"]["service"], "pv.fixed")
        self.assertEqual(specs["pv_power_w"]["aggregate"], "pv-total")
        self.assertEqual(specs["pv_power_w"]["dc_service"], "com.victronenergy.system")
        self.assertEqual(specs["pv_power_w"]["dc_path"], "/Dc/Pv/Power")
        self.assertTrue(specs["pv_power_w"]["use_dc_pv"])
        self.assertEqual(specs["battery_soc"]["service"], "")
        self.assertEqual(specs["battery_soc"]["prefix"], "com.victronenergy.battery")
        self.assertEqual(specs["battery_soc"]["aggregate"], "first-service")
        self.assertEqual(specs["battery_soc"]["path"], "/Soc")
        self.assertEqual(process_config_module.configured_device_instance(parser["DEFAULT"]), 60)
        with self.assertRaises(ValueError):
            process_config_module.load_adapter_config("/tmp/does-not-exist-venus-evcharger.ini")

    def test_publish_desired_processes_one_path_per_tick(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nDbusGatewayLocalPublishBurstLimit=1\n", encoding="utf-8")
            paths = gateway_paths(str(Path(temp_dir) / "run"))
            adapter = DbusAdapter(str(config_path), paths=paths)
            writes: list[tuple[str, object]] = []

            class _FakeDbusService(dict):
                def __setitem__(self, key: str, value: object) -> None:
                    writes.append((key, value))
                    super().__setitem__(key, value)

            adapter.set_dbus_service(_FakeDbusService())
            adapter.write_scheduler.registered_paths.update({"/A", "/B"})
            command_path = adapter.commands.enqueue(
                {
                    "kind": "publish_desired",
                    "paths": {"/A": 1, "/B": 2},
                    "coalesce_key": "publish-batch",
                    "priority": "publish",
                }
            )

            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertEqual(writes, [("/A", 1)])
            remaining = read_json_file(command_path, {})
            self.assertEqual(remaining["paths"], {"/B": 2})

            adapter.rate_limiter.next_at["write"] = time.monotonic()
            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertEqual(writes, [("/A", 1), ("/B", 2)])
            self.assertFalse(Path(command_path).exists())

    def test_publish_desired_bursts_local_evcs_paths_without_remote_rate_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nDbusGatewayLocalPublishBurstLimit=3\n", encoding="utf-8")
            paths = gateway_paths(str(Path(temp_dir) / "run"))
            adapter = DbusAdapter(str(config_path), paths=paths)
            writes: list[tuple[str, object]] = []

            class _FakeDbusService(dict):
                def __setitem__(self, key: str, value: object) -> None:
                    writes.append((key, value))
                    super().__setitem__(key, value)

            adapter.set_dbus_service(_FakeDbusService())
            adapter.write_scheduler.registered_paths.update({"/A", "/B", "/C", "/D"})
            command_path = adapter.commands.enqueue(
                {
                    "kind": "publish_desired",
                    "paths": {"/A": 1, "/B": 2, "/C": 3, "/D": 4},
                    "coalesce_key": "publish-batch",
                    "priority": "publish",
                }
            )

            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertEqual(writes, [("/A", 1), ("/B", 2), ("/C", 3)])
            self.assertEqual(read_json_file(command_path, {})["paths"], {"/D": 4})
            self.assertEqual(adapter.write_scheduler.health(now=time.time())["processed_commands_60s"], 0)

            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertEqual(writes, [("/A", 1), ("/B", 2), ("/C", 3), ("/D", 4)])
            self.assertFalse(Path(command_path).exists())
            self.assertEqual(adapter.write_scheduler.health(now=time.time())["processed_commands_60s"], 1)

    def test_publish_desired_prioritizes_gui_paths_inside_large_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nDbusGatewayLocalPublishBurstLimit=3\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            writes: list[tuple[str, object]] = []

            class _FakeDbusService(dict):
                def __setitem__(self, key: str, value: object) -> None:
                    writes.append((key, value))
                    super().__setitem__(key, value)

            adapter.set_dbus_service(_FakeDbusService())
            adapter.write_scheduler.registered_paths.update(
                {"/Auto/Reason", "/Mode", "/Status", "/StartStop", "/Ac/L2/Power"}
            )
            command_path = adapter.commands.enqueue(
                {
                    "kind": "publish_desired",
                    "paths": {
                        "/Auto/Reason": "idle",
                        "/Ac/L2/Power": 0.0,
                        "/Mode": 1,
                        "/Status": 6,
                        "/StartStop": 1,
                    },
                    "coalesce_key": "publish-batch",
                    "priority": "publish",
                }
            )

            self.assertTrue(adapter.write_scheduler.process_one())

            self.assertEqual(writes, [("/Mode", 1), ("/StartStop", 1), ("/Status", 6)])
            self.assertEqual(
                read_json_file(command_path, {})["paths"],
                {"/Ac/L2/Power": 0.0, "/Auto/Reason": "idle"},
            )

    def test_repeated_local_publish_refreshes_cache_without_rewriting_dbus(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            writes: list[tuple[str, object]] = []

            class _FakeDbusService(dict):
                def __setitem__(self, key: str, value: object) -> None:
                    writes.append((key, value))
                    super().__setitem__(key, value)

            adapter.set_dbus_service(_FakeDbusService())
            adapter.write_scheduler.registered_paths.add("/Ac/Power")

            self.assertEqual(adapter.write_scheduler.publish_path("/Ac/Power", 1200.0), "applied")
            self.assertEqual(writes, [("/Ac/Power", 1200.0)])
            key = dbus_path_key(adapter.service_name, "/Ac/Power")
            adapter.cache.update_value(key, 1200.0, source="old", now=time.time() - 60.0)

            self.assertEqual(adapter.write_scheduler.publish_path("/Ac/Power", 1200.0), "applied")

            self.assertEqual(writes, [("/Ac/Power", 1200.0)])
            refreshed = adapter.cache.value_snapshot(adapter.cache.values[key], time.time())
            self.assertEqual(refreshed["value"], 1200.0)
            self.assertEqual(refreshed["status"], "fresh")
            self.assertLess(refreshed["age_s"], 1.0)

            adapter.write_scheduler.last_values["/Unregistered"] = "same"
            self.assertEqual(adapter.write_scheduler.publish_path("/Unregistered", "same"), "applied")
            self.assertNotIn(dbus_path_key(adapter.service_name, "/Unregistered"), adapter.cache.values)

    def test_write_scheduler_registers_paths_gui_writes_and_command_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            self.assertEqual(adapter.write_scheduler.process_command({"kind": "register_service"}), "applied")
            self.assertTrue(adapter.dbus_service.registered)
            outcome = adapter.write_scheduler.process_command(
                {"kind": "register_path", "path": "/Mode", "value": 1, "writeable": True}
            )
            self.assertEqual(outcome, "applied")
            self.assertIn("/Mode", adapter.write_scheduler.registered_paths)
            self.assertEqual(adapter.write_scheduler.process_command({"kind": "register_path", "path": "/Mode"}), "applied")
            self.assertTrue(adapter.write_scheduler.handle_gui_write("/Mode", 2))
            self.assertEqual(adapter.core_commands.load_pending()[0][1]["value"], 2)

            self.assertEqual(adapter.write_scheduler.publish_command({"kind": "publish_desired", "paths": []}), "dropped")
            self.assertEqual(adapter.write_scheduler.publish_command({"kind": "publish_desired", "paths": {}}), "applied")
            self.assertEqual(adapter.write_scheduler.publish_command({"kind": "publish_desired", "paths": {"/Missing": 1}}), "dropped")
            adapter.write_scheduler.local_publish_burst_limit = 1
            adapter.write_scheduler.registered_paths.update({"/A", "/B"})
            self.assertEqual(
                adapter.write_scheduler.publish_command({"kind": "publish_desired", "paths": {"/A": 1, "/B": 2}}),
                "deferred",
            )
            self.assertEqual(adapter.dbus_service["/A"], 1)
            adapter.write_scheduler.registered_paths.update({"/Ac/Power", "/Session/Time"})
            self.assertEqual(
                adapter.write_scheduler.publish_command(
                    {"kind": "publish_fields", "fields": {"ac_power_w": 1200.0, "session_time_s": 30}}
                ),
                "deferred",
            )
            self.assertEqual(adapter.dbus_service["/Ac/Power"], 1200.0)
            self.assertEqual(adapter.write_scheduler.publish_path("", 1), "applied")
            self.assertEqual(adapter.write_scheduler.publish_path("/Missing", 1), "dropped")
            self.assertEqual(adapter.write_scheduler.publish_command({"kind": "publish_value", "path": "/Missing", "value": 1}), "dropped")
            self.assertEqual(adapter.write_scheduler.publish_command({"kind": "publish_fields", "fields": []}), "dropped")
            self.assertEqual(adapter.write_scheduler.process_command({"kind": "unknown"}), "dropped")
            self.assertEqual(adapter.write_scheduler.process_command({"kind": "set_value"}), "dropped")
            self.assertFalse(adapter.write_scheduler.command_expired({"deadline_s": "bad"}))
            adapter.write_scheduler.drop_stale_coalesced_commands("/tmp/none", {})
            processed = Path(adapter.paths.command_dir) / "processed.json"
            stale = Path(adapter.paths.command_dir) / "stale.json"
            stale.parent.mkdir(parents=True, exist_ok=True)

            processed.write_text(json.dumps({"kind": "publish_value", "coalesce_key": "same"}), encoding="utf-8")
            stale.write_text(json.dumps({"kind": "publish_value", "coalesce_key": "same"}), encoding="utf-8")
            adapter.write_scheduler.drop_stale_coalesced_commands(str(processed), {"coalesce_key": "same"})
            self.assertTrue(processed.exists())
            self.assertFalse(stale.exists())

            commands = [
                ("fresh-publish", {"kind": "publish_value", "priority": "publish", "created_at": 999.0}),
                ("old-discovery", {"kind": "refresh_services", "priority": "discovery", "created_at": 990.0}),
            ]
            with patch.object(write_health_module.time, "time", return_value=1000.0):
                prioritized = adapter.write_scheduler.prioritized_commands(commands)
                self.assertEqual(adapter.write_scheduler.select_next_command(prioritized)[0], "fresh-publish")
            with patch.object(write_health_module.time, "time", return_value=1010.0):
                prioritized = adapter.write_scheduler.prioritized_commands(commands)
                self.assertEqual(adapter.write_scheduler.select_next_command(prioritized)[0], "old-discovery")
            protected = [("fresh-user", {"priority": "user", "created_at": 999.0}), *commands]
            with patch.object(write_health_module.time, "time", return_value=1010.0):
                prioritized = adapter.write_scheduler.prioritized_commands(protected)
                self.assertEqual(adapter.write_scheduler.select_next_command(prioritized)[0], "fresh-user")
            adapter.write_scheduler.queue_class_budgets["diagnostic"] = 0
            self.assertIsNone(adapter.write_scheduler.select_next_command([("diag", {"kind": "unknown"})]))
            self.assertFalse(adapter.write_scheduler.budget_available({"queue_class": "diagnostic"}, time.time()))
            adapter.write_scheduler.queue_class_budgets["diagnostic"] = 1
            adapter.commands.enqueue(
                {
                    "kind": "publish_value",
                    "path": "/Mode",
                    "value": 1,
                    "priority": "publish",
                    "coalesce_key": "publish:/Mode",
                }
            )
            self.assertFalse(adapter.write_scheduler.process_one(include_local_publish=False))
            for command_path, _command in adapter.commands.load_pending():
                adapter.commands.remove(command_path)

            publish_burst = DbusCommandInbox.coalesce(
                [
                    (
                        "old-auto",
                        {
                            "id": "old-auto",
                            "kind": "publish_value",
                            "priority": "publish",
                            "path": "/Auto/DbusBackoffBaseSeconds",
                            "created_at": 1.0,
                            "coalesce_key": "publish:/Auto/DbusBackoffBaseSeconds",
                        },
                    ),
                    (
                        "fresh-session",
                        {
                            "id": "fresh-session",
                            "kind": "publish_value",
                            "priority": "publish",
                            "path": "/Session/Time",
                            "created_at": 2.0,
                            "coalesce_key": "publish:/Session/Time",
                        },
                    ),
                    (
                        "old-l2-energy",
                        {
                            "id": "old-l2-energy",
                            "kind": "publish_value",
                            "priority": "publish",
                            "path": "/Ac/L2/Energy/Forward",
                            "created_at": 0.5,
                            "coalesce_key": "publish:/Ac/L2/Energy/Forward",
                        },
                    ),
                ]
            )
            self.assertEqual(adapter.write_scheduler.select_next_command(publish_burst)[0], "old-l2-energy")
            self.assertIsNone(
                adapter.write_scheduler.select_next_command(publish_burst, include_local_publish=False)
            )
            with patch.object(write_health_module.time, "time", return_value=0.0):
                adapter.write_scheduler.record_budget({"queue_class": "local-publish"})
            adapter.write_scheduler.prune_budget(time.time())
            self.assertEqual(adapter.write_scheduler.queue_class_usage_1s(), {})
            with patch.object(write_publish_module.time, "time", return_value=0.0):
                adapter.write_scheduler.record_processed()
            adapter.write_scheduler.prune_processed(time.time())
            self.assertEqual(adapter.write_scheduler.health(now=time.time())["processed_commands_60s"], 0)
            with patch.object(write_health_module.time, "time", return_value=0.0):
                adapter.write_scheduler.record_lifecycle({"queue_class": "local-publish"}, "applied")
            adapter.write_scheduler.prune_lifecycle(time.time())
            self.assertEqual(adapter.write_scheduler.lifecycle_counts_60s(), {})
            self.assertEqual(write_support_module.float_or_zero(object()), 0.0)

    def test_write_scheduler_publish_contract_edges_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.set_dbus_service(_FakeVeDbusService(), registered=True)

            self.assertEqual(adapter.write_scheduler.register_path({}), "applied")
            self.assertEqual(adapter.write_scheduler.registered_paths, set())
            self.assertEqual(adapter.dbus_service.added_paths, {})

            self.assertEqual(
                adapter.write_scheduler.register_path({"path": "/Mode", "value": 1, "writeable": True}),
                "applied",
            )
            self.assertEqual(
                adapter.dbus_service.added_paths["/Mode"],
                {
                    "value": 1,
                    "writeable": True,
                    "onchangecallback": adapter.write_scheduler.handle_gui_write,
                },
            )
            self.assertEqual(adapter.write_scheduler.last_values["/Mode"], 1)

            self.assertEqual(
                adapter.write_scheduler.register_path({"path": "/Status", "value": 0, "writeable": False}),
                "applied",
            )
            self.assertEqual(
                adapter.dbus_service.added_paths["/Status"],
                {"value": 0, "writeable": False, "onchangecallback": None},
            )
            self.assertEqual(adapter.write_scheduler.last_values["/Status"], 0)

            self.assertTrue(adapter.write_scheduler.handle_gui_write("/Mode", 2))
            self.assertEqual(adapter.write_scheduler.last_values["/Mode"], 2)
            gui_command = adapter.core_commands.load_pending()[0][1]
            self.assertEqual(gui_command["kind"], "user_command")
            self.assertEqual(gui_command["source"], "dbus-gui")
            self.assertEqual(gui_command["path"], "/Mode")
            self.assertEqual(gui_command["value"], 2)
            self.assertEqual(gui_command["priority"], "user")
            self.assertEqual(gui_command["coalesce_key"], "core:/Mode")

            with patch.object(adapter.json_writer, "write") as json_write:
                adapter.write_scheduler.local_publish_burst_limit = 1
                adapter.write_scheduler.registered_paths.update({"/DeferredA", "/DeferredB"})
                self.assertEqual(
                    adapter.write_scheduler.publish_command(
                        {"kind": "publish_desired", "paths": {"/DeferredA": 1, "/DeferredB": 2}}
                    ),
                    "deferred",
                )
            json_write.assert_not_called()

            self.assertEqual(adapter.write_scheduler.publish_command({"kind": "publish_value", "value": 1}), "applied")

            adapter.write_scheduler.local_publish_burst_limit = 2
            self.assertEqual(
                adapter.write_scheduler.publish_command(
                    {"kind": "publish_desired", "paths": {"/DeferredA": 3, "/MissingAfterPartial": 4}}
                ),
                "deferred",
            )

            with patch.object(write_publish_module.logging, "debug") as log_debug:
                self.assertEqual(adapter.write_scheduler.publish_path("/Missing", 1), "dropped")
            log_debug.assert_called_once_with(
                "Dropping publish for unregistered DBus path %s",
                "/Missing",
            )

            adapter.write_scheduler.registered_paths.add("/Ac/Power")
            self.assertEqual(adapter.write_scheduler.publish_path("/Ac/Power", 1200.0), "applied")
            key = dbus_path_key(adapter.service_name, "/Ac/Power")
            self.assertEqual(adapter.cache.values[key]["source"], f"{adapter.service_name}/Ac/Power")
            self.assertEqual(adapter.cache.values[key]["confidence"], 1.0)
            self.assertEqual(adapter.cache.values[key]["value"], 1200.0)

            stale = Path(adapter.paths.command_dir) / "stale.json"
            stale.parent.mkdir(parents=True, exist_ok=True)
            stale.write_text(json.dumps({"kind": "publish_value", "coalesce_key": "XXXX"}), encoding="utf-8")
            adapter.write_scheduler.drop_stale_coalesced_commands("processed.json", {})
            self.assertTrue(stale.exists())
            adapter.commands.remove(str(stale))

            remote_path = adapter.commands.enqueue(
                {"kind": "set_value", "service": "svc", "path": "/Remote", "priority": "user"}
            )
            self.assertIsNone(adapter.write_scheduler.next_local_publish_command())
            adapter.commands.remove(remote_path)

            local_path = adapter.commands.enqueue(
                {
                    "kind": "publish_value",
                    "path": "/Ac/Power",
                    "value": 1300.0,
                    "priority": "publish",
                    "coalesce_key": "publish:/Ac/Power",
                }
            )
            self.assertEqual(adapter.write_scheduler.next_local_publish_command()[0], local_path)

            seen_pending: list[CommandFileList | None] = []

            def _process_loaded(
                path: str,
                command: CommandMapping,
                *,
                pending_commands: CommandFileList | None = None,
            ) -> str:
                seen_pending.append(pending_commands)
                return "applied"

            _install_mock(adapter.write_scheduler, "process_loaded_command", MagicMock(side_effect=_process_loaded))
            self.assertEqual(adapter.write_scheduler.process_local_publish_burst(limit=1), 1)
            self.assertIsNotNone(seen_pending[0])
            assert seen_pending[0] is not None
            self.assertGreaterEqual(len(seen_pending[0]), 1)

            with patch.object(write_publish_module.time, "time", return_value=333.0):
                adapter.write_scheduler.record_processed()
            self.assertEqual(adapter.write_scheduler.last_processed_at, 333.0)
            self.assertEqual(list(adapter.write_scheduler._processed_events)[-1], 333.0)

            candidate = write_publish_module._LocalPublishCandidate(
                processed=0,
                remaining_budget=1,
                pending_commands=[],
                started=time.monotonic(),
            )
            _install_mock(adapter.write_scheduler, "_skip_local_publish_command", MagicMock(return_value=True))
            self.assertEqual(
                adapter.write_scheduler._process_local_publish_candidate("path", {"kind": "set_value"}, candidate),
                "skip",
            )
            _install_mock(adapter.write_scheduler, "_skip_local_publish_command", MagicMock(return_value=False))
            _install_mock(adapter.write_scheduler, "process_loaded_command", MagicMock(return_value="deferred"))
            self.assertEqual(
                adapter.write_scheduler._process_local_publish_candidate("path", {"kind": "publish_value"}, candidate),
                "break",
            )
            self.assertTrue(adapter.write_scheduler._local_publish_burst_done(0, 0, time.monotonic()))
            self.assertTrue(adapter.write_scheduler._local_publish_burst_done(1, 1, time.monotonic()))

    def test_write_scheduler_health_budgets_lifecycle_and_remote_write_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            default_config_path = Path(temp_dir) / "default-config.ini"
            default_config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            default_adapter = DbusAdapter(
                str(default_config_path),
                paths=gateway_paths(str(Path(temp_dir) / "default-run")),
            )
            default_scheduler = default_adapter.write_scheduler
            self.assertEqual(default_scheduler.local_publish_burst_limit, 20)
            self.assertEqual(default_scheduler.dynamic_local_publish_burst_limit, 20)
            self.assertEqual(default_scheduler.local_publish_tick_budget_seconds, 0.075)
            self.assertEqual(default_scheduler.startup_registration_batch_limit, 100)
            self.assertEqual(default_scheduler.startup_registration_tick_budget_seconds, 0.15)
            self.assertEqual(default_scheduler.last_processed_at, 0.0)
            self.assertEqual(default_scheduler.registered_paths, set())
            self.assertEqual(default_scheduler.last_values, {})
            self.assertEqual(list(default_scheduler._processed_events), [])
            self.assertEqual(list(default_scheduler._budget_events), [])
            self.assertEqual(list(default_scheduler._lifecycle_events), [])
            self.assertEqual(default_scheduler._lifecycle_counts, {})

            clamped_config_path = Path(temp_dir) / "clamped-config.ini"
            clamped_config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewayLocalPublishBurstLimit=0\n"
                "DbusGatewayLocalPublishTickBudgetMs=0\n"
                "DbusGatewayStartupRegistrationBatchLimit=0\n"
                "DbusGatewayStartupRegistrationTickBudgetMs=0\n",
                encoding="utf-8",
            )
            clamped_adapter = DbusAdapter(
                str(clamped_config_path),
                paths=gateway_paths(str(Path(temp_dir) / "clamped-run")),
            )
            self.assertEqual(clamped_adapter.write_scheduler.local_publish_burst_limit, 1)
            self.assertEqual(clamped_adapter.write_scheduler.dynamic_local_publish_burst_limit, 1)
            self.assertEqual(clamped_adapter.write_scheduler.local_publish_tick_budget_seconds, 0.001)
            self.assertEqual(clamped_adapter.write_scheduler.startup_registration_batch_limit, 1)
            self.assertEqual(clamped_adapter.write_scheduler.startup_registration_tick_budget_seconds, 0.001)

            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewayQueueBudgetStartupRegister=0\n"
                "DbusGatewayQueueBudgetGuiCriticalPublish=2\n"
                "DbusGatewayQueueBudgetLocalPublish=0\n"
                "DbusGatewayQueueBudgetRemoteWrite=3\n"
                "DbusGatewayQueueBudgetReadFast=4\n"
                "DbusGatewayQueueBudgetReadSlow=0\n"
                "DbusGatewayQueueBudgetDiscovery=0\n"
                "DbusGatewayQueueBudgetIntrospection=0\n"
                "DbusGatewayQueueBudgetDiagnostic=0\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            scheduler = adapter.write_scheduler

            self.assertEqual(
                write_health_module.DbusWriteSchedulerHealth._queue_class_budgets({}),
                {
                    "startup/register": 100,
                    "gui-critical-publish": 50,
                    "local-publish": 30,
                    "remote-write": 2,
                    "read-fast": 4,
                    "read-slow": 2,
                    "discovery": 1,
                    "introspection": 1,
                    "diagnostic": 1,
                },
            )
            self.assertEqual(
                scheduler.queue_class_budgets,
                {
                    "startup/register": 1,
                    "gui-critical-publish": 2,
                    "local-publish": 1,
                    "remote-write": 3,
                    "read-fast": 4,
                    "read-slow": 0,
                    "discovery": 0,
                    "introspection": 0,
                    "diagnostic": 0,
                },
            )
            self.assertEqual(scheduler.queue_class_budgets["startup/register"], 1)
            self.assertEqual(scheduler.queue_class_budgets["local-publish"], 1)
            self.assertEqual(scheduler.queue_class_budgets["read-slow"], 0)
            self.assertEqual(write_health_module.remote_command_timeout({"timeout": object()}), 1.0)
            self.assertEqual(write_health_module.remote_command_timeout({}), 1.0)
            self.assertEqual(write_health_module.remote_command_timeout({"timeout": 0.0}), 1.0)
            self.assertEqual(write_health_module.remote_command_timeout({"timeout": "2.5"}), 2.5)
            self.assertIsNone(write_health_module.remote_command_target({"service": "", "path": "/Mode"}))
            self.assertIsNone(write_health_module.remote_command_target({"service": "svc", "path": ""}))
            self.assertEqual(
                write_health_module.remote_command_target({"service": "svc", "path": "/Mode"}),
                ("svc", "/Mode"),
            )

            scheduler.set_dynamic_local_publish_burst(0)
            self.assertEqual(scheduler.dynamic_local_publish_burst_limit, 1)
            self.assertEqual(scheduler.queue_class_budgets["gui-critical-publish"], 2)
            self.assertEqual(scheduler.queue_class_budgets["local-publish"], 1)
            scheduler.local_publish_burst_limit = 1
            scheduler.set_dynamic_local_publish_burst(5)
            self.assertEqual(scheduler.dynamic_local_publish_burst_limit, 5)
            self.assertEqual(scheduler.queue_class_budgets["gui-critical-publish"], 5)
            self.assertEqual(scheduler.queue_class_budgets["local-publish"], 5)
            scheduler.local_publish_burst_limit = 5
            scheduler.set_dynamic_local_publish_burst(5)
            self.assertEqual(scheduler.queue_class_budgets["gui-critical-publish"], 2)
            self.assertEqual(scheduler.queue_class_budgets["local-publish"], 1)

            scheduler._budget_events.append((99.0, "read-fast"))
            scheduler._budget_events.append((98.9, "read-fast"))
            self.assertEqual(scheduler.budget_usage("read-fast", 100.0), 1)
            with patch.object(write_health_module.time, "time", return_value=100.0):
                scheduler.record_budget({"kind": "publish_value", "path": "/Mode"})
            self.assertIn("gui-critical-publish", scheduler.queue_class_usage_1s())
            self.assertTrue(scheduler.budget_available({"queue_class": "read-fast"}, 100.0))
            scheduler.queue_class_budgets["read-fast"] = 1
            self.assertFalse(scheduler.budget_available({"queue_class": "read-fast"}, 100.0))
            self.assertTrue(scheduler.budget_available({"queue_class": "ad-hoc"}, 100.0))
            scheduler._budget_events.append((100.0, "ad-hoc"))
            self.assertFalse(scheduler.budget_available({"queue_class": "ad-hoc"}, 100.0))
            scheduler.queue_class_budgets["unknown"] = 0
            self.assertFalse(scheduler.budget_available({"queue_class": "unknown"}, 100.0))

            commands = [
                ("unknown", {"id": "unknown", "queue_class": "unknown", "created_at": 1.0}),
                ("aged", {"id": "aged", "queue_class": "read-slow", "created_at": 1.0, "priority": "diagnostic"}),
                ("fresh", {"id": "fresh", "queue_class": "read-fast", "created_at": 20.0, "priority": "diagnostic"}),
            ]
            with patch.object(write_health_module.time, "time", return_value=20.0):
                self.assertEqual(scheduler.prioritized_commands(commands)[0][0], "aged")
                same_priority = [
                    ("slow", {"id": "slow", "queue_class": "read-slow", "created_at": 19.0, "priority": "read"}),
                    ("remote", {"id": "remote", "queue_class": "remote-write", "created_at": 19.0, "priority": "read"}),
                    ("local", {"id": "local", "queue_class": "local-publish", "created_at": 19.0, "priority": "read"}),
                ]
                self.assertEqual(
                    [path for path, _command in scheduler.prioritized_commands(same_priority)],
                    ["remote", "local", "slow"],
                )
                self.assertTrue(write_health_module.aged_refresh_command(commands[1][1], 20.0))
                self.assertEqual(write_health_module.effective_command_priority_rank(commands[1][1], 20.0), 1.5)
                self.assertFalse(write_health_module.aged_refresh_command({"queue_class": "read-slow", "created_at": 5.1}, 20.0))
                self.assertTrue(write_health_module.aged_refresh_command({"queue_class": "read-slow", "created_at": 5.0}, 20.0))
                self.assertFalse(write_health_module.aged_refresh_command({"queue_class": "local-publish", "created_at": 1.0}, 20.0))
                self.assertFalse(write_health_module.aged_refresh_command({"queue_class": "read-fast", "created_at": 0.0}, 20.0))

            lifecycle_path = Path(temp_dir) / "logs" / "commands.jsonl"
            adapter.command_lifecycle_path = str(lifecycle_path)
            with patch.object(write_health_module.time, "time", return_value=123.0):
                scheduler.record_lifecycle({"kind": "set_value", "queue_class": "remote-write"}, "applied")
                scheduler.record_lifecycle({"kind": "publish_value", "path": "/Mode"}, "")
            self.assertEqual(scheduler._lifecycle_counts["applied"], 1)
            self.assertEqual(scheduler._lifecycle_counts["unknown"], 1)
            lifecycle_log = lifecycle_path.read_text(encoding="utf-8")
            self.assertIn('"at":123.0', lifecycle_log)
            self.assertIn('"queue_class":"remote-write"', lifecycle_log)
            self.assertIn('"state":"unknown"', lifecycle_log)
            self.assertEqual(scheduler.lifecycle_counts_60s(), {"applied": 1, "unknown": 1})

            health = scheduler.health(now=123.0)
            self.assertEqual(health["last_processed_at"], scheduler.last_processed_at)
            self.assertEqual(health["local_publish_burst_limit"], scheduler.local_publish_burst_limit)
            self.assertEqual(health["dynamic_local_publish_burst_limit"], 5)
            self.assertEqual(health["local_publish_tick_budget_ms"], scheduler.local_publish_tick_budget_seconds * 1000.0)
            self.assertEqual(health["startup_registration_batch_limit"], scheduler.startup_registration_batch_limit)
            self.assertEqual(
                health["startup_registration_tick_budget_ms"],
                scheduler.startup_registration_tick_budget_seconds * 1000.0,
            )
            self.assertEqual(health["lifecycle_counts"]["applied"], 1)
            self.assertEqual(health["lifecycle_counts_60s"], {"applied": 1, "unknown": 1})

            self.assertEqual(scheduler.set_remote_value({"service": "", "path": "/P"}), "dropped")
            self.assertEqual(scheduler.set_remote_value({"service": "svc", "path": ""}), "dropped")

            fake_bus = MagicMock()
            fake_obj = object()
            fake_iface = MagicMock()
            fake_bus.get_object.return_value = fake_obj
            _install_mock(adapter.connection, "bus", MagicMock(return_value=fake_bus))
            _install_mock(adapter, "timed_dbus_operation", MagicMock(side_effect=lambda _kind, fn: fn()))
            with patch.object(write_health_module.dbus, "Interface", return_value=fake_iface) as interface_factory:
                self.assertEqual(
                    scheduler.set_remote_value({"service": "svc", "path": "/P", "value": 9, "timeout": 2.5}),
                    "applied",
                )
            fake_bus.get_object.assert_called_once_with("svc", "/P", introspect=False)
            interface_factory.assert_called_once_with(fake_obj, "com.victronenergy.BusItem")
            fake_iface.SetValue.assert_called_once_with(9, timeout=2.5)
            self.assertEqual(adapter.cache.values["path:svc/P"]["value"], 9)
            self.assertEqual(adapter.cache.values["path:svc/P"]["source"], "svc/P")
            self.assertEqual(adapter.cache.values["path:svc/P"]["confidence"], 0.9)

    def test_write_scheduler_health_contract_boundaries_are_exact(self) -> None:
        self.assertEqual(
            write_health_module.UNKNOWN_QUEUE_CLASS_RANK,
            max(write_health_module._QUEUE_CLASS_RANKS.values()) + 1,
        )
        self.assertEqual(
            write_health_module.DbusWriteSchedulerHealth._queue_class_budgets(
                {
                    "DbusGatewayQueueBudgetGuiCriticalPublish": "1",
                    "DbusGatewayQueueBudgetLocalPublish": "1",
                    "DbusGatewayQueueBudgetReadFast": "1",
                }
            ),
            {
                "startup/register": 100,
                "gui-critical-publish": 1,
                "local-publish": 1,
                "remote-write": 2,
                "read-fast": 1,
                "read-slow": 2,
                "discovery": 1,
                "introspection": 1,
                "diagnostic": 1,
            },
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewayQueueBudgetGuiCriticalPublish=7\n"
                "DbusGatewayQueueBudgetLocalPublish=6\n"
                "DbusGatewayQueueBudgetReadFast=1\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            scheduler = adapter.write_scheduler

            scheduler.local_publish_burst_limit = 1
            scheduler.set_dynamic_local_publish_burst(5)
            self.assertEqual(scheduler.queue_class_budgets["gui-critical-publish"], 7)
            self.assertEqual(scheduler.queue_class_budgets["local-publish"], 6)
            self.assertEqual(scheduler.dynamic_local_publish_burst_limit, 5)

            scheduler._budget_events.clear()
            scheduler._budget_events.extend(
                [
                    (99.0, "read-fast"),
                    (99.2, "read-fast"),
                    (99.4, "diagnostic"),
                ]
            )
            self.assertEqual(
                scheduler.queue_class_usage_1s(),
                {"diagnostic": 1, "read-fast": 2},
            )

            scheduler._budget_events.clear()
            scheduler._budget_events.extend([(98.5, "read-fast"), (99.0, "read-fast")])
            scheduler.prune_budget(100.0)
            self.assertEqual(list(scheduler._budget_events), [(99.0, "read-fast")])

            scheduler._processed_events.clear()
            scheduler._processed_events.extend([39.0, 40.0])
            scheduler.prune_processed(100.0)
            self.assertEqual(list(scheduler._processed_events), [40.0])

            scheduler._lifecycle_events.clear()
            scheduler._lifecycle_events.extend(
                [
                    (39.0, "applied", "remote-write"),
                    (40.0, "applied", "remote-write"),
                ]
            )
            scheduler.prune_lifecycle(100.0)
            self.assertEqual(list(scheduler._lifecycle_events), [(40.0, "applied", "remote-write")])

            scheduler._budget_events.clear()
            with patch.object(write_health_module.time, "time", return_value=200.0):
                scheduler.record_budget({"kind": "publish_value", "path": "/Mode", "queue_class": "diagnostic"})
            self.assertEqual(list(scheduler._budget_events), [(200.0, "diagnostic")])
            self.assertFalse(scheduler.budget_available({"queue_class": "diagnostic"}, 200.0))

            scheduler._lifecycle_counts.clear()
            scheduler._lifecycle_events.clear()
            adapter.command_lifecycle_path = ""
            lifecycle_open = unittest.mock.mock_open()
            with patch.object(builtins, "open", lifecycle_open), patch.object(
                write_health_module.time,
                "time",
                return_value=210.0,
            ):
                scheduler.record_lifecycle(
                    {"kind": "publish_value", "path": "/Mode", "queue_class": "diagnostic"},
                    "deferred",
                )
                scheduler.record_lifecycle(
                    {"kind": "publish_value", "path": "/Mode", "queue_class": "diagnostic"},
                    "deferred",
                )
            lifecycle_open.assert_not_called()
            self.assertEqual(scheduler._lifecycle_counts, {"deferred": 2})
            self.assertEqual(
                scheduler.lifecycle_counts_60s(),
                {"deferred": 2},
            )
            self.assertEqual(
                list(scheduler._lifecycle_events),
                [(210.0, "deferred", "diagnostic"), (210.0, "deferred", "diagnostic")],
            )

            lifecycle_path = Path(temp_dir) / "logs" / "write-health.jsonl"
            adapter.command_lifecycle_path = str(lifecycle_path)
            with patch.object(write_health_module.time, "time", return_value=220.0):
                scheduler.record_lifecycle({"kind": "set_value", "queue_class": "remote-write"}, "applied")
                scheduler.record_lifecycle({"kind": "publish_value", "path": "/Mode"}, "queued")
            lifecycle_lines = lifecycle_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lifecycle_lines), 2)
            self.assertEqual(json.loads(lifecycle_lines[0])["at"], 220.0)
            self.assertEqual(json.loads(lifecycle_lines[0])["queue_class"], "remote-write")
            self.assertEqual(json.loads(lifecycle_lines[1])["queue_class"], "gui-critical-publish")

            with patch.object(builtins, "open", side_effect=OSError("full")), patch.object(
                write_health_module.logging,
                "debug",
            ) as log_debug:
                scheduler.record_lifecycle({"kind": "noop"}, "dropped")
            log_debug.assert_called_once_with(
                "Unable to append DBus gateway command lifecycle event",
                exc_info=True,
            )

            commands = [
                ("newer", {"id": "newer", "queue_class": "read-fast", "created_at": 20.0, "priority": "read"}),
                ("older", {"id": "older", "queue_class": "read-fast", "created_at": 10.0, "priority": "read"}),
            ]
            with patch.object(write_health_module.time, "time", return_value=21.0):
                self.assertEqual(
                    [path for path, _command in scheduler.prioritized_commands(commands)],
                    ["older", "newer"],
                )

            health = scheduler.health(now=220.0)
            self.assertIn("queue_class_usage_1s", health)
            self.assertEqual(health["queue_class_usage_1s"], {"diagnostic": 1})

    def test_write_scheduler_support_helper_contracts(self) -> None:
        self.assertEqual(write_support_module.priority_rank(" safety "), 0)
        self.assertEqual(write_support_module.priority_rank("USER"), 1)
        self.assertEqual(write_support_module.priority_rank("publish"), 2)
        self.assertEqual(write_support_module.priority_rank("read"), 3)
        self.assertEqual(write_support_module.priority_rank("normal"), 4)
        self.assertEqual(write_support_module.priority_rank("optional"), 5)
        self.assertEqual(write_support_module.priority_rank("discovery"), 5)
        self.assertEqual(write_support_module.priority_rank("diagnostic"), 6)
        self.assertEqual(write_support_module.priority_rank("unknown"), 6)
        self.assertEqual(write_support_module.priority_rank(None), 6)

        self.assertEqual(write_support_module.deadline_pair({"deadline_s": "2.5", "created_at": "4"}), (2.5, 4.0))
        self.assertTrue(
            write_support_module.has_startup_registration(
                commands=[
                    ("path", {"kind": "register_path"}),
                    ("publish", {"kind": "publish_value"}),
                ]
            )
        )
        self.assertTrue(write_support_module.has_startup_registration(commands=[("service", {"kind": "register_service"})]))
        self.assertFalse(write_support_module.has_startup_registration(commands=[("publish", {"kind": "publish_value"})]))
        self.assertTrue(write_support_module.is_local_publish_command({"kind": "publish_value"}))
        self.assertTrue(write_support_module.is_local_publish_command({"type": "publish_desired"}))
        self.assertTrue(write_support_module.is_local_publish_command({"kind": "publish_fields"}))
        self.assertFalse(write_support_module.is_local_publish_command({"kind": "set_value"}))
        self.assertTrue(write_support_module.should_follow_with_local_burst({"kind": "publish_value"}, "applied"))
        self.assertTrue(write_support_module.should_follow_with_local_burst({"kind": "publish_desired"}, "dropped"))
        self.assertTrue(write_support_module.should_follow_with_local_burst({"kind": "publish_fields"}, "applied"))
        self.assertFalse(write_support_module.should_follow_with_local_burst({"kind": "publish_value"}, "deferred"))
        self.assertFalse(write_support_module.should_follow_with_local_burst({"kind": "set_value"}, "applied"))

        self.assertEqual(write_support_module.local_publish_action_result(3, "break"), (3, True))
        self.assertEqual(write_support_module.local_publish_action_result(3, "processed"), (4, False))
        self.assertEqual(write_support_module.local_publish_action_result(3, "skip"), (3, False))
        with patch.object(write_support_module.time, "monotonic", return_value=15.0):
            self.assertFalse(write_support_module.budget_elapsed(10.0, 5.1))
            self.assertTrue(write_support_module.budget_elapsed(10.0, 5.0))
        self.assertEqual(write_support_module.command_kind({"kind": "publish_value", "type": "set_value"}), "publish_value")
        self.assertEqual(write_support_module.command_kind({"type": "set_value"}), "set_value")
        self.assertEqual(write_support_module.command_kind({}), "")
        self.assertEqual(
            write_support_module.register_service_command(
                [
                    ("first", {"kind": "register_service"}),
                    ("path", {"kind": "register_path"}),
                    ("second", {"kind": "register_service"}),
                ]
            ),
            ("second", {"kind": "register_service"}),
        )
        self.assertIsNone(write_support_module.register_service_command([("path", {"kind": "register_path"})]))
        self.assertEqual(
            write_support_module.stale_coalesced_paths(
                [
                    ("processed", {"coalesce_key": "same"}),
                    ("stale", {"coalesce_key": "same"}),
                    ("other", {"coalesce_key": "other"}),
                    ("missing", {}),
                ],
                processed_path="processed",
                key="same",
            ),
            ["stale"],
        )
        self.assertEqual(
            write_support_module.lifecycle_payload(
                {"kind": "publish_value", "id": "cmd-1", "coalesce_key": "path:/Mode"},
                "applied",
                "gui-critical-publish",
                123.5,
            ),
            {
                "at": 123.5,
                "state": "applied",
                "queue_class": "gui-critical-publish",
                "kind": "publish_value",
                "id": "cmd-1",
                "coalesce_key": "path:/Mode",
            },
        )
        self.assertEqual(
            write_support_module.lifecycle_payload({"type": "refresh_services"}, "", "", 0.0),
            {"at": 0.0, "state": "", "queue_class": "", "kind": "refresh_services", "id": "", "coalesce_key": ""},
        )

    def test_publish_path_priority_sort_is_ranked_then_stable_by_path_name(self) -> None:
        self.assertEqual(
            write_publish_module.UNKNOWN_PUBLISH_PATH_RANK,
            max(write_publish_module.PUBLISH_PATH_RANKS.values()) + 1,
        )
        self.assertEqual(
            write_publish_module._prioritized_publish_items(
                {
                    "/Status": "status",
                    "/Ac/Power": "power",
                    "/Mode": "mode",
                    "/ZZZ": "z",
                    42: "numeric-path",
                    "/AAA": "a",
                }
            ),
            [
                ("/Ac/Power", "power"),
                ("/Mode", "mode"),
                ("/Status", "status"),
                ("/AAA", "a"),
                ("/ZZZ", "z"),
                ("42", "numeric-path"),
            ],
        )

    def test_adapter_registers_identity_paths_before_service_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "Host=192.0.2.10\n"
                "DeviceInstance=77\n"
                "ProductName=Test EVCS\n"
                "CustomName=Garage\n"
                "Connection=Shelly RPC\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            adapter.ensure_dbus_service()

            self.assertFalse(adapter.dbus_service.registered)
            self.assertEqual(adapter.dbus_service["/DeviceInstance"], 77)
            self.assertEqual(adapter.dbus_service["/ProductName"], "Test EVCS")
            self.assertEqual(adapter.dbus_service["/CustomName"], "Garage")
            self.assertEqual(adapter.dbus_service["/Connected"], 1)
            self.assertEqual(adapter.dbus_service["/Mgmt/Connection"], "Shelly RPC")
            self.assertIn("/DeviceInstance", adapter.write_scheduler.registered_paths)

            adapter.register_dbus_service_name()
            adapter.register_dbus_service_name()

            self.assertTrue(adapter.dbus_service.registered)

    def test_startup_registration_batch_registers_paths_before_service_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nHost=192.0.2.10\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.commands.enqueue(
                {
                    "kind": "register_path",
                    "path": "/Mode",
                    "value": 0,
                    "writeable": True,
                    "coalesce_key": "register:/Mode",
                    "priority": "publish",
                }
            )
            adapter.commands.enqueue(
                {
                    "kind": "register_path",
                    "path": "/StartStop",
                    "value": 1,
                    "writeable": True,
                    "coalesce_key": "register:/StartStop",
                    "priority": "publish",
                }
            )
            adapter.commands.enqueue(
                {
                    "kind": "register_service",
                    "coalesce_key": "register-service",
                    "priority": "publish",
                }
            )

            self.assertTrue(adapter.write_scheduler.process_one())

            self.assertTrue(adapter.dbus_service.registered)
            self.assertEqual(adapter.dbus_service["/Mode"], 0)
            self.assertEqual(adapter.dbus_service["/StartStop"], 1)
            self.assertEqual(adapter.commands.load_pending(), [])

    def test_startup_registration_batch_honors_limit_before_registering_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nDbusGatewayStartupRegistrationBatchLimit=2\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            for path in ("/Mode", "/StartStop", "/Status"):
                adapter.commands.enqueue(
                    {
                        "kind": "register_path",
                        "path": path,
                        "value": 0,
                        "coalesce_key": f"register:{path}",
                        "priority": "publish",
                    }
                )
            adapter.commands.enqueue({"kind": "register_service", "coalesce_key": "register-service", "priority": "publish"})

            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertFalse(adapter.dbus_service.registered)
            self.assertIn("/Mode", adapter.write_scheduler.registered_paths)
            self.assertIn("/StartStop", adapter.write_scheduler.registered_paths)
            self.assertNotIn("/Status", adapter.write_scheduler.registered_paths)

            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertTrue(adapter.dbus_service.registered)
            self.assertIn("/Status", adapter.write_scheduler.registered_paths)
            self.assertEqual(adapter.commands.load_pending(), [])

    def test_write_scheduler_process_one_defers_on_priority_and_exceptions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.circuit.protective_until = time.time() + 10
            path = adapter.commands.enqueue({"kind": "refresh_services", "priority": "diagnostic"})

            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertTrue(Path(path).exists())
            adapter.write_scheduler.prune_budget(time.time() + 2.0)

            adapter.circuit.protective_until = 0
            _install_mock(
                adapter.write_scheduler,
                "process_command",
                MagicMock(side_effect=DbusOperationDeferred("write")),
            )
            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertTrue(Path(path).exists())
            adapter.write_scheduler.prune_budget(time.time() + 2.0)

            _install_mock(
                adapter.write_scheduler,
                "process_command",
                MagicMock(side_effect=RuntimeError("boom")),
            )
            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertTrue(Path(path).exists())

            empty_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "empty-run")))
            self.assertFalse(empty_adapter.write_scheduler.process_one())
            empty_adapter.write_scheduler.record_lifecycle({"kind": "noop"}, "queued")
            self.assertEqual(empty_adapter.write_scheduler.health(now=time.time())["lifecycle_counts"]["queued"], 1)
            empty_adapter.command_lifecycle_path = ""
            empty_adapter.write_scheduler.record_lifecycle({"kind": "noop"}, "dropped")
            bad_lifecycle = Path(temp_dir) / "bad-lifecycle.jsonl"
            empty_adapter.command_lifecycle_path = str(bad_lifecycle)
            with patch.object(builtins, "open", side_effect=OSError("full")):
                empty_adapter.write_scheduler.record_lifecycle({"kind": "noop"}, "dropped")
            empty_adapter.command_lifecycle_path = "lifecycle-without-dir.jsonl"
            lifecycle_handle = unittest.mock.mock_open()
            with patch.object(write_health_module.os.path, "dirname", return_value=""), patch.object(
                builtins, "open", lifecycle_handle
            ):
                empty_adapter.write_scheduler.record_lifecycle({"kind": "noop"}, "queued")
            lifecycle_handle.assert_called_once_with("lifecycle-without-dir.jsonl", "a", encoding="utf-8")

    def test_process_one_can_skip_local_publish_and_process_remote_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            local_path = adapter.commands.enqueue(
                {
                    "kind": "publish_value",
                    "path": "/Mode",
                    "value": 1,
                    "priority": "publish",
                    "coalesce_key": "publish:/Mode",
                }
            )
            remote_path = adapter.commands.enqueue(
                {
                    "kind": "set_value",
                    "service": "svc",
                    "path": "/Remote",
                    "value": 2,
                    "priority": "user",
                    "coalesce_key": "remote:/Remote",
                }
            )
            _install_mock(adapter.write_scheduler, "process_command", MagicMock(return_value="applied"))

            self.assertTrue(adapter.write_scheduler.process_one(include_local_publish=False))

            processed = adapter.write_scheduler.process_command.call_args.args[0]
            self.assertEqual(processed["kind"], "set_value")
            self.assertTrue(Path(local_path).exists())
            self.assertFalse(Path(remote_path).exists())

    def test_process_command_enforces_circuit_before_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            _install_mock(adapter.circuit, "allows_priority", MagicMock(return_value=False))
            _install_mock(adapter.write_scheduler, "dispatch_command", MagicMock(return_value="applied"))

            self.assertEqual(adapter.write_scheduler.process_command({"kind": "set_value"}, command_file=""), "deferred")

            adapter.circuit.allows_priority.assert_called_once_with("diagnostic")
            adapter.write_scheduler.dispatch_command.assert_not_called()

            _install_mock(adapter.circuit, "allows_priority", MagicMock(return_value=True))
            command = {"kind": "set_value", "priority": "user"}
            self.assertEqual(adapter.write_scheduler.process_command(command, command_file="cmd.json"), "applied")
            adapter.circuit.allows_priority.assert_called_once_with("user")
            adapter.write_scheduler.dispatch_command.assert_called_once_with(command, command_file="cmd.json")

            adapter.write_scheduler.dispatch_command.reset_mock()
            _install_mock(adapter.circuit, "allows_priority", MagicMock(return_value=True))
            self.assertEqual(adapter.write_scheduler.process_command(command), "applied")
            adapter.write_scheduler.dispatch_command.assert_called_once_with(command, command_file="")

    def testdispatch_command_passes_command_file_to_publish_handlers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            _install_mock(adapter.write_scheduler, "publish_command", MagicMock(return_value="applied"))
            _install_mock(adapter.write_scheduler, "set_remote_value", MagicMock(return_value="applied"))
            setattr(adapter, "adapter_non_write_called", False)
            _install_mock(adapter, "process_non_write_command", MagicMock(return_value="dropped"))

            publish = {"kind": "publish_value", "path": "/Mode"}
            desired = {"kind": "publish_desired", "paths": {"/Mode": 1}}
            fields = {"kind": "publish_fields", "fields": {"mode": 2}}
            remote = {"kind": "set_value", "service": "svc", "path": "/Mode"}
            unknown = {"kind": "unknown"}

            self.assertEqual(adapter.write_scheduler.dispatch_command(publish, command_file="publish.json"), "applied")
            adapter.write_scheduler.publish_command.assert_called_with(publish, command_file="publish.json")
            self.assertEqual(adapter.write_scheduler.dispatch_command(desired, command_file="desired.json"), "applied")
            adapter.write_scheduler.publish_command.assert_called_with(desired, command_file="desired.json")
            self.assertEqual(adapter.write_scheduler.dispatch_command(fields, command_file="fields.json"), "applied")
            adapter.write_scheduler.publish_command.assert_called_with(fields, command_file="fields.json")
            self.assertEqual(adapter.write_scheduler.dispatch_command(remote, command_file="remote.json"), "applied")
            adapter.write_scheduler.set_remote_value.assert_called_once_with(remote)
            self.assertEqual(adapter.write_scheduler.dispatch_command(unknown, command_file="unknown.json"), "dropped")
            adapter.process_non_write_command.assert_called_once_with(unknown)

    def test_publish_fields_rewrites_to_desired_paths_and_preserves_command_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            _install_mock(adapter.write_scheduler, "_publish_desired", MagicMock(return_value="deferred"))

            command = {
                "kind": "publish_fields",
                "fields": {"mode": 2, "ac_power_w": 1200.0},
                "priority": "publish",
            }

            self.assertEqual(
                adapter.write_scheduler.publish_command(command, command_file="fields.json"),
                "deferred",
            )
            adapter.write_scheduler._publish_desired.assert_called_once_with(
                {
                    "kind": "publish_desired",
                    "fields": {"mode": 2, "ac_power_w": 1200.0},
                    "priority": "publish",
                    "paths": {"/Mode": 2, "/Ac/Power": 1200.0},
                },
                command_file="fields.json",
            )

    def test_next_scheduled_command_runs_followup_burst_only_after_local_publish_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nDbusGatewayLocalPublishBurstLimit=4\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            _install_mock(adapter.write_scheduler, "process_loaded_command", MagicMock(return_value="applied"))
            _install_mock(adapter.write_scheduler, "process_local_publish_burst", MagicMock())

            self.assertTrue(
                adapter.write_scheduler.process_next_scheduled_command(
                    [("publish.json", {"kind": "publish_value", "path": "/Mode"})],
                    include_local_publish=True,
                )
            )
            adapter.write_scheduler.process_local_publish_burst.assert_called_once_with(3)

            adapter.write_scheduler.process_local_publish_burst.reset_mock()
            _install_mock(adapter.write_scheduler, "process_loaded_command", MagicMock(return_value="deferred"))
            self.assertTrue(
                adapter.write_scheduler.process_next_scheduled_command(
                    [("publish.json", {"kind": "publish_value", "path": "/Mode"})],
                    include_local_publish=True,
                )
            )
            adapter.write_scheduler.process_local_publish_burst.assert_not_called()

            _install_mock(adapter.write_scheduler, "process_loaded_command", MagicMock(return_value="applied"))
            self.assertTrue(
                adapter.write_scheduler.process_next_scheduled_command(
                    [("remote.json", {"kind": "set_value", "service": "svc", "path": "/Remote"})],
                    include_local_publish=True,
                )
            )
            adapter.write_scheduler.process_local_publish_burst.assert_not_called()

            adapter.write_scheduler.local_publish_burst_limit = 0
            _install_mock(adapter.write_scheduler, "process_loaded_command", MagicMock(return_value="applied"))
            self.assertTrue(
                adapter.write_scheduler.process_next_scheduled_command(
                    [("publish.json", {"kind": "publish_value", "path": "/Mode"})],
                    include_local_publish=True,
                )
            )
            adapter.write_scheduler.process_local_publish_burst.assert_called_once_with(0)

    def testprocess_loaded_command_applies_drop_defer_and_expiry_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            applied = adapter.commands.enqueue({"kind": "set_value", "priority": "user", "created_at": 1.0})
            _install_mock(adapter.write_scheduler, "process_command", MagicMock(return_value="applied"))
            self.assertEqual(
                adapter.write_scheduler.process_loaded_command(applied, read_json_file(applied, {})),
                "applied",
            )
            self.assertFalse(Path(applied).exists())

            dropped = adapter.commands.enqueue({"kind": "set_value", "priority": "user", "created_at": 2.0})
            _install_mock(adapter.write_scheduler, "process_command", MagicMock(return_value="dropped"))
            self.assertEqual(
                adapter.write_scheduler.process_loaded_command(dropped, read_json_file(dropped, {})),
                "dropped",
            )
            self.assertFalse(Path(dropped).exists())

            deferred = adapter.commands.enqueue({"kind": "set_value", "priority": "user", "created_at": 3.0})
            _install_mock(adapter.write_scheduler, "process_command", MagicMock(return_value="deferred"))
            self.assertEqual(
                adapter.write_scheduler.process_loaded_command(deferred, read_json_file(deferred, {})),
                "deferred",
            )
            self.assertTrue(Path(deferred).exists())

            expired = adapter.commands.enqueue(
                {"kind": "set_value", "priority": "user", "created_at": time.time() - 10.0, "deadline_s": 1.0}
            )
            self.assertEqual(
                adapter.write_scheduler.process_loaded_command(expired, read_json_file(expired, {})),
                "dropped",
            )
            self.assertFalse(Path(expired).exists())
            lifecycle = adapter.write_scheduler.health(now=time.time())["lifecycle_counts"]
            self.assertGreaterEqual(lifecycle["applied"], 1)
            self.assertGreaterEqual(lifecycle["dropped"], 1)
            self.assertGreaterEqual(lifecycle["deferred"], 1)
            self.assertGreaterEqual(lifecycle["expired"], 1)

    def testprocess_loaded_command_forwards_pending_commands_to_stale_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            pending = [("stale.json", {"coalesce_key": "same"})]
            command = {"kind": "set_value", "priority": "user", "created_at": 1.0, "coalesce_key": "same"}
            _install_mock(adapter.write_scheduler, "process_command", MagicMock(return_value="applied"))
            _install_mock(adapter.write_scheduler, "drop_stale_coalesced_commands", MagicMock())

            self.assertEqual(
                adapter.write_scheduler.process_loaded_command("command.json", command, pending_commands=pending),
                "applied",
            )

            adapter.write_scheduler.drop_stale_coalesced_commands.assert_called_once_with(
                "command.json",
                command,
                pending_commands=pending,
            )

            expired = {"kind": "set_value", "created_at": time.time() - 10.0, "deadline_s": 1.0, "coalesce_key": "same"}
            adapter.write_scheduler.drop_stale_coalesced_commands.reset_mock()
            self.assertEqual(
                adapter.write_scheduler.process_loaded_command("expired.json", expired, pending_commands=pending),
                "dropped",
            )
            adapter.write_scheduler.drop_stale_coalesced_commands.assert_called_once_with(
                "expired.json",
                expired,
                pending_commands=pending,
            )

    def testcommand_expired_handles_created_at_and_boundary_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            with patch.object(write_core_module.time, "time", return_value=10.0):
                self.assertFalse(adapter.write_scheduler.command_expired({"created_at": 0.0, "deadline_s": 1.0}))
                self.assertTrue(adapter.write_scheduler.command_expired({"created_at": 1.0, "deadline_s": 1.0}))
                self.assertFalse(adapter.write_scheduler.command_expired({"created_at": 9.0, "deadline_s": 1.0}))
                self.assertFalse(adapter.write_scheduler.command_expired({"created_at": 12.0, "deadline_s": 1.0}))

    def testcommand_outcome_returns_deferred_and_logs_retry_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            command = {"kind": "set_value", "priority": "user"}

            _install_mock(
                adapter.write_scheduler,
                "process_command",
                MagicMock(side_effect=DbusOperationDeferred("wait")),
            )
            self.assertEqual(adapter.write_scheduler.command_outcome("defer.json", command), "deferred")

            _install_mock(
                adapter.write_scheduler,
                "process_command",
                MagicMock(side_effect=RuntimeError("boom")),
            )
            with patch("venus_evcharger.dbus_adapter_write_core.logging.exception") as logged:
                self.assertEqual(adapter.write_scheduler.command_outcome("retry.json", command), "deferred")

            logged.assert_called_once()
            self.assertEqual(logged.call_args.args[0], "Gateway command failed; keeping for retry path=%s: %s")
            self.assertEqual(logged.call_args.args[1], "retry.json")
            self.assertIsInstance(logged.call_args.args[2], RuntimeError)

    def test_local_publish_burst_can_run_before_non_local_scheduler_slot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\nDbusGatewayLocalPublishBurstLimit=2\nDbusIntrospectionEnabled=0\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.cache.update_services(["svc"])
            adapter.write_scheduler.registered_paths.update({"/Session/Time", "/Ac/Power"})
            writes: list[tuple[str, object]] = []

            class _FakeDbusService(dict):
                def __setitem__(self, key: str, value: object) -> None:
                    writes.append((key, value))
                    super().__setitem__(key, value)

            adapter.set_dbus_service(_FakeDbusService(), registered=True)
            adapter.commands.enqueue(
                {
                    "kind": "publish_value",
                    "path": "/Session/Time",
                    "value": 10,
                    "coalesce_key": "publish:/Session/Time",
                    "priority": "publish",
                }
            )
            adapter.commands.enqueue(
                {
                    "kind": "publish_value",
                    "path": "/Ac/Power",
                    "value": 2000,
                    "coalesce_key": "publish:/Ac/Power",
                    "priority": "publish",
                }
            )
            adapter.commands.enqueue({"kind": "refresh_services", "priority": "read"})
            _install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=False))
            _install_mock(adapter, "refresh_services_if_due_once", MagicMock(return_value=False))
            _install_mock(adapter, "enqueue_background_introspection_if_due", MagicMock())
            _install_mock(adapter, "list_services", MagicMock(return_value=["svc"]))
            _install_mock(adapter.discovery, "refresh_services", MagicMock(return_value=["svc"]))

            self.assertTrue(adapter.process_one_dbus_operation_once())

            self.assertCountEqual(writes, [("/Ac/Power", 2000), ("/Session/Time", 10)])
            self.assertEqual(adapter.commands.load_pending(), [])
            self.assertGreaterEqual(adapter.write_scheduler.health(now=time.time())["processed_commands_60s"], 3)

    def test_write_scheduler_set_remote_value_uses_dbus_and_updates_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            fake_iface = MagicMock()
            fake_obj = object()
            get_object = _install_mock(adapter.connection, "get_object", MagicMock(return_value=fake_obj))

            with patch.object(write_health_module.dbus, "Interface", return_value=fake_iface):
                outcome = adapter.write_scheduler.set_remote_value(
                    {"service": "svc", "path": "/Set", "value": 9, "timeout": 2.0}
                )

            self.assertEqual(outcome, "applied")
            get_object.assert_called_once_with("svc", "/Set", introspect=False)
            fake_iface.SetValue.assert_called_once_with(9, timeout=2.0)
            self.assertEqual(adapter.cache.values["path:svc/Set"]["value"], 9)

    def test_fast_pv_poll_uses_cached_services_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nAutoPvServicePrefix=com.victronenergy.pvinverter\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.cache.update_services(["com.victronenergy.pvinverter.http_1"])
            calls: list[tuple[str, str]] = []

            def fake_read(service: str, path: str) -> float:
                calls.append((service, path))
                return 123.0

            setattr(adapter.read_executor, "read_busitem_now", fake_read)

            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "pv",
                    {"aggregate": "services-sum", "prefix": "com.victronenergy.pvinverter", "path": "/Ac/Power"},
                ),
                "applied",
            )
            self.assertEqual(calls, [("com.victronenergy.pvinverter.http_1", "/Ac/Power")])
            self.assertEqual(adapter.cache.values["pv"]["value"], 123.0)

    def test_optional_pv_read_falls_back_to_zero_without_health_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nAutoPvServicePrefix=com.victronenergy.pvinverter\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.rate_limiter.intervals["read"] = 0.0
            adapter.cache.update_services(["com.victronenergy.pvinverter.http_1"])
            _install_mock(
                adapter.read_executor,
                "read_busitem_now",
                MagicMock(side_effect=RuntimeError("offline")),
            )

            first = adapter.read_executor.poll_read_spec("pv_power_w", adapter.read_scheduler.specs["pv_power_w"])
            outcome = adapter.read_executor.poll_read_spec("pv_power_w", adapter.read_scheduler.specs["pv_power_w"])

            self.assertEqual(first, "deferred")
            self.assertEqual(outcome, "applied")
            entry = adapter.cache.values["pv_power_w"]
            self.assertEqual(entry["value"], 0.0)
            self.assertEqual(entry["status"], "fresh")
            self.assertEqual(entry["confidence"], 0.2)
            self.assertIn("offline", entry["last_error"])
            self.assertEqual(adapter.read_executor.read_busitem_now.call_count, 2)

    def test_optional_pv_member_failure_does_not_trip_circuit_breaker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.rate_limiter.intervals["read"] = 0.0
            adapter.cache.update_services(["com.victronenergy.pvinverter.http_1"])
            _install_mock(
                adapter.read_executor,
                "read_busitem_now",
                MagicMock(side_effect=RuntimeError("night pv asleep")),
            )
            _install_mock(adapter.circuit, "record_error", MagicMock())

            outcome = adapter.read_executor.poll_read_spec(
                "pv_power_w",
                {
                    "aggregate": "pv-total",
                    "prefix": "com.victronenergy.pvinverter",
                    "path": "/Ac/Power",
                    "dc_service": "",
                    "dc_path": "",
                    "use_dc_pv": "false",
                },
            )

            self.assertEqual(outcome, "applied")
            adapter.circuit.record_error.assert_not_called()
            member = adapter.cache.values["path:com.victronenergy.pvinverter.http_1/Ac/Power"]
            self.assertEqual(member["status"], "error")
            self.assertEqual(member["last_error"], "night pv asleep")
            self.assertEqual(adapter.cache.values["pv_power_w"]["value"], 0.0)

    def test_optional_direct_read_falls_back_to_fresh_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            _install_mock(adapter.read_executor, "read_busitem", MagicMock(return_value=1.0))
            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "optional_value",
                    {"aggregate": "sum", "service": "svc.optional", "paths": ["/L1", "/L2"]},
                ),
                "deferred",
            )
            self.assertTrue(adapter.read_executor.has_pending_aggregate())
            _install_mock(
                adapter.read_executor,
                "read_busitem",
                MagicMock(side_effect=RuntimeError("optional offline")),
            )

            outcome = adapter.read_executor.poll_read_spec(
                "optional_value",
                {
                    "service": "svc.optional",
                    "path": "/Maybe",
                    "optional_zero_on_error": "yes",
                    "optional_confidence": 0.45,
                },
            )

            self.assertEqual(outcome, "applied")
            entry = adapter.cache.values["optional_value"]
            self.assertEqual(entry["value"], 0.0)
            self.assertEqual(entry["status"], "fresh")
            self.assertEqual(entry["source"], "svc.optional")
            self.assertEqual(entry["confidence"], 0.45)
            self.assertEqual(entry["last_error"], "optional offline")
            self.assertFalse(adapter.read_executor.has_pending_aggregate())

    def test_optional_direct_read_uses_prefix_source_and_tolerates_missing_aggregate_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            outcome = adapter.read_executor.poll_read_spec(
                "prefix_optional",
                {
                    "aggregate": "services-sum",
                    "prefix": "svc.prefix",
                    "path": "/Maybe",
                    "optional_zero_on_error": "yes",
                },
            )

            self.assertEqual(outcome, "applied")
            entry = adapter.cache.values["prefix_optional"]
            self.assertEqual(entry["source"], "svc.prefix")
            self.assertEqual(entry["confidence"], 0.2)
            self.assertIn("No cached services", entry["last_error"])

    def test_optional_zero_on_error_accepts_only_explicit_truthy_values(self) -> None:
        truthy = ("1", "true", "TRUE", " yes ", "on")
        falsey = ("", "0", "false", "no", "off", None)

        for value in truthy:
            with self.subTest(value=value):
                with tempfile.TemporaryDirectory() as temp_dir:
                    config_path = Path(temp_dir) / "config.ini"
                    config_path.write_text("[DEFAULT]\n", encoding="utf-8")
                    adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
                    outcome = adapter.read_executor.poll_read_spec(
                        "optional",
                        {
                            "aggregate": "services-sum",
                            "prefix": "missing.",
                            "path": "/P",
                            "optional_zero_on_error": value,
                        },
                    )
                    self.assertEqual(outcome, "applied")
                    self.assertEqual(adapter.cache.values["optional"]["value"], 0.0)

        for value in falsey:
            with self.subTest(value=value):
                with tempfile.TemporaryDirectory() as temp_dir:
                    config_path = Path(temp_dir) / "config.ini"
                    config_path.write_text("[DEFAULT]\n", encoding="utf-8")
                    adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
                    outcome = adapter.read_executor.poll_read_spec(
                        "required",
                        {
                            "aggregate": "services-sum",
                            "prefix": "missing.",
                            "path": "/P",
                            "optional_zero_on_error": value,
                        },
                    )
                    self.assertEqual(outcome, "dropped")
                    self.assertEqual(adapter.cache.values["required"]["status"], "error")

    def test_pv_total_automatically_combines_ac_services_and_dc_pv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.rate_limiter.intervals["read"] = 0.0
            adapter.cache.update_services(["com.victronenergy.pvinverter.http_1"])
            values = {
                ("com.victronenergy.pvinverter.http_1", "/Ac/Power"): 120.0,
                ("com.victronenergy.system", "/Dc/Pv/Power"): 30.0,
            }
            _install_mock(
                adapter.read_executor,
                "read_busitem_now",
                MagicMock(side_effect=lambda service, path: values[(service, path)]),
            )

            first = adapter.read_executor.poll_read_spec("pv_power_w", adapter.read_scheduler.specs["pv_power_w"])
            second = adapter.read_executor.poll_read_spec("pv_power_w", adapter.read_scheduler.specs["pv_power_w"])

            self.assertEqual(first, "deferred")
            self.assertEqual(second, "applied")
            self.assertEqual(adapter.cache.values["pv_power_w"]["value"], 150.0)
            adapter.read_executor.read_busitem_now.assert_any_call("com.victronenergy.pvinverter.http_1", "/Ac/Power")
            adapter.read_executor.read_busitem_now.assert_any_call("com.victronenergy.system", "/Dc/Pv/Power")

    def test_pv_total_uses_configured_empty_confidence_when_all_sources_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.rate_limiter.intervals["read"] = 0.0
            adapter.cache.update_services(["com.victronenergy.pvinverter.http_2"])
            _install_mock(
                adapter.read_executor,
                "read_optional_busitem",
                MagicMock(side_effect=RuntimeError("all pv asleep")),
            )
            spec = {
                "aggregate": "pv-total",
                "prefix": "com.victronenergy.pvinverter",
                "path": "/Ac/Power",
                "dc_service": "com.victronenergy.system",
                "dc_path": "/Dc/Pv/Power",
                "use_dc_pv": "true",
                "optional_confidence": 0.6,
            }

            self.assertEqual(adapter.read_executor.poll_read_spec("pv_power_w", spec), "deferred")
            self.assertEqual(adapter.read_executor.poll_read_spec("pv_power_w", spec), "applied")
            entry = adapter.cache.values["pv_power_w"]
            self.assertEqual(entry["value"], 0.0)
            self.assertEqual(entry["confidence"], 0.6)
            self.assertIn("all pv asleep", entry["last_error"])

    def test_pv_total_uses_default_empty_confidence_when_all_sources_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.rate_limiter.intervals["read"] = 0.0
            adapter.cache.update_services(["com.victronenergy.pvinverter.http_2"])
            _install_mock(
                adapter.read_executor,
                "read_optional_busitem",
                MagicMock(side_effect=RuntimeError("pv asleep")),
            )
            spec = {
                "aggregate": "pv-total",
                "prefix": "com.victronenergy.pvinverter",
                "path": "/Ac/Power",
                "dc_service": "",
                "dc_path": "",
                "use_dc_pv": "false",
            }

            self.assertEqual(adapter.read_executor.poll_read_spec("pv_power_w", spec), "applied")
            entry = adapter.cache.values["pv_power_w"]
            self.assertEqual(entry["value"], 0.0)
            self.assertEqual(entry["confidence"], 0.2)
            self.assertIn("pv asleep", entry["last_error"])

    def test_pv_total_requires_at_least_one_autodetected_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            spec = {
                "aggregate": "pv-total",
                "prefix": "com.victronenergy.pvinverter",
                "path": "/Ac/Power",
                "dc_service": "",
                "dc_path": "/Dc/Pv/Power",
                "use_dc_pv": "false",
            }

            self.assertEqual(adapter.read_executor.poll_read_spec("pv_power_w", spec), "dropped")
            self.assertEqual(
                adapter.cache.values["pv_power_w"]["last_error"],
                "No available AC or DC PV source candidates",
            )

    def test_pv_total_member_discovery_keeps_ac_and_dc_sources_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.cache.update_services(
                [
                    "com.victronenergy.pvinverter.http_9",
                    "com.victronenergy.pvinverter.http_1",
                    "com.victronenergy.battery.socketcan_can1",
                ]
            )

            def members(spec: dict[str, object], *, now: float | None = None) -> list[tuple[str, str]]:
                prefix = str(spec.get("prefix") or "")
                return read_pv_module.pv_total_members(
                    spec,
                    sorted(name for name in adapter.cache.services if name.startswith(prefix)),
                    adapter.cache.values,
                    now=now,
                )

            self.assertEqual(
                members(
                    {
                        "prefix": "com.victronenergy.pvinverter",
                        "path": "/Ac/Power",
                        "dc_service": "com.victronenergy.system",
                        "dc_path": "/Dc/Pv/Power",
                        "use_dc_pv": "on",
                    }
                ),
                [
                    ("com.victronenergy.pvinverter.http_1", "/Ac/Power"),
                    ("com.victronenergy.pvinverter.http_9", "/Ac/Power"),
                    ("com.victronenergy.system", "/Dc/Pv/Power"),
                ],
            )
            self.assertEqual(
                members(
                    {
                        "prefix": "com.victronenergy.pvinverter",
                        "path": "/Ac/Power",
                        "dc_service": "com.victronenergy.system",
                        "dc_path": "/Dc/Pv/Power",
                        "use_dc_pv": "off",
                    }
                ),
                [
                    ("com.victronenergy.pvinverter.http_1", "/Ac/Power"),
                    ("com.victronenergy.pvinverter.http_9", "/Ac/Power"),
                ],
            )
            adapter.cache.mark_error(
                "path:com.victronenergy.pvinverter.http_1/Ac/Power",
                source="com.victronenergy.pvinverter.http_1/Ac/Power",
                error="asleep",
                now=100.0,
            )
            adapter.cache.mark_error(
                "path:com.victronenergy.pvinverter.http_9/Ac/Power",
                source="com.victronenergy.pvinverter.http_9/Ac/Power",
                error="old",
                now=-1000.0,
            )
            self.assertEqual(
                members(
                    {
                        "prefix": "com.victronenergy.pvinverter",
                        "path": "/Ac/Power",
                        "dc_service": "",
                        "dc_path": "",
                        "use_dc_pv": "off",
                    },
                    now=120.0,
                ),
                [("com.victronenergy.pvinverter.http_9", "/Ac/Power")],
            )
            adapter.cache.mark_error(
                "path:com.victronenergy.system/Dc/Pv/Power",
                source="com.victronenergy.system/Dc/Pv/Power",
                error="dc asleep",
                now=100.0,
            )
            self.assertEqual(
                members(
                    {
                        "prefix": "com.victronenergy.pvinverter",
                        "path": "",
                        "dc_service": "com.victronenergy.system",
                        "dc_path": "/Dc/Pv/Power",
                        "use_dc_pv": "on",
                    },
                    now=120.0,
                ),
                [],
            )
            self.assertEqual(
                members(
                    {
                        "prefix": "com.victronenergy.pvinverter",
                        "path": "/Ac/Power",
                        "dc_service": "",
                        "dc_path": "/Dc/Pv/Power",
                        "use_dc_pv": "on",
                    },
                    now=1000.0,
                ),
                [
                    ("com.victronenergy.pvinverter.http_1", "/Ac/Power"),
                    ("com.victronenergy.pvinverter.http_9", "/Ac/Power"),
                ],
            )
            self.assertEqual(
                members(
                    {
                        "prefix": "com.victronenergy.pvinverter",
                        "path": "",
                        "dc_service": "com.victronenergy.system",
                        "dc_path": "/Dc/Pv/Power",
                        "use_dc_pv": "on",
                    },
                    now=1000.0,
                ),
                [("com.victronenergy.system", "/Dc/Pv/Power")],
            )
            self.assertEqual(
                members(
                    {
                        "prefix": "com.victronenergy.pvinverter",
                        "path": "/Ac/Power",
                        "dc_service": "com.victronenergy.system",
                        "dc_path": "",
                        "use_dc_pv": "on",
                    },
                    now=1000.0,
                ),
                [
                    ("com.victronenergy.pvinverter.http_1", "/Ac/Power"),
                    ("com.victronenergy.pvinverter.http_9", "/Ac/Power"),
                ],
            )

    def test_pv_member_contracts_cover_dc_tokens_targets_and_backoff_edges(self) -> None:
        valid_dc_spec = {
            "dc_service": " com.victronenergy.system ",
            "dc_path": " /Dc/Pv/Power ",
            "use_dc_pv": " yes ",
        }
        self.assertEqual(read_pv_module.dc_pv_target(valid_dc_spec), ("com.victronenergy.system", "/Dc/Pv/Power"))
        self.assertEqual(read_pv_module.dc_pv_members(valid_dc_spec, {}, now=100.0), [("com.victronenergy.system", "/Dc/Pv/Power")])
        for raw in ("1", "true", "yes", "on", " ON "):
            with self.subTest(raw=raw):
                self.assertTrue(read_pv_module.use_dc_pv({"use_dc_pv": raw}))
        for raw in ("", "0", "false", "no", "off", object()):
            with self.subTest(raw=raw):
                self.assertFalse(read_pv_module.use_dc_pv({"use_dc_pv": raw}))
        self.assertFalse(read_pv_module.use_dc_pv({}))
        self.assertIsNone(read_pv_module.dc_pv_target({"dc_service": "", "dc_path": "/Dc/Pv/Power"}))
        self.assertIsNone(read_pv_module.dc_pv_target({"dc_service": object(), "dc_path": "/Dc/Pv/Power"}))
        self.assertIsNone(read_pv_module.dc_pv_target({"dc_service": "svc", "dc_path": "Dc/Pv/Power"}))
        self.assertIsNone(read_pv_module.dc_pv_target({"dc_service": "svc", "dc_path": object()}))
        self.assertIsNone(read_pv_module.dc_pv_target({"dc_service": "svc"}))

        failed_values = {
            "path:svc/Path": {"status": "error", "error_at": "100.0"},
            "path:svc/Other": {"status": "fresh", "error_at": "100.0"},
            "path:svc/MissingErrorAt": {"status": "error"},
            "path:svc/OneSecond": {"status": "error", "error_at": 1.0},
        }
        self.assertTrue(
            read_pv_module.pv_member_recently_failed(
                failed_values,
                "svc",
                "/Path",
                now=399.9,
                backoff_seconds=300.0,
            )
        )
        self.assertFalse(
            read_pv_module.pv_member_recently_failed(
                failed_values,
                "svc",
                "/Path",
                now=400.0,
                backoff_seconds=300.0,
            )
        )
        self.assertFalse(read_pv_module.pv_member_recently_failed(failed_values, "svc", "/Other", now=101.0))
        self.assertFalse(read_pv_module.pv_member_recently_failed(failed_values, "svc", "/MissingErrorAt", now=1.0))
        self.assertTrue(read_pv_module.pv_member_recently_failed(failed_values, "svc", "/OneSecond", now=2.0))
        self.assertFalse(
            read_pv_module.pv_member_recently_failed(
                {"path:svc/Path": {"status": "error", "error_at": True}},
                "svc",
                "/Path",
                now=101.0,
            )
        )
        self.assertFalse(
            read_pv_module.pv_member_recently_failed(
                {"path:svc/Path": {"status": "error", "error_at": "bad"}},
                "svc",
                "/Path",
                now=101.0,
            )
        )

    def test_pv_total_optional_member_errors_are_preserved_and_nonfatal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.rate_limiter.intervals["read"] = 0.0
            adapter.cache.update_services(["com.victronenergy.pvinverter.http_1"])

            def fake_read(service: str, path: str) -> float:
                if service == "com.victronenergy.pvinverter.http_1":
                    raise RuntimeError("ac asleep")
                return 70.0

            _install_mock(adapter.read_executor, "read_busitem_now", MagicMock(side_effect=fake_read))

            first = adapter.read_executor.poll_read_spec("pv_power_w", adapter.read_scheduler.specs["pv_power_w"])
            second = adapter.read_executor.poll_read_spec("pv_power_w", adapter.read_scheduler.specs["pv_power_w"])

            self.assertEqual(first, "deferred")
            self.assertEqual(second, "applied")
            entry = adapter.cache.values["pv_power_w"]
            self.assertEqual(entry["value"], 70.0)
            self.assertEqual(entry["confidence"], 1.0)
            self.assertEqual(entry["source"], "com.victronenergy.system/Dc/Pv/Power")
            self.assertIn("com.victronenergy.pvinverter.http_1/Ac/Power: ac asleep", entry["last_error"])

    def test_optional_aggregate_member_logs_and_appends_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.rate_limiter.intervals["read"] = 0.0
            adapter.cache.update_services(["com.victronenergy.pvinverter.http_1"])
            _install_mock(
                adapter.read_executor,
                "read_optional_busitem",
                MagicMock(side_effect=RuntimeError("sleeping")),
            )
            with self.assertLogs(level="DEBUG") as logs:
                outcome = adapter.read_executor.poll_read_spec(
                    "pv_power_w",
                    {
                        "aggregate": "pv-total",
                        "prefix": "com.victronenergy.pvinverter",
                        "path": "/Ac/Power",
                        "dc_service": "",
                        "dc_path": "",
                        "use_dc_pv": "false",
                        "optional_confidence": 0.7,
                    },
                )

            self.assertEqual(outcome, "applied")
            self.assertEqual(adapter.cache.values["pv_power_w"]["confidence"], 0.7)
            self.assertEqual(
                adapter.cache.values["pv_power_w"]["last_error"],
                "com.victronenergy.pvinverter.http_1/Ac/Power: sleeping",
            )
            entry = adapter.cache.values["path:com.victronenergy.pvinverter.http_1/Ac/Power"]
            self.assertEqual(entry["status"], "error")
            self.assertEqual(entry["last_error"], "sleeping")
            self.assertTrue(
                any(
                    "DBus adapter optional aggregate member failed com.victronenergy.pvinverter.http_1/Ac/Power"
                    in message
                    and "sleeping" in message
                    for message in logs.output
                )
            )

    def test_optional_aggregate_member_initializes_missing_error_list(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.rate_limiter.intervals["read"] = 0.0
            adapter.cache.update_services(["svc.optional"])
            _install_mock(
                adapter.read_executor,
                "read_optional_busitem",
                MagicMock(side_effect=RuntimeError("sleeping")),
            )
            outcome = adapter.read_executor.poll_read_spec(
                "optional_sum",
                {
                    "aggregate": "pv-total",
                    "prefix": "svc.",
                    "path": "/Power",
                    "dc_service": "",
                    "dc_path": "",
                    "use_dc_pv": "false",
                },
            )

            self.assertEqual(outcome, "applied")
            self.assertEqual(adapter.cache.values["optional_sum"]["last_error"], "svc.optional/Power: sleeping")

    def test_optional_aggregate_member_skips_cache_for_invalid_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.rate_limiter.intervals["read"] = 0.0
            adapter.cache.update_services(["svc.optional"])
            _install_mock(
                adapter.read_executor,
                "read_optional_busitem",
                MagicMock(side_effect=RuntimeError("bad path")),
            )
            outcome = adapter.read_executor.poll_read_spec(
                "optional_sum",
                {
                    "aggregate": "pv-total",
                    "prefix": "svc.",
                    "path": "NotAbsolute",
                    "dc_service": "",
                    "dc_path": "",
                    "use_dc_pv": "false",
                },
            )

            self.assertEqual(outcome, "applied")
            self.assertEqual(adapter.cache.values["optional_sum"]["last_error"], "svc.optionalNotAbsolute: bad path")
            self.assertNotIn("path:svc.optionalNotAbsolute", adapter.cache.values)

    def test_optional_aggregate_member_reraises_required_source_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            _install_mock(
                adapter.read_executor,
                "read_busitem",
                MagicMock(side_effect=RuntimeError("required offline")),
            )

            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "required_sum",
                    {"aggregate": "sum", "service": "svc", "paths": ["/Path"]},
                ),
                "dropped",
            )
            self.assertEqual(adapter.cache.values["required_sum"]["last_error"], "required offline")

            _install_mock(
                adapter.read_executor,
                "read_busitem",
                MagicMock(side_effect=DbusOperationDeferred("read")),
            )
            _install_mock(
                adapter.read_executor,
                "read_optional_busitem",
                MagicMock(side_effect=DbusOperationDeferred("read")),
            )
            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "required_deferred",
                    {"aggregate": "sum", "service": "svc", "paths": ["/Path"]},
                ),
                "deferred",
            )

    def test_optional_busitem_returns_none_for_incomplete_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            self.assertIsNone(adapter.read_executor.read_optional_busitem("", "/Path"))
            self.assertIsNone(adapter.read_executor.read_optional_busitem("svc", ""))

    def test_first_service_read_uses_discovered_battery_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.cache.update_services(["com.victronenergy.battery.socketcan_can1"])
            _install_mock(adapter.read_executor, "read_busitem", MagicMock(return_value=74.0))

            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "battery_soc",
                    {
                        "aggregate": "first-service",
                        "prefix": "com.victronenergy.battery",
                        "path": "/Soc",
                    },
                ),
                "applied",
            )
            adapter.read_executor.read_busitem.assert_called_once_with("com.victronenergy.battery.socketcan_can1", "/Soc")
            self.assertEqual(adapter.cache.values["battery_soc"]["value"], 74.0)
            member_key = "path:com.victronenergy.battery.socketcan_can1/Soc"
            self.assertEqual(adapter.cache.values[member_key]["value"], 74.0)
            self.assertEqual(adapter.cache.values[member_key]["status"], "fresh")

    def test_read_executor_covers_refresh_sum_error_and_direct_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.rate_limiter.intervals["read"] = 0.0
            values = {
                ("svc", "/L1"): 1.5,
                ("svc", "/L2"): None,
                ("svc", "/Path"): 7,
            }
            setattr(adapter.read_executor, "read_busitem_now", lambda service, path: values.get((service, path), 0.0))
            setattr(adapter.read_executor, "read_busitem", lambda service, path: values.get((service, path), 0.0))

            self.assertEqual(
                adapter.read_executor.poll_read_spec("sum", {"aggregate": "sum", "service": "svc", "paths": ["/L1", "/L2"]}),
                "deferred",
            )
            adapter.rate_limiter.next_at["read"] = time.monotonic()
            self.assertEqual(
                adapter.read_executor.poll_read_spec("sum", {"aggregate": "sum", "service": "svc", "paths": ["/L1", "/L2"]}),
                "applied",
            )
            self.assertEqual(adapter.cache.values["sum"]["value"], 1.5)
            self.assertEqual(adapter.cache.values["path:svc/L1"]["value"], 1.5)
            self.assertEqual(adapter.cache.values["path:svc/L2"]["value"], None)
            adapter.cache.update_services(["pv.1"])
            self.assertEqual(
                adapter.read_executor.poll_read_spec("pv", {"aggregate": "services-sum", "prefix": "pv.", "path": "/P"}),
                "applied",
            )
            self.assertEqual(adapter.read_executor.poll_read_spec("direct", {"service": "svc", "path": "/Path"}), "applied")
            self.assertEqual(adapter.cache.values["direct"]["value"], 7)
            self.assertEqual(adapter.cache.values["path:svc/Path"]["value"], 7)
            self.assertEqual(adapter.read_executor.poll_read_spec("invalid", {"service": "svc", "path": "Path"}), "dropped")
            self.assertNotIn("path:svcPath", adapter.cache.values)
            self.assertEqual(adapter.read_executor.refresh_requested_value({"service": "svc", "path": "/Path"}), "applied")
            self.assertEqual(adapter.read_executor.refresh_requested_value({"key": "grid_power_w"}), "deferred")
            self.assertEqual(adapter.read_executor.refresh_requested_value({}), "dropped")

            _install_mock(
                adapter.read_executor,
                "read_busitem",
                MagicMock(side_effect=RuntimeError("read failed")),
            )
            self.assertEqual(adapter.read_executor.refresh_requested_value({"service": "svc", "path": "/Broken"}), "dropped")
            self.assertEqual(adapter.cache.values["path:svc/Broken"]["status"], "error")
            refresh_path = adapter.commands.enqueue(
                {
                    "kind": "refresh_value",
                    "service": "svc",
                    "path": "/Broken",
                    "priority": "read",
                    "coalesce_key": "refresh:svc:/Broken",
                }
            )
            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertFalse(Path(refresh_path).exists())
            self.assertEqual(adapter.read_executor.poll_read_spec("bad", {"service": "svc", "path": "/Bad"}), "dropped")
            self.assertEqual(adapter.cache.values["bad"]["status"], "error")
            _install_mock(
                adapter.read_executor,
                "read_busitem",
                MagicMock(side_effect=DbusOperationDeferred("read")),
            )
            self.assertEqual(adapter.read_executor.refresh_requested_value({"service": "svc", "path": "/Deferred"}), "deferred")
            deferred_refresh_path = adapter.commands.enqueue(
                {
                    "kind": "refresh_value",
                    "service": "svc",
                    "path": "/Deferred",
                    "priority": "read",
                    "coalesce_key": "refresh:svc:/Deferred",
                }
            )
            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertTrue(Path(deferred_refresh_path).exists())
            self.assertEqual(adapter.read_executor.poll_read_spec("later", {"service": "svc", "path": "/Later"}), "deferred")
            self.assertEqual(adapter.read_executor.poll_read_spec("empty", {"aggregate": "sum", "service": "svc", "paths": []}), "applied")
            self.assertEqual(adapter.cache.values["empty"]["value"], 0.0)
            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "battery_missing",
                    {"aggregate": "first-service", "prefix": "missing.", "path": "/Soc"},
                ),
                "dropped",
            )

    def test_read_executor_direct_dbus_busitem_uses_timed_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            fake_iface = MagicMock()
            fake_iface.GetValue.return_value = 4.0
            fake_bus = MagicMock()
            fake_bus.get_object.return_value = object()
            _install_mock(adapter.connection, "bus", MagicMock(return_value=fake_bus))
            with patch.object(read_module.dbus, "Interface", return_value=fake_iface):
                self.assertEqual(adapter.read_executor.read_busitem("svc", "/P"), 4.0)
            fake_bus.get_object.assert_called_once_with("svc", "/P", introspect=False)
            self.assertIsNone(adapter.read_executor.read_busitem("", "/P"))
            self.assertIsNone(adapter.read_executor.read_busitem("svc", ""))

            adapter.cache.update_services([])
            self.assertEqual(
                adapter.read_executor.poll_read_spec("missing", {"aggregate": "services-sum", "prefix": "missing.", "path": "/P"}),
                "dropped",
            )
            self.assertEqual(adapter.cache.values["missing"]["status"], "error")
            _install_mock(adapter.read_executor, "read_busitem_now", MagicMock(return_value=None))
            adapter.rate_limiter.next_at["read"] = time.monotonic()
            self.assertEqual(
                adapter.read_executor.poll_read_spec("explicit", {"aggregate": "services-sum", "service": "explicit", "path": "/P"}),
                "applied",
            )
            self.assertEqual(adapter.cache.values["explicit"]["value"], 0.0)

    def test_socket_client_timeout_does_not_block_tick(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            conn = MagicMock()
            conn.__enter__.return_value = conn
            conn.recv.side_effect = TimeoutError("idle")
            server = MagicMock()
            server.accept.return_value = (conn, object())
            adapter._server = server

            with patch.object(process_socket_module.select, "select", return_value=([server], [], [])):
                adapter.process_socket_once()

            conn.settimeout.assert_called_once_with(0.1)
            conn.sendall.assert_not_called()

    def test_socket_payload_and_socket_poll_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            self.assertFalse(adapter.handle_socket_payload("{")["ok"])
            self.assertFalse(adapter.handle_socket_payload("[]")["ok"])
            self.assertTrue(adapter.handle_socket_payload('{"type":"snapshot"}')["ok"])
            self.assertTrue(adapter.handle_socket_payload('{"type":"health"}')["ok"])
            for request_type in ("refresh_value", "refresh_services", "publish_desired", "publish_value", "set_value"):
                self.assertTrue(adapter.handle_socket_payload(json.dumps({"type": request_type}))["ok"])
            self.assertFalse(adapter.handle_socket_payload('{"type":"wat"}')["ok"])

            adapter._server = None
            adapter.process_socket_once()
            server = MagicMock()
            adapter._server = server
            with patch.object(process_socket_module.select, "select", return_value=([], [], [])):
                adapter.process_socket_once()
            with patch.object(process_socket_module.select, "select", return_value=([server], [], [])):
                server.accept.side_effect = BlockingIOError()
                adapter.process_socket_once()

    def test_socket_process_sends_json_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            conn = MagicMock()
            conn.__enter__.return_value = conn
            conn.recv.return_value = b'{"type":"snapshot"}'
            server = MagicMock()
            server.accept.return_value = (conn, object())
            adapter._server = server
            with patch.object(process_socket_module.select, "select", return_value=([server], [], [])):
                adapter.process_socket_once()
            conn.sendall.assert_called_once()

    def test_socket_lifecycle_creates_and_removes_unix_socket(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            Path(adapter.paths.socket_path).parent.mkdir(parents=True, exist_ok=True)
            Path(adapter.paths.socket_path).write_text("stale", encoding="utf-8")

            adapter.start_socket()
            self.assertIsNotNone(adapter._server)
            adapter.close_socket()
            self.assertIsNone(adapter._server)
            adapter.close_socket()

    def testhealth_snapshot_includes_gateway_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            paths = gateway_paths(str(Path(temp_dir) / "run"))
            adapter = DbusAdapter(str(config_path), paths=paths)

            adapter.write_scheduler.registered_paths.update({"/Mode", "/StartStop"})
            adapter.commands.enqueue({"kind": "refresh_services", "priority": "read"})
            legacy_refresh = Path(paths.command_dir) / "legacy-refresh.json"
            legacy_refresh.write_text(
                '{"kind":"refresh_services","created_at":1.0,"coalesce_key":"refresh-services"}',
                encoding="utf-8",
            )
            adapter.core_commands.enqueue({"kind": "user_command", "path": "/Mode", "value": 1})
            adapter.circuit.record_success(12.5)
            adapter.cache.update_value("grid_power_w", 10.0, source="grid", now=time.time() - 1.0)
            adapter.cache.mark_error("pv_power_w", source="pv", error="offline")
            adapter._last_tick_at = 123.0
            adapter._last_tick_monotonic = time.monotonic() - 0.25
            adapter._last_tick_duration_ms = 7.5
            _install_mock(adapter.resource_monitor, "snapshot", MagicMock(return_value={"state": "ok"}))
            adapter.tick_health.record(duration_ms=7.5, expected_interval_s=adapter.tick_seconds)

            health = adapter.health_snapshot()

            self.assertEqual(health["state"], "ok")
            self.assertEqual(health["pending_command_count"], 1)
            self.assertEqual(health["physical_command_count"], 2)
            self.assertEqual(health["core_command_count"], 1)
            self.assertEqual(health["registered_path_count"], 2)
            self.assertEqual(health["last_tick_at"], 123.0)
            self.assertEqual(health["tick_duration_ms"], 7.5)
            self.assertIn("discovery_last_success_at", health)
            self.assertIn("discovery_last_error", health)
            self.assertIn("discovery_next_scan_at", health)
            self.assertGreaterEqual(health["mainloop_heartbeat_age_s"], 0.0)
            self.assertGreater(health["last_success_at"], 0.0)
            self.assertEqual(health["last_error"], "")
            self.assertEqual(health["queues"]["pending_command_count"], 1)
            self.assertEqual(health["queues"]["physical_command_count"], 2)
            self.assertGreaterEqual(health["queues"]["oldest_command_age_s"], 0.0)
            self.assertEqual(
                health["queues"]["last_processed_at"],
                health["write_scheduler"]["last_processed_at"],
            )
            self.assertEqual(health["queue_classes"]["discovery"]["pending"], 1)
            self.assertEqual(health["cache_freshness"]["grid_power_w_status"], "fresh")
            self.assertEqual(health["cache_freshness"]["pv_power_w_status"], "error")
            self.assertIn("write_scheduler", health)
            self.assertIn("queue_class_budgets", health["write_scheduler"])
            self.assertIn("queue_class_usage_1s", health["write_scheduler"])
            self.assertIn("core_reads_fresh", health["slo"]["checks"])
            self.assertIn(health["backpressure"]["state"], {"ok", "congested", "slow", "protective"})
            self.assertIn("core_should_throttle", health["backpressure"])
            self.assertEqual(health["resources"]["state"], "ok")
            self.assertEqual(health["adaptive_tick_seconds"], adapter.tick_seconds)
            self.assertEqual(health["min_tick_seconds"], adapter.min_tick_seconds)
            self.assertEqual(health["max_tick_seconds"], adapter.max_tick_seconds)
            self.assertIn("last_tick_at", health["eventloop"])
            self.assertIn("mainloop_heartbeat_age_s", health["eventloop"])
            self.assertEqual(health["eventloop"]["last_tick_at"], 123.0)
            self.assertEqual(health["eventloop"]["tick_duration_ms"], 7.5)
            self.assertEqual(
                health["eventloop"]["mainloop_heartbeat_age_s"],
                health["mainloop_heartbeat_age_s"],
            )

    def test_health_snapshot_time_and_backpressure_contracts_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            _install_mock(
                adapter.write_scheduler,
                "health",
                MagicMock(return_value={"processed_commands_60s": 7, "last_processed_at": 321.0}),
            )
            _install_mock(adapter.tick_health, "snapshot", MagicMock(return_value={"max_tick_gap_ms_60s": 12.0}))
            _install_mock(adapter, "slo_snapshot", MagicMock(return_value={"violated": [], "checks": {}}))
            adapter._last_tick_monotonic = 999.75

            with patch.object(process_health_module.time, "time", return_value=500.0), patch.object(
                process_health_module.time,
                "monotonic",
                return_value=1000.0,
            ):
                health = adapter.health_snapshot()

            adapter.write_scheduler.health.assert_called_once_with(now=500.0)
            adapter.slo_snapshot.assert_called_once()
            self.assertEqual(adapter.slo_snapshot.call_args.kwargs["current_monotonic"], 1000.0)
            adapter.tick_health.snapshot.assert_called_once_with(now=1000.0)
            self.assertEqual(health["queues"]["processed_commands_60s"], 7)
            self.assertEqual(health["queues"]["last_processed_at"], 321.0)
            self.assertEqual(health["write_scheduler"]["processed_commands_60s"], 7)
            self.assertEqual(health["mainloop_heartbeat_age_s"], 0.25)
            self.assertEqual(health["eventloop"]["mainloop_heartbeat_age_s"], 0.25)
            self.assertEqual(health["eventloop"]["max_tick_gap_ms_60s"], 12.0)
            self.assertEqual(health["backpressure"]["state"], "ok")

            degraded = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-degraded")))
            _install_mock(degraded, "slo_snapshot", MagicMock(return_value={"violated": [], "checks": {}}))
            degraded.circuit.degraded_until = time.time() + 60.0
            self.assertEqual(degraded.health_snapshot()["backpressure"]["state"], "slow")

            for last_tick, expected in ((0.0, 0.0), (0.5, 999.5)):
                heartbeat_adapter = DbusAdapter(
                    str(config_path),
                    paths=gateway_paths(str(Path(temp_dir) / f"run-heartbeat-{last_tick}")),
                )
                _install_mock(heartbeat_adapter, "slo_snapshot", MagicMock(return_value={"violated": [], "checks": {}}))
                heartbeat_adapter._last_tick_monotonic = last_tick
                with patch.object(process_health_module.time, "monotonic", return_value=1000.0):
                    self.assertEqual(heartbeat_adapter.health_snapshot()["mainloop_heartbeat_age_s"], expected)

    def test_backpressure_marks_slo_violations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewaySloGuiMaxAgeSeconds=1\n"
                "DbusGatewaySloCoreReadMaxAgeSeconds=1\n"
                "DbusGatewaySloQueueMaxAgeSeconds=1\n"
                "DbusGatewaySloMainloopGapMaxMs=100\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            now = time.time()
            adapter.cache.update_value(
                f"path:{adapter.service_name}/Ac/Power",
                10.0,
                source=f"{adapter.service_name}/Ac/Power",
                now=now - 5.0,
            )
            adapter.cache.update_value("grid_power_w", 10.0, source="grid", now=now - 5.0)
            monotonic_now = time.monotonic()
            adapter.tick_health.record(duration_ms=1.0, expected_interval_s=0.1, now=monotonic_now - 3.0)
            adapter.tick_health.record(duration_ms=1.0, expected_interval_s=0.1, now=monotonic_now)
            adapter.commands.enqueue({"kind": "refresh_services", "created_at": now - 5.0})

            health = adapter.health_snapshot()

            self.assertEqual(health["slo"]["state"], "violated")
            self.assertIn("gui_fresh", health["slo"]["violated"])
            self.assertIn("core_reads_fresh", health["slo"]["violated"])
            self.assertIn("queue_age_ok", health["slo"]["violated"])
            self.assertIn("mainloop_gap_ok", health["slo"]["violated"])
            self.assertNotEqual(health["backpressure"]["state"], "ok")
            self.assertTrue(health["backpressure"]["core_should_throttle"])

    def test_gui_freshness_ignores_idle_session_counters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewaySloGuiMaxAgeSeconds=1\n"
                "DbusGatewaySloCoreReadMaxAgeSeconds=1\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            now = time.time()
            for path, value, age in (
                ("/Ac/Power", 1.7, 0.5),
                ("/Ac/Current", 0.01, 0.5),
                ("/Session/Energy", 0.0, 600.0),
                ("/Session/Time", 0, 600.0),
                ("/Ac/Energy/Forward", 0.0, 600.0),
            ):
                adapter.cache.update_value(
                    f"path:{adapter.service_name}{path}",
                    value,
                    source=f"{adapter.service_name}{path}",
                    now=now - age,
                )

            self.assertFalse(adapter.charging_session_active_for_gui(now))
            freshness_paths = adapter.gui_freshness_paths(now)
            self.assertIn("/Ac/Power", freshness_paths)
            self.assertIn("/Ac/Current", freshness_paths)
            self.assertIn("/Connected", freshness_paths)
            self.assertIn("/Mode", freshness_paths)
            self.assertNotIn("/Session/Energy", freshness_paths)
            observed = adapter.slo_observed({}, {}, now, time.monotonic())
            self.assertIn("gui_measurement_max_age_s", observed)
            self.assertIn("gui_measurement_missing_path_count", observed)
            self.assertEqual(observed["gui_session_max_age_s"], 0.0)
            self.assertEqual(observed["gui_session_missing_path_count"], 0.0)
            self.assertGreater(observed["gui_control_missing_path_count"], 0.0)
            checks = health_slo_module.slo_checks_from_observed(observed, adapter.slo_thresholds())
            self.assertTrue(checks["gui_fresh"])
            self.assertTrue(checks["gui_controls_fresh"])

    def test_gui_freshness_tracks_control_paths_against_effective_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewaySloGuiMaxAgeSeconds=2\n"
                "DbusGatewaySloCoreReadMaxAgeSeconds=5\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            now = time.time()
            for path, value, age in (
                ("/Ac/Power", 1.0, 0.5),
                ("/Ac/Current", 0.01, 0.5),
                ("/Mode", 1, 9.0),
            ):
                adapter.cache.update_value(
                    f"path:{adapter.service_name}{path}",
                    value,
                    source=f"{adapter.service_name}{path}",
                    now=now - age,
            )

            observed = adapter.slo_observed({}, {}, now, time.monotonic())
            checks = health_slo_module.slo_checks_from_observed(observed, adapter.slo_thresholds())
            targets = health_slo_module.slo_targets(adapter.slo_thresholds())

            self.assertEqual(targets["configured_gui_max_age_s"], 2.0)
            self.assertEqual(targets["gui_control_max_age_s"], 10.0)
            self.assertEqual(observed["gui_control_max_age_s"], 9.0)
            self.assertEqual(observed["gui_control_missing_path_count"], 7.0)
            self.assertGreater(observed["gui_missing_path_count"], observed["gui_control_missing_path_count"])
            self.assertTrue(checks["gui_controls_fresh"])
            self.assertTrue(checks["gui_fresh"])

            adapter.cache.update_value(
                f"path:{adapter.service_name}/Mode",
                1,
                source=f"{adapter.service_name}/Mode",
                now=now - 10.1,
            )
            stale_observed = adapter.slo_observed({}, {}, now, time.monotonic())
            stale_checks = health_slo_module.slo_checks_from_observed(stale_observed, adapter.slo_thresholds())

            self.assertFalse(stale_checks["gui_controls_fresh"])
            self.assertFalse(stale_checks["gui_fresh"])

    def test_slo_observed_uses_tick_snapshot_time_and_named_measurement_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            _install_mock(adapter.tick_health, "snapshot", MagicMock(return_value={"max_tick_gap_ms_60s": 42.0}))

            observed = adapter.slo_observed(
                {"oldest_command_age_s": 3.0},
                {"grid_power_w_age_s": 4.0},
                now=100.0,
                current_monotonic=777.0,
            )

            adapter.tick_health.snapshot.assert_called_once_with(now=777.0)
            self.assertEqual(observed["gui_measurement_max_age_s"], 0.0)
            self.assertGreater(observed["gui_measurement_missing_path_count"], 0.0)
            self.assertEqual(observed["queue_oldest_age_s"], 3.0)
            self.assertEqual(observed["core_read_max_age_s"], 4.0)
            self.assertEqual(observed["mainloop_max_gap_ms_60s"], 42.0)

    def test_gui_freshness_includes_session_counters_while_charging(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewaySloGuiMaxAgeSeconds=1\n"
                "DbusGatewaySloCoreReadMaxAgeSeconds=1\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            now = time.time()
            for path, value, age in (
                ("/Ac/Power", 900.0, 0.5),
                ("/Ac/Current", 4.0, 0.5),
                ("/Session/Energy", 0.1, 600.0),
            ):
                adapter.cache.update_value(
                    f"path:{adapter.service_name}{path}",
                    value,
                    source=f"{adapter.service_name}{path}",
                    now=now - age,
                )

            self.assertTrue(adapter.charging_session_active_for_gui(now))
            self.assertIn("/Session/Energy", adapter.gui_freshness_paths(now))
            observed = adapter.slo_observed({}, {}, now, time.monotonic())
            self.assertFalse(health_slo_module.slo_checks_from_observed(observed, adapter.slo_thresholds())["gui_fresh"])

    def test_gui_activity_detection_uses_fresh_power_or_current_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewaySloGuiMaxAgeSeconds=1\n"
                "DbusGatewaySloCoreReadMaxAgeSeconds=1\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            now = time.time()

            adapter.cache.update_value(f"path:{adapter.service_name}/Ac/Power", 50.0, source="power", now=now)
            adapter.cache.update_value(f"path:{adapter.service_name}/Ac/Current", 0.0, source="current", now=now)
            self.assertEqual(adapter.fresh_cached_path_float("/Ac/Power", now), 50.0)
            self.assertTrue(adapter.charging_session_active_for_gui(now))

            adapter.cache.update_value(f"path:{adapter.service_name}/Ac/Power", 0.0, source="power", now=now)
            adapter.cache.update_value(f"path:{adapter.service_name}/Ac/Current", 0.2, source="current", now=now)
            self.assertEqual(adapter.fresh_cached_path_float("/Ac/Current", now), 0.2)
            self.assertTrue(adapter.charging_session_active_for_gui(now))

            adapter.cache.update_value(f"path:{adapter.service_name}/Ac/Power", 900.0, source="power", now=now - 2.0)
            adapter.cache.update_value(f"path:{adapter.service_name}/Ac/Current", 0.0, source="current", now=now)
            self.assertEqual(adapter.fresh_cached_path_float("/Ac/Power", now), 900.0)
            adapter.cache.update_value(f"path:{adapter.service_name}/Ac/Power", 900.0, source="power", now=now - 2.1)
            self.assertEqual(adapter.fresh_cached_path_float("/Ac/Power", now), 0.0)
            self.assertFalse(adapter.charging_session_active_for_gui(now))

    def test_core_read_stale_requires_fresh_status_and_age(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\nDbusGatewaySloCoreReadMaxAgeSeconds=5\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.slo_core_read_max_age_seconds = 0.5

            self.assertTrue(
                health_slo_module.core_read_stale(
                    "grid_power_w",
                    {},
                    max_age_seconds=adapter.slo_core_read_max_age_seconds,
                )
            )
            self.assertTrue(
                health_slo_module.core_read_stale(
                    "grid_power_w",
                    {"grid_power_w_status": "fresh"},
                    max_age_seconds=adapter.slo_core_read_max_age_seconds,
                )
            )
            self.assertTrue(
                health_slo_module.core_read_stale(
                    "grid_power_w",
                    {"grid_power_w_status": "error", "grid_power_w_age_s": 0.0},
                    max_age_seconds=adapter.slo_core_read_max_age_seconds,
                )
            )
            self.assertFalse(
                health_slo_module.core_read_stale(
                    "grid_power_w",
                    {"grid_power_w_status": "fresh", "grid_power_w_age_s": 0.0},
                    max_age_seconds=adapter.slo_core_read_max_age_seconds,
                )
            )
            self.assertFalse(
                health_slo_module.core_read_stale(
                    "grid_power_w",
                    {"grid_power_w_status": "fresh", "grid_power_w_age_s": 0.5},
                    max_age_seconds=adapter.slo_core_read_max_age_seconds,
                )
            )
            self.assertTrue(
                health_slo_module.core_read_stale(
                    "grid_power_w",
                    {"grid_power_w_status": "fresh", "grid_power_w_age_s": 0.6},
                    max_age_seconds=adapter.slo_core_read_max_age_seconds,
                )
            )

    def test_expired_command_is_removed_and_journaled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lifecycle_path = Path(temp_dir) / "run" / "lifecycle.jsonl"
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                f"DbusGatewayCommandLifecyclePath={lifecycle_path}\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            command_path = adapter.commands.enqueue(
                {
                    "kind": "refresh_services",
                    "created_at": time.time() - 10.0,
                    "deadline_s": 1.0,
                    "priority": "read",
                }
            )

            self.assertTrue(adapter.write_scheduler.process_one())

            self.assertFalse(Path(command_path).exists())
            health = adapter.write_scheduler.health(now=time.time())
            self.assertEqual(health["lifecycle_counts"]["expired"], 1)
            self.assertIn('"state":"expired"', lifecycle_path.read_text(encoding="utf-8"))

    def test_gui_publish_burst_drains_large_local_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewayLocalPublishBurstLimit=250\n"
                "DbusGatewayLocalPublishTickBudgetMs=10000\n"
                "DbusGatewayQueueBudgetLocalPublish=250\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            writes: list[tuple[str, object]] = []

            class _FakeDbusService(dict):
                def __setitem__(self, key: str, value: object) -> None:
                    writes.append((key, value))
                    super().__setitem__(key, value)

            adapter.set_dbus_service(_FakeDbusService(), registered=True)
            for index in range(200):
                path = f"/LoadTest/{index}"
                adapter.write_scheduler.registered_paths.add(path)
                adapter.commands.enqueue(
                    {
                        "kind": "publish_value",
                        "path": path,
                        "value": index,
                        "priority": "publish",
                        "coalesce_key": f"publish:{path}",
                    }
                )

            processed = adapter.write_scheduler.process_local_publish_burst(200)

            self.assertEqual(processed, 200)
            self.assertEqual(len(writes), 200)
            self.assertEqual(adapter.commands.load_pending(), [])

    def test_gui_publish_burst_stops_at_tick_time_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewayLocalPublishBurstLimit=10\n"
                "DbusGatewayLocalPublishTickBudgetMs=1\n"
                "DbusGatewayQueueBudgetLocalPublish=10\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            _install_mock(adapter, "timed_local_publish", MagicMock(side_effect=lambda operation: operation()))
            writes: list[tuple[str, object]] = []

            class _FakeDbusService(dict):
                def __setitem__(self, key: str, value: object) -> None:
                    writes.append((key, value))
                    super().__setitem__(key, value)

            adapter.set_dbus_service(_FakeDbusService(), registered=True)
            for index in range(5):
                path = f"/BudgetTest/{index}"
                adapter.write_scheduler.registered_paths.add(path)
                adapter.commands.enqueue(
                    {
                        "kind": "publish_value",
                        "path": path,
                        "value": index,
                        "priority": "publish",
                        "coalesce_key": f"publish:{path}",
                    }
                )

            with patch.object(write_publish_module.time, "monotonic", side_effect=[0.0, 0.0, 0.002]):
                processed = adapter.write_scheduler.process_local_publish_burst()

            self.assertEqual(processed, 1)
            self.assertEqual(len(writes), 1)
            self.assertEqual(len(adapter.commands.load_pending()), 4)

    def test_local_publish_burst_skip_and_defer_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nDbusGatewayLocalPublishBurstLimit=3\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            self.assertEqual(adapter.write_scheduler.process_local_publish_burst(), 0)
            adapter.set_dbus_service(_FakeVeDbusService(), registered=True)
            self.assertEqual(adapter.write_scheduler.process_local_publish_burst(), 0)
            adapter.commands.enqueue({"kind": "set_value", "service": "svc", "path": "/A", "priority": "user"})
            self.assertEqual(adapter.write_scheduler.process_local_publish_burst(), 0)
            adapter.commands.enqueue(
                {
                    "kind": "publish_value",
                    "path": "/Missing",
                    "value": 1,
                    "priority": "publish",
                    "coalesce_key": "publish:/Missing",
                }
            )
            self.assertEqual(adapter.write_scheduler.process_local_publish_burst(), 1)
            _install_mock(adapter.write_scheduler, "process_command", MagicMock(return_value="deferred"))
            adapter.commands.enqueue(
                {
                    "kind": "publish_value",
                    "path": "/Later",
                    "value": 1,
                    "priority": "publish",
                    "coalesce_key": "publish:/Later",
                }
            )
            self.assertEqual(adapter.write_scheduler.process_local_publish_burst(), 0)
            self.assertIsNotNone(adapter.write_scheduler.next_local_publish_command())
            for command_path, _command in adapter.commands.load_pending():
                adapter.commands.remove(command_path)
            self.assertIsNone(adapter.write_scheduler.next_local_publish_command())

    def test_local_publish_timer_records_success_and_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            self.assertEqual(adapter.write_scheduler.timed_local_publish(lambda: "ok"), "ok")
            self.assertGreater(adapter.circuit.health()["successes_60s"], 0)

            with self.assertRaises(RuntimeError):
                adapter.write_scheduler.timed_local_publish(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
            self.assertGreater(adapter.circuit.health()["errors_60s"], 0)

    def test_startup_registration_batch_stops_at_tick_time_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewayStartupRegistrationBatchLimit=10\n"
                "DbusGatewayStartupRegistrationTickBudgetMs=1\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.set_dbus_service(_FakeVeDbusService())
            for index in range(5):
                adapter.commands.enqueue(
                    {
                        "kind": "register_path",
                        "path": f"/RegisterBudget/{index}",
                        "value": index,
                    }
                )
            commands = adapter.write_scheduler.prioritized_commands(DbusCommandInbox.coalesce(adapter.commands.load_pending()))

            with patch.object(write_publish_module.time, "monotonic", side_effect=[0.0, 0.0, 0.002, 0.002]):
                self.assertTrue(adapter.write_scheduler.process_startup_registration_batch(commands))

            self.assertEqual(len(adapter.write_scheduler.registered_paths), 1)
            self.assertEqual(len(adapter.commands.load_pending()), 4)

            mixed = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-mixed")))
            _install_mock(mixed.write_scheduler, "register_path", MagicMock(side_effect=["deferred", "applied"]))
            self.assertTrue(
                mixed.write_scheduler.process_startup_registration_batch(
                    [
                        ("deferred", {"kind": "register_path", "path": "/Deferred", "priority": "publish"}),
                        ("service", {"kind": "register_service", "priority": "publish"}),
                        ("applied", {"kind": "register_path", "path": "/Applied", "priority": "publish"}),
                    ]
                )
            )
            self.assertEqual(mixed.write_scheduler.register_path.call_count, 2)
            service_then_path = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-service-then-path")))
            _install_mock(service_then_path.write_scheduler, "register_path", MagicMock(return_value="applied"))
            self.assertTrue(
                service_then_path.write_scheduler.process_startup_registration_batch(
                    [
                        ("service", {"kind": "register_service", "priority": "publish"}),
                        ("path", {"kind": "register_path", "path": "/AfterService", "priority": "publish"}),
                    ]
                )
            )
            unknown_then_path = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-unknown-then-path")))
            _install_mock(unknown_then_path.write_scheduler, "register_path", MagicMock(return_value="applied"))
            self.assertTrue(
                unknown_then_path.write_scheduler.process_startup_registration_batch(
                    [
                        ("unknown", {"kind": "unknown", "priority": "publish"}),
                        ("path", {"kind": "register_path", "path": "/AfterUnknown", "priority": "publish"}),
                    ]
                )
            )

    def test_startup_registration_service_only_and_zero_limit_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            commands = [("svc", {"kind": "register_service", "priority": "publish"})]
            self.assertTrue(adapter.write_scheduler.process_startup_registration_batch(commands))
            self.assertTrue(adapter.dbus_service.registered)

            limited = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-limited")))
            limited.write_scheduler.startup_registration_batch_limit = 0
            limited.commands.enqueue({"kind": "register_path", "path": "/A", "priority": "publish"})
            limited_commands = limited.write_scheduler.prioritized_commands(DbusCommandInbox.coalesce(limited.commands.load_pending()))
            self.assertFalse(limited.write_scheduler.process_startup_registration_batch(limited_commands))

            deferred_service = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-deferred-service")))
            _install_mock(deferred_service.write_scheduler, "process_command", MagicMock(return_value="deferred"))
            self.assertFalse(
                deferred_service.write_scheduler.process_startup_registration_batch(
                    [("svc", {"kind": "register_service", "priority": "publish"})]
                )
            )
            deferred_service.write_scheduler.process_command.assert_called_once_with(
                {"kind": "register_service", "priority": "publish"},
                command_file="svc",
            )

            waiting = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-waiting")))
            waiting.commands.enqueue({"kind": "register_path", "path": "/A", "priority": "publish"})
            self.assertTrue(waiting.write_scheduler.remaining_register_paths())
            self.assertFalse(
                waiting.write_scheduler.process_startup_registration_batch(
                    [("svc", {"kind": "register_service", "priority": "publish"})]
                )
            )
            self.assertFalse(waiting.dbus_service_registered)

            no_paths = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-no-paths")))
            self.assertEqual(no_paths.write_scheduler.process_startup_register_paths([], time.monotonic()), (False, 0))
            no_paths.write_scheduler.startup_registration_batch_limit = 1
            self.assertFalse(
                no_paths.write_scheduler.should_process_startup_service(
                    ("svc", {"kind": "register_service"}),
                    processed=1,
                    started=time.monotonic(),
                )
            )

    def test_queue_class_budget_defers_over_budget_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewayQueueBudgetRemoteWrite=1\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            first = {"kind": "set_value", "service": "svc", "path": "/A", "created_at": 1.0, "priority": "user"}
            second = {"kind": "set_value", "service": "svc", "path": "/B", "created_at": 2.0, "priority": "user"}

            self.assertIs(adapter.write_scheduler.select_next_command([("a", first), ("b", second)])[1], first)
            adapter.write_scheduler.record_budget(first)
            self.assertIsNone(adapter.write_scheduler.select_next_command([("a", first), ("b", second)]))

            adapter.write_scheduler.prune_budget(time.time() + 2.0)
            self.assertIs(adapter.write_scheduler.select_next_command([("a", first), ("b", second)])[1], first)
            health = adapter.write_scheduler.health(now=time.time())
            self.assertEqual(health["queue_class_budgets"]["remote-write"], 1)

            adapter.write_scheduler.prune_budget(time.time() + 2.0)
            _install_mock(adapter.write_scheduler, "process_command", MagicMock(return_value="deferred"))
            self.assertEqual(adapter.write_scheduler.process_loaded_command("deferred.json", first), "deferred")
            self.assertFalse(adapter.write_scheduler.budget_available(first, time.time()))

    def test_slo_regulation_adjusts_burst_reads_and_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewayLocalPublishBurstLimit=2\n"
                "DbusGatewaySloQueueMaxAgeSeconds=1\n"
                "DbusGatewaySloCoreReadMaxAgeSeconds=1\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            now = time.time()
            adapter.commands.enqueue({"kind": "publish_value", "path": "/Mode", "created_at": now - 10.0})
            adapter.cache.update_value("grid_power_w", 1.0, source="grid", now=now - 10.0)
            adapter.read_scheduler.next_read_at = {key: now + 1000.0 for key in adapter.read_scheduler.specs}

            adapter.apply_slo_regulation()

            self.assertGreater(adapter.write_scheduler.dynamic_local_publish_burst_limit, 2)
            self.assertEqual(adapter.read_scheduler.next_read_at["grid_power_w"], 0.0)
            self.assertEqual(adapter.read_scheduler.next_read_at["pv_power_w"], 0.0)
            self.assertEqual(adapter.read_scheduler.next_read_at["battery_soc"], 0.0)

            selective = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-selective")))
            selective.cache.update_value("grid_power_w", 1.0, source="grid", now=now)
            selective.cache.update_value("pv_power_w", 0.0, source="pv", now=now - 10.0)
            selective.cache.update_value("battery_soc", 10.0, source="battery", now=now - 10.0)
            selective.read_scheduler.next_read_at = {key: now + 1000.0 for key in selective.read_scheduler.specs}

            selective.apply_slo_regulation()

            self.assertGreater(selective.read_scheduler.next_read_at["grid_power_w"], now)
            self.assertEqual(selective.read_scheduler.next_read_at["pv_power_w"], 0.0)
            self.assertEqual(selective.read_scheduler.next_read_at["battery_soc"], 0.0)

            adapter.circuit.degraded_until = time.time() + 60.0
            adapter.discovery.next_scan_at = 0.0
            adapter.apply_slo_regulation()

            self.assertGreater(adapter.discovery.next_scan_at, time.time())

    def test_slo_snapshot_and_regulation_boundaries_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewayLocalPublishBurstLimit=20\n"
                "DbusGatewaySloCoreReadMaxAgeSeconds=5\n"
                "DbusGatewaySloQueueMaxAgeSeconds=10\n"
                "DbusGatewaySloMainloopGapMaxMs=100\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            _install_mock(
                adapter,
                "slo_observed",
                MagicMock(
                    return_value={
                        "gui_max_age_s": 0.0,
                        "gui_measurement_max_age_s": 0.0,
                        "gui_control_max_age_s": 0.0,
                        "gui_session_max_age_s": 0.0,
                        "core_read_max_age_s": 0.0,
                        "queue_oldest_age_s": 0.0,
                        "mainloop_max_gap_ms_60s": 0.0,
                    }
                ),
            )
            adapter.slo_snapshot(queue_health={}, cache_freshness={}, now=111.0, current_monotonic=222.0)
            adapter.slo_observed.assert_called_once_with({}, {}, 111.0, 222.0)

            _install_mock(
                adapter,
                "cache_freshness_snapshot",
                MagicMock(
                    return_value={
                        "grid_power_w_age_s": 5.0,
                        "grid_power_w_status": "fresh",
                        "pv_power_w_age_s": 5.0,
                        "pv_power_w_status": "fresh",
                        "battery_soc_age_s": 5.0,
                        "battery_soc_status": "fresh",
                    }
                ),
            )
            _install_mock(adapter.read_scheduler, "force_due", MagicMock())
            _install_mock(adapter.tick_health, "snapshot", MagicMock(return_value={"max_tick_gap_ms_60s": 0.0}))
            adapter.apply_slo_regulation()
            adapter.read_scheduler.force_due.assert_not_called()
            _install_mock(adapter, "quiet_discovery_and_introspection", MagicMock())
            adapter.apply_slo_regulation()
            adapter.quiet_discovery_and_introspection.assert_not_called()

            adapter.write_scheduler.local_publish_burst_limit = 20
            _install_mock(adapter, "cache_freshness_snapshot", MagicMock(return_value={}))
            _install_mock(
                adapter.tick_health,
                "snapshot",
                MagicMock(
                    return_value={
                        "max_tick_gap_ms_60s": health_slo_module.effective_mainloop_gap_max_ms(
                            adapter.slo_thresholds()
                        )
                        + 1.0
                    }
                ),
            )
            adapter.apply_slo_regulation()
            self.assertEqual(adapter.write_scheduler.dynamic_local_publish_burst_limit, 10)

            quiet_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-quiet")))
            quiet_adapter.discovery.next_scan_at = 0.0
            quiet_adapter._last_introspection_full_scan_at = 50.0
            quiet_adapter.quiet_discovery_and_introspection(100.0)
            self.assertEqual(quiet_adapter.discovery.next_scan_at, 160.0)
            self.assertEqual(quiet_adapter._last_introspection_full_scan_at, 100.0)

    def test_health_history_helpers_emit_exact_jsonl_payload_contract(self) -> None:
        health = {
            "state": "degraded",
            "timeouts_60s": 3,
            "queues": {
                "oldest_command_age_s": 4.5,
                "oldest_core_command_age_s": 5.5,
                "ignored": 99.0,
            },
            "eventloop": {"max_tick_gap_ms_60s": 123.0},
            "backpressure": {"state": "slow"},
            "cache_freshness": {
                "grid_power_w_age_s": 1.0,
                "grid_power_w_status": "fresh",
                "pv_power_w_age_s": 2.0,
                "pv_power_w_status": "stale",
                "battery_soc_age_s": 3.0,
                "battery_soc_status": "missing",
                "ignored": "not logged",
            },
        }
        with patch.object(health_history_module.time, "time", return_value=123.456):
            self.assertEqual(
                health_history_module.health_log_payload(health),
                {
                    "at": 123.456,
                    "state": "degraded",
                    "backpressure": "slow",
                    "queue_oldest_age_s": 4.5,
                    "core_queue_oldest_age_s": 5.5,
                    "max_tick_gap_ms_60s": 123.0,
                    "timeouts_60s": 3,
                    "cache_freshness": {
                        "grid_power_w_age_s": 1.0,
                        "grid_power_w_status": "fresh",
                        "pv_power_w_age_s": 2.0,
                        "pv_power_w_status": "stale",
                        "battery_soc_age_s": 3.0,
                        "battery_soc_status": "missing",
                    },
                },
            )

        with patch.object(health_history_module.time, "time", return_value=222.0):
            self.assertEqual(
                health_history_module.health_log_payload(
                    {
                        "queues": "bad",
                        "eventloop": [],
                        "backpressure": object(),
                        "cache_freshness": None,
                    }
                ),
                {
                    "at": 222.0,
                    "state": "unknown",
                    "backpressure": "unknown",
                    "queue_oldest_age_s": 0.0,
                    "core_queue_oldest_age_s": 0.0,
                    "max_tick_gap_ms_60s": 0.0,
                    "timeouts_60s": 0,
                    "cache_freshness": {
                        "grid_power_w_age_s": None,
                        "grid_power_w_status": None,
                        "pv_power_w_age_s": None,
                        "pv_power_w_status": None,
                        "battery_soc_age_s": None,
                        "battery_soc_status": None,
                    },
                },
            )
        self.assertEqual(health_history_module.mapping_child({"child": {"value": 1}}, "child"), {"value": 1})
        self.assertEqual(health_history_module.mapping_child({"child": "bad"}, "child"), {})
        self.assertEqual(health_history_module.mapping_child({}, "missing"), {})

        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "nested" / "health.jsonl"
            with patch.object(health_history_module.time, "time", side_effect=[1.0, 2.0]):
                health_history_module.append_health_log(str(log_path), {"state": "ok"})
                health_history_module.append_health_log(str(log_path), {"state": "protective", "timeouts_60s": 9})
            lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(lines[0]["at"], 1.0)
            self.assertEqual(lines[0]["state"], "ok")
            self.assertEqual(lines[1]["at"], 2.0)
            self.assertEqual(lines[1]["state"], "protective")
            self.assertEqual(lines[1]["timeouts_60s"], 9)

    def test_health_history_log_records_small_operational_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            health_log = Path(temp_dir) / "run" / "health-history.jsonl"
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                f"DbusGatewayHealthLogPath={health_log}\n"
                "DbusGatewayHealthLogIntervalSeconds=0.01\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            _install_mock(adapter.cache, "write_snapshot_files", MagicMock())

            adapter.publish_cache()

            payload = json.loads(health_log.read_text(encoding="utf-8").strip())
            self.assertIn("backpressure", payload)
            self.assertIn("queue_oldest_age_s", payload)
            self.assertIn("cache_freshness", payload)

            adapter.health_log_path = "health-history-without-dir.jsonl"
            adapter._last_health_log_monotonic = 0.0
            log_handle = unittest.mock.mock_open()
            with patch.object(health_history_module.os.path, "dirname", return_value=""), patch.object(
                builtins, "open", log_handle
            ):
                adapter.append_health_log({"state": "ok"})
            log_handle.assert_called_once_with("health-history-without-dir.jsonl", "a", encoding="utf-8")

            adapter._last_health_log_monotonic = 0.0
            with patch.object(builtins, "open", side_effect=OSError("full")):
                adapter.append_health_log({"state": "ok"})

    def test_health_log_due_and_error_logging_contracts_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            adapter.health_log_path = ""
            adapter.health_log_interval_seconds = 10.0
            self.assertFalse(adapter.health_log_due())
            adapter.health_log_path = str(Path(temp_dir) / "health.jsonl")
            adapter.health_log_interval_seconds = 0.0
            self.assertFalse(adapter.health_log_due())
            adapter.health_log_interval_seconds = -1.0
            self.assertFalse(adapter.health_log_due())

            adapter.health_log_interval_seconds = 10.0
            adapter._last_health_log_monotonic = 100.0
            with patch.object(process_health_module.time, "monotonic", return_value=109.999):
                self.assertFalse(adapter.health_log_due())
            with patch.object(process_health_module.time, "monotonic", return_value=110.0):
                self.assertTrue(adapter.health_log_due())

            adapter._last_health_log_monotonic = 0.0
            with patch.object(builtins, "open", side_effect=OSError("full")), patch.object(
                process_health_module.logging,
                "debug",
            ) as log_debug, patch.object(process_health_module.time, "monotonic", return_value=120.0):
                adapter.append_health_log({"state": "ok"})
            log_debug.assert_called_once_with(
                "Unable to append DBus gateway health history",
                exc_info=True,
            )

    def test_queue_age_uses_updated_at_for_coalesced_activity(self) -> None:
        commands = [
            ("fresh", {"created_at": 10.0, "updated_at": 95.0}),
            ("old", {"created_at": 90.0}),
            ("bad", {"created_at": "bad"}),
        ]

        self.assertEqual(health_queue_module.oldest_command_age(commands, 100.0), 10.0)

    def test_adaptive_tick_uses_fast_default_and_slows_under_pressure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            self.assertEqual(adapter.min_tick_seconds, 0.2)
            self.assertEqual(adapter.tick_seconds, 0.2)
            self.assertEqual(adapter.adaptive_tick_seconds(circuit_state="ok", resource_state="ok"), 0.2)
            self.assertAlmostEqual(adapter.adaptive_tick_seconds(circuit_state="ok", resource_state="busy"), 0.3)
            self.assertEqual(adapter.adaptive_tick_seconds(circuit_state="degraded", resource_state="ok"), 0.5)
            self.assertEqual(adapter.adaptive_tick_seconds(circuit_state="ok", resource_state="constrained"), 1.0)
            self.assertEqual(adapter.adaptive_tick_seconds(circuit_state="protective", resource_state="ok"), 1.0)

            _install_mock(adapter.resource_monitor, "snapshot", MagicMock(return_value={"state": "busy"}))
            adapter.update_adaptive_tick()

            self.assertAlmostEqual(adapter.tick_seconds, 0.3)
            self.assertEqual(adapter._last_resource_snapshot["state"], "busy")

    def test_tick_skips_work_until_adaptive_interval_is_due(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter._next_work_tick_monotonic = time.monotonic() + 10.0
            _install_mock(adapter, "process_socket_once", MagicMock())
            _install_mock(adapter, "process_one_dbus_operation_once", MagicMock())
            _install_mock(adapter, "publish_cache", MagicMock())

            self.assertTrue(adapter.tick())

            adapter.process_socket_once.assert_not_called()
            adapter.process_one_dbus_operation_once.assert_not_called()
            adapter.publish_cache.assert_not_called()
            adapter.tick_health.record(duration_ms=10000.0, expected_interval_s=0.1, now=time.monotonic())
            adapter.update_adaptive_tick()
            self.assertAlmostEqual(adapter.tick_seconds, 0.3)

    def test_gateway_processes_legacy_introspection_request_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            request_path = Path(temp_dir) / "requests.json"
            snapshot_path = Path(temp_dir) / "map.json"
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                f"DbusIntrospectionRequestPath={request_path}\n"
                f"DbusIntrospectionSnapshotPath={snapshot_path}\n",
                encoding="utf-8",
            )
            request_path.write_text(
                json.dumps(
                    {
                        "requests": [
                            {
                                "service": "com.victronenergy.system",
                                "path": "/Ac/Grid/L1/Power",
                                "priority": 100,
                                "source": "test",
                                "reason": "unit",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            adapter.process_introspection_requests_once()

            pending = adapter.commands.load_pending()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0][1]["kind"], "introspect")
            self.assertEqual(pending[0][1]["coalesce_key"], "introspect:com.victronenergy.system:/Ac/Grid/L1/Power")
            self.assertEqual(json.loads(request_path.read_text(encoding="utf-8")), {"requests": []})

    def test_gateway_introspection_request_and_background_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            request_path = Path(temp_dir) / "requests.json"
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                f"DbusIntrospectionRequestPath={request_path}\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.dbus_introspection_enabled = False
            adapter.process_introspection_requests_once()
            self.assertEqual(adapter.commands.load_pending(), [])

            adapter.dbus_introspection_enabled = True
            adapter.dbus_introspection_request_path = ""
            self.assertEqual(adapter.introspection_request_payload(), {})
            adapter.dbus_introspection_request_path = str(request_path)
            request_path.write_text("[]", encoding="utf-8")
            self.assertEqual(adapter.introspection_request_payload(), {})
            request_path.write_text("{", encoding="utf-8")
            self.assertEqual(adapter.introspection_request_payload(), {})
            request_path.write_text(json.dumps({"requests": "bad"}), encoding="utf-8")
            adapter.process_introspection_requests_once()
            self.assertEqual(adapter.commands.load_pending(), [])
            request_path.write_text(json.dumps({"requests": ["bad", {}, {"service": "", "path": "/P"}]}), encoding="utf-8")
            adapter.process_introspection_requests_once()
            self.assertEqual(adapter.commands.load_pending(), [])
            request_path.write_text(json.dumps({"requests": [{"service": "svc", "path": "/P"}]}), encoding="utf-8")
            adapter.process_introspection_requests_once()
            self.assertEqual(adapter._introspection_queue_depth, 1)
            clear_error = RuntimeError("readonly")
            with patch.object(
                introspection_module,
                "write_text_atomically",
                MagicMock(side_effect=clear_error),
            ), patch.object(introspection_module.logging, "debug") as debug:
                adapter.clear_introspection_request_payload()
            debug.assert_called_once_with(
                "Unable to clear DBus introspection request payload %s: %s",
                str(request_path),
                clear_error,
            )

            adapter._introspection_queue_depth = 5
            request_path.write_text(
                json.dumps({"requests": [{"service": "svc", "path": "/A"}, {"service": "svc", "path": "/B"}]}),
                encoding="utf-8",
            )
            adapter.process_introspection_requests_once()
            self.assertEqual(adapter._introspection_queue_depth, 7)

            background = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-bg")))
            _install_mock(background, "enqueue_introspection_command", MagicMock())
            background.enqueue_background_introspection_if_due()
            background.enqueue_introspection_command.assert_not_called()
            background.cache.update_services(["com.victronenergy.battery.tty1", "com.victronenergy.pvinverter.http_1"])
            background.circuit.protective_until = time.time() + 10.0
            background.enqueue_background_introspection_if_due()
            background.enqueue_introspection_command.assert_not_called()
            background.circuit.protective_until = 0.0
            background._last_introspection_full_scan_at = 0.0
            background.enqueue_background_introspection_if_due()
            self.assertGreater(background.enqueue_introspection_command.call_count, 0)
            background.enqueue_introspection_command.assert_any_call(
                "com.victronenergy.battery.tty1",
                "/Soc",
                priority=70,
                source="battery",
                reason="battery-service-discovery",
            )
            self.assertEqual(
                background.configured_or_prefixed_services(
                    "UnusedExplicit",
                    "UnusedPrefix",
                    "com.victronenergy.pvinverter",
                ),
                ["com.victronenergy.pvinverter.http_1"],
            )
            background.config["DEFAULT"]["UnusedExplicit"] = "com.victronenergy.pvinverter.missing"
            self.assertEqual(
                background.configured_or_prefixed_services(
                    "UnusedExplicit",
                    "UnusedPrefix",
                    "com.victronenergy.pvinverter",
                ),
                [],
            )

            quiet_config = Path(temp_dir) / "quiet.ini"
            quiet_config.write_text(
                "[DEFAULT]\n"
                "AutoGridService=\n"
                "AutoGridL1Path=\n"
                "AutoGridL2Path=\n"
                "AutoGridL3Path=\n"
                "AutoBatterySocPath=\n"
                "AutoPvPath=\n",
                encoding="utf-8",
            )
            quiet_background = DbusAdapter(str(quiet_config), paths=gateway_paths(str(Path(temp_dir) / "run-quiet-bg")))
            quiet_background.cache.update_services(["com.victronenergy.battery.tty1", "com.victronenergy.pvinverter.http_1"])
            self.assertEqual(quiet_background.background_introspection_specs(), [])

    def test_gateway_introspection_request_contracts(self) -> None:
        payload = {
            "requests": [
                "bad",
                {},
                {"service": "svc-missing-path"},
                {"path": "/MissingService"},
                {"service": "", "path": "/Missing"},
                {"service": " svc ", "path": " /Path ", "priority": "88.9", "source": "", "reason": ""},
                {"service": "svc-defaults", "path": "/Defaults"},
                {"service": "svc2", "path": "/P2", "priority": "bad", "source": "api", "reason": "need"},
            ]
        }

        self.assertEqual(
            introspection_module._valid_introspection_requests(payload),
            [
                {"service": "svc", "path": "/Path", "priority": 88, "source": "request", "reason": "requested"},
                {"service": "svc-defaults", "path": "/Defaults", "priority": 100, "source": "request", "reason": "requested"},
                {"service": "svc2", "path": "/P2", "priority": 100, "source": "api", "reason": "need"},
            ],
        )
        self.assertEqual(introspection_module._valid_introspection_requests({"requests": "bad"}), [])
        self.assertEqual(introspection_module._int_or_default(None, 7), 7)
        self.assertEqual(introspection_module._drop_command({"kind": "unknown"}), "dropped")

    def test_gateway_introspection_file_payload_uses_utf8_and_dict_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            request_path = Path(temp_dir) / "requests.json"
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            request_path.write_text(json.dumps({"requests": []}), encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.dbus_introspection_request_path = str(request_path)

            with patch.object(builtins, "open", wraps=builtins.open) as open_mock:
                self.assertEqual(adapter.introspection_request_payload(), {"requests": []})
            open_mock.assert_called_once_with(str(request_path), encoding="utf-8")

            request_path.write_bytes(b"\xff")
            self.assertEqual(adapter.introspection_request_payload(), {})

    def test_gateway_enqueue_introspection_requests_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            enqueue = _install_mock(adapter, "enqueue_introspection_command", MagicMock())

            accepted = adapter.enqueue_introspection_requests(
                {
                    "requests": [
                        {"service": "svc", "path": "/A", "priority": 11, "source": "src-a", "reason": "why-a"},
                        {"service": "svc", "path": "/B", "priority": 22, "source": "src-b", "reason": "why-b"},
                    ]
                }
            )

            self.assertEqual(accepted, 2)
            self.assertEqual(
                enqueue.call_args_list[0].args,
                ("svc", "/A"),
            )
            self.assertEqual(
                enqueue.call_args_list[0].kwargs,
                {"priority": 11, "source": "src-a", "reason": "why-a"},
            )
            self.assertEqual(enqueue.call_args_list[1].args, ("svc", "/B"))
            self.assertEqual(
                enqueue.call_args_list[1].kwargs,
                {"priority": 22, "source": "src-b", "reason": "why-b"},
            )

    def test_gateway_introspection_enqueue_command_payload_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nDbusIntrospectionTimeoutSeconds=2.5\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            enqueue = _install_mock(adapter.commands, "enqueue", MagicMock())

            adapter.enqueue_introspection_command("svc", "/Low", priority=89, source="test", reason="low")
            adapter.enqueue_introspection_command("svc", "/High", priority=90, source="test", reason="high")

            self.assertEqual(
                enqueue.call_args_list[0].args[0],
                {
                    "kind": "introspect",
                    "service": "svc",
                    "path": "/Low",
                    "priority": "discovery",
                    "source": "test",
                    "reason": "low",
                    "timeout": 2.5,
                    "coalesce_key": "introspect:svc:/Low",
                },
            )
            self.assertEqual(enqueue.call_args_list[1].args[0]["priority"], "optional")
            self.assertEqual(enqueue.call_args_list[1].args[0]["coalesce_key"], "introspect:svc:/High")

            default_config = Path(temp_dir) / "default.ini"
            default_config.write_text("[DEFAULT]\n", encoding="utf-8")
            default_adapter = DbusAdapter(str(default_config), paths=gateway_paths(str(Path(temp_dir) / "run-default")))
            default_enqueue = _install_mock(default_adapter.commands, "enqueue", MagicMock())
            default_adapter.enqueue_introspection_command("svc", "/Default", priority=90, source="test", reason="default")
            self.assertEqual(default_enqueue.call_args.args[0]["timeout"], 1.0)

    def test_gateway_background_introspection_spec_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusIntrospectionFullScanIntervalSeconds=1\n"
                "AutoGridService= com.victronenergy.system \n"
                "AutoGridL1Path= /Ac/Grid/L1/Power \n"
                "AutoGridL2Path=\n"
                "AutoGridL3Path=/Ac/Grid/L3/Power\n"
                "AutoBatteryServicePrefix=com.victronenergy.battery\n"
                "AutoPvServicePrefix=com.victronenergy.pvinverter\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.cache.update_services(
                [
                    "com.victronenergy.battery.tty2",
                    "com.victronenergy.battery.tty1",
                    *(f"com.victronenergy.pvinverter.http_{index:02d}" for index in range(12)),
                    "com.victronenergy.system",
                ]
            )

            self.assertFalse(adapter.background_introspection_due(59.9))
            self.assertTrue(adapter.background_introspection_due(60.0))
            adapter._last_introspection_full_scan_at = 30.0
            self.assertFalse(adapter.background_introspection_due(40.0))
            adapter._last_introspection_full_scan_at = 0.0
            adapter.dbus_introspection_enabled = False
            self.assertFalse(adapter.background_introspection_due(61.0))
            adapter.dbus_introspection_enabled = True
            allows_priority = _install_mock(adapter.circuit, "allows_priority", MagicMock(return_value=False))
            self.assertFalse(adapter.background_introspection_due(61.0))
            allows_priority.assert_called_with("discovery")

            self.assertEqual(
                adapter.grid_introspection_specs(),
                [
                    ("com.victronenergy.system", "/Ac/Grid/L1/Power", 80, "grid", "configured-grid-path"),
                    ("com.victronenergy.system", "/Ac/Grid/L3/Power", 80, "grid", "configured-grid-path"),
                ],
            )
            self.assertEqual(
                adapter.battery_introspection_specs(),
                [
                    ("com.victronenergy.battery.tty1", "/Soc", 70, "battery", "battery-service-discovery"),
                    ("com.victronenergy.battery.tty2", "/Soc", 70, "battery", "battery-service-discovery"),
                ],
            )
            pv_specs = adapter.pv_introspection_specs()
            self.assertEqual(len(pv_specs), 10)
            self.assertEqual(pv_specs[0], ("com.victronenergy.pvinverter.http_00", "/Ac/Power", 30, "pv", "pv-service-discovery"))
            self.assertEqual(pv_specs[-1][0], "com.victronenergy.pvinverter.http_09")

            adapter.config["DEFAULT"]["AutoBatteryService"] = "com.victronenergy.battery.tty2"
            self.assertEqual(adapter.configured_or_prefixed_services("AutoBatteryService", "Ignored", "x"), ["com.victronenergy.battery.tty2"])

            default_config = Path(temp_dir) / "defaults.ini"
            default_config.write_text("[DEFAULT]\n", encoding="utf-8")
            default_adapter = DbusAdapter(str(default_config), paths=gateway_paths(str(Path(temp_dir) / "run-defaults")))
            default_adapter.cache.update_services(["com.victronenergy.pvinverter.http_default"])
            self.assertFalse(default_adapter.background_introspection_due(21599.9))
            self.assertTrue(default_adapter.background_introspection_due(21600.0))
            self.assertEqual(
                default_adapter.grid_introspection_specs(),
                [
                    ("com.victronenergy.system", "/Ac/Grid/L1/Power", 80, "grid", "configured-grid-path"),
                    ("com.victronenergy.system", "/Ac/Grid/L2/Power", 80, "grid", "configured-grid-path"),
                    ("com.victronenergy.system", "/Ac/Grid/L3/Power", 80, "grid", "configured-grid-path"),
                ],
            )
            self.assertEqual(
                default_adapter.pv_introspection_specs(),
                [("com.victronenergy.pvinverter.http_default", "/Ac/Power", 30, "pv", "pv-service-discovery")],
            )

            custom_config = Path(temp_dir) / "custom.ini"
            custom_config.write_text(
                "[DEFAULT]\n"
                "AutoGridService=custom.grid\n"
                "AutoGridL1Path=/Custom/L1\n"
                "AutoGridL2Path=/Custom/L2\n"
                "AutoGridL3Path=/Custom/L3\n"
                "AutoBatterySocPath=/Custom/Soc\n"
                "AutoPvPath=/Custom/Pv\n",
                encoding="utf-8",
            )
            custom = DbusAdapter(str(custom_config), paths=gateway_paths(str(Path(temp_dir) / "run-custom")))
            custom.cache.update_services(["custom.battery.1", "custom.pv.1"])
            custom.config["DEFAULT"]["AutoBatteryServicePrefix"] = "custom.battery"
            custom.config["DEFAULT"]["AutoPvServicePrefix"] = "custom.pv"
            self.assertEqual(
                custom.grid_introspection_specs(),
                [
                    ("custom.grid", "/Custom/L1", 80, "grid", "configured-grid-path"),
                    ("custom.grid", "/Custom/L2", 80, "grid", "configured-grid-path"),
                    ("custom.grid", "/Custom/L3", 80, "grid", "configured-grid-path"),
                ],
            )
            self.assertEqual(custom.battery_introspection_specs(), [("custom.battery.1", "/Custom/Soc", 70, "battery", "battery-service-discovery")])
            self.assertEqual(custom.pv_introspection_specs(), [("custom.pv.1", "/Custom/Pv", 30, "pv", "pv-service-discovery")])

            explicit_config = Path(temp_dir) / "explicit.ini"
            explicit_config.write_text(
                "[DEFAULT]\n"
                "AutoBatteryService=custom.battery.explicit\n"
                "AutoBatteryServicePrefix=custom.battery\n"
                "AutoPvService=custom.pv.explicit\n"
                "AutoPvServicePrefix=custom.pv\n",
                encoding="utf-8",
            )
            explicit = DbusAdapter(str(explicit_config), paths=gateway_paths(str(Path(temp_dir) / "run-explicit")))
            explicit.cache.update_services(
                [
                    "custom.battery.explicit",
                    "custom.battery.other",
                    "custom.pv.explicit",
                    "custom.pv.other",
                ]
            )
            self.assertEqual(
                explicit.battery_introspection_specs(),
                [("custom.battery.explicit", "/Soc", 70, "battery", "battery-service-discovery")],
            )
            self.assertEqual(
                explicit.pv_introspection_specs(),
                [("custom.pv.explicit", "/Ac/Power", 30, "pv", "pv-service-discovery")],
            )

    def test_gateway_non_write_introspection_command_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            _install_mock(adapter.read_executor, "refresh_requested_value", MagicMock(return_value="applied"))
            self.assertEqual(adapter.process_non_write_command({"type": "refresh_value", "key": "grid_power_w"}), "applied")
            adapter.read_executor.refresh_requested_value.assert_called_once_with({"type": "refresh_value", "key": "grid_power_w"})

            list_services = _install_mock(adapter, "list_services", MagicMock(return_value=["svc1", "svc2"]))
            remove_coalesced = _install_mock(adapter.commands, "remove_coalesced", MagicMock())
            self.assertEqual(adapter.refresh_services_command({"kind": "refresh_services"}), "applied")
            list_services.assert_called_once_with()
            self.assertEqual(sorted(adapter.cache.services), ["svc1", "svc2"])
            remove_coalesced.assert_called_once_with("refresh:services")

            adapter.circuit.degraded_until = time.time() + 5.0
            remove_coalesced.reset_mock()
            self.assertEqual(adapter.refresh_services_command({"kind": "refresh_services"}), "deferred")
            remove_coalesced.assert_not_called()
            adapter.circuit.degraded_until = 0.0

            services_error = RuntimeError("dbus down")
            _install_mock(adapter, "list_services", MagicMock(side_effect=services_error))
            record_error = _install_mock(adapter.discovery, "record_error", MagicMock())
            remove_coalesced.reset_mock()
            self.assertEqual(adapter.refresh_services_command({"kind": "refresh_services"}), "dropped")
            record_error.assert_called_once()
            self.assertIs(record_error.call_args.args[0], services_error)
            remove_coalesced.assert_called_once_with("refresh:services")

    def test_gateway_writes_legacy_introspection_snapshot_from_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "map.json"
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                f"DbusIntrospectionSnapshotPath={snapshot_path}\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            xml = "<node><interface name='com.victronenergy.BusItem'/><node name='Child'/></node>"
            adapter.cache.update_value(
                "introspection:com.victronenergy.system:/Ac/Grid",
                xml,
                source="com.victronenergy.system/Ac/Grid",
                confidence=0.7,
                now=123.0,
            )

            adapter.write_introspection_snapshot()

            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
            finding = payload["services"]["com.victronenergy.system"]["paths"]["/Ac/Grid"]
            self.assertEqual(payload["worker_state"], "gateway")
            self.assertEqual(finding["status"], "fresh")
            self.assertEqual(finding["interfaces"], ["com.victronenergy.BusItem"])
            self.assertEqual(finding["children"], ["Child"])
            adapter.cache.mark_error("introspection:bad-key", source="bad", error="bad", now=124.0)
            adapter.cache.mark_error("introspection::/NoService", source="bad", error="bad", now=124.5)
            adapter.cache.mark_error("introspection:svc:/Broken", source="svc/Broken", error="offline", now=125.0)
            snapshot = adapter.introspection_services_snapshot(200.0)
            self.assertNotIn("", snapshot)
            self.assertEqual(snapshot["svc"]["paths"]["/Broken"]["status"], "unresponsive-backoff")
            self.assertEqual(adapter.parse_introspection_xml("<bad"), ([], []))

            services = {"svc": {"paths": "broken", "last_updated_at": "bad"}}
            adapter.add_introspection_service_entry(
                services,
                "svc",
                "/Recovered",
                {"status": "error", "source": "svc/Recovered", "updated_at": "bad"},
                210.0,
            )
            self.assertIsInstance(services["svc"]["paths"], dict)
            self.assertEqual(services["svc"]["paths"]["/Recovered"]["status"], "unresponsive-backoff")
            self.assertEqual(services["svc"]["last_updated_at"], 210.0)

            adapter.dbus_introspection_enabled = False
            adapter.write_introspection_snapshot()

    def test_gateway_introspection_snapshot_logs_write_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "map.json"
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                f"DbusIntrospectionSnapshotPath={snapshot_path}\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.dbus_introspection_enabled = True
            method_globals = adapter.write_introspection_snapshot.__globals__

            with (
                patch.dict(method_globals, {"write_text_atomically": MagicMock(side_effect=OSError("readonly"))}),
                patch.object(method_globals["logging"], "debug") as debug_log,
            ):
                adapter.write_introspection_snapshot()

        debug_log.assert_called_once()

    def test_tick_and_dbus_operation_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            adapter._stop = True
            _install_mock(adapter, "close_socket", MagicMock())
            self.assertFalse(adapter.tick())
            adapter.close_socket.assert_called_once()

            adapter._stop = False
            _install_mock(adapter, "process_socket_once", MagicMock(side_effect=RuntimeError("tick failed")))
            self.assertTrue(adapter.tick())
            self.assertEqual(adapter.circuit.last_error, "tick failed")

            _install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=True))
            _install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=True))
            _install_mock(adapter, "refresh_services_if_due_once", MagicMock(return_value=True))
            self.assertTrue(adapter.process_one_dbus_operation_once())
            adapter.refresh_services_if_due_once.assert_called_once()
            adapter.poll_one_due_read_once.assert_not_called()

            adapter.cache.update_services(["svc"])
            _install_mock(adapter, "refresh_services_if_due_once", MagicMock(return_value=True))
            _install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=True))
            _install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=True))
            self.assertTrue(adapter.process_one_dbus_operation_once())
            adapter.poll_one_due_read_once.assert_called_once()
            adapter.write_scheduler.process_one.assert_not_called()
            _install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=False))
            _install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=True))
            self.assertTrue(adapter.process_one_dbus_operation_once())
            adapter._prefer_read_next = True
            _install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=True))
            _install_mock(adapter, "reads_need_priority", MagicMock(return_value=False))
            self.assertTrue(adapter.try_read_then_write())
            self.assertFalse(adapter._prefer_read_next)
            adapter._prefer_read_next = True
            _install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=False))
            _install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=False))
            _install_mock(adapter, "refresh_services_if_due_once", MagicMock(return_value=False))
            self.assertFalse(adapter.process_one_dbus_operation_once())
            adapter._prefer_read_next = False
            _install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=True))
            _install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=True))
            self.assertTrue(adapter.try_write_then_read())
            adapter.poll_one_due_read_once.assert_not_called()
            adapter._prefer_read_next = True
            _install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=False))
            _install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=True))
            self.assertTrue(adapter.try_write_then_read())
            self.assertFalse(adapter._prefer_read_next)
            _install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=False))
            _install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=True))
            self.assertTrue(adapter.process_one_dbus_operation_once())
            self.assertFalse(adapter._prefer_read_next)
            _install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=False))
            _install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=False))
            _install_mock(adapter, "refresh_services_if_due_once", MagicMock(return_value=True))
            self.assertTrue(adapter.process_one_dbus_operation_once())

            priority_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-priority")))
            priority_adapter.cache.update_services(["svc"])
            _install_mock(priority_adapter, "enqueue_background_introspection_if_due", MagicMock())
            _install_mock(priority_adapter, "poll_one_due_read_once", MagicMock(return_value=True))
            _install_mock(
                priority_adapter.write_scheduler,
                "process_local_publish_burst",
                MagicMock(return_value=5),
            )
            self.assertTrue(priority_adapter.process_one_dbus_operation_once())
            priority_adapter.poll_one_due_read_once.assert_called_once()
            priority_adapter.write_scheduler.process_local_publish_burst.assert_not_called()

            aggregate_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-aggregate")))
            aggregate_adapter.cache.update_services(["svc"])
            now = time.time()
            for key in ("grid_power_w", "pv_power_w", "battery_soc"):
                aggregate_adapter.cache.update_value(key, 1.0, source="test", now=now)
            _install_mock(aggregate_adapter.read_executor, "read_busitem", MagicMock(return_value=1.0))
            self.assertEqual(
                aggregate_adapter.read_executor.poll_read_spec(
                    "pv_power_w",
                    {"aggregate": "sum", "service": "svc", "paths": ["/L1", "/L2"]},
                ),
                "deferred",
            )
            _install_mock(aggregate_adapter, "enqueue_background_introspection_if_due", MagicMock())
            _install_mock(aggregate_adapter, "poll_one_due_read_once", MagicMock(return_value=True))
            _install_mock(
                aggregate_adapter.write_scheduler,
                "process_local_publish_burst",
                MagicMock(return_value=5),
            )
            self.assertTrue(aggregate_adapter.process_one_dbus_operation_once())
            aggregate_adapter.poll_one_due_read_once.assert_called_once()
            aggregate_adapter.write_scheduler.process_local_publish_burst.assert_not_called()

    def test_tick_records_lifecycle_and_honors_stop_after_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter._next_work_tick_monotonic = 100.0
            _install_mock(adapter, "process_socket_once", MagicMock())
            _install_mock(adapter, "process_introspection_requests_once", MagicMock())
            _install_mock(adapter, "process_one_dbus_operation_once", MagicMock())
            _install_mock(adapter, "publish_cache", MagicMock())
            _install_mock(adapter.tick_health, "record", MagicMock())
            _install_mock(adapter, "update_adaptive_tick", MagicMock())

            with patch.object(process_loop_module.time, "time", return_value=123.0), patch.object(
                process_loop_module.time,
                "monotonic",
                side_effect=[100.0, 100.03, 100.04],
            ):
                self.assertTrue(adapter.tick())

            self.assertEqual(adapter._last_tick_at, 123.0)
            self.assertEqual(adapter._last_tick_monotonic, 100.0)
            self.assertAlmostEqual(adapter._last_tick_duration_ms, 30.0)
            adapter.tick_health.record.assert_called_once_with(
                duration_ms=adapter._last_tick_duration_ms,
                expected_interval_s=adapter.tick_seconds,
                now=100.0,
            )
            adapter.update_adaptive_tick.assert_called_once()
            self.assertAlmostEqual(adapter._next_work_tick_monotonic, 100.04 + adapter.tick_seconds)
            adapter.process_socket_once.assert_called_once()
            adapter.process_introspection_requests_once.assert_called_once()
            adapter.process_one_dbus_operation_once.assert_called_once()
            adapter.publish_cache.assert_called_once()

            deferred_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-deferred")))
            deferred_adapter._next_work_tick_monotonic = 100.01
            _install_mock(deferred_adapter, "process_socket_once", MagicMock())
            with patch.object(process_loop_module.time, "monotonic", return_value=100.0):
                self.assertTrue(deferred_adapter.tick())
            deferred_adapter.process_socket_once.assert_not_called()

            stop_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-stop")))
            stop_adapter._next_work_tick_monotonic = 0.0

            def stop_during_work() -> None:
                stop_adapter._stop = True

            _install_mock(stop_adapter, "process_socket_once", MagicMock(side_effect=stop_during_work))
            _install_mock(stop_adapter, "process_introspection_requests_once", MagicMock())
            _install_mock(stop_adapter, "process_one_dbus_operation_once", MagicMock())
            _install_mock(stop_adapter, "publish_cache", MagicMock())
            self.assertFalse(stop_adapter.tick())
            stop_adapter.process_introspection_requests_once.assert_called_once()
            stop_adapter.process_one_dbus_operation_once.assert_called_once()
            stop_adapter.publish_cache.assert_called_once()

    def test_run_initializes_gateway_loop_and_closes_transport(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nDbusGatewayMinTickSeconds=0.9995\n", encoding="utf-8")
            paths = gateway_paths(str(Path(temp_dir) / "run"))
            Path(paths.run_dir).mkdir(parents=True)
            Path(paths.command_dir).mkdir(parents=True)
            Path(paths.core_command_dir).mkdir(parents=True)
            adapter = DbusAdapter(str(config_path), paths=paths)
            fake_loop = MagicMock()
            _install_mock(adapter, "install_signal_handlers", MagicMock())
            _install_mock(adapter, "start_socket", MagicMock())
            _install_mock(adapter, "ensure_dbus_service", MagicMock())
            _install_mock(adapter, "close_socket", MagicMock())

            with patch.object(process_loop_module, "DBusGMainLoop") as dbus_mainloop, patch.object(
                process_loop_module.GLib,
                "MainLoop",
                return_value=fake_loop,
            ) as main_loop_factory, patch.object(process_loop_module.GLib, "timeout_add", return_value=123) as timeout_add:
                adapter.run()

            dbus_mainloop.assert_called_once_with(set_as_default=True)
            adapter.install_signal_handlers.assert_called_once()
            self.assertTrue(Path(paths.run_dir).is_dir())
            self.assertTrue(Path(paths.command_dir).is_dir())
            self.assertTrue(Path(paths.core_command_dir).is_dir())
            adapter.start_socket.assert_called_once()
            adapter.ensure_dbus_service.assert_called_once()
            main_loop_factory.assert_called_once_with()
            timeout_add.assert_called_once_with(max(50, int(adapter.min_tick_seconds * 1000)), adapter.tick)
            fake_loop.run.assert_called_once_with()
            self.assertIs(adapter._main_loop, fake_loop)
            self.assertTrue(adapter._stop)
            adapter.close_socket.assert_called_once()

    def test_run_uses_minimum_timer_interval_for_fast_ticks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nDbusGatewayMinTickSeconds=0.05\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-fast")))
            _install_mock(adapter, "install_signal_handlers", MagicMock())
            _install_mock(adapter, "start_socket", MagicMock())
            _install_mock(adapter, "ensure_dbus_service", MagicMock())
            _install_mock(adapter, "close_socket", MagicMock())

            with patch.object(process_loop_module, "DBusGMainLoop"), patch.object(
                process_loop_module.GLib,
                "MainLoop",
                return_value=MagicMock(),
            ), patch.object(process_loop_module.GLib, "timeout_add", return_value=123) as timeout_add:
                adapter.run()

            timeout_add.assert_called_once_with(50, adapter.tick)

    def test_tick_recovery_records_and_logs_gateway_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter._next_work_tick_monotonic = 0.0
            error = RuntimeError("tick boom")
            _install_mock(adapter, "process_socket_once", MagicMock(side_effect=error))
            _install_mock(adapter, "process_introspection_requests_once", MagicMock())
            _install_mock(adapter, "process_one_dbus_operation_once", MagicMock())
            _install_mock(adapter, "publish_cache", MagicMock())
            _install_mock(adapter.circuit, "record_error", MagicMock())

            with patch.object(process_loop_module.logging, "exception") as log_exception:
                self.assertTrue(adapter.tick())

            adapter.circuit.record_error.assert_called_once_with(error)
            log_exception.assert_called_once_with("DBus adapter tick failed: %s", error)
            adapter.process_introspection_requests_once.assert_not_called()
            adapter.process_one_dbus_operation_once.assert_not_called()
            adapter.publish_cache.assert_not_called()

    def test_loop_core_read_freshness_and_priority_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            stale_sentinel = adapter.slo_core_read_max_age_seconds + 1.0

            self.assertEqual(adapter.core_read_age("grid_power_w", 100.0), stale_sentinel)
            adapter.cache.values["grid_power_w"] = {"updated_at": 0.0}
            self.assertEqual(adapter.core_read_age("grid_power_w", 100.0), stale_sentinel)
            adapter.cache.values["grid_power_w"] = {"updated_at": "bad"}
            self.assertEqual(adapter.core_read_age("grid_power_w", 100.0), stale_sentinel)
            adapter.cache.values["grid_power_w"] = {"updated_at": 0.5}
            self.assertEqual(adapter.core_read_age("grid_power_w", 100.0), 99.5)
            adapter.cache.values["grid_power_w"] = {"updated_at": 95.0}
            self.assertEqual(adapter.core_read_age("grid_power_w", 100.0), 5.0)
            adapter.cache.values["grid_power_w"] = {"updated_at": 105.0}
            self.assertEqual(adapter.core_read_age("grid_power_w", 100.0), -5.0)

            now = 200.0
            for key in ("grid_power_w", "pv_power_w", "battery_soc"):
                adapter.cache.values[key] = {"updated_at": now - adapter.slo_core_read_max_age_seconds}
            with patch.object(process_loop_module.time, "time", return_value=now):
                self.assertFalse(adapter.core_reads_stale())
                self.assertFalse(adapter.reads_need_priority())

            adapter.cache.values["pv_power_w"] = {"updated_at": now - adapter.slo_core_read_max_age_seconds - 0.01}
            with patch.object(process_loop_module.time, "time", return_value=now):
                self.assertTrue(adapter.core_reads_stale())
                self.assertTrue(adapter.reads_need_priority())

            adapter.cache.values["pv_power_w"] = {"updated_at": now}
            _install_mock(adapter.read_executor, "has_pending_aggregate", MagicMock(return_value=True))
            with patch.object(process_loop_module.time, "time", return_value=now):
                self.assertTrue(adapter.reads_need_priority())
            adapter.read_executor.has_pending_aggregate.assert_called_once()

    def test_loop_read_write_preference_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            _install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=True))
            _install_mock(adapter, "reads_need_priority", MagicMock(return_value=True))
            _install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=True))
            adapter._prefer_read_next = False
            self.assertTrue(adapter.try_read_then_write())
            self.assertTrue(adapter._prefer_read_next)
            adapter.write_scheduler.process_one.assert_not_called()

            _install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=False))
            _install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=True))
            adapter._prefer_read_next = False
            self.assertTrue(adapter.try_read_then_write())
            adapter.write_scheduler.process_one.assert_called_once_with(include_local_publish=False)
            self.assertTrue(adapter._prefer_read_next)

            _install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=False))
            adapter._prefer_read_next = False
            self.assertFalse(adapter.try_scheduled_write(prefer_read_next=True))
            self.assertFalse(adapter._prefer_read_next)

            _install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=True))
            adapter._prefer_read_next = True
            self.assertTrue(adapter.try_scheduled_write(prefer_read_next=False))
            self.assertIs(adapter._prefer_read_next, False)
            adapter.write_scheduler.process_one.assert_called_once_with(include_local_publish=False)

            _install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=False))
            _install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=False))
            self.assertFalse(adapter.try_write_then_read())
            adapter.poll_one_due_read_once.assert_called_once()

            _install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=True))
            _install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=True))
            adapter._prefer_read_next = False
            self.assertTrue(adapter.try_write_then_read())
            self.assertTrue(adapter._prefer_read_next)
            adapter.write_scheduler.process_one.assert_called_once_with(include_local_publish=False)
            adapter.poll_one_due_read_once.assert_not_called()

            _install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=False))
            _install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=True))
            adapter._prefer_read_next = True
            self.assertTrue(adapter.try_write_then_read())
            self.assertIs(adapter._prefer_read_next, False)

    def test_standard_operation_and_adaptive_tick_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            _install_mock(adapter.write_scheduler, "process_local_publish_burst", MagicMock(return_value=3))
            _install_mock(adapter, "process_preferred_read_or_write", MagicMock(return_value=True))
            _install_mock(adapter, "refresh_services_if_due_once", MagicMock(return_value=True))
            self.assertTrue(adapter.process_standard_operation_once())
            adapter.write_scheduler.process_local_publish_burst.assert_called_once()
            adapter.process_preferred_read_or_write.assert_called_once()
            adapter.refresh_services_if_due_once.assert_not_called()

            _install_mock(adapter.write_scheduler, "process_local_publish_burst", MagicMock(return_value=2))
            _install_mock(adapter, "process_preferred_read_or_write", MagicMock(return_value=False))
            _install_mock(adapter, "refresh_services_if_due_once", MagicMock(return_value=False))
            self.assertTrue(adapter.process_standard_operation_once())

            _install_mock(adapter.write_scheduler, "process_local_publish_burst", MagicMock(return_value=0))
            _install_mock(adapter, "process_preferred_read_or_write", MagicMock(return_value=False))
            _install_mock(adapter, "refresh_services_if_due_once", MagicMock(return_value=False))
            self.assertFalse(adapter.process_standard_operation_once())

            adapter.tick_health.record(
                duration_ms=adapter.slo_mainloop_gap_max_ms + 1.0,
                expected_interval_s=adapter.tick_seconds,
                now=time.monotonic(),
            )
            _install_mock(adapter.resource_monitor, "snapshot", MagicMock(return_value={"state": "ok"}))
            _install_mock(adapter.circuit, "state", MagicMock(return_value="ok"))
            adapter.update_adaptive_tick()
            self.assertEqual(adapter._last_resource_snapshot, {"state": "ok"})
            self.assertAlmostEqual(adapter.tick_seconds, 0.3)

            _install_mock(adapter.resource_monitor, "snapshot", MagicMock(return_value={"state": "constrained"}))
            _install_mock(adapter.circuit, "state", MagicMock(return_value="degraded"))
            adapter.update_adaptive_tick()
            self.assertEqual(adapter.tick_seconds, adapter.max_tick_seconds)

            boundary_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-boundary")))
            _install_mock(boundary_adapter.resource_monitor, "snapshot", MagicMock(return_value={"state": "ok"}))
            _install_mock(
                boundary_adapter.tick_health,
                "snapshot",
                MagicMock(return_value={"max_tick_duration_ms_60s": boundary_adapter.slo_mainloop_gap_max_ms}),
            )
            _install_mock(boundary_adapter.circuit, "state", MagicMock(return_value="ok"))
            boundary_adapter.update_adaptive_tick()
            self.assertEqual(boundary_adapter.tick_seconds, boundary_adapter.min_tick_seconds)

            missing_state_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-missing-state")))
            _install_mock(missing_state_adapter.resource_monitor, "snapshot", MagicMock(return_value={}))
            _install_mock(
                missing_state_adapter.tick_health,
                "snapshot",
                MagicMock(return_value={"max_tick_duration_ms_60s": missing_state_adapter.slo_mainloop_gap_max_ms + 1.0}),
            )
            _install_mock(missing_state_adapter.circuit, "state", MagicMock(return_value="ok"))
            missing_state_adapter.update_adaptive_tick()
            self.assertAlmostEqual(missing_state_adapter.tick_seconds, 0.3)

            degraded_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-degraded")))
            _install_mock(degraded_adapter.resource_monitor, "snapshot", MagicMock(return_value={"state": "ok"}))
            _install_mock(
                degraded_adapter.tick_health,
                "snapshot",
                MagicMock(return_value={"max_tick_duration_ms_60s": 0.0}),
            )
            _install_mock(degraded_adapter.circuit, "state", MagicMock(return_value="degraded"))
            degraded_adapter.update_adaptive_tick()
            self.assertAlmostEqual(degraded_adapter.tick_seconds, 0.5)

            tuning_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-tuning")))
            tuning_adapter.min_tick_seconds = 0.4
            tuning_adapter.max_tick_seconds = 2.0
            self.assertAlmostEqual(
                tuning_adapter.adaptive_tick_seconds(circuit_state="degraded", resource_state="ok"),
                1.0,
            )
            self.assertAlmostEqual(
                tuning_adapter.adaptive_tick_seconds(circuit_state="ok", resource_state="busy"),
                0.6,
            )

    def test_health_regulation_edges_reduce_burst_and_ignore_bad_cached_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-health-edges")))

            adapter.write_scheduler.local_publish_burst_limit = 20
            thresholds = adapter.slo_thresholds()
            self.assertEqual(
                health_slo_module.regulated_publish_burst(
                    queue_age=0.0,
                    eventloop_gap_ms=health_slo_module.effective_mainloop_gap_max_ms(thresholds) + 1.0,
                    base_burst=adapter.write_scheduler.local_publish_burst_limit,
                    thresholds=thresholds,
                ),
                10,
            )

            now = time.time()
            adapter.cache.update_value(
                "path:com.victronenergy.evcharger.http_60/Ac/Power",
                object(),
                source="test",
                now=now,
            )
            self.assertEqual(adapter.fresh_cached_path_float("/Ac/Power", now), 0.0)

            fresh_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-fresh")))
            fresh_adapter.cache.update_services(["svc"])
            for key in ("grid_power_w", "pv_power_w", "battery_soc"):
                fresh_adapter.cache.update_value(key, 1.0, source="test", now=now)
            _install_mock(fresh_adapter, "enqueue_background_introspection_if_due", MagicMock())
            _install_mock(fresh_adapter, "poll_one_due_read_once", MagicMock(return_value=False))
            _install_mock(
                fresh_adapter.write_scheduler,
                "process_local_publish_burst",
                MagicMock(return_value=1),
            )
            _install_mock(fresh_adapter.write_scheduler, "process_one", MagicMock(return_value=False))
            _install_mock(fresh_adapter, "refresh_services_if_due_once", MagicMock(return_value=False))
            self.assertTrue(fresh_adapter.process_one_dbus_operation_once())
            fresh_adapter.write_scheduler.process_local_publish_burst.assert_called_once()

            _install_mock(adapter, "process_socket_once", MagicMock())
            _install_mock(adapter, "process_one_dbus_operation_once", MagicMock())
            _install_mock(adapter, "process_introspection_requests_once", MagicMock())
            _install_mock(adapter, "publish_cache", MagicMock())
            adapter._next_work_tick_monotonic = 0.0
            self.assertTrue(adapter.tick())
            adapter.process_socket_once.assert_called_once()
            adapter.process_introspection_requests_once.assert_called_once()
            adapter.process_one_dbus_operation_once.assert_called_once()
            adapter.publish_cache.assert_called_once()

    def test_health_log_backpressure_and_publish_failure_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.health_log_path = ""
            adapter.append_health_log({"state": "ok"})
            adapter.health_log_path = str(Path(temp_dir) / "health.log")
            adapter.health_log_interval_seconds = 0.01
            with patch.object(builtins, "open", side_effect=OSError("full")):
                adapter.append_health_log({"state": "ok"})

            with self.assertRaises(RuntimeError):
                adapter.timed_local_publish(lambda: (_ for _ in ()).throw(RuntimeError("publish failed")))

            slow = health_backpressure_module.backpressure_snapshot(
                circuit_state=adapter.circuit.state(),
                slo={"violated": []},
                queue_health={"oldest_command_age_s": adapter.slo_queue_max_age_seconds * 3.0},
                queue_max_age_seconds=adapter.slo_queue_max_age_seconds,
            )
            self.assertEqual(slow["state"], "slow")
            adapter.circuit.protective_until = time.time() + 10.0
            protective = health_backpressure_module.backpressure_snapshot(
                circuit_state=adapter.circuit.state(),
                slo={"violated": []},
                queue_health={"oldest_command_age_s": 0.0},
                queue_max_age_seconds=adapter.slo_queue_max_age_seconds,
            )
            self.assertEqual(protective["state"], "protective")
            adapter.circuit.protective_until = 0.0
            congested = health_backpressure_module.backpressure_snapshot(
                circuit_state=adapter.circuit.state(),
                slo={"violated": ["gui_fresh"]},
                queue_health={"oldest_command_age_s": 0.0},
                queue_max_age_seconds=adapter.slo_queue_max_age_seconds,
            )
            self.assertEqual(congested["state"], "ok")
            congested = health_backpressure_module.backpressure_snapshot(
                circuit_state=adapter.circuit.state(),
                slo={"violated": ["core_reads_fresh"]},
                queue_health={"oldest_command_age_s": 0.0},
                queue_max_age_seconds=adapter.slo_queue_max_age_seconds,
            )
            self.assertEqual(congested["state"], "congested")
            self.assertEqual(
                health_backpressure_module.backpressure_snapshot(
                    circuit_state="ok",
                    slo={"violated": ["core_reads_fresh", "queue_age_ok", "gui_fresh", "core_reads_fresh"]},
                    queue_health={"oldest_command_age_s": adapter.slo_queue_max_age_seconds + 0.1},
                    queue_max_age_seconds=adapter.slo_queue_max_age_seconds,
                ),
                {
                    "state": "congested",
                    "core_should_throttle": True,
                    "suppress_optional_commands": False,
                    "prefer_coalescing": True,
                    "reason": "queue-age,core_reads_fresh,queue_age_ok",
                },
            )
            self.assertEqual(
                health_backpressure_module.backpressure_snapshot(
                    circuit_state="degraded",
                    slo={"violated": ("queue_age_ok",)},
                    queue_health={"oldest_command_age_s": 0.0},
                    queue_max_age_seconds=adapter.slo_queue_max_age_seconds,
                ),
                {
                    "state": "slow",
                    "core_should_throttle": True,
                    "suppress_optional_commands": True,
                    "prefer_coalescing": True,
                    "reason": "dbus-degraded,queue_age_ok",
                },
            )
            self.assertEqual(
                health_backpressure_module.backpressure_snapshot(
                    circuit_state="protective",
                    slo={"violated": set()},
                    queue_health={"oldest_command_age_s": 0.0},
                    queue_max_age_seconds=adapter.slo_queue_max_age_seconds,
                ),
                {
                    "state": "protective",
                    "core_should_throttle": True,
                    "suppress_optional_commands": True,
                    "prefer_coalescing": True,
                    "reason": "dbus-protective",
                },
            )
            self.assertEqual(
                health_backpressure_module.backpressure_snapshot(
                    circuit_state="ok",
                    slo={"violated": "core_reads_fresh"},
                    queue_health={"oldest_command_age_s": adapter.slo_queue_max_age_seconds},
                    queue_max_age_seconds=adapter.slo_queue_max_age_seconds,
                ),
                {
                    "state": "ok",
                    "core_should_throttle": False,
                    "suppress_optional_commands": False,
                    "prefer_coalescing": False,
                    "reason": "ok",
                },
            )
            self.assertEqual(
                health_backpressure_module.backpressure_reasons(
                    "ok",
                    adapter.slo_queue_max_age_seconds + 0.01,
                    {"violated": ["mainloop_gap_ok", "queue_age_ok", "core_reads_fresh"]},
                    queue_max_age_seconds=adapter.slo_queue_max_age_seconds,
                ),
                ["queue-age", "queue_age_ok", "core_reads_fresh"],
            )
            self.assertEqual(health_backpressure_module.slo_violations({"violated": ["a", "b"]}), ["a", "b"])
            self.assertEqual(health_backpressure_module.slo_violations({"violated": ("a", "b")}), ["a", "b"])
            self.assertEqual(health_backpressure_module.slo_violations({"violated": {"a"}}), ["a"])
            self.assertEqual(health_backpressure_module.slo_violations({"violated": set()}), [])
            self.assertEqual(health_backpressure_module.slo_violations({"violated": "a"}), [])
            self.assertEqual(health_backpressure_module.slo_violations({"violated": object()}), [])
            self.assertEqual(health_backpressure_module.slo_violations({}), [])
            self.assertEqual(health_backpressure_module.slo_violations({"violated": None}), [])
            self.assertEqual(
                health_backpressure_module.backpressure_state(
                    "ok",
                    adapter.slo_queue_max_age_seconds * 2.0,
                    ["queue-age"],
                    queue_max_age_seconds=adapter.slo_queue_max_age_seconds,
                ),
                "congested",
            )
            self.assertEqual(
                health_backpressure_module.backpressure_state(
                    "ok",
                    adapter.slo_queue_max_age_seconds * 2.0 + 0.01,
                    [],
                    queue_max_age_seconds=adapter.slo_queue_max_age_seconds,
                ),
                "slow",
            )

            now = time.time()
            adapter.cache.update_value(f"path:{adapter.service_name}/Mode", 1, source="svc/Mode", now=now - 2.0)
            self.assertGreater(adapter.max_cached_path_age_for_paths({"/Mode"}, now), 0.0)
            self.assertEqual(adapter.max_cached_path_age_for_paths({"/Missing"}, now), 0.0)
            adapter.tick_health.record(duration_ms=1.0, expected_interval_s=0.1, now=time.monotonic() - 1.0)
            adapter.tick_health.record(duration_ms=1.0, expected_interval_s=0.1, now=time.monotonic())
            adapter.apply_slo_regulation()
            self.assertLessEqual(
                adapter.write_scheduler.dynamic_local_publish_burst_limit,
                adapter.write_scheduler.local_publish_burst_limit,
            )
            adapter.cache.values[f"path:{adapter.service_name}/Zero"] = {"updated_at": 0.0}
            self.assertEqual(adapter.max_cached_path_age_for_paths({"/Zero"}, now), 0.0)

    def test_poll_and_discovery_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.read_scheduler.next_read_at = {key: time.time() + 1000 for key in adapter.read_scheduler.specs}
            self.assertFalse(adapter.poll_one_due_read_once())

            adapter.read_scheduler.next_read_at = {"grid_power_w": 0.0}
            _install_mock(adapter.read_executor, "poll_read_spec", MagicMock(return_value="applied"))
            self.assertTrue(adapter.poll_one_due_read_once())
            adapter.read_scheduler.next_read_at = {"grid_power_w": 0.0}
            _install_mock(adapter.read_executor, "poll_read_spec", MagicMock(return_value="dropped"))
            self.assertTrue(adapter.poll_one_due_read_once())
            adapter.read_scheduler.next_read_at = {"grid_power_w": 0.0}
            _install_mock(adapter.read_executor, "poll_read_spec", MagicMock(return_value="deferred"))
            self.assertFalse(adapter.poll_one_due_read_once())

            adapter.discovery.next_scan_at = time.time() + 1000
            self.assertFalse(adapter.refresh_services_if_due_once())
            adapter.discovery.next_scan_at = 0.0
            refresh_path = adapter.commands.enqueue({"kind": "refresh_services", "priority": "normal"})
            self.assertTrue(Path(refresh_path).exists())
            _install_mock(adapter, "list_services", MagicMock(return_value=["svc"]))
            self.assertTrue(adapter.refresh_services_if_due_once())
            self.assertIn("svc", adapter.cache.services)
            self.assertFalse(Path(refresh_path).exists())
            adapter.discovery.next_scan_at = 0.0
            _install_mock(adapter, "list_services", MagicMock(side_effect=DbusOperationDeferred("read")))
            self.assertFalse(adapter.refresh_services_if_due_once())
            deferred_path = adapter.commands.enqueue({"kind": "refresh_services", "priority": "normal"})
            self.assertEqual(adapter.process_non_write_command({"kind": "refresh_services"}), "deferred")
            self.assertTrue(Path(deferred_path).exists())
            adapter.commands.remove(deferred_path)
            _install_mock(adapter, "list_services", MagicMock(side_effect=RuntimeError("dbus down")))
            failed_path = adapter.commands.enqueue({"kind": "refresh_services", "priority": "normal"})
            self.assertEqual(adapter.process_non_write_command({"kind": "refresh_services"}), "dropped")
            self.assertFalse(Path(failed_path).exists())
            adapter.discovery.next_scan_at = 0.0
            failed_background_path = adapter.commands.enqueue({"kind": "refresh_services", "priority": "normal"})
            self.assertTrue(adapter.refresh_services_if_due_once())
            self.assertEqual(adapter.discovery.last_error, "dbus down")
            self.assertFalse(Path(failed_background_path).exists())
            adapter.maybe_refresh_services()

    def test_poll_and_discovery_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            _install_mock(adapter.circuit, "state", MagicMock(return_value="degraded"))
            _install_mock(adapter.circuit, "allows_priority", MagicMock(return_value=True))
            next_due = _install_mock(
                adapter.read_scheduler,
                "next_due",
                MagicMock(return_value=("grid", {"service": "svc"}, 2.5)),
            )
            record_success = _install_mock(adapter.read_scheduler, "record_success", MagicMock())
            record_error = _install_mock(adapter.read_scheduler, "record_error", MagicMock())
            poll_read_spec = _install_mock(adapter.read_executor, "poll_read_spec", MagicMock(return_value="applied"))

            with patch.object(process_io_module.time, "time", return_value=123.0):
                self.assertTrue(adapter.poll_one_due_read_once())

            next_due.assert_called_once_with(
                now=123.0,
                circuit_state="degraded",
                priority_allowed=adapter.circuit.allows_priority,
            )
            poll_read_spec.assert_called_once_with("grid", {"service": "svc"})
            record_success.assert_called_once_with("grid", now=123.0, interval=2.5)
            record_error.assert_not_called()

            next_due.reset_mock()
            poll_read_spec.reset_mock(return_value=True)
            record_success.reset_mock()
            record_error.reset_mock()
            poll_read_spec.return_value = "dropped"
            with patch.object(process_io_module.time, "time", return_value=124.0):
                self.assertTrue(adapter.poll_one_due_read_once())
            record_success.assert_not_called()
            record_error.assert_called_once_with("grid", now=124.0, interval=2.5)

            poll_read_spec.return_value = "deferred"
            adapter.read_executor.last_operation_performed = True
            record_error.reset_mock()
            with patch.object(process_io_module.time, "time", return_value=125.0):
                self.assertTrue(adapter.poll_one_due_read_once())
            record_error.assert_not_called()

            next_due.return_value = None
            poll_read_spec.reset_mock()
            with patch.object(process_io_module.time, "time", return_value=126.0):
                self.assertFalse(adapter.poll_one_due_read_once())
            poll_read_spec.assert_not_called()

            _install_mock(adapter.discovery, "due", MagicMock(return_value=True))
            discovery_success = _install_mock(adapter.discovery, "record_success", MagicMock())
            discovery_error = _install_mock(adapter.discovery, "record_error", MagicMock())
            update_services = _install_mock(adapter.cache, "update_services", MagicMock())
            remove_coalesced = _install_mock(adapter.commands, "remove_coalesced", MagicMock())
            _install_mock(adapter, "list_services", MagicMock(return_value=["svc.a"]))
            with patch.object(process_io_module.time, "time", return_value=200.0):
                self.assertTrue(adapter.refresh_services_if_due_once())
            update_services.assert_called_once_with(["svc.a"])
            remove_coalesced.assert_called_once_with("refresh:services")
            discovery_success.assert_called_once_with(now=200.0)
            discovery_error.assert_not_called()

            update_services.reset_mock()
            remove_coalesced.reset_mock()
            discovery_success.reset_mock()
            error = RuntimeError("dbus down")
            adapter.list_services.side_effect = error
            with patch.object(process_io_module.time, "time", return_value=201.0):
                self.assertTrue(adapter.refresh_services_if_due_once())
            update_services.assert_not_called()
            remove_coalesced.assert_called_once_with("refresh:services")
            discovery_success.assert_not_called()
            discovery_error.assert_called_once_with(error, now=201.0)

            remove_coalesced.reset_mock()
            discovery_error.reset_mock()
            adapter.list_services.side_effect = DbusOperationDeferred("read")
            with patch.object(process_io_module.time, "time", return_value=202.0):
                self.assertFalse(adapter.refresh_services_if_due_once())
            remove_coalesced.assert_not_called()
            discovery_error.assert_not_called()

    def test_cache_publish_interval_throttles_unchanged_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nDbusGatewayCachePublishIntervalSeconds=60\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            _install_mock(adapter.cache, "write_snapshot_files", MagicMock())

            adapter.publish_cache()
            adapter.publish_cache()
            adapter.cache.update_value("path:svc/P", 1, source="svc/P")
            adapter.publish_cache()

            self.assertEqual(adapter.cache.write_snapshot_files.call_count, 2)

    def test_cache_publish_interval_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nDbusGatewayCachePublishIntervalSeconds=1\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            _install_mock(adapter, "health_snapshot", MagicMock(return_value={"state": "ok"}))
            _install_mock(adapter.cache, "write_snapshot_files", MagicMock())
            _install_mock(adapter, "append_health_log", MagicMock())
            _install_mock(adapter, "write_introspection_snapshot", MagicMock())

            with patch.object(process_io_module.time, "monotonic", side_effect=[10.0, 10.5, 11.0]):
                adapter.publish_cache()
                adapter.publish_cache()
                adapter.publish_cache()

            self.assertEqual(adapter.cache.health["state"], "ok")
            self.assertEqual(adapter.health_snapshot.call_count, 3)
            self.assertEqual(adapter.cache.write_snapshot_files.call_count, 2)
            self.assertEqual(adapter.append_health_log.call_count, 2)
            self.assertEqual(adapter.write_introspection_snapshot.call_count, 2)
            self.assertEqual(adapter._last_cache_publish_monotonic, 11.0)

            no_throttle = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-no-throttle")))
            no_throttle.cache_publish_interval_seconds = 0.0
            _install_mock(no_throttle, "health_snapshot", MagicMock(return_value={"state": "ok"}))
            _install_mock(no_throttle.cache, "write_snapshot_files", MagicMock())
            _install_mock(no_throttle, "append_health_log", MagicMock())
            _install_mock(no_throttle, "write_introspection_snapshot", MagicMock())
            with patch.object(process_io_module.time, "monotonic", MagicMock(side_effect=AssertionError("not called"))):
                no_throttle.publish_cache()
                no_throttle.publish_cache()
            self.assertEqual(no_throttle.cache.write_snapshot_files.call_count, 2)
            self.assertEqual(no_throttle.append_health_log.call_count, 2)
            self.assertEqual(no_throttle.write_introspection_snapshot.call_count, 2)

    def test_signal_handlers_andlist_services_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            fake_loop = MagicMock()
            adapter._main_loop = fake_loop
            callbacks: dict[int, Callable[[int, object | None], None]] = {}

            def fake_signal(signum: int, callback: Callable[[int, object | None], None]) -> None:
                callbacks[signum] = callback

            with patch.object(runtime_module.signal, "signal", side_effect=fake_signal), patch.object(runtime_module.GLib, "idle_add") as idle_add:
                adapter.install_signal_handlers()
                callbacks[runtime_module.signal.SIGTERM](runtime_module.signal.SIGTERM, None)
            self.assertTrue(adapter._stop)
            idle_add.assert_called_once_with(fake_loop.quit)

            adapter._main_loop = None
            adapter._stop = False
            with patch.object(runtime_module.signal, "signal", side_effect=fake_signal), patch.object(runtime_module.GLib, "idle_add") as idle_add:
                adapter.install_signal_handlers()
                callbacks[runtime_module.signal.SIGINT](runtime_module.signal.SIGINT, None)
            self.assertTrue(adapter._stop)
            idle_add.assert_not_called()

            fake_iface = MagicMock()
            fake_iface.ListNames.return_value = ["svc.a", b"svc.b"]
            fake_obj = object()
            get_object = _install_mock(adapter.connection, "get_object", MagicMock(return_value=fake_obj))
            with patch.object(process_io_module.dbus, "Interface", return_value=fake_iface) as dbus_interface:
                self.assertEqual(adapter.list_services(), ["svc.a", "b'svc.b'"])
                get_object.assert_called_once_with(
                    "org.freedesktop.DBus",
                    "/org/freedesktop/DBus",
                    introspect=False,
                )
                dbus_interface.assert_called_once_with(fake_obj, "org.freedesktop.DBus")
                fake_iface.ListNames.assert_called_once_with()
                adapter.rate_limiter.next_at["read"] = 0.0
                fake_iface.ListNames.return_value = "svc.a"
                with self.assertRaisesRegex(TypeError, "^DBus ListNames returned a non-iterable service list$"):
                    adapter.list_services()
                adapter.rate_limiter.next_at["read"] = 0.0
                fake_iface.ListNames.return_value = object()
                with self.assertRaisesRegex(TypeError, "^DBus ListNames returned a non-iterable service list$"):
                    adapter.list_services()
            self.assertEqual(process_io_module._service_names(("a", b"b")), ["a", "b'b'"])

    def test_socket_start_without_stale_path_and_default_cache_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            Path(adapter.paths.socket_path).parent.mkdir(parents=True, exist_ok=True)
            adapter.start_socket()
            adapter.close_socket()
            _install_mock(adapter.cache, "write_snapshot_files", MagicMock())
            adapter.publish_cache()
            adapter.cache.write_snapshot_files.assert_called_once()

    def test_atomic_json_writer_writes_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "payload.json"
            writer = AtomicJsonWriter()
            writer.write(str(path), {"ok": True})
            self.assertEqual(read_json_file(str(path), {}), {"ok": True})

    def test_non_write_introspection_timed_logging_main_and_json_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nLogging=DEBUG\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            self.assertEqual(adapter.process_non_write_command({}), "dropped")
            self.assertEqual(adapter.process_non_write_command({"kind": "nope"}), "dropped")
            _install_mock(adapter.read_executor, "refresh_requested_value", MagicMock(return_value="applied"))
            self.assertEqual(adapter.process_non_write_command({"kind": "refresh_value"}), "applied")
            _install_mock(adapter, "list_services", MagicMock(return_value=["svc"]))
            self.assertEqual(adapter.process_non_write_command({"kind": "refresh_services"}), "applied")
            adapter.circuit.degraded_until = time.time() + 10.0
            self.assertEqual(adapter.process_non_write_command({"kind": "refresh_services"}), "deferred")
            self.assertEqual(adapter.process_non_write_command({"kind": "introspect"}), "deferred")
            adapter.circuit.degraded_until = 0.0
            self.assertEqual(adapter.introspect_command({}), "dropped")
            timed_result = _install_mock(adapter, "timed_introspection_result", MagicMock(return_value=("deferred", None)))
            self.assertEqual(adapter.introspect_command({"service": "svc"}), "deferred")
            timed_result.assert_called_once_with("svc", "/", 1.0)
            delattr(adapter, "timed_introspection_result")
            _install_mock(adapter, "timed_dbus_operation", MagicMock(return_value="<node/>"))
            self.assertEqual(adapter.process_non_write_command({"kind": "introspect", "service": "svc", "path": "/"}), "applied")
            self.assertEqual(adapter.cache.values["introspection:svc:/"]["value"], "<node/>")

            adapter._introspection_queue_depth = 1
            _install_mock(adapter, "timed_dbus_operation", MagicMock(side_effect=RuntimeError("no reply")))
            self.assertEqual(
                adapter.process_non_write_command({"kind": "introspect", "service": "svc", "path": "/Slow"}),
                "dropped",
            )
            self.assertEqual(adapter._introspection_queue_depth, 0)
            self.assertEqual(adapter.cache.values["introspection:svc:/Slow"]["status"], "error")

            _install_mock(
                adapter,
                "timed_dbus_operation",
                MagicMock(side_effect=DbusOperationDeferred("rate limited")),
            )
            self.assertEqual(
                adapter.process_non_write_command({"kind": "introspect", "service": "svc", "path": "/Later"}),
                "deferred",
            )

            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-timed")))
            timed_error = RuntimeError("introspect failed")
            _install_mock(adapter, "timed_dbus_operation", MagicMock(side_effect=timed_error))
            drop_failed = _install_mock(adapter, "drop_failed_introspection", MagicMock(return_value="dropped"))
            self.assertEqual(adapter.timed_introspection_result("svc", "/Err", 3.0), ("dropped", None))
            drop_failed.assert_called_once_with("svc", "/Err", timed_error)
            delattr(adapter, "timed_dbus_operation")
            delattr(adapter, "drop_failed_introspection")

            fake_iface = MagicMock()
            fake_iface.Introspect.return_value = "<real/>"
            fake_obj = object()
            get_object = _install_mock(adapter.connection, "get_object", MagicMock(return_value=fake_obj))
            with patch.object(introspection_module.dbus, "Interface", return_value=fake_iface) as interface:
                self.assertEqual(adapter.introspect_command({"service": "svc", "path": "/Real", "timeout": 2.0}), "applied")
            get_object.assert_called_once_with("svc", "/Real", introspect=False)
            interface.assert_called_once_with(fake_obj, "org.freedesktop.DBus.Introspectable")
            fake_iface.Introspect.assert_called_once_with(timeout=2.0)

            adapter._introspection_queue_depth = 2
            adapter.record_introspection_xml("svc", "/Recorded", "<xml/>")
            self.assertEqual(adapter._introspection_queue_depth, 1)
            recorded = adapter.cache.values["introspection:svc:/Recorded"]
            self.assertEqual(recorded["value"], "<xml/>")
            self.assertEqual(recorded["source"], "svc/Recorded")
            self.assertEqual(recorded["confidence"], 0.5)

            adapter._introspection_queue_depth = 2
            failed_error = RuntimeError("bad")
            with patch.object(introspection_module.logging, "debug") as debug:
                self.assertEqual(adapter.drop_failed_introspection("svc", "/Failed", failed_error), "dropped")
            debug.assert_called_once()
            self.assertEqual(debug.call_args.args[0], "Dropping failed DBus introspection command service=%s path=%s: %s")
            self.assertEqual(debug.call_args.args[1:], ("svc", "/Failed", failed_error))
            self.assertEqual(adapter._introspection_queue_depth, 1)
            failed = adapter.cache.values["introspection:svc:/Failed"]
            self.assertEqual(failed["status"], "error")
            self.assertEqual(failed["source"], "svc/Failed")
            self.assertEqual(failed["last_error"], "bad")

            adapter._introspection_queue_depth = 1
            adapter.record_introspection_xml("svc", "/Zero", "<xml/>")
            self.assertEqual(adapter._introspection_queue_depth, 0)

            self.assertEqual(adapter.timed_dbus_operation("read", lambda: 42), 42)
            adapter.rate_limiter.next_at["read"] = 0.0
            with self.assertRaises(RuntimeError):
                adapter.timed_dbus_operation("read", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
            self.assertEqual(gateway_core_module._json_ready({"ok": True}), {"ok": True})
            self.assertEqual(gateway_core_module._json_ready(object()).startswith("<object object"), True)

            self.assertEqual(adapter_module._logging_level_from_config(adapter.config), logging.DEBUG)
            with patch.object(adapter_module.DbusAdapter, "run") as run:
                self.assertEqual(adapter_main([str(config_path), "--run-dir", str(Path(temp_dir) / "run2")]), 0)
            run.assert_called_once()

    def test_timed_operation_contracts_record_latency_and_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            require_due = _install_mock(adapter.rate_limiter, "require_due", MagicMock())
            record_success = _install_mock(adapter.circuit, "record_success", MagicMock())
            record_error = _install_mock(adapter.circuit, "record_error", MagicMock())

            with patch.object(process_io_module.time, "monotonic", side_effect=[10.0, 10.125]):
                self.assertEqual(adapter.timed_dbus_operation("write", lambda: "ok"), "ok")
            require_due.assert_called_once_with("write")
            record_success.assert_called_once_with(125.0, kind="write")
            record_error.assert_not_called()

            require_due.reset_mock()
            record_success.reset_mock()
            error = RuntimeError("boom")
            with patch.object(process_io_module.time, "monotonic", return_value=20.0):
                with self.assertRaises(RuntimeError):
                    adapter.timed_dbus_operation("read", lambda: (_ for _ in ()).throw(error))
            require_due.assert_called_once_with("read")
            record_success.assert_not_called()
            record_error.assert_called_once_with(error, kind="read")

            require_due.reset_mock()
            record_error.reset_mock()
            record_success.reset_mock()
            with patch.object(process_io_module.time, "monotonic", side_effect=[30.0, 30.25]):
                self.assertEqual(adapter.timed_local_publish(lambda: "published"), "published")
            require_due.assert_not_called()
            record_success.assert_called_once_with(250.0, kind="local_publish")
            record_error.assert_not_called()

            record_success.reset_mock()
            error = RuntimeError("publish failed")
            with patch.object(process_io_module.time, "monotonic", return_value=40.0):
                with self.assertRaises(RuntimeError):
                    adapter.timed_local_publish(lambda: (_ for _ in ()).throw(error))
            record_success.assert_not_called()
            record_error.assert_called_once_with(error, kind="local_publish")


if __name__ == "__main__":
    unittest.main()
