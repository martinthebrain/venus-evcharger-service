# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tests.venus_evcharger_publisher_support import (
    DbusPublishControllerTestCase,
    MagicMock,
    SimpleNamespace,
    build_publish_controller,
    patch,
)
from tests.support.gateway_pressure import FreshOkGatewayPressurePolicy
from venus_evcharger.ipc.gateway_pressure import CachedGatewayPressurePolicy
from venus_evcharger.ports.gateway_publication import GatewayPublicationPort, PublicationReceipt


def _publication(*, accepted: bool = True) -> MagicMock:
    publication = MagicMock(spec=GatewayPublicationPort)
    publication.publish_evcs_fields.return_value = PublicationReceipt(accepted, "test-publication")
    return publication


def _publish_service(publication: MagicMock | None = None, **values: object) -> SimpleNamespace:
    defaults: dict[str, object] = {
        "gateway_publication": publication or _publication(),
        "gateway_pressure_policy": FreshOkGatewayPressurePolicy(),
        "_dbus_live_publish_interval_seconds": 1.0,
        "_dbus_slow_publish_interval_seconds": 5.0,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


class TestDbusPublishControllerPublish(DbusPublishControllerTestCase):
    def test_publish_field_handles_change_and_interval_throttling(self) -> None:
        publication = _publication()
        controller = build_publish_controller(_publish_service(publication), self._age_seconds)

        self.assertTrue(controller.publish_field("mode", 1, now=100.0))
        self.assertFalse(controller.publish_field("mode", 1, now=101.0))
        self.assertFalse(controller.publish_field("mode", 2, now=103.0, interval_seconds=5.0))
        self.assertTrue(controller.publish_field("mode", 2, now=106.0, interval_seconds=5.0))
        self.assertEqual(controller.last_accepted_field("mode"), 2)
        self.assertEqual(publication.publish_evcs_fields.call_count, 2)

    def test_publish_intervals_follow_gateway_backpressure_without_blocking_force(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            health_path = f"{temp_dir}/dbus-health.json"
            Path(health_path).write_text(
                json.dumps(
                    {
                        "captured_at": 100.0,
                        "dbus_health": {"backpressure": {"state": "protective"}},
                    }
                ),
                encoding="utf-8",
            )
            publication = _publication()
            service = _publish_service(
                publication,
                gateway_pressure_policy=CachedGatewayPressurePolicy(health_path, now=lambda: 100.0),
            )
            controller = build_publish_controller(service, self._age_seconds)

            self.assertTrue(controller.publish_field("diagnostic_text", "one", now=100.0, interval_seconds=1.0))
            self.assertFalse(controller.publish_field("diagnostic_text", "two", now=104.0, interval_seconds=1.0))
            self.assertTrue(controller.publish_field("diagnostic_text", "two", now=112.0, interval_seconds=1.0))
            self.assertTrue(
                controller.publish_field("diagnostic_text", "three", now=113.0, interval_seconds=1.0, force=True)
            )
            self.assertEqual(publication.publish_evcs_fields.call_count, 3)

    def test_publish_live_measurements_does_not_accept_fields_after_gateway_error(self) -> None:
        publication = _publication()
        publication.publish_evcs_fields.side_effect = RuntimeError("gateway unavailable")
        service = _publish_service(
            publication,
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller = build_publish_controller(service, self._age_seconds)

        changed = controller.publish_live_measurements(
            1000.0,
            230.0,
            4.3,
            {
                "L1": {"power": 1000.0, "current": 4.3, "voltage": 230.0},
                "L2": {"power": 0.0, "current": 0.0, "voltage": 0.0},
                "L3": {"power": 0.0, "current": 0.0, "voltage": 0.0},
            },
            100.0,
        )

        self.assertFalse(changed)
        self.assertIsNone(controller.last_accepted_field("ac_power_w"))
        self.assertIsNone(controller.last_accepted_field("ac_voltage_v"))
        service._mark_failure.assert_called_once_with("dbus")
        service._warning_throttled.assert_called_once()

    def test_publish_config_fields_is_all_or_nothing_at_gateway_boundary(self) -> None:
        publication = _publication(accepted=False)
        service = _publish_service(
            publication,
            virtual_mode=1,
            virtual_autostart=1,
            virtual_enable=1,
            virtual_set_current=16.0,
            requested_phase_selection="P1",
            active_phase_selection="P1",
            supported_phase_selections=("P1", "P1_P2"),
            min_current=6.0,
            max_current=16.0,
        )
        controller = build_publish_controller(service, self._age_seconds)

        self.assertFalse(controller.publish_config_paths(1, 100.0))
        self.assertIsNone(controller.last_accepted_field("mode"))
        self.assertIsNone(controller.last_accepted_field("enable"))
        fields = publication.publish_evcs_fields.call_args.args[0]
        self.assertEqual(fields["mode"], 1)
        self.assertEqual(fields["enable"], 1)
        self.assertEqual(publication.publish_evcs_fields.call_args.kwargs["priority"], "critical")

    def test_publish_config_fields_refreshes_unchanged_gui_controls_on_slow_interval(self) -> None:
        publication = _publication()
        service = _publish_service(
            publication,
            virtual_mode=1,
            virtual_autostart=1,
            virtual_enable=1,
            virtual_set_current=16.0,
            requested_phase_selection="P1",
            active_phase_selection="P1",
            supported_phase_selections=("P1",),
            min_current=6.0,
            max_current=16.0,
        )
        controller = build_publish_controller(service, self._age_seconds)

        self.assertTrue(controller.publish_config_paths(1, 100.0))
        self.assertFalse(controller.publish_config_paths(1, 101.0))
        self.assertTrue(controller.publish_config_paths(1, 105.0))
        self.assertEqual(publication.publish_evcs_fields.call_count, 2)

    def test_learned_display_helpers_cover_empty_scalar_and_fault_paths(self) -> None:
        service = SimpleNamespace(
            _charger_backend=object(),
            learned_charge_power_state="stable",
            learned_charge_power_watts=None,
            learned_charge_power_updated_at=None,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            auto_learn_charge_power_max_age_seconds=0.0,
            phase="L1",
            voltage_mode="phase",
            _last_charger_state_current_amps=0.0,
            _last_health_reason="contactor-lockout-open",
            _last_charger_state_fault="contactor-lockout-open",
            _last_charger_state_at=100.0,
        )
        controller = build_publish_controller(service, self._age_seconds)

        self.assertIsNone(controller.learned._charger_current_readback(100.0))
        self.assertFalse(controller.learned._learned_charge_power_expired_for_display(100.0))
        service.auto_learn_charge_power_max_age_seconds = 60.0
        self.assertTrue(controller.learned._learned_charge_power_expired_for_display(100.0))
        self.assertIsNone(controller.learned._validated_learned_display_scalars(None, 230.0))
        self.assertIsNone(controller.learned._validated_learned_display_scalars(2000.0, None))
        self.assertIsNone(controller.learned._raw_learned_display_values())
        self.assertIsNone(controller.learned._stable_learned_display_inputs(100.0))
        self.assertIsNone(controller.learned._rounded_display_current(0.4))
        self.assertIsNone(controller.learned._derived_learned_set_current(100.0))
        self.assertEqual(controller.runtime_view.fault_active(service), 1)

    def test_learned_display_falls_back_to_minimum_current_without_backend_readback(self) -> None:
        service = SimpleNamespace(
            _charger_backend=None,
            virtual_set_current=16.0,
            learned_charge_power_state="stable",
            learned_charge_power_watts=230.0,
            learned_charge_power_updated_at=100.0,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            min_current=6.0,
            max_current=16.0,
            voltage_mode="phase",
            phase="L1",
        )
        controller = build_publish_controller(service, self._age_seconds)

        self.assertEqual(controller.learned.display_set_current(100.0), 6.0)

    def test_learned_display_helpers_cover_remaining_fallback_edges(self) -> None:
        service = SimpleNamespace(min_current=6.0, max_current=16.0, voltage_mode="phase")
        controller = build_publish_controller(service, self._age_seconds)

        with patch.object(controller.learned, "_learned_display_current_allowed", return_value=True):
            with patch.object(controller.learned, "_raw_learned_display_values", return_value=None):
                self.assertIsNone(controller.learned._stable_learned_display_inputs(100.0))
            with (
                patch.object(controller.learned, "_raw_learned_display_values", return_value=(2300.0, 230.0, "L1")),
                patch.object(controller.learned, "_phase_voltage_for_display_current", return_value=None),
            ):
                self.assertIsNone(controller.learned._stable_learned_display_inputs(100.0))
            with (
                patch.object(
                    controller.learned,
                    "_stable_learned_display_inputs",
                    return_value=SimpleNamespace(power_w=1.0, phase_voltage_v=230.0, phase_count=1.0),
                ),
                patch.object(controller.learned, "_rounded_display_current", return_value=None),
            ):
                self.assertIsNone(controller.learned._derived_learned_set_current(100.0))

    def test_learned_display_helpers_cover_minimal_candidate_and_unbounded_current_edges(self) -> None:
        service = SimpleNamespace(
            _charger_backend=object(),
            _dbus_live_publish_interval_seconds=0.0,
            auto_shelly_soft_fail_seconds=0.0,
            min_current=None,
            max_current=0.0,
        )
        controller = build_publish_controller(service, self._age_seconds)

        self.assertEqual(controller.learned._charger_state_max_age_seconds(), 2.0)
        self.assertEqual(controller.learned._clamped_display_current(12.5), 12.5)

    def test_rejected_group_does_not_partially_update_accepted_fields(self) -> None:
        publication = _publication(accepted=False)
        controller = build_publish_controller(_publish_service(publication), self._age_seconds)

        changed = controller.core.publish_fields(
            "generic",
            {"ac_power_w": 1, "ac_voltage_v": 2},
            100.0,
            force=True,
        )

        self.assertFalse(changed)
        self.assertIsNone(controller.last_accepted_field("ac_power_w"))
        self.assertIsNone(controller.last_accepted_field("ac_voltage_v"))
        publication.publish_evcs_fields.assert_called_once_with(
            {"ac_power_w": 1, "ac_voltage_v": 2},
            priority="live",
        )

    def test_publish_group_failure_uses_runtime_logging_without_custom_warning_callback(self) -> None:
        publication = _publication()
        publication.publish_evcs_fields.side_effect = RuntimeError("mailbox unavailable")
        service = _publish_service(publication, _mark_failure=MagicMock())
        controller = build_publish_controller(service, self._age_seconds)

        with patch("logging.warning") as warning:
            changed = controller.core.publish_fields(
                "diagnostic-summary",
                {"auto_health_reason": "offline"},
                100.0,
                force=True,
            )

        self.assertFalse(changed)
        service._mark_failure.assert_called_once_with("dbus")
        warning.assert_called_once()

    def test_publish_fields_returns_false_when_group_is_fully_throttled(self) -> None:
        publication = _publication()
        controller = build_publish_controller(_publish_service(publication), self._age_seconds)

        self.assertTrue(controller.core.publish_fields("generic", {"ac_power_w": 5}, 95.0, force=True))
        publication.publish_evcs_fields.reset_mock()
        changed = controller.core.publish_fields(
            "generic",
            {"ac_power_w": 5},
            100.0,
            interval_seconds=10.0,
        )

        self.assertFalse(changed)
        self.assertEqual(controller.last_accepted_field("ac_power_w"), 5)
        publication.publish_evcs_fields.assert_not_called()

    def test_semantic_field_group_is_forwarded_without_paths_or_transport_helpers(self) -> None:
        publication = _publication()
        service = _publish_service(publication)
        controller = build_publish_controller(service, self._age_seconds)

        changed = controller.core.publish_fields(
            "generic",
            {"mode": 2, "enable": 1},
            100.0,
            force=True,
        )

        self.assertTrue(changed)
        publication.publish_evcs_fields.assert_called_once_with(
            {"mode": 2, "enable": 1},
            priority="critical",
        )
        self.assertFalse(any(field.startswith("/") for field in publication.publish_evcs_fields.call_args.args[0]))

    def test_update_index_is_gateway_owned_not_published_by_backend(self) -> None:
        publication = _publication()
        controller = build_publish_controller(_publish_service(publication), self._age_seconds)

        self.assertTrue(controller.publish_field("mode", 2, now=100.0, force=True))

        publication.publish_evcs_fields.assert_called_once_with({"mode": 2}, priority="critical")
        self.assertNotIn("update_index", publication.publish_evcs_fields.call_args.args[0])

    def test_live_measurements_are_forwarded_as_one_semantic_gateway_transaction(self) -> None:
        publication = _publication()
        controller = build_publish_controller(_publish_service(publication), self._age_seconds)

        changed = controller.publish_live_measurements(
            1000.0,
            230.0,
            4.3,
            {
                "L1": {"power": 1000.0, "current": 4.3, "voltage": 230.0},
                "L2": {"power": 0.0, "current": 0.0, "voltage": 0.0},
                "L3": {"power": 0.0, "current": 0.0, "voltage": 0.0},
            },
            100.0,
        )

        self.assertTrue(changed)
        publication.publish_evcs_fields.assert_called_once()
        fields = publication.publish_evcs_fields.call_args.args[0]
        self.assertEqual(publication.publish_evcs_fields.call_args.kwargs["priority"], "live")
        self.assertEqual(fields["ac_power_w"], 1000.0)
        self.assertEqual(fields["charge_current_a"], 4.3)
        self.assertEqual(fields["l1_voltage_v"], 230.0)
        self.assertFalse(any(field.startswith("/") for field in fields))

    def test_gateway_exception_is_contained_without_accepting_the_field(self) -> None:
        publication = _publication()
        publication.publish_evcs_fields.side_effect = RuntimeError("gateway unavailable")
        service = _publish_service(
            publication,
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller = build_publish_controller(service, self._age_seconds)

        self.assertFalse(controller.publish_field("mode", 2, now=100.0, force=True))
        service._mark_failure.assert_called_once_with("dbus")
        service._warning_throttled.assert_called_once()
        self.assertIsNone(controller.last_accepted_field("mode"))

    def test_failed_newer_publication_preserves_last_accepted_semantic_value(self) -> None:
        publication = _publication()
        service = _publish_service(
            publication,
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller = build_publish_controller(service, self._age_seconds)

        self.assertTrue(controller.publish_field("mode", 1, now=90.0, force=True))
        publication.publish_evcs_fields.side_effect = RuntimeError("gateway unavailable")
        self.assertFalse(controller.publish_field("mode", 2, now=100.0, force=True))

        self.assertEqual(controller.last_accepted_field("mode"), 1)
        service._mark_failure.assert_called_once_with("dbus")
        service._warning_throttled.assert_called_once()
