# SPDX-License-Identifier: GPL-3.0-or-later
"""Exhaustive behavioral contracts for transport-neutral control commands."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock

from venus_evcharger.control.models import ControlCommand, ControlCommandName
from venus_evcharger.control.service import ControlApiV1Service


CURRENT_PATHS = ("/SetCurrent", "/CurrentLimit")
CUSTOM_AUTO_PATH = "/Auto/Custom"


class ControlServiceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        auto_paths = (
            ControlApiV1Service._FLOAT_AUTO_RUNTIME_PATHS
            | ControlApiV1Service._STRING_AUTO_RUNTIME_PATHS
            | ControlApiV1Service._BINARY_AUTO_RUNTIME_PATHS
            | ControlApiV1Service._INTEGER_AUTO_RUNTIME_PATHS
            | {CUSTOM_AUTO_PATH}
        )
        self.api = ControlApiV1Service(
            current_setting_paths=CURRENT_PATHS,
            auto_runtime_setting_paths=set(auto_paths),
        )

    def test_command_name_and_direct_path_contract_is_complete(self) -> None:
        expected_names = frozenset(
            {
                "legacy_unknown_write",
                "reset_contactor_lockout",
                "reset_phase_lockout",
                "set_auto_runtime_setting",
                "set_auto_start",
                "set_current_setting",
                "set_enable",
                "set_mode",
                "set_phase_selection",
                "set_start_stop",
                "trigger_software_update",
            }
        )
        expected_paths: dict[str, ControlCommandName] = {
            "/Mode": "set_mode",
            "/AutoStart": "set_auto_start",
            "/StartStop": "set_start_stop",
            "/Enable": "set_enable",
            "/PhaseSelection": "set_phase_selection",
            "/Auto/PhaseLockoutReset": "reset_phase_lockout",
            "/Auto/ContactorLockoutReset": "reset_contactor_lockout",
            "/Auto/SoftwareUpdateRun": "trigger_software_update",
        }
        self.assertEqual(ControlApiV1Service._COMMAND_NAMES, expected_names)
        self.assertEqual(ControlApiV1Service._DIRECT_PATH_COMMANDS, expected_paths)
        self.assertEqual(
            ControlApiV1Service._COMMAND_DEFAULT_PATHS,
            {name: path for path, name in expected_paths.items()},
        )
        for name in expected_names:
            self.assertTrue(ControlApiV1Service._is_command_name(name), name)
        self.assertFalse(ControlApiV1Service._is_command_name(""))
        self.assertFalse(ControlApiV1Service._is_command_name("set_unknown"))

    def test_every_write_path_produces_the_complete_canonical_command(self) -> None:
        for path, name in ControlApiV1Service._DIRECT_PATH_COMMANDS.items():
            with self.subTest(path=path):
                self.assertEqual(
                    self.api.command_for_write(path, 1, source="mqtt"),
                    ControlCommand(name=name, path=path, value=1, source="mqtt"),
                )
        for path in CURRENT_PATHS:
            self.assertEqual(
                self.api.command_for_write(path, 7.5, source="internal"),
                ControlCommand(name="set_current_setting", path=path, value=7.5, source="internal"),
            )
        self.assertEqual(
            self.api.command_for_write(CUSTOM_AUTO_PATH, {"opaque": True}, source="http"),
            ControlCommand(
                name="set_auto_runtime_setting",
                path=CUSTOM_AUTO_PATH,
                value={"opaque": True},
                source="http",
            ),
        )
        self.assertEqual(
            self.api.command_for_write("/Unknown", 9, source="dbus"),
            ControlCommand(
                name="legacy_unknown_write",
                path="/Unknown",
                value=9,
                source="dbus",
                detail="No canonical Control API command is registered for this write path.",
            ),
        )
        self.assertEqual(
            self.api.command_for_write("/Unknown", 9, source="mqtt"),
            ControlCommand(
                name="legacy_unknown_write",
                path="/Unknown",
                value=9,
                source="mqtt",
                detail="No canonical Control API command is registered for this write path.",
            ),
        )
        self.assertEqual(
            self.api.command_for_dbus_write("/Mode", 2),
            ControlCommand(name="set_mode", path="/Mode", value=2, source="dbus"),
        )
        self.assertEqual(
            self.api.command_for_write("/Mode", 1),
            ControlCommand(name="set_mode", path="/Mode", value=1, source="dbus"),
        )

    def test_named_and_path_payloads_preserve_and_normalize_all_fields(self) -> None:
        tracking = {
            "detail": "  requested by operator  ",
            "command_id": "  command-7  ",
            "idempotency_key": "  idem-9  ",
        }
        self.assertEqual(
            self.api.command_from_payload(
                {"name": " set_mode ", "value": 2, **tracking},
                source="http",
            ),
            ControlCommand(
                name="set_mode",
                path="/Mode",
                value=2,
                source="http",
                detail="requested by operator",
                command_id="command-7",
                idempotency_key="idem-9",
            ),
        )
        self.assertEqual(
            self.api.command_from_payload({"path": "/Mode", "value": 0}),
            ControlCommand(name="set_mode", path="/Mode", value=0, source="http"),
        )
        self.assertEqual(
            self.api.command_from_payload(
                {"path": " /Mode ", "value": 1, **tracking},
                source="mqtt",
            ),
            ControlCommand(
                name="set_mode",
                path="/Mode",
                value=1,
                source="mqtt",
                detail="requested by operator",
                command_id="command-7",
                idempotency_key="idem-9",
            ),
        )

    def test_all_default_commands_accept_their_exact_path_and_valid_value(self) -> None:
        values: dict[ControlCommandName, Any] = {
            "reset_contactor_lockout": 1,
            "reset_phase_lockout": False,
            "set_auto_start": True,
            "set_enable": 0,
            "set_mode": 2,
            "set_phase_selection": "P1_P2_P3",
            "set_start_stop": 1,
            "trigger_software_update": False,
        }
        for name, value in values.items():
            path = ControlApiV1Service._COMMAND_DEFAULT_PATHS[name]
            with self.subTest(name=name):
                self.assertEqual(
                    self.api.command_from_payload({"name": name, "path": path, "value": value}),
                    ControlCommand(name=name, path=path, value=value, source="http"),
                )

    def test_primitive_value_validators_have_exact_type_boundaries(self) -> None:
        for value in (True, False, 0, 1):
            self.assertTrue(ControlApiV1Service._is_bool_or_binary_int(value), repr(value))
        for value in (-1, 2, 0.0, 1.0, "0", None):
            self.assertFalse(ControlApiV1Service._is_bool_or_binary_int(value), repr(value))
        for value in (-1, 0, 1, -0.5, 0.5):
            self.assertTrue(ControlApiV1Service._is_numeric(value), repr(value))
        for value in (True, False, "1", None):
            self.assertFalse(ControlApiV1Service._is_numeric(value), repr(value))
        for value in (0, 1, 2):
            self.assertTrue(ControlApiV1Service._is_known_mode_value(value), repr(value))
        for value in (-1, 3, True, 1.0, "1"):
            self.assertFalse(ControlApiV1Service._is_known_mode_value(value), repr(value))

    def test_runtime_setting_groups_and_boundaries_are_exhaustive(self) -> None:
        groups = (
            (ControlApiV1Service._FLOAT_AUTO_RUNTIME_PATHS, "float", 0.0),
            (ControlApiV1Service._STRING_AUTO_RUNTIME_PATHS, "string", "value"),
            (ControlApiV1Service._BINARY_AUTO_RUNTIME_PATHS, "binary", 1),
            (ControlApiV1Service._INTEGER_AUTO_RUNTIME_PATHS, "integer", 0),
        )
        for paths, kind, valid_value in groups:
            for path in paths:
                with self.subTest(path=path):
                    self.assertEqual(self.api._auto_runtime_value_kind(path), kind)
                    if path == "/Auto/LearnChargePowerAlpha":
                        valid_value = 1.0
                    elif path == "/Auto/ScheduledLatestEndTime":
                        valid_value = "23:59"
                    self.assertTrue(self.api._auto_runtime_value_validator(path)(valid_value))
        self.assertEqual(self.api._auto_runtime_value_kind(CUSTOM_AUTO_PATH), "any")
        self.assertTrue(self.api._auto_runtime_value_validator(CUSTOM_AUTO_PATH)(object()))

        for path in ("/Auto/MinSoc", "/Auto/ResumeSoc"):
            self.assertTrue(self.api._within_auto_runtime_bounds(path, 0.0))
            self.assertTrue(self.api._within_auto_runtime_bounds(path, 100.0))
            self.assertFalse(self.api._within_auto_runtime_bounds(path, 100.0001))
        self.assertFalse(self.api._within_auto_runtime_bounds("/Auto/LearnChargePowerAlpha", 0.0))
        self.assertTrue(self.api._within_auto_runtime_bounds("/Auto/LearnChargePowerAlpha", 0.0001))
        self.assertTrue(self.api._within_auto_runtime_bounds("/Auto/LearnChargePowerAlpha", 1.0))
        self.assertFalse(self.api._within_auto_runtime_bounds("/Auto/LearnChargePowerAlpha", 1.0001))

    def test_scheduled_time_contract_covers_format_and_numeric_edges(self) -> None:
        for value in ("00:00", "0:0", "09:07", "23:59", " 12:30 "):
            self.assertTrue(ControlApiV1Service._is_valid_hour_minute(value), value)
        for value in ("", "12", ":", "1:2:3", "12-30", "a:30", "12:b", "-1:00", "24:00", "23:60"):
            self.assertFalse(ControlApiV1Service._is_valid_hour_minute(value), value)
        self.assertTrue(ControlApiV1Service._hour_in_range("0"))
        self.assertTrue(ControlApiV1Service._hour_in_range("23"))
        self.assertFalse(ControlApiV1Service._hour_in_range("24"))
        self.assertTrue(ControlApiV1Service._minute_in_range("0"))
        self.assertTrue(ControlApiV1Service._minute_in_range("59"))
        self.assertFalse(ControlApiV1Service._minute_in_range("60"))

    def test_invalid_payloads_return_exact_contract_errors(self) -> None:
        cases = (
            ({}, "Control command payload must include either 'name' or 'path'."),
            ({"path": " ", "value": 1}, "Control command payload field 'path' must be a non-empty string."),
            ({"path": "/Unknown", "value": 1}, "Unsupported control path '/Unknown'."),
            ({"name": "unknown", "value": 1}, "Unsupported control command 'unknown'."),
            (
                {"name": "set_mode", "value": 1, "z": 1, "a": 2},
                "Unsupported payload field(s): a, z.",
            ),
            (
                {"name": "set_mode", "path": "/Enable", "value": 1},
                "Control command 'set_mode' does not support path '/Enable'.",
            ),
            (
                {"name": "set_current_setting", "value": 1},
                "Control command 'set_current_setting' requires an explicit 'path'.",
            ),
            (
                {"name": "set_current_setting", "path": "/Bad", "value": 1},
                "Control command 'set_current_setting' requires one of: /CurrentLimit, /SetCurrent.",
            ),
            (
                {"name": "set_mode", "value": True},
                "Control command 'set_mode' requires one of: 0, 1, 2.",
            ),
            (
                {"name": "set_phase_selection", "value": "P4"},
                "Control command 'set_phase_selection' requires one of: P1, P1_P2, P1_P2_P3.",
            ),
            (
                {"name": "set_current_setting", "path": "/SetCurrent", "value": -0.01},
                "Control command 'set_current_setting' requires a non-negative numeric value for path '/SetCurrent'.",
            ),
            (
                {"name": "set_auto_runtime_setting", "path": "/Auto/MinSoc", "value": 100.01},
                "Control command 'set_auto_runtime_setting' requires a numeric value between 0 and 100 for path '/Auto/MinSoc'.",
            ),
            (
                {"name": "set_auto_runtime_setting", "path": "/Auto/LearnChargePowerAlpha", "value": 0.0},
                "Control command 'set_auto_runtime_setting' requires a numeric value in the interval (0, 1] for path '/Auto/LearnChargePowerAlpha'.",
            ),
            (
                {"name": "set_auto_runtime_setting", "path": "/Auto/ScheduledEnabledDays", "value": " "},
                "Control command 'set_auto_runtime_setting' requires a non-empty string value for path '/Auto/ScheduledEnabledDays'.",
            ),
            (
                {"name": "set_auto_runtime_setting", "path": "/Auto/ScheduledLatestEndTime", "value": "24:00"},
                "Control command 'set_auto_runtime_setting' requires a HH:MM time string for path '/Auto/ScheduledLatestEndTime'.",
            ),
            (
                {"name": "set_auto_runtime_setting", "path": "/Auto/PhaseSwitching", "value": 2},
                "Control command 'set_auto_runtime_setting' requires a boolean or binary integer value (0 or 1) for path '/Auto/PhaseSwitching'.",
            ),
            (
                {"name": "set_auto_runtime_setting", "path": "/Auto/PhaseMismatchLockoutCount", "value": -1},
                "Control command 'set_auto_runtime_setting' requires a non-negative integer value for path '/Auto/PhaseMismatchLockoutCount'.",
            ),
        )
        for payload, expected in cases:
            with self.subTest(payload=payload), self.assertRaises(ValueError) as raised:
                self.api.command_from_payload(payload)
            self.assertEqual(str(raised.exception), expected)

    def test_private_path_and_error_contracts_cover_all_fallbacks(self) -> None:
        self.assertEqual(self.api._validated_explicit_path({"path": 17}), "17")
        with self.assertRaises(ValueError) as raised:
            self.api._validated_explicit_path({})
        self.assertEqual(
            str(raised.exception),
            "Control command payload field 'path' must be a non-empty string.",
        )
        self.assertEqual(
            self.api._specialized_command_path_error("set_auto_runtime_setting", "/Auto/Invalid"),
            "Control command 'set_auto_runtime_setting' requires one of: "
            + ", ".join(sorted(self.api._auto_runtime_setting_paths))
            + ".",
        )
        self.assertEqual(
            self.api._specialized_command_path_error("legacy_unknown_write", "/Legacy"),
            "",
        )
        self.assertEqual(
            self.api._specialized_command_path_error("set_mode", "/Mode"),
            "",
        )
        self.assertEqual(
            self.api._auto_runtime_numeric_error("/Auto/ResumeSoc"),
            "Control command 'set_auto_runtime_setting' requires a numeric value between 0 and 100 for path '/Auto/ResumeSoc'.",
        )
        self.assertEqual(
            self.api._auto_runtime_numeric_error("/Auto/StopDelaySeconds"),
            "Control command 'set_auto_runtime_setting' requires a non-negative numeric value for path '/Auto/StopDelaySeconds'.",
        )
        self.assertEqual(self.api._auto_runtime_error_kind(CUSTOM_AUTO_PATH), "generic")
        self.assertEqual(
            self.api._auto_runtime_value_error(CUSTOM_AUTO_PATH),
            "Control command 'set_auto_runtime_setting' received an invalid value for path '/Auto/Custom'.",
        )
        self.assertEqual(
            self.api._command_value_error("legacy_unknown_write", "/Legacy"),
            "Control command 'legacy_unknown_write' received an invalid value for path '/Legacy'.",
        )
        fallback_validator = self.api._command_value_validator("legacy_unknown_write", "/Legacy")
        self.assertTrue(fallback_validator(object()))

    def test_path_payloads_apply_path_specific_validation(self) -> None:
        cases = (
            (
                {"path": "/SetCurrent", "value": -1},
                "Control command 'set_current_setting' requires a non-negative numeric value for path '/SetCurrent'.",
            ),
            (
                {"path": "/Auto/MinSoc", "value": 101},
                "Control command 'set_auto_runtime_setting' requires a numeric value between 0 and 100 for path '/Auto/MinSoc'.",
            ),
        )
        for payload, expected in cases:
            with self.subTest(payload=payload), self.assertRaises(ValueError) as raised:
                self.api.command_from_payload(payload)
            self.assertEqual(str(raised.exception), expected)

    def test_execute_dispatches_every_command_to_its_declared_handler_shape(self) -> None:
        controller = type("Controller", (), {})()
        handlers: dict[str, MagicMock] = {}
        for handler_name, _include_path in ControlApiV1Service._HANDLER_SPECS.values():
            handler = MagicMock()
            setattr(controller, handler_name, handler)
            handlers[handler_name] = handler

        for name, (handler_name, include_path) in ControlApiV1Service._HANDLER_SPECS.items():
            with self.subTest(name=name):
                command = ControlCommand(name=name, path=f"/{name}", value={"value": name})
                self.api.execute(controller, command)
                expected_args = (command.path, command.value) if include_path else (command.value,)
                handlers[handler_name].assert_called_once_with(*expected_args)
                handlers[handler_name].reset_mock()


if __name__ == "__main__":
    unittest.main()
