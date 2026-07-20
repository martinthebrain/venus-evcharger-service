# SPDX-License-Identifier: GPL-3.0-or-later
import json
import subprocess
import sys
import tempfile
import time
import unittest
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch


TESTBED_SCRIPT = Path("scripts/dev/venus_cerbo_testbed.py")


def _load_testbed_module() -> ModuleType:
    loader = SourceFileLoader("venus_cerbo_testbed_under_test", str(TESTBED_SCRIPT))
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


class TestVenusEvchargerLiveTestbed(unittest.TestCase):
    def test_simulated_unplug_replug_scenario_is_machine_readable(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(TESTBED_SCRIPT), "simulate", "unplug-replug"],
            check=True,
            capture_output=True,
            text=True,
        )

        payload = json.loads(completed.stdout)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["kind"], "venus-cerbo-testbed")
        self.assertEqual(payload["scenario"], "unplug-replug")
        self.assertTrue(payload["expectations"]["session_energy_should_reset"])
        self.assertIn("/Session/Energy", {item["path"] for item in payload["services"]})

    def test_real_probe_without_dbus_cli_skips_or_reports_probe_results(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(TESTBED_SCRIPT), "probe-real", "--timeout", "0.1"],
            check=False,
            capture_output=True,
            text=True,
        )

        payload = json.loads(completed.stdout)

        self.assertEqual(payload["kind"], "venus-cerbo-testbed")
        self.assertIn(payload["mode"], {"probe-real"})
        self.assertIn("probes", payload)

    def test_real_probe_treats_missing_relay_paths_as_skipped(self) -> None:
        module = _load_testbed_module()

        client = MagicMock()
        client.send.return_value = {"ok": True}
        client.load_cache.return_value = {
            "values": {
                module.dbus_path_key(service, path): {
                    "status": "unavailable" if index < 3 else "fresh",
                    "value": index,
                    "last_error": "missing" if index < 3 else "",
                    "confirmed_at": time.time() + 1.0,
                    "error_at": time.time() + 1.0,
                }
                for index, (service, path) in enumerate(module.CERBO_READ_ONLY_PROBES)
            }
        }
        with patch.object(module, "GatewayClient", return_value=client):
            payload = module.probe_real_cerbo(0.1)

        self.assertTrue(payload["ok"])
        self.assertFalse(payload["skipped"])
        self.assertEqual(len(payload["probes"]), 4)

    def test_gateway_probe_reports_rejection_and_timeout(self) -> None:
        module = _load_testbed_module()
        rejected = MagicMock()
        rejected.send.return_value = {"ok": False, "error": "busy"}
        self.assertEqual(module._read_gateway_value(rejected, "service", "/Path", 0.1)["error"], "busy")

        timed_out = MagicMock()
        timed_out.send.return_value = {"ok": True}
        timed_out.load_cache.return_value = {"values": {}}
        with patch.object(module.time, "sleep"):
            result = module._read_gateway_value(timed_out, "service", "/Path", 0.1)
        self.assertEqual(result["error"], "timeout")
        self.assertFalse(result["skipped"])

    def test_gateway_probe_without_gateway_is_skipped(self) -> None:
        module = _load_testbed_module()
        client = MagicMock()
        client.send.return_value = {"ok": False, "error": "offline"}
        with patch.object(module, "GatewayClient", return_value=client):
            payload = module.probe_real_cerbo(0.1, "/run/test")
        self.assertTrue(payload["skipped"])
        self.assertIn("offline", payload["reason"])

    def test_gateway_probe_ignores_pending_cache_entry_until_timeout(self) -> None:
        module = _load_testbed_module()
        self.assertIsNone(
            module._probe_result_from_entry(
                "service",
                "/Path",
                {"status": "pending", "confirmed_at": time.time() + 1.0},
                requested_at=time.time(),
            )
        )
        self.assertIsNone(
            module._probe_result_from_entry(
                "service",
                "/Path",
                {"status": "fresh", "confirmed_at": 1.0},
                requested_at=2.0,
            )
        )
        self.assertEqual(module._float_value("4.5"), 4.5)
        self.assertEqual(module._float_value(object()), 0.0)

    def test_probe_cli_accepts_gateway_run_dir(self) -> None:
        module = _load_testbed_module()
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(module, "GatewayClient") as client_type:
            client_type.return_value.send.return_value = {"ok": False, "error": "offline"}
            self.assertEqual(module.main(["probe-real", "--gateway-run-dir", temp_dir, "--timeout", "0.1"]), 0)
