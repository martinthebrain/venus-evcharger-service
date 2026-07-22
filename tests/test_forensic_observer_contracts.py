# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact contracts for the read-only forensic observer."""

import configparser
import inspect
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from tests.gateway_diagnostics_fixtures import gateway_diagnostics_snapshot
from venus_evcharger.ops import forensic_observer as observer
from venus_evcharger.ports.gateway_diagnostics import GatewayDiagnosticsUnavailable


class _DiagnosticsReader:
    def __init__(self, *, error: str = "") -> None:
        self.snapshot = gateway_diagnostics_snapshot()
        self.error = error

    def read_snapshot(self):
        if self.error:
            raise GatewayDiagnosticsUnavailable(self.error)
        return self.snapshot


def _defaults(text: str) -> configparser.SectionProxy:
    parser = observer._CaseSensitiveConfigParser()
    parser.read_string(f"[DEFAULT]\n{text}")
    return parser["DEFAULT"]


class ForensicObserverContractTests(unittest.TestCase):
    def test_public_reader_defaults_are_part_of_the_contract(self) -> None:
        expectations = {
            observer.read_mounts: {"path": "/proc/mounts"},
            observer.command_output: {"timeout": 3.0},
            observer.fetch_shelly_status: {"timeout": 2.0},
            observer.tail_file: {"max_bytes": 20000},
            observer.tail_log_dir: {"max_bytes": 20000},
        }
        for function, defaults in expectations.items():
            signature = inspect.signature(function)
            for name, expected in defaults.items():
                self.assertEqual(signature.parameters[name].default, expected)

    def test_config_parser_and_derived_paths_are_exact(self) -> None:
        defaults = _defaults(
            "DeviceInstance=71\n"
            "AutoInputSnapshotPath= /tmp/auto \n"
            "GatewayDiagnosticsSnapshotPath= /tmp/diagnostics \n"
            "Host= host.example \n"
        )
        self.assertEqual(observer.device_instance(defaults), 71)
        self.assertEqual(observer.auto_input_snapshot_path(defaults), "/tmp/auto")
        self.assertEqual(observer.gateway_diagnostics_snapshot_path(defaults), "/tmp/diagnostics")
        self.assertEqual(observer.runtime_state_path(defaults), "/run/dbus-venus-evcharger-71.json")
        self.assertEqual(observer.configured_host(defaults), "host.example")

        empty = _defaults("")
        self.assertEqual(observer.device_instance(empty), 60)
        self.assertEqual(observer.configured_host(empty), "")
        self.assertEqual(
            observer.gateway_diagnostics_snapshot_path(empty),
            "/run/venus-evcharger/gateway-diagnostics.json",
        )

        fallback = _defaults("DeviceInstance=invalid\n")
        self.assertEqual(observer.device_instance(fallback), 60)
        self.assertEqual(observer.auto_input_snapshot_path(fallback), "/run/dbus-venus-evcharger-auto-60.json")

    def test_load_defaults_preserves_case_sensitive_keys(self) -> None:
        with tempfile.NamedTemporaryFile("w+", suffix=".ini") as handle:
            handle.write("[DEFAULT]\nHost=upper\nhost=lower\n")
            handle.flush()
            defaults = observer.load_defaults(handle.name)
        self.assertEqual(defaults["Host"], "upper")
        self.assertEqual(defaults["host"], "lower")

    def test_redaction_contract_is_line_preserving_and_case_insensitive(self) -> None:
        source = "plain\nHost=x\nPassword=p=tail\nAPI_TOKEN=t\nsecretKey=s\nAuthorization=a\n"
        self.assertEqual(
            observer.redact_config_text(source),
            "plain\nHost=x\nPassword=<redacted>\nAPI_TOKEN=<redacted>\n"
            "secretKey=<redacted>\nAuthorization=<redacted>\n",
        )
        self.assertEqual(observer.redact_config_text(""), "\n")

    def test_mount_candidates_require_supported_device_and_mount_prefix(self) -> None:
        mounts = (
            "short\n"
            "/dev/sdc1 /media/two-parts\n"
            "/dev/root /media/root ext4 rw 0 0\n"
            "/dev/sdb1 /srv/not-removable ext4 rw 0 0\n"
            "/dev/sda1 /media/Card\\040One vfat rw 0 0\n"
            "/dev/mmcblk0p1 /run/media/card ext4 rw 0 0\n"
            "/dev/disk/by-id/x /mnt/archive ext4 rw 0 0\n"
            "/dev/sda2 /srv/no ext4 rw 0 0\n"
        )
        self.assertEqual(
            observer.mounted_storage_candidates(mounts),
            ["/media/two-parts", "/media/Card One", "/run/media/card", "/mnt/archive"],
        )

    def test_mount_and_writable_directory_error_contracts(self) -> None:
        with patch.object(Path, "read_text", side_effect=OSError("missing")):
            self.assertEqual(observer.read_mounts("mounts"), "")
        with patch.object(Path, "read_text", return_value="mounted") as read:
            self.assertEqual(observer.read_mounts("mounts"), "mounted")
        read.assert_called_once_with(encoding="utf-8")

        with tempfile.TemporaryDirectory() as temp_dir:
            blocked = Path(temp_dir) / "blocked"
            blocked.write_text("file", encoding="utf-8")
            valid = Path(temp_dir) / "valid"
            selected = observer.first_writable_log_dir([str(blocked), str(valid)], subdir="evidence")
            self.assertEqual(selected, str(valid / "evidence"))
            self.assertFalse((valid / "evidence/.write-test").exists())
            self.assertEqual(
                observer.first_writable_log_dir([str(valid)], subdir="evidence"),
                str(valid / "evidence"),
            )
        self.assertEqual(observer.first_writable_log_dir([], subdir="evidence"), "")

    def test_command_output_bounds_and_reports_subprocess_contract(self) -> None:
        completed = SimpleNamespace(returncode=3, stdout="a" * 5000, stderr="b" * 5001)
        with patch.object(observer.subprocess, "run", return_value=completed) as run:
            result = observer.command_output(["cmd", "arg"], timeout=4.5)
        run.assert_called_once_with(
            ["cmd", "arg"], capture_output=True, text=True, timeout=4.5, check=False
        )
        self.assertEqual(result, {"ok": False, "returncode": 3, "stdout": "a" * 4000, "stderr": "b" * 4000})
        with patch.object(observer.subprocess, "run", side_effect=OSError("broken")):
            self.assertEqual(observer.command_output(["cmd"]), {"ok": False, "error": "broken"})
        with patch.object(
            observer.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=1, stdout="", stderr=""),
        ):
            self.assertFalse(observer.command_output(["cmd"])["ok"])

    def test_gateway_reader_envelope_is_semantic_and_reports_unavailability(self) -> None:
        reader = _DiagnosticsReader()
        self.assertEqual(
            observer.read_gateway_diagnostics(reader),
            {"available": True, "snapshot": reader.snapshot.to_payload()},
        )
        self.assertEqual(
            observer.read_gateway_diagnostics(_DiagnosticsReader(error="offline")),
            {"available": False, "error": "offline"},
        )

    def test_incident_reasons_ignore_unavailable_gateway_diagnostics(self) -> None:
        self.assertEqual(
            observer.incident_reasons(
                {
                    "gateway_diagnostics": {"available": False, "error": "offline"},
                    "svstat": {"ok": True, "stdout": "service up"},
                    "trace_markers": [],
                }
            ),
            [],
        )

    def test_shelly_fetch_has_exact_url_limit_decode_and_error_contract(self) -> None:
        self.assertEqual(observer.fetch_shelly_status(""), {"ok": False, "skipped": "no-host"})
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"\xffstatus"
        with patch.object(observer.urllib.request, "urlopen", return_value=response) as urlopen:
            result = observer.fetch_shelly_status("host", timeout=3.5)
        urlopen.assert_called_once_with("http://host/rpc/Shelly.GetStatus", timeout=3.5)
        response.__enter__.return_value.read.assert_called_once_with(65536)
        self.assertEqual(
            result,
            {"ok": True, "url": "http://host/rpc/Shelly.GetStatus", "payload": "\ufffdstatus"},
        )
        with patch.object(observer.urllib.request, "urlopen", side_effect=TimeoutError("late")):
            self.assertEqual(
                observer.fetch_shelly_status("host"),
                {"ok": False, "url": "http://host/rpc/Shelly.GetStatus", "error": "late"},
            )

    def test_tail_and_safe_file_read_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "data"
            path.write_bytes(b"0123456789")
            self.assertEqual(observer.tail_file(str(path), max_bytes=4), "6789")
            self.assertEqual(observer.tail_file(str(path), max_bytes=20), "0123456789")
            self.assertEqual(observer.read_text_safe(str(path)), "0123456789")
            path.write_bytes(b"abc\xff")
            self.assertEqual(observer.tail_file(str(path), max_bytes=4), "abc\ufffd")
            missing = str(Path(temp_dir) / "missing")
            self.assertTrue(observer.tail_file(missing).startswith("<unavailable: "))
            self.assertTrue(observer.read_text_safe(missing).startswith("<unavailable: "))
            self.assertTrue(observer.read_text_safe(missing).endswith("\n"))

    def test_tail_log_dir_uses_four_newest_files_and_requested_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            names = ["z", "y", "x", "w", "v"]
            for index, name in enumerate(names):
                path = root / name
                path.write_text(str(index), encoding="utf-8")
                os.utime(path, (index + 1, index + 1))
            (root / "subdir").mkdir()
            with patch.object(observer, "tail_file", side_effect=lambda path, max_bytes: f"{Path(path).name}:{max_bytes}") as tail:
                result = observer.tail_log_dir(str(root), max_bytes=7)
        self.assertEqual(result, {name: f"{name}:7" for name in names[1:]})
        self.assertEqual(tail.call_count, 4)
        self.assertEqual(observer.tail_log_dir("/definitely/missing"), {})

    def test_marker_json_and_process_contracts_are_exact(self) -> None:
        self.assertEqual(observer.trace_markers_in_text("stale NoReply Traceback"), ["Traceback", "NoReply", "stale"])
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            path.write_text('{"value": 1}', encoding="utf-8")
            self.assertEqual(observer.read_json_file(str(path)), {"value": 1, "ok": True, "path": str(path)})
            path.write_text("[]", encoding="utf-8")
            self.assertEqual(
                observer.read_json_file(str(path)),
                {"ok": False, "path": str(path), "error": "not-a-json-object"},
            )
            path.write_text("{", encoding="utf-8")
            invalid = observer.read_json_file(str(path))
            self.assertEqual(set(invalid), {"ok", "path", "error"})
            self.assertIs(invalid["ok"], False)
            self.assertEqual(invalid["path"], str(path))
            self.assertIsInstance(invalid["error"], str)
            self.assertNotEqual(invalid["error"], "None")
        with patch.object(Path, "read_text", return_value='{"x": 1}') as read_json_text:
            self.assertTrue(observer.read_json_file("state")["ok"])
        read_json_text.assert_called_once_with(encoding="utf-8")
        with patch.object(Path, "read_text", return_value="text") as read_safe_text:
            self.assertEqual(observer.read_text_safe("state"), "text")
        read_safe_text.assert_called_once_with(encoding="utf-8")
        ps_text = "123 user command marker rest\nno match\nmarker-only\n"
        self.assertEqual(
            observer.matching_processes(ps_text, "marker"),
            [
                {"pid": "123", "line": "123 user command marker rest"},
                {"pid": "marker-only", "line": "marker-only"},
            ],
        )
        with patch.object(observer, "matching_processes", return_value=[{"pid": "1"}]) as matching:
            self.assertEqual(observer.helper_processes("ps"), [{"pid": "1"}])
        matching.assert_called_once_with("ps", "venus_evcharger_auto_input_helper.py")

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
            "gateway_diagnostics": {"available": True, "snapshot": diagnostics.to_payload()},
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
        self.assertEqual(observer._runit_incident_reasons({"svstat": {"ok": False}}), ["runit-status-failed"])
        self.assertEqual(observer._runit_incident_reasons({"svstat": "bad"}), [])
        self.assertEqual(observer._runit_incident_reasons({"svstat": {"ok": True, "stdout": "service up now"}}), [])
        self.assertEqual(observer._runit_incident_reasons({"svstat": {"ok": True}}), ["runit-not-up"])
        self.assertEqual(observer.incident_reasons({}), [])
        invalid = {"gateway_diagnostics": {"available": True, "snapshot": {}}, "trace_markers": []}
        self.assertEqual(observer.incident_reasons(invalid), ["gateway-diagnostics-invalid"])
        self.assertEqual(observer._slug(" A/B ++ "), "a-b")
        self.assertEqual(observer._slug("---"), "event")

    def test_collect_snapshot_wires_every_source_into_stable_schema(self) -> None:
        defaults = _defaults("Host=host\n")
        reader = _DiagnosticsReader()
        command_results = [
            {"ok": True, "stdout": "11 root venus_evcharger_auto_input_helper.py\n"},
            {"ok": True, "stdout": "up"},
            {"ok": True, "stdout": "uptime"},
        ]
        with (
            patch.object(observer, "load_defaults", return_value=defaults) as load_defaults,
            patch.object(observer, "tail_log_dir", return_value={"current": "NoReply", "old": "stale"}) as tail_logs,
            patch.object(observer, "command_output", side_effect=command_results) as command,
            patch.object(observer.time, "time", return_value=123.5),
            patch.object(observer, "read_gateway_diagnostics", return_value={"available": True}) as gateway,
            patch.object(observer, "auto_input_snapshot_path", return_value="auto") as auto_path,
            patch.object(observer, "runtime_state_path", return_value="runtime") as runtime_path,
            patch.object(observer, "read_json_file", side_effect=[{"auto": 1}, {"runtime": 1}]) as read_json,
            patch.object(observer, "fetch_shelly_status", return_value={"shelly": 1}) as shelly,
        ):
            snapshot = observer.collect_snapshot("config", diagnostics_reader=reader)
        load_defaults.assert_called_once_with("config")
        tail_logs.assert_called_once_with("/var/volatile/log/dbus-venus-evcharger")
        self.assertEqual(command.call_args_list, [call(["ps", "w"]), call(["svstat", "/service/dbus-venus-evcharger"]), call(["uptime"])])
        gateway.assert_called_once_with(reader)
        shelly.assert_called_once_with("host")
        auto_path.assert_called_once_with(defaults)
        runtime_path.assert_called_once_with(defaults)
        self.assertEqual(read_json.call_args_list, [call("auto"), call("runtime")])
        self.assertEqual(
            snapshot,
            {
                "timestamp": 123.5,
                "config_path": "config",
                "gateway_diagnostics": {"available": True},
                "auto_input_snapshot": {"auto": 1},
                "runtime_state": {"runtime": 1},
                "helper_processes": [{"pid": "11", "line": "11 root venus_evcharger_auto_input_helper.py"}],
                "shelly": {"shelly": 1},
                "svstat": {"ok": True, "stdout": "up"},
                "ps": command_results[0],
                "uptime": {"ok": True, "stdout": "uptime"},
                "runtime_logs": {"current": "NoReply", "old": "stale"},
                "trace_markers": ["NoReply", "stale"],
            },
        )

    def test_write_incident_persists_exact_payload_and_redacted_config(self) -> None:
        with self.assertRaisesRegex(ValueError, "timestamp must be numeric"):
            observer.write_incident("log", {"timestamp": True}, "config", ["reason"])
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "nested" / "evidence"
            config = Path(temp_dir) / "config.ini"
            config.write_text("Password=secret\n", encoding="utf-8")
            with patch.object(observer.time, "strftime", return_value="STAMP") as stamp:
                with patch.object(observer.time, "localtime", return_value="LOCAL") as localtime:
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
            self.assertEqual(
                json.loads((incident_path / "snapshot.json").read_text(encoding="utf-8")),
                {"reasons": ["Reason A", "Reason B"], "timestamp": 42.0, "value": 1},
            )
            self.assertEqual(
                (incident_path / "snapshot.json").read_text(encoding="utf-8"),
                '{\n  "reasons": [\n    "Reason A",\n    "Reason B"\n  ],\n  "timestamp": 42.0,\n  "value": 1\n}',
            )
            self.assertEqual(
                (incident_path / "config.redacted.ini").read_text(encoding="utf-8"),
                "Password=<redacted>\n",
            )

            long_reason = "x" * 100
            long_incident = observer.write_incident(
                str(root), {"timestamp": 43.0}, str(config), [long_reason]
            )
            self.assertEqual(len(Path(long_incident).name.rsplit("-", 1)[-1]), 80)
            self.assertEqual(
                observer.write_incident(str(root), {"timestamp": 43.0}, str(config), [long_reason]),
                long_incident,
            )

        with (
            patch.object(observer.time, "strftime", return_value="STAMP"),
            patch.object(observer.time, "localtime", return_value="LOCAL"),
            patch.object(Path, "mkdir"),
            patch.object(Path, "write_text", return_value=1) as write_text,
            patch.object(observer, "read_text_safe", return_value="Host=x\n"),
        ):
            observer.write_incident("log", {"timestamp": 1.0}, "config", ["reason"])
        self.assertEqual(write_text.call_count, 2)
        self.assertEqual([item.kwargs for item in write_text.call_args_list], [{"encoding": "utf-8"}] * 2)

    def test_observer_iteration_obeys_storage_reasons_and_cooldown(self) -> None:
        with (
            patch.object(observer, "read_mounts", return_value="mounts") as read_mounts,
            patch.object(observer, "mounted_storage_candidates", return_value=["card"]) as candidates,
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
            patch.object(observer, "collect_snapshot", return_value=snapshot) as collect,
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
                    diagnostics_reader="reader",
                ),
                20.0,
            )
        collect.assert_called_once_with("config", diagnostics_reader="reader")
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
                ),
                10.0,
            )
        no_write.assert_not_called()

    def test_observer_loop_clamps_delays_and_carries_incident_timestamp(self) -> None:
        self.assertEqual(inspect.signature(observer.observer_loop).parameters["start_delay"].default, 180.0)
        self.assertEqual(inspect.signature(observer.observer_loop).parameters["interval"].default, 30.0)
        self.assertEqual(inspect.signature(observer.observer_loop).parameters["incident_cooldown"].default, 900.0)
        self.assertEqual(inspect.signature(observer.observer_loop).parameters["mounts_path"].default, "/proc/mounts")
        sleeps: list[float] = []

        def sleep(value: float) -> None:
            sleeps.append(value)
            if len(sleeps) == 3:
                raise KeyboardInterrupt

        with (
            patch.object(observer.time, "sleep", side_effect=sleep),
            patch.object(observer, "_observer_iteration", side_effect=[7.0, 8.0]) as iteration,
        ):
            with self.assertRaises(KeyboardInterrupt):
                observer.observer_loop(
                    "config",
                    start_delay=-2.0,
                    interval=0.5,
                    incident_cooldown=9.0,
                    mounts_path="mounts",
                    diagnostics_reader="reader",
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
                    diagnostics_reader="reader",
                ),
                call(
                    "config",
                    7.0,
                    incident_cooldown=9.0,
                    mounts_path="mounts",
                    diagnostics_reader="reader",
                ),
            ],
        )

    def test_collect_snapshot_normalizes_missing_ps_stdout_before_process_matching(self) -> None:
        defaults = _defaults("")
        with (
            patch.object(observer, "load_defaults", return_value=defaults),
            patch.object(observer, "tail_log_dir", return_value={}),
            patch.object(observer, "command_output", side_effect=[{"ok": False}, {}, {}]),
            patch.object(observer, "helper_processes", return_value=[]) as helpers,
            patch.object(observer, "read_gateway_diagnostics", return_value={}),
            patch.object(observer, "read_json_file", return_value={}),
            patch.object(observer, "fetch_shelly_status", return_value={}),
        ):
            observer.collect_snapshot("config")
        helpers.assert_called_once_with("")


if __name__ == "__main__":
    unittest.main()
