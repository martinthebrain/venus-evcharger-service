# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
import sys
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen


def _load_mock_shelly_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "dev" / "mock_shelly_rpc.py"
    spec = importlib.util.spec_from_file_location("mock_shelly_rpc", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestMockShellyRpc(unittest.TestCase):
    def setUp(self):
        self.module = _load_mock_shelly_module()
        self.server = self.module.MockShellyRpcServer(
            ("127.0.0.1", 0),
            self.module.MockShellyState(
                relay_on=False,
                apower=0.0,
                current=0.0,
                voltage=230.0,
                total_energy_wh=1200.0,
            ),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)

    def _read_json(self, path: str):
        with urlopen(f"{self.base_url}{path}", timeout=2.0) as response:  # noqa: S310
            return response.read().decode("utf-8")

    def test_device_info_and_switch_status_endpoints_return_expected_json(self):
        device_info = self._read_json("/rpc/Shelly.GetDeviceInfo")
        status = self._read_json("/rpc/Switch.GetStatus?id=0")

        self.assertIn('"name": "Mock Shelly Relay"', device_info)
        self.assertIn('"model": "Shelly 1PM Gen4"', device_info)
        self.assertIn('"output": false', status)
        self.assertIn('"voltage": 230.0', status)
        self.assertIn('"total": 1200.0', status)

    def test_switch_set_and_admin_state_update_runtime_values(self):
        self._read_json("/rpc/Switch.Set?id=0&on=true")
        status = self._read_json("/rpc/Switch.GetStatus?id=0")
        self.assertIn('"output": true', status)

        admin_state = self._read_json("/__admin/state?apower=2300&current=10&total_energy_wh=12500")
        self.assertIn('"apower": 2300.0', admin_state)
        self.assertIn('"current": 10.0', admin_state)
        self.assertIn('"total_energy_wh": 12500.0', admin_state)

        status = self._read_json("/rpc/Switch.GetStatus?id=0")
        self.assertIn('"apower": 2300.0', status)
        self.assertIn('"current": 10.0', status)
        self.assertIn('"total": 12500.0', status)

    def test_fault_endpoint_can_force_http_500_until_reset(self):
        self._read_json("/__admin/fault?mode=http500")
        with self.assertRaises(HTTPError) as error_context:
            self._read_json("/rpc/Switch.GetStatus?id=0")
        error_context.exception.close()

        self._read_json("/__admin/reset")
        status = self._read_json("/rpc/Switch.GetStatus?id=0")
        self.assertIn('"output": false', status)
