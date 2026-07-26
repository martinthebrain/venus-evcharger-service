# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from collections.abc import Mapping
from unittest.mock import ANY, patch

from tests.gateway_diagnostics_fixtures import gateway_diagnostics_reader
from venus_evcharger.bootstrap.publication import EvcsPublicationOwner
from venus_evcharger.ports.gateway_publication import (
    CompanionServiceIdentity,
    EvcsServiceIdentity,
    PublicationPriority,
    PublicationReceipt,
)
from venus_evcharger.publish.dbus import DbusPublishController
from venus_evcharger.publish.dbus_config import DbusPublishConfig
from venus_evcharger.publish.dbus_core import DbusPublishCore
from venus_evcharger.publish.dbus_diagnostics import DbusPublishDiagnostics
from venus_evcharger.publish.dbus_diagnostics_contracts import DiagnosticSnapshot
from venus_evcharger.publish.dbus_learned import DbusPublishLearned
from venus_evcharger.publish.dbus_measurements import DbusMeasurementPublisher
from venus_evcharger.publish.dbus_runtime_view import DbusRuntimeView


class _GatewayPublicationStub:
    def register_evcs(
        self,
        identity: EvcsServiceIdentity,
        initial_fields: Mapping[str, object],
    ) -> PublicationReceipt:
        del identity, initial_fields
        return PublicationReceipt(True)

    def publish_evcs_fields(
        self,
        fields: Mapping[str, object],
        *,
        priority: PublicationPriority,
    ) -> PublicationReceipt:
        del fields, priority
        return PublicationReceipt(True)

    def register_companion(
        self,
        identity: CompanionServiceIdentity,
        initial_fields: Mapping[str, object],
    ) -> PublicationReceipt:
        del identity, initial_fields
        return PublicationReceipt(True)

    def publish_companion_fields(
        self,
        service_id: str,
        fields: Mapping[str, object],
        *,
        priority: PublicationPriority,
    ) -> PublicationReceipt:
        del service_id, fields, priority
        return PublicationReceipt(True)


class _PublishRuntimeStub:
    def mark_failure(self, source_key: str) -> None:
        del source_key

    def source_retry_remaining(self, source_key: str, now: float | None = None) -> int:
        del source_key, now
        return 0

    def update_is_stale(self, now: float | None = None) -> bool:
        del now
        return False

    def warning_throttled(
        self,
        key: str,
        interval_seconds: float,
        message: str,
        *args: object,
        **kwargs: object,
    ) -> None:
        del key, interval_seconds, message, args, kwargs


class _PublishServiceHarness:
    """Minimal typed implementation of the mandatory publish-service port."""

    def __init__(self) -> None:
        self.gateway_publication = _GatewayPublicationStub()
        self.runtime = _PublishRuntimeStub()
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


def _controller() -> DbusPublishController:
    service = _service()
    return DbusPublishController(
        service,
        lambda _timestamp, _now: 0.0,
        gateway_diagnostics_reader(),
        EvcsPublicationOwner(service, script_path="test-service.py"),
    )


class DbusPublishCompositionContractTests(unittest.TestCase):
    def test_controller_owns_linear_components_with_explicit_shared_dependencies(self) -> None:
        controller = _controller()

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
        controller = _controller()
        phase_data = {
            "L1": {"power": 100.0, "current": 1.0, "voltage": 230.0},
            "L2": {"power": 0.0, "current": 0.0, "voltage": 230.0},
            "L3": {"power": 0.0, "current": 0.0, "voltage": 230.0},
        }
        snapshot = DiagnosticSnapshot(counters={"status": 1}, ages={"auto_stale_seconds": 2.0})

        with patch.object(controller.core, "ensure_state") as ensure_state:
            controller.ensure_state()
        ensure_state.assert_called_once_with()

        with patch.object(
            controller._publication_owner,
            "maintain_registration",
            return_value=True,
        ) as maintain:
            self.assertTrue(controller.maintain_evcs_registration(10.0))
        maintain.assert_called_once_with(controller._gateway_diagnostics, 10.0)

        with patch.object(controller.core, "publish_field", return_value=True) as publish_field:
            self.assertTrue(controller.publish_field("mode", 2, 11.0, 4.0, True))
        publish_field.assert_called_once_with("mode", 2, 11.0, 4.0, True)

        with patch.object(controller.core, "last_accepted_field", return_value=2) as last_accepted:
            self.assertEqual(controller.last_accepted_field("mode"), 2)
        last_accepted.assert_called_once_with("mode")

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
            self.assertEqual(controller.diagnostics.snapshot(100.0), snapshot)
        counter_values.assert_called_once_with(100.0, ANY)
        age_values.assert_called_once_with(100.0, ANY)

        with patch.object(controller.diagnostics, "publish_diagnostic_paths", return_value=True) as publish_diagnostics:
            self.assertTrue(controller.publish_diagnostic_paths(17.0))
        publish_diagnostics.assert_called_once_with(17.0)


if __name__ == "__main__":
    unittest.main()
