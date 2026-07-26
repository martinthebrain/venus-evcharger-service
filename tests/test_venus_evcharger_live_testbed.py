# SPDX-License-Identifier: GPL-3.0-or-later
import json
import subprocess
import sys
import tempfile
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
        client.load_health.return_value = {"state": "ok"}
        operations = MagicMock()
        operations.read_gx_relay_state.side_effect = [None, 1]
        with patch.object(module, "GatewayClient", return_value=client), patch.object(
            module,
            "GatewayOperationsClient",
            return_value=operations,
        ), patch.object(module.time, "monotonic", side_effect=[0.0, 0.0, 1.0, 0.0, 0.0]), patch.object(
            module.time,
            "sleep",
        ):
            payload = module.probe_real_cerbo(0.1)

        self.assertTrue(payload["ok"])
        self.assertFalse(payload["skipped"])
        self.assertEqual(len(payload["probes"]), 2)
        self.assertTrue(payload["probes"][0]["skipped"])
        self.assertEqual(payload["probes"][1]["value"], 1)

    def test_gateway_probe_reports_value_and_unavailable_state(self) -> None:
        module = _load_testbed_module()
        operations = MagicMock()
        operations.read_gx_relay_state.return_value = 1
        result = module._read_gateway_relay(operations, 0, 0.1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], 1)

        operations.read_gx_relay_state.return_value = None
        with patch.object(module.time, "monotonic", side_effect=[0.0, 0.0, 1.0]), patch.object(
            module.time,
            "sleep",
        ):
            result = module._read_gateway_relay(operations, 1, 0.1)
        self.assertEqual(result["error"], "relay-state-unavailable")
        self.assertTrue(result["skipped"])

    def test_gateway_probe_without_gateway_is_skipped(self) -> None:
        module = _load_testbed_module()
        client = MagicMock()
        client.load_health.return_value = {}
        with patch.object(module, "GatewayClient", return_value=client):
            payload = module.probe_real_cerbo(0.1, "/run/test")
        self.assertTrue(payload["skipped"])
        self.assertIn("health snapshot", payload["reason"])

    def test_probe_cli_accepts_gateway_run_dir(self) -> None:
        module = _load_testbed_module()
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(module, "GatewayClient") as client_type:
            client_type.return_value.load_health.return_value = {}
            self.assertEqual(module.main(["probe-real", "--gateway-run-dir", temp_dir, "--timeout", "0.1"]), 0)
