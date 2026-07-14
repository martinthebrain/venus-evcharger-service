# SPDX-License-Identifier: GPL-3.0-or-later
"""Focused contracts for RAM-staged runtime override persistence."""

from __future__ import annotations

import configparser
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.controllers import state_runtime_overrides
from venus_evcharger.controllers.state import ServiceStateController
from venus_evcharger.controllers.state_specs import RUNTIME_OVERRIDE_SPECS, RuntimeOverrideSpec


def _normalize_mode(value: object) -> int:
    return int(value)


class TestStateRuntimeOverridesContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SimpleNamespace()
        self.controller = ServiceStateController(self.service, _normalize_mode)

    @staticmethod
    def _defaults(**values: str) -> configparser.SectionProxy:
        parser = configparser.ConfigParser()
        parser["DEFAULT"] = values
        return parser["DEFAULT"]

    def test_path_contract_uses_device_instance_and_explicit_override(self) -> None:
        self.assertEqual(
            self.controller.runtime_overrides_path(self._defaults()),
            "/run/dbus-venus-evcharger-overrides-60.ini",
        )
        self.assertEqual(
            self.controller.runtime_overrides_path(self._defaults(DeviceInstance=" 71 ")),
            "/run/dbus-venus-evcharger-overrides-71.ini",
        )
        self.assertEqual(
            self.controller.runtime_overrides_path(self._defaults(DeviceInstance=" ")),
            "/run/dbus-venus-evcharger-overrides-60.ini",
        )
        self.assertEqual(
            self.controller.runtime_overrides_path(
                self._defaults(DeviceInstance="71", RuntimeOverridesPath=" /tmp/custom.ini ")
            ),
            "/tmp/custom.ini",
        )

    def test_override_item_and_renderer_contracts_cover_every_value_kind(self) -> None:
        mode_spec = next(spec for spec in RUNTIME_OVERRIDE_SPECS if spec.config_key == "Mode")
        self.assertEqual(self.controller._normalized_runtime_override_item(" Mode ", " 2 "), ("Mode", "2"))
        self.assertIsNone(self.controller._normalized_runtime_override_item("Unknown", 1))
        self.assertEqual(
            self.controller._normalized_runtime_override_section_items(
                [("Mode", "2"), ("Unknown", "x"), ("Mode", "1")]
            ),
            {"Mode": "1"},
        )
        cases = (
            (RuntimeOverrideSpec("/x", "x", "x", "bool"), "yes", "1"),
            (RuntimeOverrideSpec("/x", "x", "x", "int"), "4", "4"),
            (RuntimeOverrideSpec("/x", "x", "x", "phase"), "P1_P2", "P1_P2"),
            (RuntimeOverrideSpec("/x", "x", "x", "weekday_set"), "Sat,Sun", "Sat,Sun"),
            (RuntimeOverrideSpec("/x", "x", "x", "hhmm"), " 7:05 ", "07:05"),
            (RuntimeOverrideSpec("/x", "x", "x", "float"), "2.5", "2.5"),
            (RuntimeOverrideSpec("/x", "x", "x", "unknown"), "3.5", "3.5"),
        )
        for spec, raw, expected in cases:
            with self.subTest(kind=spec.value_kind):
                self.assertEqual(self.controller._override_value_as_text(spec, raw), expected)
        self.assertEqual(
            self.controller._override_value_as_text(RuntimeOverrideSpec("/x", "x", "x", "hhmm"), "invalid"),
            "06:30",
        )
        self.assertEqual(self.controller._override_value_as_text(mode_spec, 2), "2")

    def test_default_values_are_exact_for_every_kind(self) -> None:
        expected = {
            "bool": 0,
            "int": 0,
            "phase": "P1",
            "weekday_set": (0, 1, 2, 3, 4),
            "hhmm": "06:30",
            "float": 0.0,
            "unknown": 0.0,
        }
        for kind, value in expected.items():
            spec = RuntimeOverrideSpec("/x", "x", "x", kind)
            actual = self.controller._runtime_override_default_value(spec)
            self.assertEqual(actual, value)
            self.assertIs(type(actual), type(value))

    def test_current_overrides_cover_every_spec_and_missing_attribute_default(self) -> None:
        values = self.controller.current_runtime_overrides()
        self.assertEqual(set(values), {spec.config_key for spec in RUNTIME_OVERRIDE_SPECS})
        self.assertEqual(len(values), len(RUNTIME_OVERRIDE_SPECS))
        self.assertEqual(values["Mode"], "0")
        self.assertEqual(values["PhaseSelection"], "P1")
        self.assertEqual(values["AutoScheduledEnabledDays"], "Mon,Tue,Wed,Thu,Fri")
        self.assertEqual(values["AutoScheduledLatestEndTime"], "06:30")
        self.service.virtual_mode = 2
        self.service.requested_phase_selection = "P1_P2"
        values = self.controller.current_runtime_overrides()
        self.assertEqual(values["Mode"], "2")
        self.assertEqual(values["PhaseSelection"], "P1_P2")

        sentinel = object()
        with (
            patch.object(self.controller, "_runtime_override_default_value", return_value=sentinel) as default,
            patch.object(self.controller, "_override_value_as_text", return_value="rendered") as render,
            patch("venus_evcharger.controllers.state_runtime_overrides.RUNTIME_OVERRIDE_SPECS", (RUNTIME_OVERRIDE_SPECS[0],)),
        ):
            del self.service.virtual_mode
            self.assertEqual(self.controller.current_runtime_overrides(), {"Mode": "rendered"})
        default.assert_called_once_with(RUNTIME_OVERRIDE_SPECS[0])
        render.assert_called_once_with(RUNTIME_OVERRIDE_SPECS[0], sentinel)

    def test_interval_clock_due_and_pending_payload_boundaries_are_exact(self) -> None:
        self.assertEqual(self.controller._runtime_override_write_min_interval_seconds(SimpleNamespace()), 1.0)
        self.assertEqual(
            self.controller._runtime_override_write_min_interval_seconds(
                SimpleNamespace(runtime_overrides_write_min_interval_seconds=-2)
            ),
            0.0,
        )
        self.assertEqual(
            self.controller._runtime_override_write_min_interval_seconds(
                SimpleNamespace(runtime_overrides_write_min_interval_seconds=2.5)
            ),
            2.5,
        )
        clock = MagicMock(return_value="7.5")
        with patch("venus_evcharger.controllers.state_runtime_overrides.time.time", return_value=9.0) as system:
            self.assertEqual(self.controller._runtime_now(SimpleNamespace(_time_now=clock)), 7.5)
        clock.assert_called_once_with()
        system.assert_called_once_with()
        self.assertTrue(self.controller._runtime_override_write_due(SimpleNamespace(), 5.0))
        self.assertFalse(
            self.controller._runtime_override_write_due(
                SimpleNamespace(_runtime_overrides_pending_due_at=5.1), 5.0
            )
        )
        self.assertTrue(
            self.controller._runtime_override_write_due(
                SimpleNamespace(_runtime_overrides_pending_due_at=5.0), 5.0
            )
        )
        self.assertEqual(self.controller._runtime_override_due_at(10, 12, 1, 5), 12)
        self.assertIsNone(self.controller._runtime_override_due_at(12, 12, None, 5))
        self.assertEqual(self.controller._runtime_override_due_at(10, 9, 8, 5), 13)
        self.assertIsNone(self.controller._runtime_override_due_at(10, 9, 5, 5))
        self.assertIsNone(self.controller._pending_runtime_overrides_payload(SimpleNamespace(), "/tmp/x"))
        pending = SimpleNamespace(
            _runtime_overrides_pending_serialized='{"Mode":"2"}',
            _runtime_overrides_pending_values={"Mode": "2"},
            _runtime_overrides_pending_text="text",
        )
        self.assertEqual(
            self.controller._pending_runtime_overrides_payload(pending, "/tmp/x"),
            ({"Mode": "2"}, '{"Mode":"2"}', "text"),
        )
        self.assertIsNone(self.controller._pending_runtime_overrides_payload(pending, ""))
        pending._runtime_overrides_pending_values = []
        self.assertIsNone(self.controller._pending_runtime_overrides_payload(pending, "/tmp/x"))

        with patch("venus_evcharger.controllers.state_runtime_overrides.time.time", return_value=11.0) as system:
            self.assertEqual(self.controller._runtime_now(SimpleNamespace()), 11.0)
        self.assertEqual(system.call_count, 2)

    def test_stage_write_and_clear_mutate_the_complete_state_contract(self) -> None:
        svc = SimpleNamespace()
        payload = {"Mode": "2"}
        self.controller._stage_runtime_overrides_write(svc, payload, "serialized", "rendered", 12.5)
        self.assertEqual(
            vars(svc),
            {
                "_runtime_overrides_pending_serialized": "serialized",
                "_runtime_overrides_pending_values": {"Mode": "2"},
                "_runtime_overrides_pending_text": "rendered",
                "_runtime_overrides_pending_due_at": 12.5,
                "_runtime_overrides_active": True,
                "_runtime_overrides_values": {"Mode": "2"},
            },
        )
        payload["Mode"] = "changed"
        self.assertEqual(svc._runtime_overrides_pending_values, {"Mode": "2"})
        with patch.object(self.controller, "_write_text_atomically") as write:
            self.controller._write_runtime_overrides_payload(
                svc, "/tmp/x", {"Mode": "1"}, "new", "ini", 14.0
            )
        write.assert_called_once_with("/tmp/x", "ini")
        self.assertEqual(svc._runtime_overrides_serialized, "new")
        self.assertEqual(svc._runtime_overrides_last_saved_at, 14.0)
        self.assertTrue(svc._runtime_overrides_active)
        self.assertEqual(svc._runtime_overrides_values, {"Mode": "1"})
        self.assertIsNone(svc._runtime_overrides_pending_serialized)
        self.assertIsNone(svc._runtime_overrides_pending_values)
        self.assertIsNone(svc._runtime_overrides_pending_text)
        self.assertIsNone(svc._runtime_overrides_pending_due_at)

    def test_read_and_apply_contract_accepts_only_known_override_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "overrides.ini"
            path.write_text(
                "[RuntimeOverrides]\nMode = 2\nUnknown = ignored\nSetCurrent = 10.5\n",
                encoding="utf-8",
            )
            self.assertEqual(
                self.controller._read_runtime_override_values(str(path)),
                {"Mode": "2", "SetCurrent": "10.5"},
            )
            self.assertEqual(self.controller._read_runtime_override_values(""), {})
            self.assertEqual(self.controller._read_runtime_override_values(str(path.with_suffix(".missing"))), {})

            config = configparser.ConfigParser()
            config["DEFAULT"] = {"RuntimeOverridesPath": str(path), "Mode": "0"}
            result = self.controller._apply_runtime_overrides_to_config(self.service, config)
        self.assertIs(result, config)
        self.assertEqual(config["DEFAULT"]["Mode"], "2")
        self.assertEqual(config["DEFAULT"]["SetCurrent"], "10.5")
        self.assertEqual(self.service.runtime_overrides_path, str(path))
        self.assertTrue(self.service._runtime_overrides_active)
        self.assertEqual(self.service._runtime_overrides_values, {"Mode": "2", "SetCurrent": "10.5"})
        self.assertEqual(self.service._runtime_overrides_serialized, '{"Mode":"2","SetCurrent":"10.5"}')

    def test_read_contract_short_circuits_blank_path_and_reports_read_errors(self) -> None:
        parser = MagicMock()
        with patch("venus_evcharger.controllers.state_runtime_overrides._CasePreservingConfigParser", return_value=parser):
            self.assertEqual(self.controller._read_runtime_override_values("   "), {})
        parser.read.assert_not_called()

        error = OSError("unreadable")
        parser.read.side_effect = error
        with (
            patch("venus_evcharger.controllers.state_runtime_overrides._CasePreservingConfigParser", return_value=parser),
            patch("venus_evcharger.controllers.state_runtime_overrides.logging.warning") as warning,
        ):
            self.assertEqual(self.controller._read_runtime_override_values("/tmp/overrides.ini"), {})
        warning.assert_called_once_with(
            "Unable to read runtime overrides from %s: %s",
            "/tmp/overrides.ini",
            error,
        )

        parser.reset_mock()
        parser.read.side_effect = None
        parser.read.return_value = []
        parser.has_section.return_value = True
        with patch("venus_evcharger.controllers.state_runtime_overrides._CasePreservingConfigParser", return_value=parser):
            self.assertEqual(self.controller._read_runtime_override_values("/tmp/overrides.ini"), {})
        parser.__getitem__.assert_not_called()

    def test_serializers_and_atomic_writer_have_exact_boundary_contracts(self) -> None:
        with patch.object(self.controller, "current_runtime_overrides", return_value={"Mode": "2"}) as current:
            self.assertEqual(self.controller._serialized_runtime_overrides(), '{"Mode":"2"}')
        current.assert_called_once_with()

        with patch.object(self.controller, "current_runtime_state", return_value={"mode": 2}) as current_state:
            self.assertEqual(self.controller._serialized_runtime_state(), '{"mode":2}')
        current_state.assert_called_once_with()

        rendered = self.controller._runtime_override_ini_text({"Mode": "2"})
        self.assertEqual(rendered, "[RuntimeOverrides]\nMode = 2\n\n")
        with patch.object(state_runtime_overrides, "write_text_atomically") as writer:
            self.controller._write_text_atomically("/tmp/overrides.ini", rendered)
        writer.assert_called_once_with("/tmp/overrides.ini", rendered)

    def test_save_contract_covers_no_path_unchanged_immediate_and_deferred_writes(self) -> None:
        svc = SimpleNamespace()
        controller = ServiceStateController(svc, _normalize_mode)
        with patch.object(controller, "current_runtime_overrides") as current:
            controller.save_runtime_overrides()
        current.assert_not_called()

        payload = {"Mode": "2"}
        serialized = '{"Mode":"2"}'
        svc.runtime_overrides_path = "/tmp/overrides.ini"
        svc._runtime_overrides_serialized = serialized
        svc._runtime_overrides_pending_serialized = "old"
        with (
            patch.object(controller, "current_runtime_overrides", return_value=payload),
            patch.object(controller, "_clear_pending_runtime_overrides") as clear,
            patch.object(controller, "_write_runtime_overrides_payload") as write,
        ):
            controller.save_runtime_overrides()
        clear.assert_called_once_with(svc)
        write.assert_not_called()

        del svc._runtime_overrides_serialized
        svc._runtime_overrides_last_saved_at = "7.0"
        svc._runtime_overrides_pending_due_at = "8.0"
        with (
            patch.object(controller, "current_runtime_overrides", return_value=payload),
            patch.object(controller, "_runtime_override_ini_text", return_value="ini") as render,
            patch.object(controller, "_runtime_now", return_value=10.0) as runtime_now,
            patch.object(controller, "_coerce_optional_runtime_float", side_effect=(7.0, 8.0)) as optional,
            patch.object(controller, "_runtime_override_write_min_interval_seconds", return_value=3.0) as interval,
            patch.object(controller, "_runtime_override_due_at", return_value=None) as due,
            patch.object(controller, "_write_runtime_overrides_payload") as write,
        ):
            controller.save_runtime_overrides()
        render.assert_called_once_with(payload)
        runtime_now.assert_called_once_with(svc)
        self.assertEqual(
            optional.call_args_list,
            [unittest.mock.call("7.0"), unittest.mock.call("8.0")],
        )
        interval.assert_called_once_with(svc)
        due.assert_called_once_with(10.0, 8.0, 7.0, 3.0)
        write.assert_called_once_with(svc, "/tmp/overrides.ini", payload, serialized, "ini", 10.0)

        with (
            patch.object(controller, "current_runtime_overrides", return_value=payload),
            patch.object(controller, "_runtime_override_ini_text", return_value="ini"),
            patch.object(controller, "_runtime_now", return_value=10.0),
            patch.object(controller, "_runtime_override_write_min_interval_seconds", return_value=3.0),
            patch.object(controller, "_runtime_override_due_at", return_value=12.0),
            patch.object(controller, "_stage_runtime_overrides_write") as stage,
            patch.object(controller, "_write_runtime_overrides_payload") as write,
        ):
            controller.save_runtime_overrides()
        stage.assert_called_once_with(svc, payload, serialized, "ini", 12.0)
        write.assert_not_called()

    def test_save_contract_stages_failed_write_for_exact_retry_time(self) -> None:
        svc = SimpleNamespace(runtime_overrides_path="/tmp/overrides.ini")
        controller = ServiceStateController(svc, _normalize_mode)
        payload = {"Mode": "2"}
        error = OSError("full")
        with (
            patch.object(controller, "current_runtime_overrides", return_value=payload),
            patch.object(controller, "_runtime_override_ini_text", return_value="ini"),
            patch.object(controller, "_runtime_now", return_value=10.0),
            patch.object(controller, "_runtime_override_write_min_interval_seconds", return_value=3.0),
            patch.object(controller, "_runtime_override_due_at", return_value=None),
            patch.object(controller, "_write_runtime_overrides_payload", side_effect=error),
            patch.object(controller, "_stage_runtime_overrides_write") as stage,
            patch("venus_evcharger.controllers.state_runtime_overrides.logging.warning") as warning,
        ):
            controller.save_runtime_overrides()
        stage.assert_called_once_with(svc, payload, '{"Mode":"2"}', "ini", 13.0)
        warning.assert_called_once_with(
            "Unable to write runtime overrides to %s: %s",
            "/tmp/overrides.ini",
            error,
        )

    def test_flush_and_save_orchestration_covers_noop_defer_success_and_failure(self) -> None:
        svc = SimpleNamespace()
        controller = ServiceStateController(svc, _normalize_mode)
        with (
            patch.object(controller, "_pending_runtime_overrides_payload", return_value=None) as pending_call,
            patch.object(controller, "_write_runtime_overrides_payload") as write,
        ):
            controller.flush_runtime_overrides(1.0)
        pending_call.assert_called_once_with(svc, "")
        write.assert_not_called()

        pending = ({"Mode": "2"}, "serialized", "rendered")
        svc.runtime_overrides_path = "/tmp/x"
        with (
            patch.object(controller, "_pending_runtime_overrides_payload", return_value=pending) as pending_call,
            patch.object(controller, "_runtime_override_write_due", return_value=False) as due,
            patch.object(controller, "_write_runtime_overrides_payload") as write,
        ):
            controller.flush_runtime_overrides(2.0)
        pending_call.assert_called_once_with(svc, "/tmp/x")
        due.assert_called_once_with(svc, 2.0)
        write.assert_not_called()

        with (
            patch.object(controller, "_pending_runtime_overrides_payload", return_value=pending) as pending_call,
            patch.object(controller, "_runtime_override_write_due", return_value=True) as due,
            patch.object(controller, "_write_runtime_overrides_payload") as write,
        ):
            controller.flush_runtime_overrides(3.0)
        pending_call.assert_called_once_with(svc, "/tmp/x")
        due.assert_called_once_with(svc, 3.0)
        write.assert_called_once_with(svc, "/tmp/x", {"Mode": "2"}, "serialized", "rendered", 3.0)

        with (
            patch.object(controller, "_pending_runtime_overrides_payload", return_value=pending),
            patch.object(controller, "_runtime_now", return_value=4.0) as runtime_now,
            patch.object(controller, "_runtime_override_write_due", return_value=False),
        ):
            controller.flush_runtime_overrides()
        runtime_now.assert_called_once_with(svc)

        svc._runtime_overrides_pending_due_at = None
        error = OSError("full")
        with (
            patch.object(controller, "_pending_runtime_overrides_payload", return_value=pending),
            patch.object(controller, "_runtime_override_write_due", return_value=True),
            patch.object(controller, "_write_runtime_overrides_payload", side_effect=error),
            patch.object(controller, "_runtime_override_write_min_interval_seconds", return_value=4.0) as interval,
            patch("venus_evcharger.controllers.state_runtime_overrides.logging.warning") as warning,
        ):
            controller.flush_runtime_overrides(3.0)
        self.assertEqual(svc._runtime_overrides_pending_due_at, 7.0)
        interval.assert_called_once_with(svc)
        warning.assert_called_once_with(
            "Unable to write runtime overrides to %s: %s",
            "/tmp/x",
            error,
        )


if __name__ == "__main__":
    unittest.main()
