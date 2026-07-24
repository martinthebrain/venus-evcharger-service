# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter bounded health history contracts."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    DbusAdapter,
    GatewayAdapterContractCase,
    MagicMock,
    Path,
    builtins,
    gateway_paths,
    health_history_module,
    install_mock,
    json,
    jsonl_module,
    patch,
    process_health_module,
    tempfile,
    unittest,
)


class GatewayHealthHistoryCases(GatewayAdapterContractCase):
    """Exercise bounded health history contracts."""

    def test_health_history_helpers_emit_exact_jsonl_payload_contract(self) -> None:
        health = {
            "state": "degraded",
            "timeouts_60s": 3,
            "queues": {
                "oldest_command_age_s": 4.5,
                "oldest_slo_command_age_s": 3.5,
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
                "optional_source_error_count": 1,
                "optional_source_unavailable_count": 2,
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
                    "queue_oldest_slo_age_s": 3.5,
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
                        "optional_source_error_count": 1,
                        "optional_source_unavailable_count": 2,
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
                    "queue_oldest_slo_age_s": 0.0,
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
                        "optional_source_error_count": None,
                        "optional_source_unavailable_count": None,
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

    def test_jsonl_retention_bounds_ram_backed_gateway_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "nested" / "events.jsonl"
            jsonl_module.append_jsonl(str(log_path), {"idx": 0, "payload": "seed"}, max_bytes=0)
            original = log_path.read_text(encoding="utf-8")
            jsonl_module.retain_jsonl_tail(str(log_path), max_bytes=log_path.stat().st_size + 100)
            self.assertEqual(log_path.read_text(encoding="utf-8"), original)

            jsonl_module.retain_jsonl_tail(str(Path(temp_dir) / "missing.jsonl"), max_bytes=1)
            self.assertEqual(jsonl_module.trim_target_bytes(4), 3)
            self.assertEqual(jsonl_module.drop_partial_first_jsonl_line(b"partial"), b"partial")
            self.assertEqual(jsonl_module.drop_partial_first_jsonl_line(b"half\nwhole\n"), b"whole\n")

            for idx in range(1, 10):
                jsonl_module.append_jsonl(str(log_path), {"idx": idx, "payload": "x" * 20}, max_bytes=160)
            retained_lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            self.assertLessEqual(log_path.stat().st_size, 160)
            self.assertEqual(retained_lines[-1]["idx"], 9)
            self.assertGreater(retained_lines[0]["idx"], 0)

            exact_path = Path(temp_dir) / "exact.jsonl"
            exact_path.write_bytes(b"one\n")
            jsonl_module.rewrite_jsonl_tail(str(exact_path), target_bytes=99, size=4)
            self.assertEqual(exact_path.read_bytes(), b"one\n")

    def test_health_history_log_records_small_operational_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            health_log = Path(temp_dir) / "run" / "health-history.jsonl"
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                f"[DEFAULT]\nDbusGatewayHealthLogPath={health_log}\nDbusGatewayHealthLogIntervalSeconds=0.01\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            install_mock(adapter.cache, "write_cache_snapshot", MagicMock())
            install_mock(adapter.diagnostics_role, "write_gateway_diagnostics", MagicMock())

            adapter.io_role.publish_cache()

            payload = json.loads(health_log.read_text(encoding="utf-8").strip())
            self.assertIn("backpressure", payload)
            self.assertIn("queue_oldest_age_s", payload)
            self.assertIn("cache_freshness", payload)

            adapter.health_log_path = "health-history-without-dir.jsonl"
            adapter._last_health_log_monotonic = 0.0
            log_handle = unittest.mock.mock_open()
            with patch.object(builtins, "open", log_handle):
                adapter.health_role.append_health_log({"state": "ok"})
            log_handle.assert_not_called()

            adapter._last_health_log_monotonic = 0.0
            with patch.object(builtins, "open", side_effect=OSError("full")):
                adapter.health_role.append_health_log({"state": "ok"})

    def test_health_log_due_and_error_logging_contracts_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            adapter.health_log_path = ""
            adapter.health_log_interval_seconds = 10.0
            self.assertFalse(adapter.health_role.health_log_due())
            adapter.health_log_path = str(Path(temp_dir) / "health.jsonl")
            adapter.health_log_interval_seconds = 0.0
            self.assertFalse(adapter.health_role.health_log_due())
            adapter.health_log_interval_seconds = -1.0
            self.assertFalse(adapter.health_role.health_log_due())

            adapter.health_log_interval_seconds = 10.0
            adapter._last_health_log_monotonic = 100.0
            with patch.object(process_health_module.time, "monotonic", return_value=109.999):
                self.assertFalse(adapter.health_role.health_log_due())
            with patch.object(process_health_module.time, "monotonic", return_value=110.0):
                self.assertTrue(adapter.health_role.health_log_due())

            adapter.health_log_path = str(Path(temp_dir) / "health-history.jsonl")
            adapter.health_log_max_bytes = 321
            adapter._last_health_log_monotonic = 100.0
            with (
                patch.object(process_health_module.time, "monotonic", return_value=130.0),
                patch.object(
                    process_health_module,
                    "append_health_log",
                ) as append_health_log,
            ):
                adapter.health_role.append_health_log({"state": "ok"})
            append_health_log.assert_called_once_with(
                adapter.health_log_path,
                {"state": "ok"},
                max_bytes=321,
            )
            self.assertEqual(adapter._last_health_log_monotonic, 130.0)

            adapter._last_health_log_monotonic = 0.0
            with (
                patch.object(builtins, "open", side_effect=OSError("full")),
                patch.object(
                    process_health_module.logging,
                    "debug",
                ) as log_debug,
                patch.object(process_health_module.time, "monotonic", return_value=120.0),
            ):
                adapter.health_role.append_health_log({"state": "ok"})
            log_debug.assert_called_once_with(
                "Unable to append DBus gateway health history",
                exc_info=True,
            )
