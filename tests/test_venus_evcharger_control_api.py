# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_write_controller_support import *
from venus_evcharger.control import ControlApiV1Service, ControlCommand


class TestControlApiV1(ControlWriteControllerTestBase):
    def test_command_for_target_maps_canonical_commands(self) -> None:
        api = ControlApiV1Service(
            current_setting_targets=ControlWriteController.CURRENT_SETTING_TARGETS,
            auto_runtime_setting_targets=ControlWriteController.AUTO_RUNTIME_SETTING_TARGETS,
        )

        mode_command = api.command_for_target("set_mode", "mode", 1)
        current_command = api.command_for_target(
            "set_current_setting", "set_current", 12.5
        )
        auto_command = api.command_for_target(
            "set_auto_runtime_setting", "auto_start_surplus_watts", 1800.0
        )

        self.assertEqual(mode_command.name, "set_mode")
        self.assertEqual(current_command.name, "set_current_setting")
        self.assertEqual(auto_command.name, "set_auto_runtime_setting")
        with self.assertRaisesRegex(ValueError, "does not support target 'unknown'"):
            api.command_for_target("set_mode", "unknown", 1)

    def test_command_for_target_preserves_transport_source(self) -> None:
        api = ControlApiV1Service(
            current_setting_targets=ControlWriteController.CURRENT_SETTING_TARGETS,
            auto_runtime_setting_targets=ControlWriteController.AUTO_RUNTIME_SETTING_TARGETS,
        )

        command = api.command_for_target("set_mode", "mode", 1, source="mqtt")

        self.assertEqual(command.name, "set_mode")
        self.assertEqual(command.source, "mqtt")

    def test_command_from_payload_accepts_canonical_name_and_default_target(self) -> None:
        api = ControlApiV1Service(
            current_setting_targets=ControlWriteController.CURRENT_SETTING_TARGETS,
            auto_runtime_setting_targets=ControlWriteController.AUTO_RUNTIME_SETTING_TARGETS,
        )

        command = api.command_from_payload(
            {"name": "set_mode", "value": 1, "command_id": "cmd-1", "idempotency_key": "idem-1"},
            source="http",
        )

        self.assertEqual(command.name, "set_mode")
        self.assertEqual(command.target, "mode")
        self.assertEqual(command.source, "http")
        self.assertEqual(command.command_id, "cmd-1")
        self.assertEqual(command.idempotency_key, "idem-1")

    def test_command_from_payload_requires_explicit_target_for_runtime_setting_commands(self) -> None:
        api = ControlApiV1Service(
            current_setting_targets=ControlWriteController.CURRENT_SETTING_TARGETS,
            auto_runtime_setting_targets=ControlWriteController.AUTO_RUNTIME_SETTING_TARGETS,
        )

        with self.assertRaisesRegex(ValueError, "requires an explicit 'target'"):
            api.command_from_payload({"name": "set_current_setting", "value": 12.5}, source="http")

    def test_command_from_payload_rejects_unknown_command_names(self) -> None:
        api = ControlApiV1Service(
            current_setting_targets=ControlWriteController.CURRENT_SETTING_TARGETS,
            auto_runtime_setting_targets=ControlWriteController.AUTO_RUNTIME_SETTING_TARGETS,
        )

        with self.assertRaisesRegex(ValueError, "Unsupported control command"):
            api.command_from_payload({"name": "set_everything_on", "value": 1}, source="http")

    def test_command_from_payload_accepts_explicit_target_and_rejects_missing_shape(self) -> None:
        api = ControlApiV1Service(
            current_setting_targets=ControlWriteController.CURRENT_SETTING_TARGETS,
            auto_runtime_setting_targets=ControlWriteController.AUTO_RUNTIME_SETTING_TARGETS,
        )

        command = api.command_from_payload(
            {"name": "set_current_setting", "target": "set_current", "value": 10.0},
            source="http",
        )

        self.assertEqual(command.target, "set_current")
        with self.assertRaisesRegex(ValueError, "must include 'name'"):
            api.command_from_payload({"target": "mode", "value": 1}, source="http")
        with self.assertRaisesRegex(ValueError, "must include 'name'"):
            api.command_from_payload({}, source="http")

    def test_command_from_payload_rejects_extra_fields_unknown_paths_and_invalid_types(self) -> None:
        api = ControlApiV1Service(
            current_setting_targets=ControlWriteController.CURRENT_SETTING_TARGETS,
            auto_runtime_setting_targets=ControlWriteController.AUTO_RUNTIME_SETTING_TARGETS,
        )

        with self.assertRaisesRegex(ValueError, "Unsupported payload field"):
            api.command_from_payload({"name": "set_mode", "value": 1, "unexpected": True}, source="http")
        with self.assertRaisesRegex(ValueError, "Unsupported payload field"):
            api.command_from_payload(
                {"name": "set_mode", "path": "/Mode", "value": 1}, source="http"
            )
        with self.assertRaisesRegex(ValueError, "requires one of: 0, 1, 2"):
            api.command_from_payload({"name": "set_mode", "value": "1"}, source="http")
        with self.assertRaisesRegex(ValueError, "requires a boolean or binary integer"):
            api.command_from_payload({"name": "set_auto_start", "value": 2}, source="http")
        with self.assertRaisesRegex(ValueError, "requires one of: P1, P1_P2, P1_P2_P3"):
            api.command_from_payload({"name": "set_phase_selection", "value": ""}, source="http")
        with self.assertRaisesRegex(ValueError, "requires one of"):
            api.command_from_payload(
                {"name": "set_auto_runtime_setting", "target": "auto_unknown", "value": 1},
                source="http",
            )

    def test_command_from_payload_rejects_blank_or_wrong_explicit_default_target(self) -> None:
        api = ControlApiV1Service(
            current_setting_targets=ControlWriteController.CURRENT_SETTING_TARGETS,
            auto_runtime_setting_targets=ControlWriteController.AUTO_RUNTIME_SETTING_TARGETS,
        )

        with self.assertRaisesRegex(ValueError, "does not support target 'enable'"):
            api.command_from_payload(
                {"name": "set_mode", "target": "enable", "value": 1}, source="http"
            )

    def test_command_from_payload_validates_target_specific_runtime_value_types(self) -> None:
        api = ControlApiV1Service(
            current_setting_targets=ControlWriteController.CURRENT_SETTING_TARGETS,
            auto_runtime_setting_targets=ControlWriteController.AUTO_RUNTIME_SETTING_TARGETS,
        )

        command = api.command_from_payload(
            {"name": "set_auto_runtime_setting", "target": "auto_scheduled_enabled_days", "value": "1,2,3"},
            source="http",
        )
        self.assertEqual(command.target, "auto_scheduled_enabled_days")
        with self.assertRaisesRegex(ValueError, "requires a non-empty string value"):
            api.command_from_payload(
                {"name": "set_auto_runtime_setting", "target": "auto_scheduled_enabled_days", "value": 3},
                source="http",
            )
        with self.assertRaisesRegex(ValueError, "requires a boolean or binary integer"):
            api.command_from_payload(
                {"name": "set_auto_runtime_setting", "target": "auto_phase_switching", "value": 3},
                source="http",
            )
        with self.assertRaisesRegex(ValueError, "requires a non-negative integer value"):
            api.command_from_payload(
                {"name": "set_auto_runtime_setting", "target": "auto_phase_mismatch_lockout_count", "value": 1.5},
                source="http",
            )
        with self.assertRaisesRegex(ValueError, "requires a HH:MM time string"):
            api.command_from_payload(
                {"name": "set_auto_runtime_setting", "target": "auto_scheduled_latest_end_time", "value": "25:99"},
                source="http",
            )
        with self.assertRaisesRegex(ValueError, "between 0 and 100"):
            api.command_from_payload(
                {"name": "set_auto_runtime_setting", "target": "auto_min_soc", "value": 101},
                source="http",
            )
        with self.assertRaisesRegex(ValueError, "interval \\(0, 1\\]"):
            api.command_from_payload(
                {"name": "set_auto_runtime_setting", "target": "auto_learn_charge_power_alpha", "value": 0},
                source="http",
            )

    def test_command_from_payload_accepts_boolean_binary_values_and_reports_numeric_errors(self) -> None:
        api = ControlApiV1Service(
            current_setting_targets=ControlWriteController.CURRENT_SETTING_TARGETS,
            auto_runtime_setting_targets=ControlWriteController.AUTO_RUNTIME_SETTING_TARGETS,
        )

        command = api.command_from_payload({"name": "set_auto_start", "value": True}, source="http")

        self.assertIs(command.value, True)
        with self.assertRaisesRegex(ValueError, "requires a non-negative numeric value for target 'set_current'"):
            api.command_from_payload({"name": "set_current_setting", "target": "set_current", "value": "bad"}, source="http")
        with self.assertRaisesRegex(ValueError, "requires a non-negative numeric value for target 'set_current'"):
            api.command_from_payload({"name": "set_current_setting", "target": "set_current", "value": -1}, source="http")

    def test_control_api_helper_error_paths_cover_generic_runtime_and_unknown_commands(self) -> None:
        api = ControlApiV1Service(
            current_setting_targets=ControlWriteController.CURRENT_SETTING_TARGETS,
            auto_runtime_setting_targets=ControlWriteController.AUTO_RUNTIME_SETTING_TARGETS,
        )

        self.assertEqual(api._auto_runtime_value_kind("auto_unknown"), "any")
        self.assertEqual(
            ControlApiV1Service._auto_runtime_error_kind("auto_start_surplus_watts"),
            "numeric",
        )
        self.assertEqual(
            ControlApiV1Service._auto_runtime_error_kind("auto_phase_switching"),
            "binary",
        )
        self.assertEqual(
            ControlApiV1Service._auto_runtime_error_kind("auto_phase_mismatch_lockout_count"),
            "integer",
        )
        self.assertEqual(
            ControlApiV1Service._auto_runtime_error_kind("auto_unknown"),
            "generic",
        )
        self.assertEqual(
            ControlApiV1Service._auto_runtime_value_error("auto_unknown"),
            "Control command 'set_auto_runtime_setting' received an invalid value for target 'auto_unknown'.",
        )
        self.assertEqual(
            ControlApiV1Service._auto_runtime_value_error("auto_start_surplus_watts"),
            "Control command 'set_auto_runtime_setting' requires a non-negative numeric value for target 'auto_start_surplus_watts'.",
        )
        self.assertEqual(
            ControlApiV1Service._auto_runtime_value_error("auto_scheduled_latest_end_time"),
            "Control command 'set_auto_runtime_setting' requires a HH:MM time string for target 'auto_scheduled_latest_end_time'.",
        )

    def test_control_api_helper_validation_edges_cover_generic_paths_and_time_parsing(self) -> None:
        api = ControlApiV1Service(
            current_setting_targets=ControlWriteController.CURRENT_SETTING_TARGETS,
            auto_runtime_setting_targets=set(ControlWriteController.AUTO_RUNTIME_SETTING_TARGETS) | {"auto_custom"},
        )

        command = api.command_from_payload(
            {"name": "set_auto_runtime_setting", "target": "auto_custom", "value": {"free": "form"}},
            source="http",
        )

        self.assertEqual(command.target, "auto_custom")
        self.assertEqual(command.value, {"free": "form"})
        self.assertTrue(ControlApiV1Service._always_valid_value(object()))
        self.assertTrue(ControlApiV1Service._within_auto_runtime_bounds("auto_custom", 5.0))
        scheduled_time = "auto_scheduled_latest_end_time"
        self.assertFalse(ControlApiV1Service._valid_auto_runtime_text(scheduled_time, "1230"))
        self.assertFalse(ControlApiV1Service._valid_auto_runtime_text(scheduled_time, "ab:30"))
        self.assertFalse(ControlApiV1Service._valid_auto_runtime_text(scheduled_time, "12:99"))

    def test_build_control_command_from_payload_delegates_to_control_api(self) -> None:
        service = SimpleNamespace(
            virtual_autostart=0,
            _dbusservice={"/AutoStart": 0},
            time_now=MagicMock(return_value=10.0),
            _publish_dbus_field=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
            _save_runtime_overrides=MagicMock(),
        )
        controller = ControlWriteController(WriteControllerPort(service))

        command = controller.build_control_command_from_payload({"name": "set_mode", "value": 1}, source="http")

        self.assertEqual(command.name, "set_mode")
        self.assertEqual(command.target, "mode")
        self.assertEqual(command.source, "http")

    def test_handle_control_command_returns_applied_result(self) -> None:
        state = SimpleNamespace(
            publish_field=MagicMock(),
            summary=MagicMock(side_effect=self._state_summary),
            save_runtime_state=MagicMock(),
            save_runtime_overrides=MagicMock(),
        )
        service = SimpleNamespace(
            virtual_autostart=0,
            _dbusservice={"/AutoStart": 0},
            time_now=MagicMock(return_value=10.0),
            state=state,
            auto=SimpleNamespace(normalize_mode=self._normalize_mode),
        )
        state.publish_field.side_effect = self._publish_field_side_effect(service)
        controller = ControlWriteController(WriteControllerPort(service))
        command = ControlCommand(name="set_auto_start", target="auto_start", value=1)

        result = controller.handle_control_command(command)

        self.assertTrue(result.accepted)
        self.assertTrue(result.applied)
        self.assertTrue(result.persisted)
        self.assertEqual(result.status, "applied")
        self.assertFalse(result.external_side_effect_started)
        self.assertEqual(service.virtual_autostart, 1)
        self.assertEqual(service._dbusservice["/AutoStart"], 1)
        state.save_runtime_state.assert_called_once()
        state.save_runtime_overrides.assert_called_once()

    def test_handle_control_command_returns_rejected_result_for_reversible_failures(self) -> None:
        state = SimpleNamespace(
            publish_field=MagicMock(),
            summary=MagicMock(side_effect=self._state_summary),
            save_runtime_state=MagicMock(side_effect=RuntimeError("save failed")),
            save_runtime_overrides=MagicMock(),
        )
        service = SimpleNamespace(
            virtual_autostart=0,
            _dbusservice={"/AutoStart": 0},
            time_now=MagicMock(return_value=10.0),
            state=state,
            auto=SimpleNamespace(normalize_mode=self._normalize_mode),
        )
        state.publish_field.side_effect = self._publish_field_side_effect(service)
        controller = ControlWriteController(WriteControllerPort(service))
        command = ControlCommand(name="set_auto_start", target="auto_start", value=1)

        result = controller.handle_control_command(command)

        self.assertFalse(result.accepted)
        self.assertFalse(result.applied)
        self.assertFalse(result.persisted)
        self.assertTrue(result.reversible_failure)
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.detail, "save failed")
        self.assertEqual(service.virtual_autostart, 0)
        self.assertEqual(service._dbusservice["/AutoStart"], 0)

    def test_handle_control_command_returns_in_flight_result_after_external_side_effects(self) -> None:
        backend = SimpleNamespace(set_current=MagicMock())
        state = SimpleNamespace(
            publish_field=MagicMock(),
            summary=MagicMock(side_effect=self._state_summary),
            save_runtime_state=MagicMock(side_effect=RuntimeError("save failed")),
            save_runtime_overrides=MagicMock(),
        )
        service = SimpleNamespace(
            virtual_set_current=6.0,
            _charger_backend=backend,
            _dbusservice={"/SetCurrent": 6.0},
            time_now=MagicMock(return_value=10.0),
            state=state,
            auto=SimpleNamespace(normalize_mode=self._normalize_mode),
        )
        state.publish_field.side_effect = self._publish_field_side_effect(service)
        controller = ControlWriteController(WriteControllerPort(service))
        command = ControlCommand(name="set_current_setting", target="set_current", value=12.5)

        result = controller.handle_control_command(command)

        self.assertTrue(result.accepted)
        self.assertFalse(result.applied)
        self.assertFalse(result.persisted)
        self.assertEqual(result.status, "accepted_in_flight")
        self.assertTrue(result.external_side_effect_started)
        self.assertEqual(result.detail, "save failed")
        backend.set_current.assert_called_once_with(12.5)
        self.assertEqual(service.virtual_set_current, 12.5)
        self.assertEqual(service._dbusservice["/SetCurrent"], 12.5)

    def test_execute_dispatches_commands_with_and_without_explicit_targets(self) -> None:
        api = ControlApiV1Service(
            current_setting_targets=ControlWriteController.CURRENT_SETTING_TARGETS,
            auto_runtime_setting_targets=ControlWriteController.AUTO_RUNTIME_SETTING_TARGETS,
        )
        controller = SimpleNamespace(
            _handle_mode_value_write=MagicMock(),
            _handle_current_setting_write=MagicMock(),
        )

        api.execute(controller, ControlCommand(name="set_mode", target="mode", value=1))
        api.execute(
            controller,
            ControlCommand(name="set_current_setting", target="set_current", value=12.5),
        )

        controller._handle_mode_value_write.assert_called_once_with(1)
        controller._handle_current_setting_write.assert_called_once_with("set_current", 12.5)
