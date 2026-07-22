# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.update.software_update_run import _SoftwareUpdateRun


class SoftwareUpdateGatewayPressureContractTests(unittest.TestCase):
    def test_optional_update_work_continues_without_pressure(self) -> None:
        service = SimpleNamespace(_software_update_boot_auto_due_at=100.0)
        policy = MagicMock()
        policy.should_throttle_optional_work.return_value = False

        with patch(
            "venus_evcharger.update.software_update_run.service_gateway_pressure_policy",
            return_value=policy,
        ) as resolve:
            deferred = _SoftwareUpdateRun._defer_optional_software_update_work(
                service,
                200.0,
                "_software_update_boot_auto_due_at",
            )

        self.assertFalse(deferred)
        self.assertEqual(service._software_update_boot_auto_due_at, 100.0)
        resolve.assert_called_once_with(service)
        policy.optional_work_interval_seconds.assert_not_called()

    def test_optional_update_work_uses_gateway_pressure_budget(self) -> None:
        service = SimpleNamespace(_software_update_boot_auto_due_at=100.0)
        policy = MagicMock()
        policy.should_throttle_optional_work.return_value = True
        policy.optional_work_interval_seconds.return_value = 720.0

        with patch(
            "venus_evcharger.update.software_update_run.service_gateway_pressure_policy",
            return_value=policy,
        ):
            deferred = _SoftwareUpdateRun._defer_optional_software_update_work(
                service,
                200.0,
                "_software_update_boot_auto_due_at",
            )

        self.assertTrue(deferred)
        self.assertEqual(service._software_update_boot_auto_due_at, 920.0)
        policy.optional_work_interval_seconds.assert_called_once_with(60.0)


if __name__ == "__main__":
    unittest.main()
