# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact command-boundary contracts for the semantic control writer."""

from __future__ import annotations

import unittest
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.auto.policy import AutoPolicy
from venus_evcharger.control import ControlCommand, ControlResult
from venus_evcharger.controllers import write as write_module
from venus_evcharger.controllers.write import ControlWriteController


class TestWriteControllerConstructionContracts(unittest.TestCase):
    def test_constructor_builds_control_service_from_declared_target_sets(self) -> None:
        port = SimpleNamespace()
        control_api = MagicMock()
        with patch.object(write_module, "ControlApiV1Service", return_value=control_api) as api_type:
            controller = ControlWriteController(port)
        api_type.assert_called_once_with(
            current_setting_targets=ControlWriteController.CURRENT_SETTING_TARGETS,
            auto_runtime_setting_targets=ControlWriteController.AUTO_RUNTIME_SETTING_TARGETS,
        )
        self.assertIs(controller.port, port)
        self.assertIs(controller._control_api, control_api)
        self.assertIs(controller._external_side_effect_started, False)

    def test_failure_detail_uses_message_or_exception_type(self) -> None:
        self.assertEqual(ControlWriteController._write_failure_detail(ValueError("bad")), "bad")
        self.assertEqual(ControlWriteController._write_failure_detail(ValueError()), "ValueError")


class TestWriteControllerCommandBoundaryContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.port = SimpleNamespace()
        self.controller = ControlWriteController(self.port)
        self.controller._control_api = MagicMock()

    def test_semantic_targets_build_exact_canonical_commands(self) -> None:
        command = MagicMock(spec=ControlCommand)
        self.controller._control_api.command_for_target.return_value = command
        self.assertIs(
            self.controller.build_control_command(
                "set_mode", "mode", 2, source="control-surface"
            ),
            command,
        )
        self.controller._control_api.command_for_target.assert_called_once_with(
            "set_mode", "mode", 2, source="control-surface"
        )

        self.controller._control_api.command_for_target.reset_mock()
        self.controller.build_control_command("set_enable", "enable", 1)
        self.controller._control_api.command_for_target.assert_called_once_with(
            "set_enable", "enable", 1, source="internal"
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
            begin_publication_transaction=MagicMock(),
            commit_publication_transaction=MagicMock(),
            discard_publication_transaction=MagicMock(),
        )
        controller = ControlWriteController(port)
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
        port.begin_publication_transaction.assert_called_once_with()
        port.commit_publication_transaction.assert_called_once_with()
        result.assert_called_once_with(command, external_side_effect_started=False)
        self.assertIs(controller._external_side_effect_started, False)

    def test_concurrent_commands_are_serialized_across_the_whole_transaction(self) -> None:
        service = SimpleNamespace()
        port = SimpleNamespace(
            _service=service,
            save_runtime_state=MagicMock(),
            save_runtime_overrides=MagicMock(),
            begin_publication_transaction=MagicMock(),
            commit_publication_transaction=MagicMock(),
            discard_publication_transaction=MagicMock(),
        )
        controller = ControlWriteController(port)
        first_entered = Event()
        release_first = Event()
        second_entered = Event()
        calls = 0

        def execute(_controller: object, _command: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                first_entered.set()
                self.assertTrue(release_first.wait(2.0))
            else:
                second_entered.set()

        controller._control_api = MagicMock()
        controller._control_api.execute.side_effect = execute
        commands = (MagicMock(spec=ControlCommand), MagicMock(spec=ControlCommand))
        with patch.object(controller, "_snapshot_write_state", return_value="snapshot"):
            first = Thread(target=controller.handle_control_command, args=(commands[0],))
            second = Thread(target=controller.handle_control_command, args=(commands[1],))
            first.start()
            self.assertTrue(first_entered.wait(2.0))
            second.start()
            self.assertFalse(second_entered.wait(0.05))
            release_first.set()
            first.join(2.0)
            second.join(2.0)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertTrue(second_entered.is_set())
        self.assertEqual(port.begin_publication_transaction.call_count, 2)
        self.assertEqual(port.commit_publication_transaction.call_count, 2)

    def test_built_command_is_forwarded_to_the_canonical_handler(self) -> None:
        command = MagicMock(spec=ControlCommand)
        result = SimpleNamespace(accepted=True)
        with (
            patch.object(self.controller, "build_control_command", return_value=command) as build,
            patch.object(self.controller, "handle_control_command", return_value=result) as handle,
        ):
            built = self.controller.build_control_command(
                "set_mode", "mode", 2, source="control-surface"
            )
            self.assertTrue(self.controller.handle_control_command(built).accepted)
        build.assert_called_once_with(
            "set_mode", "mode", 2, source="control-surface"
        )
        handle.assert_called_once_with(command)

    def test_unsupported_target_is_rejected_before_state_or_persistence_work(self) -> None:
        controller = ControlWriteController(SimpleNamespace())

        with self.assertRaisesRegex(
            ValueError,
            "^Control command 'set_mode' does not support target 'unknown'\\.$",
        ):
            controller.build_control_command("set_mode", "unknown", 1)


class TestWriteControllerRuntimeSettingContracts(unittest.TestCase):
    def test_apply_policy_setting_updates_the_canonical_object(self) -> None:
        policy = AutoPolicy()
        port = SimpleNamespace(auto_policy=policy, validate_runtime_config=MagicMock())

        self.assertEqual(
            ControlWriteController._apply_auto_runtime_setting(
                port,
                "auto_start_surplus_watts",
                "123.5",
            ),
            123.5,
        )

        self.assertEqual(policy.normal_profile.start_surplus_watts, 123.5)
        self.assertFalse(hasattr(port, "auto_start_surplus_watts"))
        port.validate_runtime_config.assert_called_once_with()

    def test_apply_runtime_setting_uses_its_declared_normalizer(self) -> None:
        port = SimpleNamespace(validate_runtime_config=MagicMock())
        runtime_normalizer = MagicMock(return_value="normalized")
        specs = {
            "runtime": ("runtime_value", runtime_normalizer),
        }
        with patch.object(ControlWriteController, "AUTO_RUNTIME_SETTING_SPECS", specs):
            self.assertEqual(
                ControlWriteController._apply_auto_runtime_setting(port, "runtime", " value "),
                "normalized",
            )
        runtime_normalizer.assert_called_once_with(" value ")
        port.validate_runtime_config.assert_called_once_with()
        self.assertEqual(port.runtime_value, "normalized")

    def test_runtime_setting_handler_publishes_normalized_value_once(self) -> None:
        port = SimpleNamespace(time_now=MagicMock(return_value=50.0), publish_field=MagicMock())
        controller = ControlWriteController(port)
        with (
            patch.object(controller, "_apply_auto_runtime_setting", return_value=7.5) as apply,
            patch.object(ControlWriteController, "AUTO_RUNTIME_SETTING_FIELDS", {"setting": "field"}),
        ):
            controller._handle_auto_runtime_setting_write("setting", "7.5")
        apply.assert_called_once_with(port, "setting", "7.5")
        port.publish_field.assert_called_once_with("field", 7.5, 50.0, force=True)

    def test_value_adapters_have_exact_normalization(self) -> None:
        controller = ControlWriteController(SimpleNamespace())
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


if __name__ == "__main__":
    unittest.main()
