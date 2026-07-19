# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from collections.abc import MutableMapping
from unittest.mock import patch

from venus_evcharger.publish.dbus import DbusPublishController
from venus_evcharger.publish.dbus_config import DbusPublishConfig
from venus_evcharger.publish.dbus_core import DbusPublishCore
from venus_evcharger.publish.dbus_diagnostics import DbusPublishDiagnostics
from venus_evcharger.publish.dbus_diagnostics_contracts import DiagnosticSnapshot
from venus_evcharger.publish.dbus_learned import DbusPublishLearned
from venus_evcharger.publish.dbus_measurements import DbusMeasurementPublisher
from venus_evcharger.publish.dbus_runtime_view import DbusRuntimeView
from venus_evcharger.publish.dbus_shared import DbusValueStore, PublishStateEntry


class _PublishServiceHarness:
    """Minimal typed implementation of the mandatory publish-service port."""

    def __init__(self) -> None:
        self._dbusservice: DbusValueStore = {}
        self._dbus_publish_state: MutableMapping[str, PublishStateEntry] = {}
        self._dbus_live_publish_interval_seconds = 1.0
        self._dbus_slow_publish_interval_seconds = 5.0
        self._last_health_code = 0
        self._last_health_reason = "init"
        self._last_successful_update_at: float | None = None
        self._last_pv_at: float | None = None
        self._last_battery_soc_at: float | None = None
        self._last_grid_at: float | None = None
        self._last_dbus_ok_at: float | None = None
        self._recovery_attempts = 0
        self.last_status = 0
        self.started_at = 0.0
        self.virtual_set_current = 0.0

    def _is_update_stale(self, now: float) -> bool:
        del now
        return False


def _service() -> _PublishServiceHarness:
    return _PublishServiceHarness()


class DbusPublishCompositionContractTests(unittest.TestCase):
    def test_controller_owns_linear_components_with_explicit_shared_dependencies(self) -> None:
        controller = DbusPublishController(_service(), lambda _timestamp, _now: 0.0)

        for component_type in (
            DbusPublishController,
            DbusPublishCore,
            DbusPublishLearned,
            DbusRuntimeView,
            DbusPublishConfig,
            DbusMeasurementPublisher,
            DbusPublishDiagnostics,
        ):
            with self.subTest(component=component_type.__name__):
                self.assertEqual(component_type.__bases__, (object,))
        self.assertIs(controller.config.core, controller.core)
        self.assertIs(controller.config.learned, controller.learned)
        self.assertIs(controller.config.runtime_view, controller.runtime_view)
        self.assertIs(controller.measurements.core, controller.core)
        self.assertIs(controller.diagnostics.core, controller.core)
        self.assertIs(controller.diagnostics.learned, controller.learned)
        self.assertIs(controller.diagnostics.runtime_views.sources, controller.runtime_view)
        self.assertIs(controller.diagnostics.runtime_views.decisions, controller.runtime_view)
        self.assertIs(controller.diagnostics.runtime_views.phases, controller.runtime_view)
        self.assertIs(controller.diagnostics.runtime_views.summary, controller.runtime_view)
        self.assertFalse(hasattr(controller.diagnostics.schedule, "sources"))

    def test_public_facade_delegates_each_publish_use_case_to_its_owner(self) -> None:
        controller = DbusPublishController(_service(), lambda _timestamp, _now: 0.0)
        phase_data = {
            "L1": {"power": 100.0, "current": 1.0, "voltage": 230.0},
            "L2": {"power": 0.0, "current": 0.0, "voltage": 230.0},
            "L3": {"power": 0.0, "current": 0.0, "voltage": 230.0},
        }
        snapshot = DiagnosticSnapshot(counters={"status": 1}, ages={"auto_stale_seconds": 2.0})

        with patch.object(controller.core, "ensure_state") as ensure_state:
            controller.ensure_state()
        ensure_state.assert_called_once_with()

        with patch.object(controller.core, "publish_path", return_value=True) as publish_path:
            self.assertTrue(controller.publish_path("/Mode", 2, 10.0, 3.0, True))
        publish_path.assert_called_once_with("/Mode", 2, 10.0, 3.0, True)

        with patch.object(controller.core, "publish_field", return_value=True) as publish_field:
            self.assertTrue(controller.publish_field("mode", 2, 11.0, 4.0, True))
        publish_field.assert_called_once_with("mode", 2, 11.0, 4.0, True)

        with patch.object(controller.core, "bump_update_index") as bump_update_index:
            controller.bump_update_index(12.0)
        bump_update_index.assert_called_once_with(12.0)

        with patch.object(controller.measurements, "publish_live_measurements", return_value=True) as publish_live:
            self.assertTrue(controller.publish_live_measurements(100.0, 230.0, 1.0, phase_data, 13.0))
        publish_live.assert_called_once_with(100.0, 230.0, 1.0, phase_data, 13.0)

        with patch.object(
            controller.measurements,
            "publish_energy_time_measurements",
            return_value=True,
        ) as publish_energy:
            self.assertTrue(controller.publish_energy_time_measurements(4.0, {"L1": 4.0}, 60, 1.0, 14.0))
        publish_energy.assert_called_once_with(4.0, {"L1": 4.0}, 60, 1.0, 14.0)

        with patch.object(controller.config, "publish_config_paths", return_value=True) as publish_config:
            self.assertTrue(controller.publish_config_paths(1, 15.0))
        publish_config.assert_called_once_with(1, 15.0)

        with patch.object(controller.diagnostics, "snapshot", return_value=snapshot) as diagnostic_snapshot:
            self.assertIs(controller.diagnostic_snapshot(16.0), snapshot)
        diagnostic_snapshot.assert_called_once_with(16.0)

        with patch.object(
            controller.diagnostics,
            "counter_values",
            return_value={"status": 1},
        ) as counter_values, patch.object(
            controller.diagnostics,
            "age_values",
            return_value={"auto_stale_seconds": 2.0},
        ) as age_values:
            self.assertEqual(controller.diagnostics.snapshot(16.5), snapshot)
        counter_values.assert_called_once_with(16.5)
        age_values.assert_called_once_with(16.5)

        with patch.object(controller.diagnostics, "publish_diagnostic_paths", return_value=True) as publish_diagnostics:
            self.assertTrue(controller.publish_diagnostic_paths(17.0))
        publish_diagnostics.assert_called_once_with(17.0)


if __name__ == "__main__":
    unittest.main()
