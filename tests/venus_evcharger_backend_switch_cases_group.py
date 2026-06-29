# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from venus_evcharger.backend.models import SwitchState
from venus_evcharger.backend.shelly_contactor_switch import ShellyContactorSwitchBackend
from venus_evcharger.backend.switch_group import (
    SwitchGroupBackend,
    SwitchGroupSettings,
    SwitchGroupMember,
    _child_switch_backend,
    _member_backend_type,
    _phase_members,
    _required_phase_label,
    _resolved_member_path,
    _supported_phase_selections,
    _validated_member_capabilities,
    _validate_phase_members,
    _validate_supported_phase_selection_list,
    load_switch_group_settings,
)
from tests.venus_evcharger_backend_switch_support import SwitchBackendTestCaseBase, _FakeResponse, MagicMock, Path, tempfile


class TestShellyWallboxBackendSwitchGroup(SwitchBackendTestCaseBase):
    def test_switch_group_state_infers_phase_selection_from_child_states(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_switch_group_config(temp_dir)
            session = MagicMock()
            session.get.side_effect = [
                _FakeResponse({"enabled": True}),
                _FakeResponse({"output": True}),
                _FakeResponse({"enabled": False}),
            ]
            backend = SwitchGroupBackend(self._service(session), config_path=config_path)
            state = backend.read_switch_state()
            self.assertTrue(state.enabled)
            self.assertEqual(state.phase_selection, "P1_P2")

    def test_switch_group_aggregates_explicit_feedback_and_interlock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            p1_path = Path(temp_dir) / "phase1-switch.ini"
            p2_path = Path(temp_dir) / "phase2-switch.ini"
            path = Path(temp_dir) / "switch-group.ini"
            p1_path.write_text(
                "[Adapter]\nType=template_switch\nBaseUrl=http://phase1.local\n"
                "[StateRequest]\nMethod=GET\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=data.enabled\nFeedbackClosedPath=data.feedback_closed\nInterlockOkPath=data.interlock_ok\n"
                "[CommandRequest]\nMethod=POST\nUrl=/control\n",
                encoding="utf-8",
            )
            p2_path.write_text(
                "[Adapter]\nType=template_switch\nBaseUrl=http://phase2.local\n"
                "[StateRequest]\nMethod=GET\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=data.enabled\nFeedbackClosedPath=data.feedback_closed\nInterlockOkPath=data.interlock_ok\n"
                "[CommandRequest]\nMethod=POST\nUrl=/control\n",
                encoding="utf-8",
            )
            path.write_text("[Adapter]\nType=switch_group\n[Members]\nP1=phase1-switch.ini\nP2=phase2-switch.ini\n", encoding="utf-8")
            session = MagicMock()
            session.get.side_effect = [
                _FakeResponse({"data": {"enabled": True, "feedback_closed": True, "interlock_ok": True}}),
                _FakeResponse({"data": {"enabled": False, "feedback_closed": False, "interlock_ok": True}}),
            ]
            backend = SwitchGroupBackend(self._service(session), config_path=str(path))
            state = backend.read_switch_state()
            self.assertTrue(state.feedback_closed)
            self.assertTrue(state.interlock_ok)

    def test_switch_group_keeps_feedback_and_interlock_unknown_until_all_members_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            p1_path = Path(temp_dir) / "phase1-switch.ini"
            p2_path = Path(temp_dir) / "phase2-switch.ini"
            path = Path(temp_dir) / "switch-group.ini"
            p1_path.write_text(
                "[Adapter]\nType=template_switch\nBaseUrl=http://phase1.local\n"
                "[StateRequest]\nMethod=GET\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=data.enabled\nFeedbackClosedPath=data.feedback_closed\nInterlockOkPath=data.interlock_ok\n"
                "[CommandRequest]\nMethod=POST\nUrl=/control\n",
                encoding="utf-8",
            )
            p2_path.write_text(
                "[Adapter]\nType=template_switch\nBaseUrl=http://phase2.local\n[StateRequest]\nMethod=GET\nUrl=/state\n[StateResponse]\nEnabledPath=data.enabled\n[CommandRequest]\nMethod=POST\nUrl=/control\n",
                encoding="utf-8",
            )
            path.write_text("[Adapter]\nType=switch_group\n[Members]\nP1=phase1-switch.ini\nP2=phase2-switch.ini\n", encoding="utf-8")
            session = MagicMock()
            session.get.side_effect = [
                _FakeResponse({"data": {"enabled": True, "feedback_closed": True, "interlock_ok": True}}),
                _FakeResponse({"data": {"enabled": False}}),
            ]
            backend = SwitchGroupBackend(self._service(session), config_path=str(path))
            state = backend.read_switch_state()
            self.assertIsNone(state.feedback_closed)
            self.assertIsNone(state.interlock_ok)

    def test_switch_group_helper_edges_cover_paths_validation_and_aggregation(self) -> None:
        absolute = _resolved_member_path("/tmp/group.ini", "/tmp/child.ini")
        self.assertEqual(str(absolute), "/tmp/child.ini")
        with self.assertRaises(FileNotFoundError):
            load_switch_group_settings(self._service(MagicMock()), "/definitely/missing.ini")
        backend = SwitchGroupBackend.__new__(SwitchGroupBackend)
        backend._selected_phase_selection = "P1"
        backend.settings = SwitchGroupSettings(
            phase_members={},
            supported_phase_selections=("P1", "P1_P2"),
            phase_switch_targets={"P1": ("P1",), "P1_P2": ("P1", "P2")},
            switching_mode="direct",
            requires_charge_pause_for_phase_change=False,
            max_direct_switch_power_w=None,
        )
        self.assertEqual(backend._phase_selection_from_active_labels(frozenset()), "P1")
        self.assertFalse(
            backend._aggregate_feedback_closed(
                {
                    "P1": SwitchState(enabled=True, phase_selection="P1", feedback_closed=True),
                    "P2": SwitchState(enabled=True, phase_selection="P1", feedback_closed=True),
                },
                frozenset({"P1"}),
            )
        )
        with self.assertRaisesRegex(ValueError, "Unsupported phase selection"):
            backend.set_phase_selection(cast(Any, "P1_P2_P3"))
        member = SwitchGroupMember("P1", "bad_switch", Path("/tmp/bad-switch.ini"))
        with patch.dict("venus_evcharger.backend.registry.SWITCH_BACKENDS", {"bad_switch": lambda *_args, **_kwargs: object()}):
            with self.assertRaisesRegex(TypeError, "does not implement SwitchBackend"):
                _child_switch_backend(self._service(MagicMock()), member)

    def test_contactor_mode_has_no_direct_switch_power_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "switch.ini"
            path.write_text(
                "[Adapter]\nType=shelly_switch\nHost=192.168.1.11\nComponent=Switch\nId=0\n[Capabilities]\nSwitchingMode=contactor\nSupportedPhaseSelections=P1\n",
                encoding="utf-8",
            )
            backend = ShellyContactorSwitchBackend(self._service(MagicMock()), config_path=str(path))
            self.assertEqual(backend.capabilities().switching_mode, "contactor")

    def test_contactor_switch_backend_defaults_to_contactor_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "switch.ini"
            path.write_text("[Adapter]\nType=shelly_contactor_switch\nHost=192.168.1.11\nComponent=Switch\nId=0\n", encoding="utf-8")
            backend = ShellyContactorSwitchBackend(self._service(MagicMock()), config_path=str(path))
            self.assertEqual(backend.capabilities().switching_mode, "contactor")

    def test_read_switch_state_exposes_optional_shelly_feedback_and_interlock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "switch.ini"
            path.write_text(
                "[Adapter]\nType=shelly_contactor_switch\nHost=192.168.1.11\nComponent=Switch\nId=0\n"
                "[Feedback]\nComponent=Input\nId=7\nValuePath=state\n"
                "[Interlock]\nComponent=Input\nId=8\nValuePath=state\nInvert=1\n",
                encoding="utf-8",
            )
            session = MagicMock()
            session.get.side_effect = [
                _FakeResponse({"output": True}),
                _FakeResponse({"state": True}),
                _FakeResponse({"state": False}),
            ]
            backend = ShellyContactorSwitchBackend(self._service(session), config_path=str(path))
            state = backend.read_switch_state()
            self.assertTrue(state.feedback_closed)
            self.assertTrue(state.interlock_ok)

    def test_switch_group_helper_supports_member_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            child = Path(temp_dir) / "child.ini"
            child.write_text("[Adapter]\nType=template_switch\nBaseUrl=http://phase1.local\n", encoding="utf-8")
            self.assertEqual(_member_backend_type(child), "template_switch")

    def test_switch_group_helper_edges_cover_remaining_validation_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            group_path = Path(temp_dir) / "group.ini"
            child = Path(temp_dir) / "child.ini"
            child.write_text("[DEFAULT]\nBaseUrl=http://phase1.local\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "may not be empty"):
                _resolved_member_path(str(group_path), "   ")

            self.assertEqual(_member_backend_type(child), "shelly_combined")
            with self.assertRaisesRegex(ValueError, "Unsupported switch-group member key"):
                _required_phase_label("P9")

            parser = configparser.ConfigParser()
            parser.read_dict({"Members": {"P1": "child.ini"}})
            phase_members = _phase_members(str(group_path), parser["Members"])
            self.assertEqual(phase_members["P1"].backend_type, "shelly_combined")

            with self.assertRaisesRegex(ValueError, "requires a member config for P1"):
                _validate_phase_members({"P2": phase_members["P1"]})
            with self.assertRaisesRegex(ValueError, "requires P2"):
                _validate_phase_members(
                    {
                        "P1": phase_members["P1"],
                        "P3": SwitchGroupMember("P3", "template_switch", child),
                    }
                )
            with self.assertRaisesRegex(ValueError, "must include P1"):
                _validate_supported_phase_selection_list(["P1_P2"])

            capabilities = configparser.ConfigParser()
            capabilities.read_dict({"Capabilities": {"SupportedPhaseSelections": "P1,P1"}})
            self.assertEqual(_supported_phase_selections(capabilities["Capabilities"], {"P1": phase_members["P1"]}), ("P1",))
            capabilities.read_dict({"Capabilities": {"SupportedPhaseSelections": "P1_P2"}})
            with self.assertRaisesRegex(ValueError, "unsupported phase selection"):
                _supported_phase_selections(capabilities["Capabilities"], {"P1": phase_members["P1"]})

            backend = SwitchGroupBackend.__new__(SwitchGroupBackend)
            backend._selected_phase_selection = "P1"
            backend.settings = SwitchGroupSettings(
                phase_members={"P1": phase_members["P1"]},
                phase_switch_targets={"P1": ("P1",)},
                supported_phase_selections=("P1",),
                switching_mode="direct",
                requires_charge_pause_for_phase_change=False,
                max_direct_switch_power_w=None,
            )
            self.assertEqual(backend._phase_selection_from_active_labels(frozenset({"P9"})), "P1")
            self.assertIsNone(backend._aggregate_feedback_closed({}, frozenset({"P1"})))
            self.assertFalse(
                backend._aggregate_interlock_ok(
                    {"P1": SwitchState(enabled=True, phase_selection="P1", interlock_ok=False)}
                )
            )
            self.assertIsNone(backend._aggregate_interlock_ok({}))

            with self.assertRaisesRegex(ValueError, "may not themselves be switch_group"):
                _child_switch_backend(
                    self._service(MagicMock()),
                    SwitchGroupMember("P1", "switch_group", child),
                )
            with self.assertRaisesRegex(ValueError, "Unsupported switch-group child backend"):
                _child_switch_backend(
                    self._service(MagicMock()),
                    SwitchGroupMember("P1", "missing_backend", child),
                )

            bad_backend = SimpleNamespace(
                capabilities=MagicMock(
                    return_value=SimpleNamespace(
                        supported_phase_selections=("P1", "P1_P2"),
                        switching_mode="direct",
                        requires_charge_pause_for_phase_change=False,
                        max_direct_switch_power_w=None,
                    )
                )
            )
            with self.assertRaisesRegex(ValueError, "single-phase support only"):
                _validated_member_capabilities(phase_members["P1"], bad_backend)

            with self.assertRaisesRegex(ValueError, "requires a config path"):
                load_switch_group_settings(self._service(MagicMock()), "")
