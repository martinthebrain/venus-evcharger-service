# SPDX-License-Identifier: GPL-3.0-or-later
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import venus_evcharger_dbus_introspection_worker as worker_module
from venus_evcharger.dbus_introspection import path_unusable_until, request_introspection
from venus_evcharger_dbus_introspection_worker import DbusIntrospectionWorker


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
            "DbusIntrospectionFullScanIntervalSeconds=60\n"
            "DbusIntrospectionMinJobIntervalSeconds=0.1\n"
            "DbusIntrospectionMinMemAvailableKb=0\n"
            "DbusIntrospectionPvQuietHours=\n"
            f"{extra}",
            encoding="utf-8",
        )
        return str(path)

    def test_run_once_trickles_background_introspection_and_writes_mapping(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = str(Path(temp_dir) / "map.json")
            request_path = str(Path(temp_dir) / "requests.json")
            bus = _FakeBus(["com.victronenergy.pvinverter.http_48"])
            bus.objects[("com.victronenergy.pvinverter.http_48", "/Ac/Power")] = (
                "<node><interface name='com.victronenergy.BusItem'/><node name='L1'/></node>"
            )
            with patch.object(worker_module, "dbus", _FakeDbus(bus)):
                worker = DbusIntrospectionWorker(self._config(temp_dir), snapshot_path, request_path)
                worker.run_once(100.0)
                worker.run_once(101.0)

            snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
            finding = snapshot["services"]["com.victronenergy.pvinverter.http_48"]["paths"]["/Ac/Power"]
            self.assertEqual(finding["status"], "fresh")
            self.assertEqual(finding["interfaces"], ["com.victronenergy.BusItem"])
            self.assertIn(("com.victronenergy.pvinverter.http_48", "/Ac/Power", False), bus.get_calls)

    def test_priority_request_runs_before_background_queue(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = str(Path(temp_dir) / "map.json")
            request_path = str(Path(temp_dir) / "requests.json")
            bus = _FakeBus(["com.victronenergy.pvinverter.http_48"])
            bus.objects[("svc.priority", "/Fast")] = "<node><interface name='x.Fast'/></node>"
            bus.objects[("com.victronenergy.pvinverter.http_48", "/Ac/Power")] = "<node/>"
            request_introspection(request_path, "svc.priority", "/Fast", priority=10, now=100.0)
            with patch.object(worker_module, "dbus", _FakeDbus(bus)):
                worker = DbusIntrospectionWorker(self._config(temp_dir), snapshot_path, request_path)
                worker.run_once(100.0)

            snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
            self.assertIn("svc.priority", snapshot["services"])
            self.assertNotIn("com.victronenergy.pvinverter.http_48", snapshot["services"])

    def test_resource_gate_defers_work_without_dropping_queue(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = str(Path(temp_dir) / "map.json")
            request_path = str(Path(temp_dir) / "requests.json")
            bus = _FakeBus(["com.victronenergy.pvinverter.http_48"])
            bus.objects[("com.victronenergy.pvinverter.http_48", "/Ac/Power")] = "<node/>"
            with patch.object(worker_module, "dbus", _FakeDbus(bus)):
                worker = DbusIntrospectionWorker(
                    self._config(temp_dir, "DbusIntrospectionLoadAvgMax=1\n"),
                    snapshot_path,
                    request_path,
                )
                with patch.object(worker_module.os, "getloadavg", return_value=(10.0, 0.0, 0.0)):
                    snapshot = worker.run_once(100.0)

            self.assertEqual(snapshot["worker_state"], "deferred-resource-gate")
            self.assertGreater(snapshot["queue_depth"], 0)
            self.assertEqual(worker._services, {})

    def test_unresponsive_error_sets_retry_backoff_and_cache_skip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = str(Path(temp_dir) / "map.json")
            request_path = str(Path(temp_dir) / "requests.json")
            bus = _FakeBus(["com.victronenergy.pvinverter.http_48"])
            bus.objects[("com.victronenergy.pvinverter.http_48", "/Ac/Power")] = RuntimeError("NoReply")
            with patch.object(worker_module, "dbus", _FakeDbus(bus)):
                worker = DbusIntrospectionWorker(self._config(temp_dir), snapshot_path, request_path)
                worker.run_once(100.0)
                worker.run_once(101.0)

            snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
            skip, reason = path_unusable_until(snapshot, "com.victronenergy.pvinverter.http_48", "/Ac/Power", now=101.0)
            self.assertTrue(skip)
            self.assertEqual(reason, "unresponsive-backoff")


if __name__ == "__main__":
    unittest.main()
