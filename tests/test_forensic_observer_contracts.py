# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact runtime contracts for the read-only forensic observer."""

from __future__ import annotations

import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

from tests.forensic_observer_io_contract_cases import (
    DiagnosticsReader as _DiagnosticsReader,
    ForensicObserverIOContractCases as TestForensicObserverIOContractCases,
    config as _config,
)
from tests.gateway_diagnostics_fixtures import gateway_diagnostics_snapshot
from venus_evcharger.ops import forensic_observer as observer
from venus_evcharger.ops.forensic_observer_probe import DisabledBackendProbe

__all__ = ["ForensicObserverContractTests", "TestForensicObserverIOContractCases"]


class ForensicObserverContractTests(unittest.TestCase):
    def test_incident_reason_contract_covers_gateway_runit_markers_and_deduplication(self) -> None:
        diagnostics = gateway_diagnostics_snapshot(
            status_overrides={
                "operating_mode": "error",
                "charging_enabled": "unavailable",
                "ac_power_w": "unknown",
            },
            health_state="protective",
        )
        snapshot = {
            "gateway_diagnostics": {
                "available": True,
                "snapshot": diagnostics.to_payload(),
            },
            "svstat": {"ok": True, "stdout": "service: down"},
            "trace_markers": ["NoReply", "NoReply", "malloc()"],
        }
        self.assertEqual(
            observer.incident_reasons(snapshot),
            [
                "gateway-ac-power-w-unavailable",
                "gateway-charging-enabled-unavailable",
                "gateway-health-protective",
                "gateway-operating-mode-unavailable",
                "log-marker-malloc",
                "log-marker-noreply",
                "runit-not-up",
            ],
        )
        self.assertEqual(
            observer._runit_incident_reasons({"svstat": {"ok": False}}),
            ["runit-status-failed"],
        )
        self.assertEqual(observer._runit_incident_reasons({"svstat": "bad"}), [])
        self.assertEqual(
            observer._runit_incident_reasons(
                {"svstat": {"ok": True, "stdout": "service up now"}}
            ),
            [],
        )
        self.assertEqual(
            observer._runit_incident_reasons({"svstat": {"ok": True}}),
            ["runit-not-up"],
        )
        self.assertEqual(observer.incident_reasons({}), [])
        invalid: dict[str, object] = {
            "gateway_diagnostics": {"available": True, "snapshot": {}},
            "trace_markers": [],
        }
        self.assertEqual(
            observer.incident_reasons(invalid),
            ["gateway-diagnostics-invalid"],
        )
        self.assertEqual(observer._slug(" A/B ++ "), "a-b")
        self.assertEqual(observer._slug("---"), "event")

    def test_collect_snapshot_wires_every_source_into_stable_schema(self) -> None:
        config = _config("")
        defaults, selection = config["DEFAULT"], observer.backend_selection_view_from_config(config)
        reader = _DiagnosticsReader()
        probe = DisabledBackendProbe("test-disabled")
        command_results = [
            {"ok": True, "stdout": "11 root venus_evcharger_auto_input_helper.py\n"},
            {"ok": True, "stdout": "up"},
            {"ok": True, "stdout": "uptime"},
        ]
        with (
            patch.object(observer, "load_config", return_value=config) as load_config,
            patch.object(
                observer,
                "backend_diagnostics",
                return_value=(
                    selection,
                    {
                        "available": False,
                        "reason_code": "test",
                        "error": "test-error",
                    },
                ),
            ) as backend_diagnostics,
            patch.object(observer, "_effective_probe", return_value=probe) as effective_probe,
            patch.object(
                observer,
                "tail_log_dir",
                return_value={"current": "NoReply", "old": "stale"},
            ) as tail_logs,
            patch.object(
                observer,
                "command_output",
                side_effect=command_results,
            ) as command,
            patch.object(observer.time, "time", return_value=123.5),
            patch.object(
                observer,
                "read_gateway_diagnostics",
                return_value={"available": False, "error": "test"},
            ) as gateway,
            patch.object(
                observer,
                "auto_input_snapshot_path",
                return_value="auto",
            ) as auto_path,
            patch.object(
                observer,
                "runtime_state_path",
                return_value="runtime",
            ) as runtime_path,
            patch.object(
                observer,
                "read_json_file",
                side_effect=[{"auto": 1}, {"runtime": 1}],
            ) as read_json,
        ):
            snapshot = observer.collect_snapshot(
                "config",
                diagnostics_reader=reader,
                backend_probe=probe,
            )
        load_config.assert_called_once_with("config")
        backend_diagnostics.assert_called_once_with(config)
        effective_probe.assert_called_once_with(probe, config, selection, config_path="config")
        tail_logs.assert_called_once_with("/var/volatile/log/dbus-venus-evcharger")
        self.assertEqual(
            command.call_args_list,
            [
                call(["ps", "w"]),
                call(["svstat", "/service/dbus-venus-evcharger"]),
                call(["uptime"]),
            ],
        )
        gateway.assert_called_once_with(reader)
        auto_path.assert_called_once_with(defaults)
        runtime_path.assert_called_once_with(defaults)
        self.assertEqual(read_json.call_args_list, [call("auto"), call("runtime")])
        self.assertEqual(
            snapshot,
            {
                "timestamp": 123.5,
                "config_path": "config",
                "gateway_diagnostics": {"available": False, "error": "test"},
                "backend_diagnostics": {
                    "available": False,
                    "reason_code": "test",
                    "error": "test-error",
                },
                "backend_probe": {
                    "status": "disabled",
                    "probe_type": "none",
                    "role": "",
                    "backend_type": "",
                    "reason_code": "test-disabled",
                    "payload": "",
                },
                "auto_input_snapshot": {"auto": 1},
                "runtime_state": {"runtime": 1},
                "helper_processes": [
                    {
                        "pid": "11",
                        "line": "11 root venus_evcharger_auto_input_helper.py",
                    }
                ],
                "svstat": {"ok": True, "stdout": "up"},
                "ps": command_results[0],
                "uptime": {"ok": True, "stdout": "uptime"},
                "runtime_logs": {"current": "NoReply", "old": "stale"},
                "trace_markers": ["NoReply", "stale"],
            },
        )

    def test_default_reader_and_probe_follow_normalized_configuration(self) -> None:
        config = _config("")
        defaults = config["DEFAULT"]
        reader = object()
        configured_probe = DisabledBackendProbe("configured")
        with (
            patch.object(
                observer,
                "GatewayDiagnosticsFileReader",
                return_value=reader,
            ) as reader_factory,
            patch.object(
                observer,
                "gateway_diagnostics_snapshot_path",
                return_value="/tmp/diagnostics",
            ) as diagnostics_path,
            patch.object(
                observer,
                "configured_backend_probe",
                return_value=configured_probe,
            ) as probe_factory,
        ):
            self.assertIs(
                observer._effective_diagnostics_reader(None, defaults),
                reader,
            )
            self.assertIs(
                observer._configured_probe(
                    config,
                    observer.backend_selection_view_from_config(config),
                    config_path="/tmp/config.ini",
                ),
                configured_probe,
            )
        diagnostics_path.assert_called_once_with(defaults)
        reader_factory.assert_called_once_with("/tmp/diagnostics")
        probe_factory.assert_called_once()
        self.assertEqual(
            observer._configured_probe(
                config,
                None,
                config_path="/tmp/config.ini",
            ).probe().reason_code,
            "backend-diagnostics-unavailable",
        )

    def test_write_incident_persists_exact_payload_and_redacted_config(self) -> None:
        for invalid_timestamp in (True, "1"):
            with self.subTest(timestamp=invalid_timestamp):
                with self.assertRaisesRegex(ValueError, "timestamp must be numeric"):
                    observer.write_incident(
                        "log",
                        {"timestamp": invalid_timestamp},
                        "config",
                        ["reason"],
                    )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "nested" / "evidence"
            config = Path(temp_dir) / "config.ini"
            config.write_text("Password=secret\n", encoding="utf-8")
            with (
                patch.object(observer.time, "strftime", return_value="STAMP") as stamp,
                patch.object(observer.time, "localtime", return_value="LOCAL") as localtime,
            ):
                incident = observer.write_incident(
                    str(root),
                    {"timestamp": 42.0, "value": 1},
                    str(config),
                    ["Reason A", "Reason B"],
                )
            localtime.assert_called_once_with(42.0)
            stamp.assert_called_once_with("%Y%m%d-%H%M%S", "LOCAL")
            incident_path = Path(incident)
            self.assertEqual(incident_path.name, "incident-STAMP-reason-a-reason-b")
            snapshot_text = (incident_path / "snapshot.json").read_text(encoding="utf-8")
            self.assertEqual(
                json.loads(snapshot_text),
                {
                    "reasons": ["Reason A", "Reason B"],
                    "timestamp": 42.0,
                    "value": 1,
                },
            )
            self.assertEqual(
                snapshot_text,
                '{\n  "reasons": [\n    "Reason A",\n    "Reason B"\n  ],\n'
                '  "timestamp": 42.0,\n  "value": 1\n}',
            )
            self.assertEqual(
                (incident_path / "config.redacted.ini").read_text(encoding="utf-8"),
                "Password=<redacted>\n",
            )

            long_reason = "x" * 100
            long_incident = observer.write_incident(
                str(root),
                {"timestamp": 43},
                str(config),
                [long_reason],
            )
            self.assertEqual(len(Path(long_incident).name.rsplit("-", 1)[-1]), 80)
            self.assertEqual(
                observer.write_incident(
                    str(root),
                    {"timestamp": 43},
                    str(config),
                    [long_reason],
                ),
                long_incident,
            )

        with (
            patch.object(observer.time, "strftime", return_value="STAMP"),
            patch.object(observer.time, "localtime", return_value="LOCAL"),
            patch.object(Path, "mkdir"),
            patch.object(Path, "write_text", return_value=1) as write_text,
            patch.object(observer, "read_text_safe", return_value="Host=x\n"),
        ):
            observer.write_incident(
                "log",
                {"timestamp": 1.0},
                "config",
                ["reason"],
            )
        self.assertEqual(write_text.call_count, 2)
        self.assertEqual(
            [item.kwargs for item in write_text.call_args_list],
            [{"encoding": "utf-8"}] * 2,
        )

    def test_observer_iteration_obeys_storage_reasons_and_cooldown(self) -> None:
        diagnostics_reader = _DiagnosticsReader()
        backend_probe = DisabledBackendProbe("iteration")
        with (
            patch.object(observer, "read_mounts", return_value="mounts") as read_mounts,
            patch.object(
                observer,
                "mounted_storage_candidates",
                return_value=["card"],
            ) as candidates,
            patch.object(observer, "first_writable_log_dir", return_value="") as writable,
            patch.object(observer, "collect_snapshot") as collect,
        ):
            self.assertEqual(
                observer._observer_iteration(
                    "config",
                    5.0,
                    incident_cooldown=10.0,
                    mounts_path="mounts-file",
                    diagnostics_reader=None,
                    backend_probe=None,
                ),
                5.0,
            )
        read_mounts.assert_called_once_with("mounts-file")
        candidates.assert_called_once_with("mounts")
        writable.assert_called_once_with(["card"])
        collect.assert_not_called()

        snapshot = {"timestamp": 1.0}
        with (
            patch.object(observer, "read_mounts", return_value="mounts"),
            patch.object(observer, "mounted_storage_candidates", return_value=["card"]),
            patch.object(observer, "first_writable_log_dir", return_value="log"),
            patch.object(
                observer,
                "collect_snapshot",
                return_value=snapshot,
            ) as collect,
            patch.object(observer, "incident_reasons", return_value=["reason"]) as reasons,
            patch.object(observer.time, "time", return_value=20.0),
            patch.object(observer, "write_incident") as write,
        ):
            self.assertEqual(
                observer._observer_iteration(
                    "config",
                    10.0,
                    incident_cooldown=10.0,
                    mounts_path="mounts",
                    diagnostics_reader=diagnostics_reader,
                    backend_probe=backend_probe,
                ),
                20.0,
            )
        collect.assert_called_once_with(
            "config",
            diagnostics_reader=diagnostics_reader,
            backend_probe=backend_probe,
        )
        reasons.assert_called_once_with(snapshot)
        write.assert_called_once_with("log", snapshot, "config", ["reason"])

        with (
            patch.object(observer, "read_mounts", return_value="mounts"),
            patch.object(observer, "mounted_storage_candidates", return_value=["card"]),
            patch.object(observer, "first_writable_log_dir", return_value="log"),
            patch.object(observer, "collect_snapshot", return_value=snapshot),
            patch.object(observer, "incident_reasons", return_value=["reason"]),
            patch.object(observer.time, "time", return_value=19.9),
            patch.object(observer, "write_incident") as no_write,
        ):
            self.assertEqual(
                observer._observer_iteration(
                    "config",
                    10.0,
                    incident_cooldown=10.0,
                    mounts_path="mounts",
                    diagnostics_reader=None,
                    backend_probe=None,
                ),
                10.0,
            )
        no_write.assert_not_called()

    def test_observer_loop_clamps_delays_and_carries_incident_timestamp(self) -> None:
        signature = inspect.signature(observer.observer_loop)
        self.assertEqual(signature.parameters["start_delay"].default, 180.0)
        self.assertEqual(signature.parameters["interval"].default, 30.0)
        self.assertEqual(signature.parameters["incident_cooldown"].default, 900.0)
        self.assertEqual(signature.parameters["mounts_path"].default, "/proc/mounts")
        sleeps: list[float] = []
        diagnostics_reader = _DiagnosticsReader()
        backend_probe = DisabledBackendProbe("loop")

        def sleep(value: float) -> None:
            sleeps.append(value)
            if len(sleeps) == 3:
                raise KeyboardInterrupt

        with (
            patch.object(observer.time, "sleep", side_effect=sleep),
            patch.object(
                observer,
                "_observer_iteration",
                side_effect=[7.0, 8.0],
            ) as iteration,
        ):
            with self.assertRaises(KeyboardInterrupt):
                observer.observer_loop(
                    "config",
                    start_delay=-2.0,
                    interval=0.5,
                    incident_cooldown=9.0,
                    mounts_path="mounts",
                    diagnostics_reader=diagnostics_reader,
                    backend_probe=backend_probe,
                )
        self.assertEqual(sleeps, [0.0, 1.0, 1.0])
        self.assertEqual(
            iteration.call_args_list,
            [
                call(
                    "config",
                    0.0,
                    incident_cooldown=9.0,
                    mounts_path="mounts",
                    diagnostics_reader=diagnostics_reader,
                    backend_probe=backend_probe,
                ),
                call(
                    "config",
                    7.0,
                    incident_cooldown=9.0,
                    mounts_path="mounts",
                    diagnostics_reader=diagnostics_reader,
                    backend_probe=backend_probe,
                ),
            ],
        )

    def test_collect_snapshot_normalizes_missing_ps_stdout_before_process_matching(self) -> None:
        config = _config("")
        with (
            patch.object(observer, "load_config", return_value=config),
            patch.object(
                observer,
                "backend_diagnostics",
                return_value=(
                    None,
                    {
                        "available": False,
                        "reason_code": "test",
                        "error": "test",
                    },
                ),
            ),
            patch.object(observer, "tail_log_dir", return_value={}),
            patch.object(observer, "command_output", side_effect=[{"ok": False}, {}, {}]),
            patch.object(observer, "helper_processes", return_value=[]) as helpers,
            patch.object(
                observer,
                "read_gateway_diagnostics",
                return_value={"available": False, "error": "test"},
            ),
            patch.object(observer, "read_json_file", return_value={}),
        ):
            observer.collect_snapshot(
                "config",
                backend_probe=DisabledBackendProbe(),
            )
        helpers.assert_called_once_with("")


if __name__ == "__main__":
    unittest.main()
