# SPDX-License-Identifier: GPL-3.0-or-later
import runpy
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.ops import forensic_observer as observer
from venus_evcharger.dbus_gateway import DbusCacheStore, dbus_path_key, gateway_paths


class _FakeDbusObject:
    def __init__(self, values, path):
        self._values = values
        self._path = path

    def get_dbus_method(self, _name, _interface):
        def _method(timeout=1.0):
            if self._path == "/StartStop":
                raise RuntimeError(f"timeout {timeout}")
            return self._values[self._path]

        return _method


class _FakeDbusBus:
    def __init__(self):
        self.values = {"/Mode": 1, "/Ac/Power": object()}

    def get_object(self, _service_name, path, introspect=True):
        return _FakeDbusObject(self.values, path)


class ForensicObserverTests(unittest.TestCase):
    def _write_gateway_cache(self, temp_dir: str, service_name: str, values: dict[str, object]) -> str:
        paths = gateway_paths(str(Path(temp_dir) / "run"))
        store = DbusCacheStore(paths)
        for path, value in values.items():
            store.update_value(dbus_path_key(service_name, path), value, source=f"{service_name}{path}", now=time.time())
        store.write_snapshot_files()
        return paths.cache_path

    def test_config_identity_redaction_mounts_and_writable_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\nDeviceInstance=70\nServiceName=com.example.ev\nPassword=secret\nControlApiToken=abc\nHost=192.0.2.1\n",
                encoding="utf-8",
            )
            defaults = observer.load_defaults(str(config_path))

            self.assertEqual(observer.device_instance(defaults), 70)
            self.assertEqual(observer.evcharger_service_name(defaults), "com.example.ev.http_70")
            self.assertEqual(observer.configured_host(defaults), "192.0.2.1")
            self.assertIn("Password=<redacted>", observer.redact_config_text(config_path.read_text(encoding="utf-8")))
            config_path.write_text("[DEFAULT]\nDeviceInstance=bad\nServiceName=\n", encoding="utf-8")
            invalid_defaults = observer.load_defaults(str(config_path))
            self.assertEqual(observer.device_instance(invalid_defaults), 60)
            self.assertEqual(observer.evcharger_service_name(invalid_defaults), "com.victronenergy.evcharger.http_60")
            self.assertEqual(observer.auto_input_snapshot_path(invalid_defaults), "/run/dbus-venus-evcharger-auto-60.json")
            self.assertEqual(observer.runtime_state_path(invalid_defaults), "/run/dbus-venus-evcharger-60.json")
            self.assertEqual(
                observer.mounted_storage_candidates(
                    "broken-line\n"
                    "/dev/root / ext4 rw 0 0\n"
                    "/dev/sda2 /not-removable ext4 rw 0 0\n"
                    "/dev/sda1 /media/SD\\040Card vfat rw 0 0\n"
                    "/dev/mmcblk1p1 /mnt/card ext4 rw 0 0\n"
                ),
                ["/media/SD Card", "/mnt/card"],
            )
            self.assertEqual(observer.first_writable_log_dir([str(Path(temp_dir) / "card")]), str(Path(temp_dir) / "card/venus-evcharger-forensics"))
            self.assertEqual(observer.first_writable_log_dir([str(config_path)]), "")
            self.assertEqual(observer.read_mounts(str(Path(temp_dir) / "missing-mounts")), "")
            config_path.write_text("[DEFAULT]\nAutoInputSnapshotPath=/tmp/custom-auto.json\n", encoding="utf-8")
            self.assertEqual(observer.auto_input_snapshot_path(observer.load_defaults(str(config_path))), "/tmp/custom-auto.json")
            config_path.write_text("[DEFAULT]\nDbusIntrospectionSnapshotPath=/tmp/custom-map.json\n", encoding="utf-8")
            self.assertEqual(observer.dbus_introspection_snapshot_path(observer.load_defaults(str(config_path))), "/tmp/custom-map.json")

    def test_dbus_snapshot_and_incident_reasons(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = self._write_gateway_cache(
                temp_dir,
                "com.example.ev.http_70",
                {"/Mode": 1, "/Ac/Power": object()},
            )
            dbus_state = observer.read_dbus_paths(
                "com.example.ev.http_70",
                paths=("/Mode", "/StartStop", "/Ac/Power"),
                cache_path=cache_path,
            )

        self.assertFalse(dbus_state["ok"])
        self.assertEqual(dbus_state["values"]["/Mode"], 1)
        self.assertIn("/StartStop", dbus_state["errors"])
        self.assertIn("object object", dbus_state["values"]["/Ac/Power"])

        reasons = observer.incident_reasons(
            {
                "dbus": dbus_state,
                "svstat": {"ok": True, "stdout": "/service/dbus-venus-evcharger: down\n"},
                "trace_markers": ["NoReply", "malloc()"],
            }
        )

        self.assertIn("dbus-/StartStop-failed", reasons)
        self.assertIn("runit-not-up", reasons)
        self.assertIn("log-marker-noreply", reasons)
        self.assertIn("log-marker-malloc", reasons)
        self.assertEqual(observer.incident_reasons({"dbus": "bad", "svstat": "bad", "trace_markers": []}), [])
        json_payload = observer.json_ready({"a": [object(), 1]})
        self.assertTrue(json_payload["a"][0].startswith("<object object"))
        self.assertEqual(json_payload["a"][1], 1)

    def test_default_dbus_import_path_and_successful_fetches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = self._write_gateway_cache(temp_dir, "com.example.ev.http_70", {"/Mode": 1})
            dbus_state = observer.read_dbus_paths("com.example.ev.http_70", paths=("/Mode",), cache_path=cache_path)

        self.assertTrue(dbus_state["ok"])
        self.assertEqual(dbus_state["values"]["/Mode"], 1)

        completed = SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
        with patch.object(observer.subprocess, "run", return_value=completed):
            self.assertTrue(observer.command_output(["true"])["ok"])

        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok":true}'
        with patch.object(observer.urllib.request, "urlopen", return_value=response):
            shelly = observer.fetch_shelly_status("192.0.2.1")

        self.assertTrue(shelly["ok"])
        self.assertIn('"ok":true', shelly["payload"])

    def test_readers_commands_fetch_and_incident_bundle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_source = Path(temp_dir) / "logs"
            log_source.mkdir()
            (log_source / "current").write_text("old\nTraceback\n", encoding="utf-8")
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nPassword=secret\n", encoding="utf-8")
            target = Path(temp_dir) / "sd"

            missing_tail = observer.tail_file(str(Path(temp_dir) / "missing"))
            self.assertIn("<unavailable:", missing_tail)
            self.assertIn("missing", missing_tail)
            self.assertEqual(observer.trace_markers_in_text("hello malloc() NoReply"), ["malloc()", "NoReply"])
            self.assertIn("Traceback", observer.tail_log_dir(str(log_source))["current"])
            self.assertEqual(observer.tail_log_dir(str(Path(temp_dir) / "missing-logs")), {})
            self.assertIn("<unavailable:", observer.read_text_safe(str(Path(temp_dir) / "missing-config")))

            with patch.object(observer.subprocess, "run", side_effect=RuntimeError("boom")):
                self.assertFalse(observer.command_output(["false"])["ok"])
            with patch.object(observer.urllib.request, "urlopen", side_effect=OSError("offline")):
                self.assertFalse(observer.fetch_shelly_status("192.0.2.1")["ok"])
            self.assertEqual(observer.fetch_shelly_status("")["skipped"], "no-host")

            incident_dir = observer.write_incident(
                str(target),
                {"timestamp": 100.0, "dbus": {"ok": False}, "trace_markers": []},
                str(config_path),
                ["dbus-/Mode-failed"],
            )

            self.assertTrue((Path(incident_dir) / "snapshot.json").is_file())
            redacted = (Path(incident_dir) / "config.redacted.ini").read_text(encoding="utf-8")
            self.assertIn("Password=<redacted>", redacted)

    def test_collect_snapshot_uses_helpers_and_open_failures_are_reported(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            run_dir = Path(temp_dir) / "run"
            cache_path = self._write_gateway_cache(temp_dir, "com.victronenergy.evcharger.http_70", {"/Mode": 1})
            config_path.write_text(
                "[DEFAULT]\nDeviceInstance=70\nHost=\n"
                f"DbusGatewayRunDir={run_dir}\nDbusGatewayCachePath={cache_path}\n",
                encoding="utf-8",
            )

            with (
                patch.object(observer, "tail_log_dir", return_value={"current": "NoReply\n"}),
                patch.object(observer, "command_output", return_value={"ok": True, "stdout": " up "}),
            ):
                snapshot = observer.collect_snapshot(str(config_path), bus_factory=MagicMock(side_effect=RuntimeError("dbus down")))

        self.assertEqual(snapshot["service_name"], "com.victronenergy.evcharger.http_70")
        self.assertEqual(snapshot["trace_markers"], ["NoReply"])
        self.assertFalse(snapshot["dbus"]["ok"])
        self.assertIn("auto_input_snapshot", snapshot)
        self.assertIn("runtime_state", snapshot)
        self.assertIn("helper_processes", snapshot)
        self.assertEqual(observer.incident_reasons({"dbus": {"errors": {}}, "svstat": {"ok": False}, "trace_markers": []}), ["runit-status-failed"])

    def test_json_file_and_helper_process_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = Path(temp_dir) / "state.json"
            json_path.write_text('{"value": 1}', encoding="utf-8")
            self.assertTrue(observer.read_json_file(str(json_path))["ok"])
            json_path.write_text("[1]", encoding="utf-8")
            self.assertEqual(observer.read_json_file(str(json_path))["error"], "not-a-json-object")
            self.assertFalse(observer.read_json_file(str(Path(temp_dir) / "missing.json"))["ok"])

        helpers = observer.helper_processes(
            "123 root python3 venus_evcharger_auto_input_helper.py /config /run/snapshot\n"
            "not-helper\n"
        )
        self.assertEqual(helpers, [{"pid": "123", "line": "123 root python3 venus_evcharger_auto_input_helper.py /config /run/snapshot"}])
        self.assertEqual(observer.helper_processes("venus_evcharger_auto_input_helper.py\n"), [{"pid": "venus_evcharger_auto_input_helper.py", "line": "venus_evcharger_auto_input_helper.py"}])

    def test_observer_loop_skips_collection_without_removable_storage(self):
        with (
            patch.object(observer.time, "sleep", return_value=None),
            patch.object(observer, "read_mounts", side_effect=["", KeyboardInterrupt]),
            patch.object(observer, "collect_snapshot") as collect_snapshot,
        ):
            with self.assertRaises(KeyboardInterrupt):
                observer.observer_loop("/tmp/config.ini", start_delay=0, interval=1)

        collect_snapshot.assert_not_called()

    def test_observer_loop_writes_incident_only_when_sd_and_reasons_exist(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            card = Path(temp_dir) / "card"
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nPassword=secret\n", encoding="utf-8")
            sleep_calls = []

            def fake_sleep(_seconds):
                sleep_calls.append(_seconds)
                if len(sleep_calls) >= 2:
                    raise KeyboardInterrupt

            with (
                patch.object(observer.time, "sleep", side_effect=fake_sleep),
                patch.object(observer, "read_mounts", return_value="mounted\n"),
                patch.object(observer, "mounted_storage_candidates", return_value=[str(card)]),
                patch.object(observer, "collect_snapshot", return_value={"timestamp": 100.0}),
                patch.object(observer, "incident_reasons", return_value=["dbus-/Mode-failed"]),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    observer.observer_loop(str(config_path), start_delay=0, interval=1, incident_cooldown=900)

            incidents = list((card / "venus-evcharger-forensics").glob("incident-*"))
            self.assertEqual(len(incidents), 1)

    def test_observer_loop_does_not_write_without_incident_reason(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            card = Path(temp_dir) / "card"
            sleep_calls = []

            def fake_sleep(_seconds):
                sleep_calls.append(_seconds)
                if len(sleep_calls) >= 2:
                    raise KeyboardInterrupt

            with (
                patch.object(observer.time, "sleep", side_effect=fake_sleep),
                patch.object(observer, "read_mounts", return_value="mounted\n"),
                patch.object(observer, "mounted_storage_candidates", return_value=[str(card)]),
                patch.object(observer, "collect_snapshot", return_value={"timestamp": 100.0}),
                patch.object(observer, "incident_reasons", return_value=[]),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    observer.observer_loop("/tmp/config.ini", start_delay=0, interval=1)

            self.assertFalse(any((card / "venus-evcharger-forensics").glob("incident-*")))

    def test_observer_entrypoint_delegates_to_loop(self):
        module_path = Path(__file__).resolve().parents[1] / "venus_evcharger_observer.py"
        with patch("venus_evcharger.ops.forensic_observer.observer_loop") as observer_loop:
            with patch.object(sys, "argv", [str(module_path), "/tmp/config.ini", "--start-delay", "1", "--interval", "2", "--cooldown", "3"]):
                with self.assertRaises(SystemExit) as exit_ctx:
                    runpy.run_path(str(module_path), run_name="__main__")

        self.assertEqual(exit_ctx.exception.code, 0)
        observer_loop.assert_called_once_with("/tmp/config.ini", start_delay=1.0, interval=2.0, incident_cooldown=3.0)

    def test_observer_entrypoint_import_does_not_start_loop(self):
        module_path = Path(__file__).resolve().parents[1] / "venus_evcharger_observer.py"
        with patch("venus_evcharger.ops.forensic_observer.observer_loop") as observer_loop:
            runpy.run_path(str(module_path), run_name="venus_evcharger_observer_import_test")

        observer_loop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
