# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import runpy
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock, patch

import venus_evcharger.dbus_introspection as introspection_module
import venus_evcharger_dbus_introspection_worker as worker_module
from venus_evcharger.dbus_introspection import (
    DBUS_INTROSPECTION_SCHEMA_VERSION,
    load_introspection_snapshot,
    load_owner_introspection_snapshot,
    owner_path_children,
    owner_path_unusable,
    path_children,
    path_unusable_until,
    request_introspection,
    request_owner_introspection,
    service_path_finding,
)
from venus_evcharger.dbus_gateway import DbusCacheStore, DbusCommandInbox, gateway_paths
from venus_evcharger.inputs.helper.sources_dbus import _AutoInputHelperSourceDbusMixin
from venus_evcharger.inputs.introspection_supervisor import DbusIntrospectionSupervisor
from venus_evcharger_dbus_introspection_worker import DbusIntrospectionWorker, IntrospectionJob


class _FakeDbusInterface:
    def __init__(self, behavior):
        self.behavior = behavior

    def Introspect(self, timeout=1.0):
        if isinstance(self.behavior, Exception):
            raise self.behavior
        return self.behavior


class _FakeBus:
    def __init__(self, names=None):
        self.names = names or []
        self.objects = {}
        self.get_calls = []

    def get_object(self, service, path, introspect=True):
        self.get_calls.append((service, path, introspect))
        return (service, path)


class _FakeDbus:
    def __init__(self, bus):
        self.bus = bus

    def SystemBus(self, private=True):
        return self.bus

    def Interface(self, obj, interface):
        service, path = obj
        if interface == "org.freedesktop.DBus":
            return MagicMock(ListNames=MagicMock(return_value=self.bus.names))
        return _FakeDbusInterface(self.bus.objects[(service, path)])


class DbusIntrospectionWorkerTests(unittest.TestCase):
    def _config(self, directory: str, extra: str = "") -> str:
        path = Path(directory) / "config.ini"
        path.write_text(
            "[DEFAULT]\n"
            "DeviceInstance=60\n"
            "AutoPvServicePrefix=com.victronenergy.pvinverter\n"
            "AutoPvPath=/Ac/Power\n"
            "AutoGridService=\n"
            "AutoGridL1Path=/Ac/Grid/L1/Power\n"
            "AutoGridL2Path=\n"
            "AutoGridL3Path=\n"
            "AutoBatteryServicePrefix=com.victronenergy.battery\n"
            "AutoBatterySocPath=/Soc\n"
            "AutoUseDcPv=0\n"
            "DbusIntrospectionFullScanIntervalSeconds=60\n"
            "DbusIntrospectionMinJobIntervalSeconds=0.1\n"
            "DbusIntrospectionLoadAvgMax=0\n"
            "DbusIntrospectionMinMemAvailableKb=0\n"
            "DbusIntrospectionPvQuietHours=\n"
            f"DbusGatewayRunDir={Path(directory) / 'run'}\n"
            f"{extra}",
            encoding="utf-8",
        )
        return str(path)

    def _config_from_lines(self, directory: str, lines: list[str]) -> str:
        path = Path(directory) / "config-custom.ini"
        if not any(line.startswith("DbusGatewayRunDir=") for line in lines):
            lines = [*lines, f"DbusGatewayRunDir={Path(directory) / 'run'}"]
        path.write_text("[DEFAULT]\n" + "\n".join(lines) + "\n", encoding="utf-8")
        return str(path)

    def _seed_gateway_services(self, directory: str, names: list[str]) -> None:
        store = DbusCacheStore(gateway_paths(str(Path(directory) / "run")))
        store.update_services(names)
        store.write_snapshot_files()

    def _gateway_commands(self, directory: str) -> list[dict[str, object]]:
        inbox = DbusCommandInbox(gateway_paths(str(Path(directory) / "run")).command_dir)
        return [payload for _path, payload in inbox.load_pending()]

    def test_run_once_trickles_background_introspection_and_writes_mapping(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = str(Path(temp_dir) / "map.json")
            request_path = str(Path(temp_dir) / "requests.json")
            self._seed_gateway_services(temp_dir, ["com.victronenergy.pvinverter.http_48"])
            worker = DbusIntrospectionWorker(self._config(temp_dir), snapshot_path, request_path)
            worker.run_once(100.0)

            snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
            finding = snapshot["services"]["com.victronenergy.pvinverter.http_48"]["paths"]["/Ac/Power"]
            self.assertEqual(finding["status"], "requested")
            self.assertEqual(finding["interfaces"], [])
            commands = self._gateway_commands(temp_dir)
            self.assertTrue(
                any(
                    command.get("kind") == "introspect"
                    and command.get("service") == "com.victronenergy.pvinverter.http_48"
                    and command.get("path") == "/Ac/Power"
                    for command in commands
                )
            )

    def test_priority_request_runs_before_background_queue(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = str(Path(temp_dir) / "map.json")
            request_path = str(Path(temp_dir) / "requests.json")
            self._seed_gateway_services(temp_dir, ["com.victronenergy.pvinverter.http_48", "com.victronenergy.battery.tty0"])
            request_introspection(request_path, "svc.priority", "/Fast", priority=100, now=100.0)
            worker = DbusIntrospectionWorker(self._config(temp_dir), snapshot_path, request_path)
            worker.run_once(100.0)

            snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
            self.assertIn("svc.priority", snapshot["services"])
            self.assertNotIn("com.victronenergy.pvinverter.http_48", snapshot["services"])

    def test_resource_gate_defers_work_without_dropping_queue(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = str(Path(temp_dir) / "map.json")
            request_path = str(Path(temp_dir) / "requests.json")
            self._seed_gateway_services(temp_dir, ["com.victronenergy.pvinverter.http_48"])
            worker = DbusIntrospectionWorker(
                self._config(temp_dir),
                snapshot_path,
                request_path,
            )
            worker.load_avg_max = 1.0
            with patch.object(worker_module.os, "getloadavg", return_value=(10.0, 0.0, 0.0)):
                snapshot = worker.run_once(100.0)

            self.assertEqual(snapshot["worker_state"], "deferred-resource-gate")
            self.assertGreater(snapshot["queue_depth"], 0)
            self.assertEqual(worker._services, {})

    def test_worker_requests_gateway_introspection_without_dbus_io(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = str(Path(temp_dir) / "map.json")
            request_path = str(Path(temp_dir) / "requests.json")
            self._seed_gateway_services(temp_dir, ["com.victronenergy.pvinverter.http_48"])
            worker = DbusIntrospectionWorker(self._config(temp_dir), snapshot_path, request_path)
            worker.run_once(100.0)

            snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
            skip, reason = path_unusable_until(snapshot, "com.victronenergy.pvinverter.http_48", "/Ac/Power", now=101.0)
            self.assertFalse(skip)
            self.assertEqual(reason, "")
            self.assertEqual(snapshot["services"]["com.victronenergy.pvinverter.http_48"]["paths"]["/Ac/Power"]["status"], "requested")
            self.assertEqual(self._gateway_commands(temp_dir)[0]["kind"], "introspect")

    def test_shared_snapshot_helpers_cover_invalid_payloads_cache_and_requests(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_path = str(Path(temp_dir) / "missing.json")
            self.assertEqual(load_introspection_snapshot("", max_age_seconds=10, now=10.0), {})
            self.assertEqual(load_introspection_snapshot(missing_path, max_age_seconds=10, now=10.0), {})

            path = Path(temp_dir) / "map.json"
            path.write_text("[]", encoding="utf-8")
            self.assertEqual(load_introspection_snapshot(str(path), max_age_seconds=10, now=10.0), {})
            path.write_text(json.dumps({"schema_version": 0}), encoding="utf-8")
            self.assertEqual(load_introspection_snapshot(str(path), max_age_seconds=10, now=10.0), {})
            path.write_text(json.dumps({"schema_version": DBUS_INTROSPECTION_SCHEMA_VERSION, "heartbeat_at": "bad"}), encoding="utf-8")
            self.assertEqual(load_introspection_snapshot(str(path), max_age_seconds=10, now=10.0), {})
            path.write_text(json.dumps({"schema_version": DBUS_INTROSPECTION_SCHEMA_VERSION, "heartbeat_at": 1.0}), encoding="utf-8")
            self.assertEqual(load_introspection_snapshot(str(path), max_age_seconds=1, now=10.0), {})

            snapshot = {
                "schema_version": DBUS_INTROSPECTION_SCHEMA_VERSION,
                "heartbeat_at": 10.0,
                "services": {
                    "svc": {
                        "paths": {
                            "/fresh": {"status": "fresh", "children": ["A", "", None, "B"]},
                            "/bad-children": {"status": "fresh", "children": "bad"},
                            "/missing": {"status": "known-missing"},
                            "/retry": {"status": "unresponsive-backoff", "retry_after": 20.0},
                            "/expired": {"status": "unresponsive-backoff", "retry_after": 5.0},
                        }
                    }
                },
            }
            path.write_text(json.dumps(snapshot), encoding="utf-8")
            loaded = load_introspection_snapshot(str(path), max_age_seconds=100, now=10.0)
            self.assertEqual(path_children(loaded, "svc", "/fresh"), ["A", "B"])
            self.assertEqual(path_children(loaded, "svc", "/bad-children"), [])
            self.assertEqual(path_children(loaded, "svc", "/missing"), [])
            self.assertEqual(path_children({"services": []}, "svc", "/fresh"), [])
            self.assertEqual(service_path_finding({"services": {"svc": []}}, "svc", "/fresh"), {})
            self.assertEqual(service_path_finding({"services": {"svc": {"paths": []}}}, "svc", "/fresh"), {})
            self.assertEqual(service_path_finding({"services": {"svc": {"paths": {"/fresh": []}}}}, "svc", "/fresh"), {})
            self.assertEqual(path_unusable_until(loaded, "svc", "/none", now=10.0), (False, ""))
            self.assertEqual(path_unusable_until(loaded, "svc", "/missing", now=10.0), (True, "known-missing"))
            self.assertEqual(path_unusable_until(loaded, "svc", "/retry", now=10.0), (True, "unresponsive-backoff"))
            self.assertEqual(path_unusable_until(loaded, "svc", "/expired", now=10.0), (False, ""))

            owner = SimpleNamespace(
                dbus_introspection_snapshot_path=str(path),
                dbus_introspection_request_path=str(Path(temp_dir) / "requests.json"),
                dbus_introspection_max_age_seconds=100.0,
                _dbus_introspection_snapshot_loaded_at=0.0,
            )
            self.assertEqual(owner_path_children(owner, "svc", "/fresh", now=10.0), ["A", "B"])
            self.assertEqual(owner_path_unusable(owner, "svc", "/missing", now=10.0), (True, "known-missing"))
            owner._dbus_introspection_snapshot_cache = []
            owner._dbus_introspection_snapshot_loaded_at = 10.0
            self.assertEqual(load_owner_introspection_snapshot(owner, now=11.0), {})
            self.assertFalse(request_owner_introspection(SimpleNamespace(dbus_introspection_request_path=""), "svc", "/p"))
            self.assertFalse(request_introspection("", "svc", "/p"))
            self.assertFalse(request_introspection(owner.dbus_introspection_request_path, "", "/p"))
            self.assertTrue(request_owner_introspection(owner, "svc", "/p", priority=7, reason="r", source="s", now=12.0))
            requests = json.loads(Path(owner.dbus_introspection_request_path).read_text(encoding="utf-8"))["requests"]
            self.assertEqual(requests[-1]["priority"], 7)
            Path(owner.dbus_introspection_request_path).write_text(json.dumps({"requests": "bad"}), encoding="utf-8")
            self.assertTrue(request_introspection(owner.dbus_introspection_request_path, "svc", "/q", now=13.0))
            Path(owner.dbus_introspection_request_path).write_text("[]", encoding="utf-8")
            self.assertTrue(request_introspection(owner.dbus_introspection_request_path, "svc", "/r", now=14.0))
            with patch.object(introspection_module, "write_text_atomically", side_effect=RuntimeError("no write")):
                self.assertFalse(request_introspection(owner.dbus_introspection_request_path, "svc", "/s"))

    def test_worker_edges_for_config_parent_bus_resources_quiet_hours_and_main(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ValueError):
                DbusIntrospectionWorker(str(Path(temp_dir) / "missing.ini"))
            snapshot_path = str(Path(temp_dir) / "map.json")
            request_path = str(Path(temp_dir) / "requests.json")
            config = self._config_from_lines(
                temp_dir,
                [
                    "DeviceInstance=60",
                    "AutoGridService=com.victronenergy.system",
                    "AutoGridL1Path=/Ac/Grid/L1/Power",
                    "AutoGridL2Path=/Ac/Grid/L2/Power",
                    "AutoGridL3Path=/Ac/Grid/L3/Power",
                    "AutoBatteryService=com.victronenergy.battery.tty0",
                    "AutoBatteryServicePrefix=com.victronenergy.battery",
                    "AutoBatterySocPath=/Soc",
                    "AutoPvService=com.victronenergy.pvinverter.http_48",
                    "AutoPvServicePrefix=com.victronenergy.pvinverter",
                    "AutoPvPath=/Ac/Power",
                    "AutoUseDcPv=0",
                    "DbusIntrospectionFullScanIntervalSeconds=60",
                    "DbusIntrospectionMinJobIntervalSeconds=0.1",
                    "DbusIntrospectionMinMemAvailableKb=1",
                    "DbusIntrospectionPvQuietHours=00:00-23:59",
                ],
            )
            service_names = ["com.victronenergy.pvinverter.http_48", "com.victronenergy.battery.tty0"]
            self._seed_gateway_services(temp_dir, service_names)
            worker = DbusIntrospectionWorker(config, snapshot_path, request_path, parent_pid=os.getpid())
            self.assertTrue(worker._as_bool(None, True))
            self.assertTrue(worker._parent_alive())
            with patch.object(worker_module.os, "kill", side_effect=ProcessLookupError):
                self.assertFalse(worker._parent_alive())
            with patch.object(worker_module.os, "kill", side_effect=RuntimeError("perm")):
                self.assertTrue(worker._parent_alive())
            worker._enqueue_background_jobs(100.0)
            self.assertTrue(any(job.source == "pv" and job.due_at > 100.0 for job in worker._jobs.values()))
            self.assertEqual(worker._explicit_or_prefixed_services("missing", "com.victronenergy.battery", service_names), [])
            worker.pv_quiet_hours = "bad"
            self.assertFalse(worker._pv_quiet_now(100.0))
            worker.pv_quiet_hours = "bad-bad"
            self.assertFalse(worker._pv_quiet_now(100.0))
            worker.pv_quiet_hours = ""
            self.assertFalse(worker._pv_quiet_now(100.0))
            worker.pv_quiet_hours = "22:00-05:00"
            self.assertTrue(worker._pv_quiet_now(23 * 3600.0))
            self.assertFalse(worker._pv_quiet_now(12 * 3600.0))
            self.assertEqual(worker._hhmm_minutes("23:59"), 1439)
            with self.assertRaises(ValueError):
                worker._hhmm_minutes("24:00")
            worker.load_avg_max = 0.0
            self.assertFalse(worker._load_gate_active())
            worker.load_avg_max = 1.0
            with patch.object(worker_module.os, "getloadavg", side_effect=RuntimeError("load")):
                self.assertFalse(worker._load_gate_active())
            worker.min_mem_available_kb = 0
            self.assertFalse(worker._memory_gate_active())
            worker.min_mem_available_kb = 1
            with patch("builtins.open", side_effect=RuntimeError("mem")):
                self.assertFalse(worker._memory_gate_active())
            meminfo = MagicMock()
            meminfo.__enter__.return_value = iter(["MemAvailable: 0 kB\n"])
            meminfo.__exit__.return_value = None
            with patch("builtins.open", return_value=meminfo):
                self.assertTrue(worker._memory_gate_active())
            meminfo = MagicMock()
            meminfo.__enter__.return_value = iter(["Other: 1\n"])
            meminfo.__exit__.return_value = None
            with patch("builtins.open", return_value=meminfo):
                self.assertFalse(worker._memory_gate_active())
            worker._system_bus = SimpleNamespace(close=MagicMock(side_effect=RuntimeError("close")))
            worker._reset_system_bus()

            disabled = DbusIntrospectionWorker(self._config(temp_dir, "DbusIntrospectionEnabled=0\n"), snapshot_path, request_path)
            self.assertEqual(disabled.run_forever(), 0)
            self.assertEqual(json.loads(Path(snapshot_path).read_text(encoding="utf-8"))["worker_state"], "disabled")

            argv = ["venus_evcharger_dbus_introspection_worker.py", self._config(temp_dir, "DbusIntrospectionEnabled=0\n"), snapshot_path, request_path]
            with patch.object(sys, "argv", argv), self.assertRaises(SystemExit) as exit_ctx:
                runpy.run_module("venus_evcharger_dbus_introspection_worker", run_name="__main__")
            self.assertEqual(exit_ctx.exception.code, 0)

    def test_worker_request_and_error_edges(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = str(Path(temp_dir) / "map.json")
            request_path = str(Path(temp_dir) / "requests.json")
            config = self._config(temp_dir)
            worker = DbusIntrospectionWorker(config, snapshot_path, request_path)
            Path(request_path).write_text(json.dumps({"requests": ["bad", {}, {"service": "svc"}, {"service": "svc", "path": "/p", "priority": 5}]}), encoding="utf-8")
            worker._enqueue_request_file_jobs(100.0)
            self.assertIn(("svc", "/p"), worker._jobs)
            self.assertEqual(json.loads(Path(request_path).read_text(encoding="utf-8"))["requests"], [])
            Path(request_path).write_text(json.dumps({"requests": {}}), encoding="utf-8")
            worker._enqueue_request_file_jobs(101.0)
            with patch.object(worker_module, "write_text_atomically", side_effect=RuntimeError("clear")):
                with self.assertLogs(level="DEBUG") as log_ctx:
                    worker._clear_request_payload()
            self.assertIn("Unable to clear DBus introspection request payload", "\n".join(log_ctx.output))
            worker._enqueue_job("svc", "/p", priority=50, source="old", reason="old", due_at=200.0)
            worker._enqueue_job("svc", "/p", priority=60, source="later", reason="later", due_at=300.0)
            self.assertEqual(worker._jobs[("svc", "/p")].source, "request")
            worker._enqueue_job("svc", "/same", priority=30, source="background", reason="old", due_at=100.0)
            worker._enqueue_job("svc", "/same", priority=100, source="request", reason="fresh", due_at=100.0)
            self.assertEqual(worker._jobs[("svc", "/same")].source, "request")
            worker._jobs.pop(("svc", "/same"))
            worker._enqueue_job("svc", "/old", priority=30, source="background", reason="old", due_at=90.0)
            worker._enqueue_job("svc", "/high", priority=100, source="request", reason="fresh", due_at=100.0)
            self.assertEqual(worker._next_due_job(100.0).key, ("svc", "/high"))
            self.assertIsNone(DbusIntrospectionWorker(config, snapshot_path, request_path)._next_due_job(100.0))

            class MissingError(Exception):
                def get_dbus_name(self):
                    return "org.freedesktop.DBus.Error.ServiceUnknown"

            class BadNameError(Exception):
                def get_dbus_name(self):
                    raise RuntimeError("name")

            job = worker._jobs[("svc", "/p")]
            missing = worker._error_finding(job, MissingError("missing"), 100.0)
            self.assertEqual(missing["status"], "known-missing")
            self.assertTrue(worker._is_missing_dbus_error(BadNameError("Unknown")))
            worker.retry_base_seconds = 5.0
            worker.retry_max_seconds = 6.0
            backoff = worker._error_finding(job, RuntimeError("NoReply"), 100.0)
            self.assertEqual(backoff["retry_after"], 106.0)
            worker._services["svc"] = {"paths": []}
            worker._store_finding("svc", "/ignored", {"status": "fresh"})
            self.assertEqual(worker._services["svc"]["last_updated_at"], worker._services["svc"]["last_updated_at"])
            worker._services["gone"] = {"paths": {"/p": {"status": "fresh"}}}
            worker._failures[("gone", "/p")] = 2
            worker._jobs[("gone", "/p")] = IntrospectionJob("gone", "/p", 30, "background", "old", 100.0, 1.0)
            worker._jobs[("gone-request", "/p")] = IntrospectionJob("gone-request", "/p", 100, "request", "keep", 100.0, 1.0)
            worker._prune_missing_services(["svc", "gone-request"])
            self.assertNotIn("gone", worker._services)
            self.assertNotIn(("gone", "/p"), worker._failures)
            self.assertNotIn(("gone", "/p"), worker._jobs)
            self.assertIn(("gone-request", "/p"), worker._jobs)
            with patch.object(worker_module, "write_text_atomically", side_effect=RuntimeError("snapshot")):
                self.assertEqual(worker._write_snapshot("x")["worker_state"], "x")
            listing_worker = DbusIntrospectionWorker(config, snapshot_path, request_path)
            self._seed_gateway_services(temp_dir, ["svc"])
            self.assertEqual(listing_worker._list_dbus_services(), ["svc"])
            with self.assertRaisesRegex(RuntimeError, "Direct DBus access is disabled"):
                listing_worker._get_system_bus()
            bus_fail_worker = DbusIntrospectionWorker(config, snapshot_path, request_path)
            bus_fail_worker._list_dbus_services = MagicMock(side_effect=RuntimeError("dbus down"))
            bus_fail_worker._reset_system_bus = MagicMock()
            bus_fail_worker._enqueue_background_jobs(100.0)
            bus_fail_worker._reset_system_bus.assert_called_once()

            branch_config = self._config_from_lines(
                temp_dir,
                [
                    "DeviceInstance=60",
                    "AutoGridService=com.victronenergy.system",
                    "AutoGridL1Path=",
                    "AutoGridL2Path=",
                    "AutoGridL3Path=",
                    "AutoBatteryServicePrefix=com.victronenergy.battery",
                    "AutoBatterySocPath=",
                    "AutoPvServicePrefix=com.victronenergy.pvinverter",
                    "AutoPvPath=",
                    "AutoUseDcPv=1",
                    "AutoDcPvService=",
                    "AutoDcPvPath=/Dc/Pv/Power",
                    "DbusIntrospectionMinMemAvailableKb=0",
                ],
            )
            branch_worker = DbusIntrospectionWorker(branch_config, snapshot_path, request_path)
            self.assertEqual(
                branch_worker._background_specs(["com.victronenergy.battery.tty0", "com.victronenergy.pvinverter.http_1"]),
                [],
            )

    def test_worker_run_forever_loop_stops_on_parent_and_run_once_throttle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = str(Path(temp_dir) / "map.json")
            request_path = str(Path(temp_dir) / "requests.json")
            worker = DbusIntrospectionWorker(self._config(temp_dir), snapshot_path, request_path)
            calls = []

            def fake_run_once():
                calls.append("run")
                worker.stop()

            worker.run_once = fake_run_once
            with patch.object(worker_module.time, "sleep", MagicMock()), patch.object(worker_module.signal, "signal", MagicMock()):
                self.assertEqual(worker.run_forever(), 0)
            self.assertEqual(calls, ["run"])

            self._seed_gateway_services(temp_dir, ["com.victronenergy.pvinverter.http_48", "com.victronenergy.battery.tty0"])
            throttled = DbusIntrospectionWorker(self._config(temp_dir), snapshot_path, request_path)
            throttled.min_job_interval_seconds = 10.0
            throttled.run_once(100.0)
            self.assertGreater(len(throttled._jobs), 0)
            throttled._last_service_names = ["com.victronenergy.pvinverter.http_48"]
            throttled._last_full_scan_at = 100.0
            throttled._jobs.clear()
            throttled._last_job_at = 100.0
            snapshot = throttled.run_once(101.0)
            self.assertEqual(snapshot["worker_state"], "running")
            throttled._last_job_at = 0.0
            snapshot = throttled.run_once(102.0)
            self.assertEqual(snapshot["worker_state"], "running")

    def test_supervisor_edges(self):
        class FakeProcess:
            pid = 123

            def __init__(self, poll_value=None):
                self.poll_value = poll_value
                self.terminated = False
                self.killed = False

            def poll(self):
                return self.poll_value

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True

        svc = SimpleNamespace(
            dbus_introspection_enabled=True,
            dbus_introspection_restart_seconds=30.0,
            dbus_introspection_snapshot_path="/snapshot",
            dbus_introspection_request_path="/request",
            _dbus_introspection_worker_process=None,
            _dbus_introspection_worker_last_start_at=0.0,
            _ensure_worker_state=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _dbus_introspection_worker_path=MagicMock(return_value="/worker.py"),
            _config_path=MagicMock(return_value="/config.ini"),
            _warning_throttled=MagicMock(),
        )
        supervisor = DbusIntrospectionSupervisor(svc)
        supervisor.stop_worker()
        process = FakeProcess()
        svc._dbus_introspection_worker_process = process
        supervisor.stop_worker()
        self.assertTrue(process.terminated)
        process = FakeProcess()
        svc._dbus_introspection_worker_process = process
        supervisor.stop_worker(force=True)
        self.assertTrue(process.killed)
        svc._dbus_introspection_worker_process = FakeProcess(poll_value=0)
        supervisor.stop_worker()
        self.assertIsNone(svc._dbus_introspection_worker_process)
        failing_process = FakeProcess()
        failing_process.terminate = MagicMock(side_effect=RuntimeError("stop"))
        svc._dbus_introspection_worker_process = failing_process
        supervisor.stop_worker()

        svc._dbus_introspection_worker_process = FakeProcess()
        supervisor.ensure_worker_process(100.0)
        svc._dbus_introspection_worker_process = None
        svc._dbus_introspection_worker_last_start_at = 90.0
        supervisor.ensure_worker_process(100.0)
        svc._dbus_introspection_worker_last_start_at = 0.0
        fake_popen_process = FakeProcess()
        with patch.object(worker_module.subprocess if False else __import__("subprocess"), "Popen", return_value=fake_popen_process) as popen:
            supervisor.ensure_worker_process(100.0)
        self.assertIs(svc._dbus_introspection_worker_process, fake_popen_process)
        self.assertEqual(popen.call_args[0][0][2], "/worker.py")
        svc._dbus_introspection_worker_process = None
        with patch.object(__import__("subprocess"), "Popen", side_effect=RuntimeError("start")):
            supervisor.ensure_worker_process(200.0)
        svc._warning_throttled.assert_called()
        svc.dbus_introspection_enabled = False
        svc._dbus_introspection_worker_process = FakeProcess()
        supervisor.ensure_worker_process(300.0)
        self.assertTrue(svc._dbus_introspection_worker_process.terminated)

    def test_helper_legacy_child_parser_still_handles_xml(self):
        self.assertEqual(_AutoInputHelperSourceDbusMixin._child_nodes_from_introspection("<node><node name='L1'/></node>"), ["L1"])


if __name__ == "__main__":
    unittest.main()
