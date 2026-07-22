# SPDX-License-Identifier: GPL-3.0-or-later
"""Exhaustive behavioral contracts for transport-neutral control commands."""

from __future__ import annotations

import unittest
from typing import Any, cast
from unittest.mock import MagicMock

from venus_evcharger.control.models import ControlCommand, ControlCommandName
from venus_evcharger.control.service import ControlApiV1Service


CURRENT_TARGETS = frozenset({"set_current", "max_current"})
CUSTOM_AUTO_TARGET = "auto_custom"


class ControlServiceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        auto_targets = (
            ControlApiV1Service._FLOAT_AUTO_RUNTIME_TARGETS
            | ControlApiV1Service._STRING_AUTO_RUNTIME_TARGETS
            | ControlApiV1Service._BINARY_AUTO_RUNTIME_TARGETS
            | ControlApiV1Service._INTEGER_AUTO_RUNTIME_TARGETS
            | {CUSTOM_AUTO_TARGET}
        )
        self.api = ControlApiV1Service(
            current_setting_targets=CURRENT_TARGETS,
            auto_runtime_setting_targets=auto_targets,
        )

    def test_command_name_and_default_target_contract_is_complete(self) -> None:
        expected_names = frozenset(
            {
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
        expected_defaults: dict[ControlCommandName, str] = {
            "reset_contactor_lockout": "auto_contactor_lockout_reset",
            "reset_phase_lockout": "auto_phase_lockout_reset",
            "set_auto_start": "auto_start",
            "set_enable": "enable",
            "set_mode": "mode",
            "set_phase_selection": "phase_selection",
            "set_start_stop": "start_stop",
            "trigger_software_update": "auto_software_update_run",
        }
        self.assertEqual(ControlApiV1Service._COMMAND_NAMES, expected_names)
        self.assertEqual(ControlApiV1Service._COMMAND_DEFAULT_TARGETS, expected_defaults)
        for name in expected_names:
            self.assertTrue(ControlApiV1Service._is_command_name(name), name)
        self.assertFalse(ControlApiV1Service._is_command_name(""))
        self.assertFalse(ControlApiV1Service._is_command_name("set_unknown"))

    def test_command_for_target_builds_complete_canonical_commands(self) -> None:
        for name, target in ControlApiV1Service._COMMAND_DEFAULT_TARGETS.items():
            value: object = "P1" if name == "set_phase_selection" else 1
            with self.subTest(name=name):
                self.assertEqual(
                    self.api.command_for_target(name, target, value, source="mqtt"),
                    ControlCommand(name=name, target=target, value=value, source="mqtt"),
                )
        for target in CURRENT_TARGETS:
            self.assertEqual(
                self.api.command_for_target("set_current_setting", target, 7.5),
                ControlCommand(
                    name="set_current_setting",
                    target=target,
                    value=7.5,
                    source="internal",
                ),
            )
        opaque = {"opaque": True}
        self.assertEqual(
            self.api.command_for_target(
                "set_auto_runtime_setting",
                CUSTOM_AUTO_TARGET,
                opaque,
                source="http",
            ),
            ControlCommand(
                name="set_auto_runtime_setting",
                target=CUSTOM_AUTO_TARGET,
                value=opaque,
                source="http",
            ),
        )
        with self.assertRaisesRegex(
            ValueError,
            "^Control command 'set_mode' does not support target 'unknown'\\.$",
        ):
            self.api.command_for_target("set_mode", "unknown", 1)

    def test_named_payloads_preserve_tracking_and_resolve_optional_target(self) -> None:
        tracking = {
            "detail": "  requested by operator  ",
            "command_id": "  command-7  ",
            "idempotency_key": "  idem-9  ",
        }
        expected = ControlCommand(
            name="set_mode",
            target="mode",
            value=2,
            source="http",
            detail="requested by operator",
            command_id="command-7",
            idempotency_key="idem-9",
        )
        self.assertEqual(
            self.api.command_from_payload(
                {"name": " set_mode ", "value": 2, **tracking},
                source="http",
            ),
            expected,
        )
        self.assertEqual(
            self.api.command_from_payload(
                {"name": "set_mode", "target": " mode ", "value": 2, **tracking}
            ),
            expected,
        )

    def test_specialized_commands_require_an_explicit_target(self) -> None:
        cases = (
            (
                "set_current_setting",
                "set_current",
                6.0,
                ControlCommand("set_current_setting", "set_current", 6.0, "http"),
            ),
            (
                "set_auto_runtime_setting",
                "auto_min_soc",
                20.0,
                ControlCommand("set_auto_runtime_setting", "auto_min_soc", 20.0, "http"),
            ),
        )
        for name, target, value, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    self.api.command_from_payload(
                        {"name": name, "target": target, "value": value}
                    ),
                    expected,
                )
                with self.assertRaisesRegex(
                    ValueError,
                    f"^Control command '{name}' requires an explicit 'target'\\.$",
                ):
                    self.api.command_from_payload({"name": name, "value": value})

    def test_default_commands_accept_omitted_or_exact_targets(self) -> None:
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
            target = ControlApiV1Service._COMMAND_DEFAULT_TARGETS[name]
            expected = ControlCommand(name=name, target=target, value=value, source="http")
            with self.subTest(name=name, target="omitted"):
                self.assertEqual(
                    self.api.command_from_payload({"name": name, "value": value}),
                    expected,
                )
            with self.subTest(name=name, target="explicit"):
                self.assertEqual(
                    self.api.command_from_payload(
                        {"name": name, "target": target, "value": value}
                    ),
                    expected,
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
            (ControlApiV1Service._FLOAT_AUTO_RUNTIME_TARGETS, "float", 0.0),
            (ControlApiV1Service._STRING_AUTO_RUNTIME_TARGETS, "string", "value"),
            (ControlApiV1Service._BINARY_AUTO_RUNTIME_TARGETS, "binary", 1),
            (ControlApiV1Service._INTEGER_AUTO_RUNTIME_TARGETS, "integer", 0),
        )
        for targets, kind, default_value in groups:
            for target in targets:
                valid_value: object = default_value
                if target == "auto_learn_charge_power_alpha":
                    valid_value = 1.0
                elif target == "auto_scheduled_latest_end_time":
                    valid_value = "23:59"
                with self.subTest(target=target):
                    self.assertEqual(self.api._auto_runtime_value_kind(target), kind)
                    self.assertTrue(
                        self.api._auto_runtime_value_validator(target)(valid_value)
                    )
        self.assertEqual(self.api._auto_runtime_value_kind(CUSTOM_AUTO_TARGET), "any")
        self.assertTrue(self.api._auto_runtime_value_validator(CUSTOM_AUTO_TARGET)(object()))

        for target in ("auto_min_soc", "auto_resume_soc"):
            self.assertTrue(self.api._within_auto_runtime_bounds(target, 0.0))
            self.assertTrue(self.api._within_auto_runtime_bounds(target, 100.0))
            self.assertFalse(self.api._within_auto_runtime_bounds(target, 100.0001))
        alpha = "auto_learn_charge_power_alpha"
        self.assertFalse(self.api._within_auto_runtime_bounds(alpha, 0.0))
        self.assertTrue(self.api._within_auto_runtime_bounds(alpha, 0.0001))
        self.assertTrue(self.api._within_auto_runtime_bounds(alpha, 1.0))
        self.assertFalse(self.api._within_auto_runtime_bounds(alpha, 1.0001))

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
            ({}, "Control command payload must include 'name'."),
            ({"target": "mode", "value": 1}, "Control command payload must include 'name'."),
            ({"name": "unknown", "value": 1}, "Unsupported control command 'unknown'."),
            (
                {"name": "set_mode", "path": "/Mode", "value": 1},
                "Unsupported payload field(s): path.",
            ),
            (
                {"name": "set_mode", "value": 1, "z": 1, "a": 2},
                "Unsupported payload field(s): a, z.",
            ),
            (
                {"name": "set_mode", "target": "enable", "value": 1},
                "Control command 'set_mode' does not support target 'enable'.",
            ),
            (
                {"name": "set_current_setting", "target": "bad", "value": 1},
                "Control command 'set_current_setting' requires one of: max_current, set_current.",
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
                {"name": "set_current_setting", "target": "set_current", "value": -0.01},
                "Control command 'set_current_setting' requires a non-negative numeric value for target 'set_current'.",
            ),
            (
                {"name": "set_auto_runtime_setting", "target": "auto_min_soc", "value": 100.01},
                "Control command 'set_auto_runtime_setting' requires a numeric value between 0 and 100 for target 'auto_min_soc'.",
            ),
            (
                {"name": "set_auto_runtime_setting", "target": "auto_learn_charge_power_alpha", "value": 0.0},
                "Control command 'set_auto_runtime_setting' requires a numeric value in the interval (0, 1] for target 'auto_learn_charge_power_alpha'.",
            ),
            (
                {"name": "set_auto_runtime_setting", "target": "auto_scheduled_enabled_days", "value": " "},
                "Control command 'set_auto_runtime_setting' requires a non-empty string value for target 'auto_scheduled_enabled_days'.",
            ),
            (
                {"name": "set_auto_runtime_setting", "target": "auto_scheduled_latest_end_time", "value": "24:00"},
                "Control command 'set_auto_runtime_setting' requires a HH:MM time string for target 'auto_scheduled_latest_end_time'.",
            ),
            (
                {"name": "set_auto_runtime_setting", "target": "auto_phase_switching", "value": 2},
                "Control command 'set_auto_runtime_setting' requires a boolean or binary integer value (0 or 1) for target 'auto_phase_switching'.",
            ),
            (
                {"name": "set_auto_runtime_setting", "target": "auto_phase_mismatch_lockout_count", "value": -1},
                "Control command 'set_auto_runtime_setting' requires a non-negative integer value for target 'auto_phase_mismatch_lockout_count'.",
            ),
        )
        for payload, expected in cases:
            with self.subTest(payload=payload), self.assertRaises(ValueError) as raised:
                self.api.command_from_payload(payload)
            self.assertEqual(str(raised.exception), expected)

    def test_private_target_and_error_contracts_cover_fallbacks(self) -> None:
        self.assertEqual(
            self.api._specialized_command_target_error(
                "set_auto_runtime_setting", "auto_invalid"
            ),
            "Control command 'set_auto_runtime_setting' requires one of: "
            + ", ".join(sorted(self.api._auto_runtime_setting_targets))
            + ".",
        )
        self.assertEqual(self.api._specialized_command_target_error("set_mode", "mode"), "")
        self.assertEqual(
            self.api._auto_runtime_numeric_error("auto_resume_soc"),
            "Control command 'set_auto_runtime_setting' requires a numeric value between 0 and 100 for target 'auto_resume_soc'.",
        )
        self.assertEqual(
            self.api._auto_runtime_numeric_error("auto_stop_delay_seconds"),
            "Control command 'set_auto_runtime_setting' requires a non-negative numeric value for target 'auto_stop_delay_seconds'.",
        )
        self.assertEqual(self.api._auto_runtime_error_kind(CUSTOM_AUTO_TARGET), "generic")
        self.assertEqual(
            self.api._auto_runtime_value_error(CUSTOM_AUTO_TARGET),
            "Control command 'set_auto_runtime_setting' received an invalid value for target 'auto_custom'.",
        )

    def test_execute_rejects_a_runtime_forged_command_before_dispatch(self) -> None:
        forged_name = cast(ControlCommandName, "set_everything_on")
        controller = MagicMock()

        with self.assertRaisesRegex(
            ValueError,
            "^Unsupported control command 'set_everything_on'\\.$",
        ):
            self.api.execute(
                controller,
                ControlCommand(name=forged_name, target="mode", value=1),
            )
        controller.assert_not_called()

    def test_execute_dispatches_every_command_to_declared_handler_shape(self) -> None:
        controller = type("Controller", (), {})()
        handlers: dict[str, MagicMock] = {}
        for handler_name, _include_target in ControlApiV1Service._HANDLER_SPECS.values():
            handler = MagicMock()
            setattr(controller, handler_name, handler)
            handlers[handler_name] = handler

        commands: dict[ControlCommandName, ControlCommand] = {
            "reset_contactor_lockout": ControlCommand("reset_contactor_lockout", "auto_contactor_lockout_reset", 1),
            "reset_phase_lockout": ControlCommand("reset_phase_lockout", "auto_phase_lockout_reset", 1),
            "set_auto_runtime_setting": ControlCommand("set_auto_runtime_setting", "auto_start_surplus_watts", 1.0),
            "set_auto_start": ControlCommand("set_auto_start", "auto_start", 1),
            "set_current_setting": ControlCommand("set_current_setting", "set_current", 1.0),
            "set_enable": ControlCommand("set_enable", "enable", 1),
            "set_mode": ControlCommand("set_mode", "mode", 1),
            "set_phase_selection": ControlCommand("set_phase_selection", "phase_selection", "P1"),
            "set_start_stop": ControlCommand("set_start_stop", "start_stop", 1),
            "trigger_software_update": ControlCommand("trigger_software_update", "auto_software_update_run", 1),
        }
        for name, (handler_name, include_target) in ControlApiV1Service._HANDLER_SPECS.items():
            with self.subTest(name=name):
                command = commands[name]
                self.api.execute(controller, command)
                expected_args = (
                    (command.target, command.value)
                    if include_target
                    else (command.value,)
                )
                handlers[handler_name].assert_called_once_with(*expected_args)
                handlers[handler_name].reset_mock()


if __name__ == "__main__":
    unittest.main()
