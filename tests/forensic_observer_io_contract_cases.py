# SPDX-License-Identifier: GPL-3.0-or-later
"""Reusable IO and payload boundary contracts for the forensic observer."""

from __future__ import annotations

import configparser
import inspect
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.gateway_diagnostics_fixtures import gateway_diagnostics_snapshot
from venus_evcharger.ops import forensic_observer as observer
from venus_evcharger.ops import forensic_observer_artifacts as artifacts
from venus_evcharger.ops import forensic_observer_schema as schema
from venus_evcharger.ports.gateway_diagnostics import (
    GatewayDiagnosticsSnapshot,
    GatewayDiagnosticsUnavailable,
)


class DiagnosticsReader:
    def __init__(self, *, error: str = "") -> None:
        self.snapshot = gateway_diagnostics_snapshot()
        self.error = error

    def read_snapshot(self) -> GatewayDiagnosticsSnapshot:
        if self.error:
            raise GatewayDiagnosticsUnavailable(self.error)
        return self.snapshot


def defaults(text: str) -> configparser.SectionProxy:
    return config(text)["DEFAULT"]


def config(text: str) -> configparser.ConfigParser:
    parser = observer._CaseSensitiveConfigParser()
    parser.read_string(f"[DEFAULT]\n{text}")
    return parser


class ForensicObserverIOContractCases(unittest.TestCase):
    def test_payload_schema_names_every_stable_field(self) -> None:
        self.assertEqual(
            schema.CommandCompletedPayload.__required_keys__,
            frozenset({"ok", "returncode", "stdout", "stderr"}),
        )
        self.assertEqual(
            schema.CommandErrorPayload.__required_keys__,
            frozenset({"ok", "error"}),
        )
        self.assertEqual(
            schema.ForensicSnapshotPayload.__required_keys__,
            frozenset(
                {
                    "timestamp",
                    "config_path",
                    "gateway_diagnostics",
                    "backend_diagnostics",
                    "backend_probe",
                    "auto_input_snapshot",
                    "runtime_state",
                    "helper_processes",
                    "svstat",
                    "ps",
                    "uptime",
                    "runtime_logs",
                    "trace_markers",
                }
            ),
        )

    def test_public_reader_defaults_are_part_of_the_contract(self) -> None:
        expectations = {
            artifacts.read_mounts: {"path": "/proc/mounts"},
            observer.command_output: {"timeout": 3.0},
            observer.tail_file: {"max_bytes": 20000},
            observer.tail_log_dir: {"max_bytes": 20000},
        }
        for function, expected_defaults in expectations.items():
            signature = inspect.signature(function)
            for name, expected in expected_defaults.items():
                self.assertEqual(signature.parameters[name].default, expected)

    def test_config_parser_and_derived_paths_are_exact(self) -> None:
        configured = defaults(
            "DeviceInstance=71\n"
            "AutoInputSnapshotPath= /tmp/auto \n"
            "GatewayDiagnosticsSnapshotPath= /tmp/diagnostics \n"
        )
        self.assertEqual(observer.device_instance(configured), 71)
        self.assertEqual(observer.auto_input_snapshot_path(configured), "/tmp/auto")
        self.assertEqual(observer.gateway_diagnostics_snapshot_path(configured), "/tmp/diagnostics")
        self.assertEqual(observer.runtime_state_path(configured), "/run/dbus-venus-evcharger-71.json")

        empty = defaults("")
        self.assertEqual(observer.device_instance(empty), 60)
        self.assertEqual(
            observer.gateway_diagnostics_snapshot_path(empty),
            "/run/venus-evcharger/gateway-diagnostics.json",
        )

        fallback = defaults("DeviceInstance=invalid\n")
        self.assertEqual(observer.device_instance(fallback), 60)
        self.assertEqual(
            observer.auto_input_snapshot_path(fallback),
            "/run/dbus-venus-evcharger-auto-60.json",
        )

    def test_load_config_is_fail_fast_and_preserves_case_sensitive_keys(self) -> None:
        parser = MagicMock()
        parser.read.return_value = ["config.ini"]
        with patch.object(
            observer,
            "_CaseSensitiveConfigParser",
            return_value=parser,
        ) as parser_factory:
            self.assertIs(observer.load_config("config.ini"), parser)
        parser_factory.assert_called_once_with()
        parser.read.assert_called_once_with("config.ini")

        missing_path = "/definitely/missing/observer.ini"
        with self.assertRaises(FileNotFoundError) as raised:
            observer.load_config(missing_path)
        self.assertEqual(raised.exception.args, (missing_path,))
        with tempfile.NamedTemporaryFile("w+", suffix=".ini") as handle:
            handle.write("[DEFAULT]\nHost=upper\nhost=lower\n")
            handle.flush()
            loaded_defaults = observer.load_config(handle.name)["DEFAULT"]
        self.assertEqual(loaded_defaults["Host"], "upper")
        self.assertEqual(loaded_defaults["host"], "lower")

    def test_redaction_contract_is_line_preserving_and_case_insensitive(self) -> None:
        source = "plain\nHost=x\nPassword=p=tail\nAPI_TOKEN=t\nsecretKey=s\nAuthorization=a\n"
        self.assertEqual(
            artifacts.redact_config_text(source),
            "plain\nHost=x\nPassword=<redacted>\nAPI_TOKEN=<redacted>\n"
            "secretKey=<redacted>\nAuthorization=<redacted>\n",
        )
        self.assertEqual(artifacts.redact_config_text(""), "\n")

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
            artifacts.mounted_storage_candidates(mounts),
            ["/media/two-parts", "/media/Card One", "/run/media/card", "/mnt/archive"],
        )

    def test_mount_and_writable_directory_error_contracts(self) -> None:
        with patch.object(Path, "read_text", side_effect=OSError("missing")):
            self.assertEqual(artifacts.read_mounts("mounts"), "")
        with patch.object(Path, "read_text", return_value="mounted") as read:
            self.assertEqual(artifacts.read_mounts("mounts"), "mounted")
        read.assert_called_once_with(encoding="utf-8")

        with tempfile.TemporaryDirectory() as temp_dir:
            blocked = Path(temp_dir) / "blocked"
            blocked.write_text("file", encoding="utf-8")
            valid = Path(temp_dir) / "valid"
            selected = artifacts.first_writable_log_dir(
                [str(blocked), str(valid)],
                subdir="evidence",
            )
            self.assertEqual(selected, str(valid / "evidence"))
            self.assertFalse((valid / "evidence/.write-test").exists())
            self.assertEqual(
                artifacts.first_writable_log_dir([str(valid)], subdir="evidence"),
                str(valid / "evidence"),
            )
        self.assertEqual(artifacts.first_writable_log_dir([], subdir="evidence"), "")

    def test_command_output_bounds_and_reports_subprocess_contract(self) -> None:
        completed = SimpleNamespace(returncode=3, stdout="a" * 5000, stderr="b" * 5001)
        with patch.object(observer.subprocess, "run", return_value=completed) as run:
            result = observer.command_output(["cmd", "arg"], timeout=4.5)
        run.assert_called_once_with(
            ["cmd", "arg"],
            capture_output=True,
            text=True,
            timeout=4.5,
            check=False,
        )
        self.assertEqual(
            result,
            {
                "ok": False,
                "returncode": 3,
                "stdout": "a" * 4000,
                "stderr": "b" * 4000,
            },
        )
        with patch.object(observer.subprocess, "run", side_effect=OSError("broken")):
            self.assertEqual(observer.command_output(["cmd"]), {"ok": False, "error": "broken"})
        with patch.object(
            observer.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
        ):
            self.assertIs(observer.command_output(["cmd"])["ok"], True)

    def test_gateway_reader_envelope_is_semantic_and_reports_unavailability(self) -> None:
        reader = DiagnosticsReader()
        self.assertEqual(
            observer.read_gateway_diagnostics(reader),
            {"available": True, "snapshot": reader.snapshot.to_payload()},
        )
        self.assertEqual(
            observer.read_gateway_diagnostics(DiagnosticsReader(error="offline")),
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

    def test_backend_diagnostics_use_canonical_selection_contract(self) -> None:
        selection, payload = observer.backend_diagnostics(config(""))
        self.assertIsNotNone(selection)
        self.assertIs(payload["available"], True)
        if "selection" not in payload:
            self.fail("backend diagnostics unexpectedly unavailable")
        assert selection is not None
        self.assertEqual(selection["switch_type"], "shelly_contactor_switch")
        selection_payload = payload["selection"]
        self.assertEqual(
            selection_payload,
            {
                "mode": "combined",
                "meter_type": "shelly_meter",
                "switch_type": "shelly_contactor_switch",
                "charger_type": None,
                "meter_config_path": "",
                "switch_config_path": "",
                "charger_config_path": "",
            },
        )
        selection_with_paths = selection.copy()
        selection_with_paths["meter_config_path"] = Path("meter.ini")
        selection_with_paths["switch_config_path"] = Path("switch.ini")
        selection_with_paths["charger_config_path"] = Path("charger.ini")
        self.assertEqual(
            observer._backend_selection_payload(selection_with_paths),
            {
                **selection_payload,
                "meter_config_path": "meter.ini",
                "switch_config_path": "switch.ini",
                "charger_config_path": "charger.ini",
            },
        )
        self.assertEqual(observer._path_text(None), "")
        self.assertEqual(observer._path_text(Path("backend.ini")), "backend.ini")

        with patch.object(
            observer,
            "backend_selection_view_from_config",
            side_effect=ValueError("invalid"),
        ):
            failed_selection, failed_payload = observer.backend_diagnostics(config(""))
        self.assertIsNone(failed_selection)
        self.assertEqual(
            failed_payload,
            {
                "available": False,
                "reason_code": "backend-configuration-invalid",
                "error": "invalid",
            },
        )

    def test_tail_and_safe_file_read_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "data"
            path.write_bytes(b"0123456789")
            self.assertEqual(observer.tail_file(str(path), max_bytes=4), "6789")
            self.assertEqual(observer.tail_file(str(path), max_bytes=20), "0123456789")
            self.assertEqual(artifacts.read_text_safe(str(path)), "0123456789")
            path.write_bytes(b"abc\xff")
            self.assertEqual(observer.tail_file(str(path), max_bytes=4), "abc\ufffd")
            missing = str(Path(temp_dir) / "missing")
            self.assertTrue(observer.tail_file(missing).startswith("<unavailable: "))
            self.assertTrue(artifacts.read_text_safe(missing).startswith("<unavailable: "))
            self.assertTrue(artifacts.read_text_safe(missing).endswith("\n"))
        with patch.object(Path, "read_text", return_value="safe") as read_text:
            self.assertEqual(artifacts.read_text_safe("state"), "safe")
        read_text.assert_called_once_with(encoding="utf-8")

    def test_tail_log_dir_uses_four_newest_files_and_requested_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            names = ["z", "y", "x", "w", "v"]
            for index, name in enumerate(names):
                path = root / name
                path.write_text(str(index), encoding="utf-8")
                os.utime(path, (index + 1, index + 1))
            (root / "subdir").mkdir()

            def tailed(path: str, max_bytes: int) -> str:
                return f"{Path(path).name}:{max_bytes}"

            with patch.object(
                observer,
                "tail_file",
                side_effect=tailed,
            ) as tail:
                result = observer.tail_log_dir(str(root), max_bytes=7)
        self.assertEqual(result, {name: f"{name}:7" for name in names[1:]})
        self.assertEqual(tail.call_count, 4)
        self.assertEqual(observer.tail_log_dir("/definitely/missing"), {})

    def test_dynamic_payload_parsers_accept_only_relevant_shapes(self) -> None:
        self.assertIsNone(observer._string_key_mapping([]))
        self.assertEqual(
            observer._string_key_mapping({"stdout": "text", 1: "ignored"}),
            {"stdout": "text"},
        )
        self.assertEqual(observer._command_stdout({"stdout": "text"}), "text")
        self.assertEqual(observer._command_stdout({"stdout": 1}), "")
        self.assertEqual(observer._command_stdout("invalid"), "")
        self.assertEqual(observer._string_sequence(["one", 2, "three"]), ("one", "three"))
        self.assertEqual(observer._string_sequence("one"), ())
        self.assertEqual(observer._string_sequence(1), ())

    def test_probe_selection_and_gateway_health_helpers_are_exact(self) -> None:
        main_config = config("")
        selection = observer.backend_selection_view_from_config(main_config)
        with patch.object(observer, "configured_backend_probe") as configured_backend:
            self.assertEqual(
                observer._configured_probe(
                    main_config,
                    None,
                    config_path="/tmp/config.ini",
                ),
                observer.DisabledBackendProbe("backend-diagnostics-unavailable"),
            )
        configured_backend.assert_not_called()

        expected_probe = observer.DisabledBackendProbe("configured")
        with patch.object(
            observer,
            "configured_backend_probe",
            return_value=expected_probe,
        ) as configured_backend:
            self.assertIs(
                observer._configured_probe(
                    main_config,
                    selection,
                    config_path="/tmp/config.ini",
                ),
                expected_probe,
            )
        configured_backend.assert_called_once_with(
            main_config,
            selection,
            config_path="/tmp/config.ini",
        )

        supplied = observer.DisabledBackendProbe("supplied")
        with patch.object(observer, "_configured_probe") as configured:
            self.assertIs(
                observer._effective_probe(
                    supplied,
                    main_config,
                    selection,
                    config_path="/tmp/config.ini",
                ),
                supplied,
            )
        configured.assert_not_called()

        generated = observer.DisabledBackendProbe("generated")
        with patch.object(
            observer,
            "_configured_probe",
            return_value=generated,
        ) as configured:
            self.assertIs(
                observer._effective_probe(
                    None,
                    main_config,
                    selection,
                    config_path="/tmp/config.ini",
                ),
                generated,
            )
        configured.assert_called_once_with(
            main_config,
            selection,
            config_path="/tmp/config.ini",
        )

        self.assertEqual(
            observer._gateway_health_incident_reason(
                gateway_diagnostics_snapshot(health_state="protective")
            ),
            "gateway-health-protective",
        )
        self.assertEqual(
            observer._gateway_health_incident_reason(
                gateway_diagnostics_snapshot(health_state="unavailable")
            ),
            "gateway-health-unavailable",
        )
        self.assertEqual(
            observer._gateway_health_incident_reason(gateway_diagnostics_snapshot()),
            "",
        )

    def test_artifact_timestamp_accepts_numbers_but_not_bool_or_text(self) -> None:
        self.assertEqual(artifacts._artifact_timestamp({"timestamp": 7}), 7.0)
        self.assertEqual(artifacts._artifact_timestamp({"timestamp": 7.5}), 7.5)
        for invalid in (True, "7", None):
            with self.subTest(value=invalid):
                with self.assertRaisesRegex(
                    ValueError,
                    "^forensic snapshot timestamp must be numeric$",
                ):
                    artifacts._artifact_timestamp({"timestamp": invalid})

    def test_marker_json_and_process_contracts_are_exact(self) -> None:
        self.assertEqual(
            observer.trace_markers_in_text("stale NoReply Traceback"),
            ["Traceback", "NoReply", "stale"],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            path.write_text('{"value": 1}', encoding="utf-8")
            self.assertEqual(
                observer.read_json_file(str(path)),
                {"value": 1, "ok": True, "path": str(path)},
            )
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
        with patch.object(Path, "read_text", return_value='{"x": 1}') as read_json_text:
            self.assertTrue(observer.read_json_file("state")["ok"])
        read_json_text.assert_called_once_with(encoding="utf-8")
        with patch.object(Path, "read_text", side_effect=OSError("broken")):
            self.assertEqual(
                observer.read_json_file("state"),
                {"ok": False, "path": "state", "error": "broken"},
            )

        ps_text = "123 user command marker rest\nno match\nmarker-only\n"
        self.assertEqual(
            observer.matching_processes(ps_text, "marker"),
            [
                {"pid": "123", "line": "123 user command marker rest"},
                {"pid": "marker-only", "line": "marker-only"},
            ],
        )
        with patch.object(
            observer,
            "matching_processes",
            return_value=[{"pid": "1", "line": "helper"}],
        ) as matching:
            self.assertEqual(
                observer.helper_processes("ps"),
                [{"pid": "1", "line": "helper"}],
            )
        matching.assert_called_once_with("ps", "venus_evcharger_auto_input_helper.py")
