# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_write_controller_support import *
from venus_evcharger.control import ControlApiV1Service, ControlCommand


class TestControlApiV1(DbusWriteControllerTestBase):
    def test_command_for_dbus_write_maps_canonical_commands(self) -> None:
        api = ControlApiV1Service(
            current_setting_paths=DbusWriteController.CURRENT_SETTING_PATHS,
            auto_runtime_setting_paths=DbusWriteController.AUTO_RUNTIME_SETTING_PATHS,
        )

        mode_command = api.command_for_dbus_write("/Mode", 1)
        current_command = api.command_for_dbus_write("/SetCurrent", 12.5)
        auto_command = api.command_for_dbus_write("/Auto/StartSurplusWatts", 1800.0)
        unknown_command = api.command_for_dbus_write("/UnknownPath", 1)

        self.assertEqual(mode_command.name, "set_mode")
        self.assertEqual(current_command.name, "set_current_setting")
        self.assertEqual(auto_command.name, "set_auto_runtime_setting")
        self.assertEqual(unknown_command.name, "legacy_unknown_write")
        self.assertEqual(unknown_command.source, "dbus")

    def test_command_for_write_preserves_transport_source(self) -> None:
        api = ControlApiV1Service(
            current_setting_paths=DbusWriteController.CURRENT_SETTING_PATHS,
            auto_runtime_setting_paths=DbusWriteController.AUTO_RUNTIME_SETTING_PATHS,
        )

        command = api.command_for_write("/Mode", 1, source="mqtt")

        self.assertEqual(command.name, "set_mode")
        self.assertEqual(command.source, "mqtt")

    def test_command_from_payload_accepts_canonical_name_and_default_path(self) -> None:
        api = ControlApiV1Service(
            current_setting_paths=DbusWriteController.CURRENT_SETTING_PATHS,
            auto_runtime_setting_paths=DbusWriteController.AUTO_RUNTIME_SETTING_PATHS,
        )

        command = api.command_from_payload(
            {"name": "set_mode", "value": 1, "command_id": "cmd-1", "idempotency_key": "idem-1"},
            source="http",
        )

        self.assertEqual(command.name, "set_mode")
        self.assertEqual(command.path, "/Mode")
        self.assertEqual(command.source, "http")
        self.assertEqual(command.command_id, "cmd-1")
        self.assertEqual(command.idempotency_key, "idem-1")

    def test_command_from_payload_requires_explicit_path_for_runtime_setting_commands(self) -> None:
        api = ControlApiV1Service(
            current_setting_paths=DbusWriteController.CURRENT_SETTING_PATHS,
            auto_runtime_setting_paths=DbusWriteController.AUTO_RUNTIME_SETTING_PATHS,
        )

        with self.assertRaisesRegex(ValueError, "requires an explicit 'path'"):
            api.command_from_payload({"name": "set_current_setting", "value": 12.5}, source="http")

    def test_command_from_payload_rejects_unknown_command_names(self) -> None:
        api = ControlApiV1Service(
            current_setting_paths=DbusWriteController.CURRENT_SETTING_PATHS,
            auto_runtime_setting_paths=DbusWriteController.AUTO_RUNTIME_SETTING_PATHS,
        )

        with self.assertRaisesRegex(ValueError, "Unsupported control command"):
            api.command_from_payload({"name": "set_everything_on", "value": 1}, source="http")

    def test_command_from_payload_accepts_explicit_path_and_rejects_missing_shape(self) -> None:
        api = ControlApiV1Service(
            current_setting_paths=DbusWriteController.CURRENT_SETTING_PATHS,
            auto_runtime_setting_paths=DbusWriteController.AUTO_RUNTIME_SETTING_PATHS,
        )

        command = api.command_from_payload(
            {"name": "set_current_setting", "path": "/SetCurrent", "value": 10.0},
            source="http",
        )

        self.assertEqual(command.path, "/SetCurrent")
        path_command = api.command_from_payload({"path": "/Mode", "value": 1}, source="http")
        self.assertEqual(path_command.name, "set_mode")
        with self.assertRaisesRegex(ValueError, "must include either 'name' or 'path'"):
            api.command_from_payload({}, source="http")

    def test_command_from_payload_rejects_extra_fields_unknown_paths_and_invalid_types(self) -> None:
        api = ControlApiV1Service(
            current_setting_paths=DbusWriteController.CURRENT_SETTING_PATHS,
            auto_runtime_setting_paths=DbusWriteController.AUTO_RUNTIME_SETTING_PATHS,
        )

        with self.assertRaisesRegex(ValueError, "Unsupported payload field"):
            api.command_from_payload({"name": "set_mode", "value": 1, "unexpected": True}, source="http")
        with self.assertRaisesRegex(ValueError, "Unsupported control path"):
            api.command_from_payload({"path": "/UnknownPath", "value": 1}, source="http")
        with self.assertRaisesRegex(ValueError, "requires one of: 0, 1, 2"):
            api.command_from_payload({"name": "set_mode", "value": "1"}, source="http")
        with self.assertRaisesRegex(ValueError, "requires a boolean or binary integer"):
            api.command_from_payload({"name": "set_auto_start", "value": 2}, source="http")
        with self.assertRaisesRegex(ValueError, "requires one of: P1, P1_P2, P1_P2_P3"):
            api.command_from_payload({"name": "set_phase_selection", "value": ""}, source="http")
        with self.assertRaisesRegex(ValueError, "requires one of"):
            api.command_from_payload(
                {"name": "set_auto_runtime_setting", "path": "/Auto/NotARealSetting", "value": 1},
                source="http",
            )

    def test_command_from_payload_rejects_blank_path_and_wrong_explicit_default_path(self) -> None:
        api = ControlApiV1Service(
            current_setting_paths=DbusWriteController.CURRENT_SETTING_PATHS,
            auto_runtime_setting_paths=DbusWriteController.AUTO_RUNTIME_SETTING_PATHS,
        )

        with self.assertRaisesRegex(ValueError, "must be a non-empty string"):
            api.command_from_payload({"path": "   ", "value": 1}, source="http")
        with self.assertRaisesRegex(ValueError, "does not support path '/Enable'"):
            api.command_from_payload({"name": "set_mode", "path": "/Enable", "value": 1}, source="http")

    def test_command_from_payload_validates_path_specific_runtime_value_types(self) -> None:
        api = ControlApiV1Service(
            current_setting_paths=DbusWriteController.CURRENT_SETTING_PATHS,
            auto_runtime_setting_paths=DbusWriteController.AUTO_RUNTIME_SETTING_PATHS,
        )

        command = api.command_from_payload(
            {"name": "set_auto_runtime_setting", "path": "/Auto/ScheduledEnabledDays", "value": "1,2,3"},
            source="http",
        )
        self.assertEqual(command.path, "/Auto/ScheduledEnabledDays")
        with self.assertRaisesRegex(ValueError, "requires a non-empty string value"):
            api.command_from_payload(
                {"name": "set_auto_runtime_setting", "path": "/Auto/ScheduledEnabledDays", "value": 3},
                source="http",
            )
        with self.assertRaisesRegex(ValueError, "requires a boolean or binary integer"):
            api.command_from_payload(
                {"name": "set_auto_runtime_setting", "path": "/Auto/PhaseSwitching", "value": 3},
                source="http",
            )
        with self.assertRaisesRegex(ValueError, "requires a non-negative integer value"):
            api.command_from_payload(
                {"name": "set_auto_runtime_setting", "path": "/Auto/PhaseMismatchLockoutCount", "value": 1.5},
                source="http",
            )
        with self.assertRaisesRegex(ValueError, "requires a HH:MM time string"):
            api.command_from_payload(
                {"name": "set_auto_runtime_setting", "path": "/Auto/ScheduledLatestEndTime", "value": "25:99"},
                source="http",
            )
        with self.assertRaisesRegex(ValueError, "between 0 and 100"):
            api.command_from_payload(
                {"name": "set_auto_runtime_setting", "path": "/Auto/MinSoc", "value": 101},
                source="http",
            )
        with self.assertRaisesRegex(ValueError, "interval \\(0, 1\\]"):
            api.command_from_payload(
                {"name": "set_auto_runtime_setting", "path": "/Auto/LearnChargePowerAlpha", "value": 0},
                source="http",
            )

    def test_command_from_payload_accepts_boolean_binary_values_and_reports_numeric_errors(self) -> None:
        api = ControlApiV1Service(
            current_setting_paths=DbusWriteController.CURRENT_SETTING_PATHS,
            auto_runtime_setting_paths=DbusWriteController.AUTO_RUNTIME_SETTING_PATHS,
        )

        command = api.command_from_payload({"name": "set_auto_start", "value": True}, source="http")

        self.assertIs(command.value, True)
        with self.assertRaisesRegex(ValueError, "requires a non-negative numeric value for path '/SetCurrent'"):
            api.command_from_payload({"name": "set_current_setting", "path": "/SetCurrent", "value": "bad"}, source="http")
        with self.assertRaisesRegex(ValueError, "requires a non-negative numeric value for path '/SetCurrent'"):
            api.command_from_payload({"name": "set_current_setting", "path": "/SetCurrent", "value": -1}, source="http")

    def test_control_api_helper_error_paths_cover_generic_runtime_and_unknown_commands(self) -> None:
        api = ControlApiV1Service(
            current_setting_paths=DbusWriteController.CURRENT_SETTING_PATHS,
            auto_runtime_setting_paths=DbusWriteController.AUTO_RUNTIME_SETTING_PATHS,
        )

        self.assertEqual(api._auto_runtime_value_kind("/Auto/Unknown"), "any")
        self.assertEqual(
            ControlApiV1Service._auto_runtime_error_kind("/Auto/StartSurplusWatts"),
            "numeric",
        )
        self.assertEqual(
            ControlApiV1Service._auto_runtime_error_kind("/Auto/PhaseSwitching"),
            "binary",
        )
        self.assertEqual(
            ControlApiV1Service._auto_runtime_error_kind("/Auto/PhaseMismatchLockoutCount"),
            "integer",
        )
        self.assertEqual(
            ControlApiV1Service._auto_runtime_error_kind("/Auto/Unknown"),
            "generic",
        )
        self.assertEqual(
            ControlApiV1Service._command_value_error("legacy_unknown_write", "/Unknown"),
            "Control command 'legacy_unknown_write' received an invalid value for path '/Unknown'.",
        )
        self.assertEqual(
            ControlApiV1Service._auto_runtime_value_error("/Auto/Unknown"),
            "Control command 'set_auto_runtime_setting' received an invalid value for path '/Auto/Unknown'.",
        )
        self.assertEqual(
            ControlApiV1Service._auto_runtime_value_error("/Auto/StartSurplusWatts"),
            "Control command 'set_auto_runtime_setting' requires a non-negative numeric value for path '/Auto/StartSurplusWatts'.",
        )
        self.assertEqual(
            ControlApiV1Service._auto_runtime_value_error("/Auto/ScheduledLatestEndTime"),
            "Control command 'set_auto_runtime_setting' requires a HH:MM time string for path '/Auto/ScheduledLatestEndTime'.",
        )

    def test_control_api_helper_validation_edges_cover_generic_paths_and_time_parsing(self) -> None:
        api = ControlApiV1Service(
            current_setting_paths=DbusWriteController.CURRENT_SETTING_PATHS,
            auto_runtime_setting_paths=set(DbusWriteController.AUTO_RUNTIME_SETTING_PATHS) | {"/Auto/Custom"},
        )

        command = api.command_from_payload(
            {"name": "set_auto_runtime_setting", "path": "/Auto/Custom", "value": {"free": "form"}},
            source="http",
        )

        self.assertEqual(command.path, "/Auto/Custom")
        self.assertEqual(command.value, {"free": "form"})
        self.assertTrue(ControlApiV1Service._always_valid_value(object()))
        self.assertTrue(ControlApiV1Service._within_auto_runtime_bounds("/Auto/Custom", 5.0))
        self.assertFalse(ControlApiV1Service._valid_auto_runtime_text("/Auto/ScheduledLatestEndTime", "1230"))
        self.assertFalse(ControlApiV1Service._valid_auto_runtime_text("/Auto/ScheduledLatestEndTime", "ab:30"))
        self.assertFalse(ControlApiV1Service._valid_auto_runtime_text("/Auto/ScheduledLatestEndTime", "12:99"))

    def test_build_control_command_from_payload_delegates_to_control_api(self) -> None:
        service = SimpleNamespace(
            virtual_autostart=0,
            _dbusservice={"/AutoStart": 0},
            _time_now=MagicMock(return_value=10.0),
            _publish_dbus_field=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
            _save_runtime_overrides=MagicMock(),
        )
        controller = DbusWriteController(WriteControllerPort(service))

        command = controller.build_control_command_from_payload({"name": "set_mode", "value": 1}, source="http")

        self.assertEqual(command.name, "set_mode")
        self.assertEqual(command.path, "/Mode")
        self.assertEqual(command.source, "http")

    def test_handle_control_command_returns_applied_result(self) -> None:
        service = SimpleNamespace(
            virtual_autostart=0,
            _dbusservice={"/AutoStart": 0},
            _time_now=MagicMock(return_value=10.0),
            _publish_dbus_field=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
            _save_runtime_overrides=MagicMock(),
        )
        service._publish_dbus_field.side_effect = self._publish_field_side_effect(service)
        controller = DbusWriteController(WriteControllerPort(service))
        command = ControlCommand(name="set_auto_start", path="/AutoStart", value=1)

        result = controller.handle_control_command(command)

        self.assertTrue(result.accepted)
        self.assertTrue(result.applied)
        self.assertTrue(result.persisted)
        self.assertEqual(result.status, "applied")
        self.assertFalse(result.external_side_effect_started)
        self.assertEqual(service.virtual_autostart, 1)
        self.assertEqual(service._dbusservice["/AutoStart"], 1)
        service._save_runtime_state.assert_called_once()
        service._save_runtime_overrides.assert_called_once()

    def test_handle_control_command_returns_rejected_result_for_reversible_failures(self) -> None:
        service = SimpleNamespace(
            virtual_autostart=0,
            _dbusservice={"/AutoStart": 0},
            _time_now=MagicMock(return_value=10.0),
            _publish_dbus_field=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(side_effect=RuntimeError("save failed")),
        )
        service._publish_dbus_field.side_effect = self._publish_field_side_effect(service)
        controller = DbusWriteController(WriteControllerPort(service))
        command = ControlCommand(name="set_auto_start", path="/AutoStart", value=1)

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
        service = SimpleNamespace(
            virtual_set_current=6.0,
            _charger_backend=backend,
            _dbusservice={"/SetCurrent": 6.0},
            _time_now=MagicMock(return_value=10.0),
            _publish_dbus_field=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(side_effect=RuntimeError("save failed")),
        )
        service._publish_dbus_field.side_effect = self._publish_field_side_effect(service)
        controller = DbusWriteController(WriteControllerPort(service))
        command = ControlCommand(name="set_current_setting", path="/SetCurrent", value=12.5)

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

    def test_execute_dispatches_commands_with_and_without_paths(self) -> None:
        api = ControlApiV1Service(
            current_setting_paths=DbusWriteController.CURRENT_SETTING_PATHS,
            auto_runtime_setting_paths=DbusWriteController.AUTO_RUNTIME_SETTING_PATHS,
        )
        controller = SimpleNamespace(
            _handle_mode_value_write=MagicMock(),
            _handle_current_setting_write=MagicMock(),
        )

        api.execute(controller, ControlCommand(name="set_mode", path="/Mode", value=1))
        api.execute(controller, ControlCommand(name="set_current_setting", path="/SetCurrent", value=12.5))

        controller._handle_mode_value_write.assert_called_once_with(1)
        controller._handle_current_setting_write.assert_called_once_with("/SetCurrent", 12.5)
