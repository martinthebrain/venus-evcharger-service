# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest

from tests.support.auto_input_helper import FakeEnergyGateway, FakeSnapshots
from venus_evcharger.inputs.helper.energy_gateway import EnergyRefreshCoordinator
from venus_evcharger.ipc.energy import EnergyInputsSnapshot, EnergyTopologySnapshot, MeasuredValue


class AutoInputHelperRefreshContracts(unittest.TestCase):
    def test_startup_requests_missing_inputs_and_topology_then_refreshes_snapshot(self) -> None:
        gateway = FakeEnergyGateway()
        snapshots = FakeSnapshots()
        coordinator = EnergyRefreshCoordinator(gateway, snapshots, lambda: False)
        self.assertFalse(coordinator.refresh())
        self.assertEqual(
            gateway.requests,
            [
                ("all", "initial semantic energy snapshot", True),
                ("topology", "initial semantic energy topology", False),
            ],
        )
        self.assertEqual(gateway.input_refreshes, 1)
        self.assertEqual(gateway.topology_refreshes, 1)
        self.assertEqual(snapshots.refresh_all_calls, 1)

    def test_existing_snapshots_need_no_startup_request(self) -> None:
        gateway = FakeEnergyGateway()
        missing = MeasuredValue(None, 0.0, "unknown", 0.0)
        gateway.inputs = EnergyInputsSnapshot(1, 100.0, 1, missing, missing, missing, missing)
        gateway.topology = EnergyTopologySnapshot(1, 100.0, ())
        snapshots = FakeSnapshots()
        coordinator = EnergyRefreshCoordinator(gateway, snapshots, lambda: False)
        self.assertFalse(coordinator.refresh())
        self.assertEqual(gateway.requests, [])

    def test_periodic_topology_refresh_stop_and_reset(self) -> None:
        gateway = FakeEnergyGateway()
        snapshots = FakeSnapshots()
        stopped = False
        coordinator = EnergyRefreshCoordinator(gateway, snapshots, lambda: stopped)
        self.assertTrue(coordinator.timer_tick())
        self.assertEqual(
            gateway.requests[-1],
            ("topology", "periodic semantic topology refresh", False),
        )
        self.assertEqual(gateway.topology_refreshes, 1)
        coordinator.reset()
        self.assertEqual(gateway.reset_calls, 1)
        stopped = True
        self.assertFalse(coordinator.timer_tick())
        self.assertFalse(coordinator.refresh())
        self.assertEqual(gateway.topology_refreshes, 1)
        self.assertEqual(gateway.input_refreshes, 0)
        self.assertEqual(snapshots.refresh_all_calls, 0)


if __name__ == "__main__":
    unittest.main()
