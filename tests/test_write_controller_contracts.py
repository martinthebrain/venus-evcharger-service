# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact command-boundary contracts for the DBus write controller."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from venus_evcharger.control import ControlCommand, ControlResult
from venus_evcharger.controllers import write as write_module
from venus_evcharger.controllers.write import DbusWriteController


class TestWriteControllerConstructionContracts(unittest.TestCase):
    def test_constructor_builds_control_service_from_declared_path_sets(self) -> None:
        port = SimpleNamespace()
        control_api = MagicMock()
        with patch.object(write_module, "ControlApiV1Service", return_value=control_api) as api_type:
            controller = DbusWriteController(port)
        api_type.assert_called_once_with(
            current_setting_paths=DbusWriteController.CURRENT_SETTING_PATHS,
            auto_runtime_setting_paths=DbusWriteController.AUTO_RUNTIME_SETTING_PATHS,
        )
        self.assertIs(controller.port, port)
        self.assertIs(controller._control_api, control_api)
        self.assertIs(controller._external_side_effect_started, False)

    def test_failure_detail_uses_message_or_exception_type(self) -> None:
        self.assertEqual(DbusWriteController._write_failure_detail(ValueError("bad")), "bad")
        self.assertEqual(DbusWriteController._write_failure_detail(ValueError()), "ValueError")


class TestWriteControllerCommandBoundaryContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.port = SimpleNamespace()
        self.controller = DbusWriteController(self.port)
        self.controller._control_api = MagicMock()

    def test_transport_writes_build_exact_canonical_commands(self) -> None:
        command = MagicMock(spec=ControlCommand)
        self.controller._control_api.command_for_write.return_value = command
        self.assertIs(
            self.controller.build_control_command("/Mode", 2, source="dbus"),
            command,
        )
        self.controller._control_api.command_for_write.assert_called_once_with(
            "/Mode", 2, source="dbus"
        )

        self.controller._control_api.command_for_write.reset_mock()
        self.controller.build_control_command("/Enable", 1)
        self.controller._control_api.command_for_write.assert_called_once_with(
            "/Enable", 1, source="dbus"
        )

    def test_structured_payload_builds_exact_canonical_command(self) -> None:
        payload = {"name": "set_mode", "value": 2}
        command = MagicMock(spec=ControlCommand)
        self.controller._control_api.command_from_payload.return_value = command
        self.assertIs(
            self.controller.build_control_command_from_payload(payload, source="http"),
            command,
        )
        self.controller._control_api.command_from_payload.assert_called_once_with(
            payload, source="http"
        )
        self.controller._control_api.command_from_payload.reset_mock()
        self.controller.build_control_command_from_payload(payload)
        self.controller._control_api.command_from_payload.assert_called_once_with(
            payload, source="http"
        )

    def test_successful_control_command_executes_and_persists_once(self) -> None:
        service = SimpleNamespace()
        port = SimpleNamespace(
            _service=service,
            save_runtime_state=MagicMock(),
            save_runtime_overrides=MagicMock(),
        )
        controller = DbusWriteController(port)
        command = MagicMock(spec=ControlCommand)
        applied = MagicMock(spec=ControlResult)
        controller._control_api = MagicMock()
        with (
            patch.object(controller, "_snapshot_write_state", return_value="snapshot") as snapshot,
            patch.object(write_module.ControlResult, "applied_result", return_value=applied) as result,
        ):
            self.assertIs(controller.handle_control_command(command), applied)
        snapshot.assert_called_once_with(service)
        controller._control_api.execute.assert_called_once_with(controller, command)
        port.save_runtime_state.assert_called_once_with()
        port.save_runtime_overrides.assert_called_once_with()
        result.assert_called_once_with(command, external_side_effect_started=False)
        self.assertIs(controller._external_side_effect_started, False)

    def test_handle_write_returns_the_canonical_result_acceptance(self) -> None:
        command = MagicMock(spec=ControlCommand)
        result = SimpleNamespace(accepted=True)
        with (
            patch.object(self.controller, "build_control_command", return_value=command) as build,
            patch.object(self.controller, "handle_control_command", return_value=result) as handle,
        ):
            self.assertTrue(self.controller.handle_write("/Mode", 2))
        build.assert_called_once_with("/Mode", 2, source="dbus")
        handle.assert_called_once_with(command)


class TestWriteControllerRuntimeSettingContracts(unittest.TestCase):
    def test_sync_auto_policy_rebuilds_validates_and_revalidates_runtime(self) -> None:
        service = SimpleNamespace()
        port = SimpleNamespace(_service=service, validate_runtime_config=MagicMock())
        policy = SimpleNamespace()
        with (
            patch.object(write_module.AutoPolicy, "from_service", return_value=policy) as build,
            patch.object(write_module, "validate_auto_policy") as validate,
        ):
            DbusWriteController._sync_auto_policy_runtime(port)
        build.assert_called_once_with(service)
        validate.assert_called_once_with(policy, service)
        port.validate_runtime_config.assert_called_once_with()

    def test_apply_runtime_setting_uses_declared_normalizer_and_validation_kind(self) -> None:
        port = SimpleNamespace(_service=SimpleNamespace(), validate_runtime_config=MagicMock())
        policy_normalizer = MagicMock(return_value=123.5)
        runtime_normalizer = MagicMock(return_value="normalized")
        specs = {
            "/Policy": ("policy_value", policy_normalizer, "policy"),
            "/Runtime": ("runtime_value", runtime_normalizer, "runtime"),
        }
        with (
            patch.object(DbusWriteController, "AUTO_RUNTIME_SETTING_SPECS", specs),
            patch.object(DbusWriteController, "_sync_auto_policy_runtime") as sync,
        ):
            self.assertEqual(
                DbusWriteController._apply_auto_runtime_setting(port, "/Policy", "123.5"),
                123.5,
            )
            self.assertEqual(
                DbusWriteController._apply_auto_runtime_setting(port, "/Runtime", " value "),
                "normalized",
            )
        policy_normalizer.assert_called_once_with("123.5")
        runtime_normalizer.assert_called_once_with(" value ")
        sync.assert_called_once_with(port)
        port.validate_runtime_config.assert_called_once_with()
        self.assertEqual(port.policy_value, 123.5)
        self.assertEqual(port.runtime_value, "normalized")

    def test_runtime_setting_handler_publishes_normalized_value_once(self) -> None:
        port = SimpleNamespace(time_now=MagicMock(return_value=50.0), publish_dbus_field=MagicMock())
        controller = DbusWriteController(port)
        with (
            patch.object(controller, "_apply_auto_runtime_setting", return_value=7.5) as apply,
            patch.object(DbusWriteController, "AUTO_RUNTIME_SETTING_FIELDS", {"/Setting": "field"}),
        ):
            controller._handle_auto_runtime_setting_write("/Setting", "7.5")
        apply.assert_called_once_with(port, "/Setting", "7.5")
        port.publish_dbus_field.assert_called_once_with("field", 7.5, 50.0, force=True)

    def test_value_adapters_and_unknown_write_have_exact_normalization(self) -> None:
        controller = DbusWriteController(SimpleNamespace())
        with (
            patch.object(controller, "_handle_mode_write") as mode,
            patch.object(controller, "_handle_startstop_write") as startstop,
            patch.object(controller, "_handle_enable_write") as enable,
        ):
            controller._handle_mode_value_write("2")
            controller._handle_startstop_value_write("0")
            controller._handle_enable_value_write("1")
        mode.assert_called_once_with(2)
        startstop.assert_called_once_with(False)
        enable.assert_called_once_with(True)
        self.assertIsNone(controller._handle_unknown_write("/Unknown", object()))


if __name__ == "__main__":
    unittest.main()
