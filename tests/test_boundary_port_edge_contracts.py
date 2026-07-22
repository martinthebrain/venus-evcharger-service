# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavior and structure contracts for boundary-port edge cases."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Protocol, cast

from tests.dbus_adapter_venus_stubs import install_venus_adapter_stubs

install_venus_adapter_stubs()

from venus_evcharger.auto.policy import AutoPolicy
from venus_evcharger.bootstrap.contracts import (
    require_config_state,
    require_controller_owner,
    require_gobject_timers,
    require_runtime_state,
)
from venus_evcharger.dbus_adapter.process.protocols.context import DbusAdapterProcessContext
from venus_evcharger.dbus_adapter.process.protocols.health import DbusAdapterHealthContext
from venus_evcharger.dbus_adapter.process.protocols.introspection import (
    DbusAdapterIntrospectionContext,
    DbusAdapterIntrospectionSnapshotContext,
)
from venus_evcharger.dbus_adapter.process.protocols.io import DbusAdapterIoContext
from venus_evcharger.dbus_adapter.process.protocols.loop import DbusAdapterLoopContext
from venus_evcharger.dbus_adapter.process.protocols.runtime import (
    DbusAdapterPublicationContext,
    DbusAdapterRuntimeContext,
    DbusAdapterSocketContext,
)
from venus_evcharger.ports.write import WriteControllerPort
from venus_evcharger.ports.write_runtime import WriteControllerRuntimePort, WriteRuntimeServicePort
from venus_evcharger.publish.dbus_diagnostics_sources import DbusDiagnosticsSources
from venus_evcharger.service.composition_guards import (
    is_auto_input_service,
    is_backend_target,
    is_publish_service,
    is_update_cycle_service,
    require_auto_input_service,
    require_backend_target,
    require_publish_service,
    require_update_cycle_service,
)


def _no_operation(*_args: object) -> None:
    return None


def _scheduled_mode(_value: object) -> int:
    return 2


def _integer_mode(value: object) -> int:
    return int(str(value))


def _runtime_port(service: SimpleNamespace) -> WriteControllerRuntimePort:
    return WriteControllerRuntimePort(cast(WriteRuntimeServicePort, service))


def _composition_host() -> SimpleNamespace:
    return SimpleNamespace(
        runtime=SimpleNamespace(
            enqueue_dbus_publish_fields=_no_operation,
            update_worker_snapshot=_no_operation,
            source_retry_ready=_no_operation,
            mark_recovery=_no_operation,
            mark_failure=_no_operation,
            delay_source_retry=_no_operation,
            warning_throttled=_no_operation,
            worker_snapshot=_no_operation,
        ),
        auto=SimpleNamespace(
            mode_uses_auto_logic=_no_operation,
            decide_relay=_no_operation,
        ),
        state=SimpleNamespace(flush_runtime_overrides=_no_operation),
        gateway_publication=object(),
        _dbus_live_publish_interval_seconds=1.0,
        _dbus_slow_publish_interval_seconds=5.0,
        _last_health_code=0,
        _last_health_reason="ok",
        last_status=0,
        started_at=0.0,
        virtual_set_current=6.0,
        auto_input_helper_restart_seconds=5.0,
        auto_input_helper_stale_seconds=10.0,
        auto_input_snapshot_path="/run/input.json",
        virtual_mode=1,
        _auto_input_helper_generation=1,
        _auto_input_runtime_instance_id="instance",
        _readback_store=object(),
        time_now=_no_operation,
    )


class BoundaryRequirementFailureTests(unittest.TestCase):
    """Verify malformed collaborators fail immediately at bootstrap boundaries."""

    def test_require_config_state_rejects_missing_port(self) -> None:
        with self.assertRaisesRegex(TypeError, "does not implement ConfigStatePort"):
            require_config_state(SimpleNamespace())

    def test_require_runtime_state_rejects_missing_port(self) -> None:
        with self.assertRaisesRegex(TypeError, "does not implement RuntimeStatePort"):
            require_runtime_state(SimpleNamespace())

    def test_require_controller_owner_rejects_missing_port(self) -> None:
        with self.assertRaisesRegex(TypeError, "does not expose ControllerOwnerPort"):
            require_controller_owner(SimpleNamespace())

    def test_require_gobject_timers_rejects_missing_port(self) -> None:
        with self.assertRaisesRegex(TypeError, "does not implement GobjectTimersPort"):
            require_gobject_timers(object())


class StructuralProtocolContractTests(unittest.TestCase):
    """Protect protocol composition without invoking type-only method bodies."""

    def test_adapter_process_context_composes_all_required_protocols(self) -> None:
        self.assertTrue(getattr(DbusAdapterProcessContext, "_is_protocol", False))
        self.assertEqual(
            DbusAdapterProcessContext.__bases__,
            (
                DbusAdapterRuntimeContext,
                DbusAdapterSocketContext,
                DbusAdapterLoopContext,
                DbusAdapterIoContext,
                DbusAdapterHealthContext,
                DbusAdapterPublicationContext,
                DbusAdapterIntrospectionContext,
                DbusAdapterIntrospectionSnapshotContext,
                Protocol,
            ),
        )


class ServiceCompositionGuardContractTests(unittest.TestCase):
    """Require every declared service attribute and collaborator method."""

    def test_valid_host_satisfies_every_composition_boundary(self) -> None:
        host = _composition_host()

        for guard, requirement in (
            (is_publish_service, require_publish_service),
            (is_auto_input_service, require_auto_input_service),
            (is_update_cycle_service, require_update_cycle_service),
            (is_backend_target, require_backend_target),
        ):
            with self.subTest(guard=guard.__name__):
                self.assertTrue(guard(host))
                self.assertIs(requirement(host), host)

    def test_publish_guard_requires_semantic_gateway_and_runtime_attributes(self) -> None:
        missing_runtime = _composition_host()
        del missing_runtime.runtime
        runtime_only = SimpleNamespace(runtime=_composition_host().runtime)

        bootstrap_phase = _composition_host()
        del bootstrap_phase.gateway_publication
        del bootstrap_phase.last_status
        del bootstrap_phase.virtual_set_current

        self.assertFalse(is_publish_service(missing_runtime))
        self.assertFalse(is_publish_service(runtime_only))
        self.assertFalse(is_publish_service(bootstrap_phase))

    def test_auto_input_guard_requires_all_three_boundary_parts(self) -> None:
        missing_attributes = _composition_host()
        del missing_attributes.virtual_mode
        missing_runtime_method = _composition_host()
        missing_runtime_method.runtime = SimpleNamespace()
        missing_auto_method = _composition_host()
        missing_auto_method.auto = SimpleNamespace()

        self.assertFalse(is_auto_input_service(missing_attributes))
        self.assertFalse(is_auto_input_service(missing_runtime_method))
        self.assertFalse(is_auto_input_service(missing_auto_method))

    def test_update_guard_requires_every_collaborator_method(self) -> None:
        missing_attributes = _composition_host()
        del missing_attributes._readback_store
        missing_runtime_method = _composition_host()
        missing_runtime_method.runtime = SimpleNamespace()
        missing_state_method = _composition_host()
        missing_state_method.state = SimpleNamespace()
        missing_auto_method = _composition_host()
        missing_auto_method.auto = SimpleNamespace()

        self.assertFalse(is_update_cycle_service(missing_attributes))
        self.assertFalse(is_update_cycle_service(missing_runtime_method))
        self.assertFalse(is_update_cycle_service(missing_state_method))
        self.assertFalse(is_update_cycle_service(missing_auto_method))

    def test_requirements_report_the_exact_failed_boundary(self) -> None:
        expected_errors = (
            (require_publish_service, "wallbox service does not implement PublishServicePort"),
            (require_auto_input_service, "wallbox service does not implement AutoInputSupervisorService"),
            (require_update_cycle_service, "wallbox service does not implement UpdateCycleServicePort"),
            (require_backend_target, "wallbox service does not expose mutable backend state"),
        )

        for requirement, expected_message in expected_errors:
            with self.subTest(requirement=requirement.__name__):
                with self.assertRaises(TypeError) as caught:
                    requirement(1)
                self.assertEqual(str(caught.exception), expected_message)


class WritePortEdgeContractTests(unittest.TestCase):
    """Exercise normalization and freshness behavior at the write boundary."""

    def test_last_relay_output_rejects_non_numeric_confirmation_timestamp(self) -> None:
        service = SimpleNamespace(
            _last_confirmed_pm_status={"output": True},
            _last_confirmed_pm_status_at="invalid",
        )

        self.assertIsNone(WriteControllerPort(service)._fresh_last_output(100.0, 2.0))

    def test_last_relay_output_rejects_stale_confirmation_timestamp(self) -> None:
        service = SimpleNamespace(
            _last_confirmed_pm_status={"output": True},
            _last_confirmed_pm_status_at=97.0,
        )

        self.assertIsNone(WriteControllerPort(service)._fresh_last_output(100.0, 2.0))

    def test_runtime_tracking_properties_normalize_service_values(self) -> None:
        service = SimpleNamespace(
            auto_start_condition_since="12.5",
            auto_stop_condition_since=float("inf"),
            manual_override_until="7.25",
        )
        port = _runtime_port(service)

        self.assertEqual(port.auto_start_condition_since, 12.5)
        self.assertIsNone(port.auto_stop_condition_since)
        self.assertEqual(port.manual_override_until, 7.25)

    def test_runtime_property_surface_normalizes_all_scalar_groups(self) -> None:
        policy = AutoPolicy()
        service = SimpleNamespace(
            auto=SimpleNamespace(normalize_mode=_scheduled_mode),
            auto_policy=policy,
            time_now=lambda: 100.0,
            virtual_mode=-1,
            virtual_autostart=0,
            virtual_startstop=-2,
            virtual_enable=4,
            auto_manual_override_seconds="18.5",
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            auto_scheduled_enabled_days="mon,wed",
            auto_scheduled_latest_end_time="6:05",
            _software_update_run_requested_at=None,
            supported_phase_selections=("P1_P2_P3", "P1"),
            requested_phase_selection="P1_P2_P3",
            active_phase_selection="P1",
            _auto_mode_cutover_pending=0,
            _ignore_min_offtime_once=1,
        )
        float_properties = (
            "virtual_set_current",
            "min_current",
            "max_current",
            "auto_start_delay_seconds",
            "auto_stop_delay_seconds",
            "auto_scheduled_night_start_delay_seconds",
            "auto_scheduled_night_current_amps",
            "auto_dbus_backoff_base_seconds",
            "auto_dbus_backoff_max_seconds",
            "manual_override_until",
        )
        for property_name in float_properties:
            setattr(service, property_name, "12.5")
        port = _runtime_port(service)

        self.assertEqual(port.virtual_mode, 0)
        port.virtual_mode = "scheduled"
        self.assertEqual(service.virtual_mode, 2)
        self.assertEqual((port.virtual_autostart, port.virtual_startstop, port.virtual_enable), (0, 0, 1))
        port.virtual_autostart = "1"
        port.virtual_startstop = "0"
        port.virtual_enable = "invalid"
        self.assertEqual((service.virtual_autostart, service.virtual_startstop, service.virtual_enable), (1, 0, 0))
        self.assertEqual(port.auto_manual_override_seconds, 18.5)
        self.assertIs(port.auto_policy, policy)

        for property_name in float_properties:
            with self.subTest(property_name=property_name):
                self.assertEqual(getattr(port, property_name), 12.5)
                setattr(port, property_name, float("inf"))
                self.assertEqual(getattr(service, property_name), 0.0)

    def test_runtime_property_surface_normalizes_schedule_timestamps_and_flags(self) -> None:
        service = SimpleNamespace(
            auto=SimpleNamespace(normalize_mode=_integer_mode),
            time_now=lambda: 100.0,
            auto_scheduled_enabled_days="mon,wed",
            auto_scheduled_latest_end_time="6:05",
            supported_phase_selections=("P1_P2_P3", "P1"),
            requested_phase_selection="P1_P2_P3",
            active_phase_selection="P1",
            _auto_mode_cutover_pending=0,
            _ignore_min_offtime_once=1,
        )
        port = _runtime_port(service)

        port.auto_start_condition_since = "12.5"
        port.auto_stop_condition_since = float("inf")
        port._software_update_run_requested_at = "42.25"
        self.assertEqual(port.auto_start_condition_since, 12.5)
        self.assertIsNone(port.auto_stop_condition_since)
        self.assertEqual(port._software_update_run_requested_at, 42.25)

        self.assertEqual(port.auto_scheduled_enabled_days, "Mon,Wed")
        port.auto_scheduled_enabled_days = "weekend"
        self.assertEqual(service.auto_scheduled_enabled_days, "Sat,Sun")
        self.assertEqual(port.auto_scheduled_latest_end_time, "06:05")
        port.auto_scheduled_latest_end_time = "invalid"
        self.assertEqual(service.auto_scheduled_latest_end_time, "06:30")

        self.assertEqual(port.supported_phase_selections, ("P1_P2_P3", "P1"))
        port.supported_phase_selections = ("invalid",)
        self.assertEqual(service.supported_phase_selections, ("P1",))
        service.supported_phase_selections = ("P1_P2_P3", "P1")
        self.assertEqual(port.requested_phase_selection, "P1_P2_P3")
        port.requested_phase_selection = "invalid"
        self.assertEqual(service.requested_phase_selection, "P1_P2_P3")
        self.assertEqual(port.active_phase_selection, "P1")
        port.active_phase_selection = "invalid"
        self.assertEqual(service.active_phase_selection, "P1_P2_P3")

        self.assertFalse(port.auto_mode_cutover_pending)
        self.assertTrue(port.ignore_min_offtime_once)
        port.auto_mode_cutover_pending = 1
        port.ignore_min_offtime_once = 0
        self.assertTrue(service._auto_mode_cutover_pending)
        self.assertFalse(service._ignore_min_offtime_once)


class DiagnosticSourceEdgeContractTests(unittest.TestCase):
    """Protect legacy retry fallback behavior at the diagnostics boundary."""

    def test_shelly_retry_fallback_returns_zero_when_deadline_is_absent(self) -> None:
        self.assertEqual(DbusDiagnosticsSources._shelly_retry_remaining_value(SimpleNamespace(), 50.0), 0)

    def test_shelly_retry_fallback_reports_remaining_whole_seconds(self) -> None:
        service = SimpleNamespace(_shelly_retry_after=55.9)

        self.assertEqual(DbusDiagnosticsSources._shelly_retry_remaining_value(service, 50.0), 5)

    def test_shelly_retry_fallback_clamps_expired_deadline_to_zero(self) -> None:
        service = SimpleNamespace(_shelly_retry_after=49.9)

        self.assertEqual(DbusDiagnosticsSources._shelly_retry_remaining_value(service, 50.0), 0)

    def test_shelly_retry_fallback_rejects_non_numeric_deadline(self) -> None:
        service = SimpleNamespace(_shelly_retry_after="tomorrow")

        self.assertEqual(DbusDiagnosticsSources._shelly_retry_remaining_value(service, 50.0), 0)


if __name__ == "__main__":
    unittest.main()
