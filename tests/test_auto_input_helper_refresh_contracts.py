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
        self.assertEqual([request[0] for request in gateway.requests], ["all", "topology"])
        self.assertTrue(gateway.requests[0][2])
        self.assertEqual(snapshots.refresh_all_calls, 1)

    def test_existing_snapshots_need_no_startup_request(self) -> None:
        gateway = FakeEnergyGateway()
        missing = MeasuredValue(None, 0.0, "unknown", 0.0)
        gateway.inputs = EnergyInputsSnapshot(1, 100.0, 1, missing, missing, missing)
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
        self.assertEqual(gateway.requests[-1][0], "topology")
        coordinator.reset()
        self.assertEqual(gateway.reset_calls, 1)
        stopped = True
        self.assertFalse(coordinator.timer_tick())
        self.assertFalse(coordinator.refresh())


if __name__ == "__main__":
    unittest.main()
