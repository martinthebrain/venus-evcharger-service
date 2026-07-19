# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import call, patch

from venus_evcharger.backend.models import SwitchCapabilities, SwitchState
from venus_evcharger.backend.registry import create_switch_backend
from venus_evcharger.backend.shelly_contactor_switch import ShellyContactorSwitchBackend
from venus_evcharger.backend.switch_group import (
    SwitchGroupBackend,
    SwitchGroupSettings,
    SwitchGroupMember,
    _aggregated_max_direct_switch_power_w,
    _aggregated_requires_charge_pause,
    _aggregated_switching_mode,
    _available_supported_phase_selections,
    _child_switch_backend,
    _config,
    _member_backend_type,
    _normalized_phase_label,
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
            backend = SwitchGroupBackend(
                self._service(session),
                config_path=config_path,
                child_backend_factory=create_switch_backend,
            )
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
            backend = SwitchGroupBackend(
                self._service(session),
                config_path=str(path),
                child_backend_factory=create_switch_backend,
            )
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
            backend = SwitchGroupBackend(
                self._service(session),
                config_path=str(path),
                child_backend_factory=create_switch_backend,
            )
            state = backend.read_switch_state()
            self.assertIsNone(state.feedback_closed)
            self.assertIsNone(state.interlock_ok)

    def test_switch_group_helper_edges_cover_paths_validation_and_aggregation(self) -> None:
        absolute = _resolved_member_path("/tmp/group.ini", "/tmp/child.ini")
        self.assertEqual(str(absolute), "/tmp/child.ini")
        with self.assertRaises(FileNotFoundError):
            load_switch_group_settings(
                self._service(MagicMock()),
                "/definitely/missing.ini",
                child_backend_factory=create_switch_backend,
            )
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
        bad_factory = MagicMock(return_value=object())
        with self.assertRaisesRegex(TypeError, "does not implement SwitchBackend"):
            _child_switch_backend(self._service(MagicMock()), member, bad_factory)

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
            adapter_without_type = Path(temp_dir) / "adapter-without-type.ini"
            adapter_spaced_type = Path(temp_dir) / "adapter-spaced-type.ini"
            child.write_text("[DEFAULT]\nBaseUrl=http://phase1.local\n", encoding="utf-8")
            adapter_without_type.write_text("[Adapter]\nBaseUrl=http://phase1.local\n", encoding="utf-8")
            adapter_spaced_type.write_text("[Adapter]\nType=  TEMPLATE_SWITCH  \nBaseUrl=http://phase1.local\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "^Switch group member config path may not be empty$"):
                _resolved_member_path(str(group_path), "   ")

            self.assertEqual(_member_backend_type(child), "shelly_combined")
            self.assertEqual(_member_backend_type(adapter_without_type), "shelly_combined")
            self.assertEqual(_member_backend_type(adapter_spaced_type), "template_switch")
            with self.assertRaisesRegex(ValueError, "Unsupported switch-group member key"):
                _required_phase_label("P9")

            parser = configparser.ConfigParser()
            parser.read_dict({"Members": {"P1": "child.ini"}})
            phase_members = _phase_members(str(group_path), parser["Members"])
            self.assertEqual(phase_members["P1"].phase_label, "P1")
            self.assertEqual(phase_members["P1"].backend_type, "shelly_combined")

            with self.assertRaisesRegex(ValueError, "^Switch group requires a member config for P1$"):
                _validate_phase_members({"P2": phase_members["P1"]})
            with self.assertRaisesRegex(ValueError, "^Switch group requires P2 when P3 is configured$"):
                _validate_phase_members(
                    {
                        "P1": phase_members["P1"],
                        "P3": SwitchGroupMember("P3", "template_switch", child),
                    }
                )
            with self.assertRaisesRegex(ValueError, "^Switch group SupportedPhaseSelections must include P1$"):
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

            with self.assertRaisesRegex(ValueError, "^Switch group members may not themselves be switch_group backends$"):
                _child_switch_backend(
                    self._service(MagicMock()),
                    SwitchGroupMember("P1", "switch_group", child),
                    create_switch_backend,
                )
            with self.assertRaisesRegex(ValueError, "Unsupported switch-group child backend"):
                unsupported_factory = MagicMock(
                    side_effect=ValueError("Unsupported switch-group child backend 'missing_backend'")
                )
                _child_switch_backend(
                    self._service(MagicMock()),
                    SwitchGroupMember("P1", "missing_backend", child),
                    unsupported_factory,
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

            with self.assertRaisesRegex(ValueError, "^Switch group backend requires a config path$"):
                load_switch_group_settings(
                    self._service(MagicMock()),
                    "",
                    child_backend_factory=create_switch_backend,
                )
            with self.assertRaisesRegex(ValueError, "^Switch group backend requires a config path$"):
                load_switch_group_settings(
                    self._service(MagicMock()),
                    "",
                    child_backend_factory=create_switch_backend,
                )

    def test_switch_group_helper_contracts_for_config_phase_and_capability_aggregation(self) -> None:
        parser = configparser.ConfigParser()
        service = self._service(MagicMock())
        with patch("venus_evcharger.backend.switch_group.load_required_backend_config", return_value=parser) as load_config:
            self.assertIs(_config("/tmp/switch-group.ini"), parser)
        load_config.assert_called_once_with("/tmp/switch-group.ini", "switch group")

        self.assertEqual(_normalized_phase_label(" p1 "), "P1")
        self.assertEqual(_normalized_phase_label("P2"), "P2")
        self.assertEqual(_normalized_phase_label("p3"), "P3")
        self.assertIsNone(_normalized_phase_label("P4"))
        self.assertIsNone(_normalized_phase_label(None))

        member = SwitchGroupMember("P1", "template_switch", Path("/tmp/p1.ini"))
        self.assertEqual(_available_supported_phase_selections({"P1": member}), ("P1",))
        self.assertEqual(
            _available_supported_phase_selections({"P1": member, "P2": member}),
            ("P1", "P1_P2"),
        )
        self.assertEqual(
            _available_supported_phase_selections({"P1": member, "P2": member, "P3": member}),
            ("P1", "P1_P2", "P1_P2_P3"),
        )

        direct = SwitchCapabilities(
            switching_mode="direct",
            supported_phase_selections=("P1",),
            requires_charge_pause_for_phase_change=False,
            max_direct_switch_power_w=5000.0,
        )
        pause_direct = SwitchCapabilities(
            switching_mode="direct",
            supported_phase_selections=("P1",),
            requires_charge_pause_for_phase_change=True,
            max_direct_switch_power_w=3000.0,
        )
        contactor = SwitchCapabilities(
            switching_mode="contactor",
            supported_phase_selections=("P1",),
            requires_charge_pause_for_phase_change=False,
            max_direct_switch_power_w=1000.0,
        )

        self.assertEqual(_aggregated_switching_mode({"P1": direct, "P2": pause_direct}), "direct")
        self.assertEqual(_aggregated_switching_mode({"P1": direct, "P2": contactor}), "contactor")
        self.assertFalse(_aggregated_requires_charge_pause({"P1": direct}))
        self.assertTrue(_aggregated_requires_charge_pause({"P1": direct, "P2": pause_direct}))
        self.assertEqual(_aggregated_max_direct_switch_power_w({"P1": direct, "P2": pause_direct}, "direct"), 3000.0)
        self.assertIsNone(_aggregated_max_direct_switch_power_w({"P1": direct, "P2": contactor}, "contactor"))
        self.assertIsNone(_aggregated_max_direct_switch_power_w({}, "direct"))

        capabilities = configparser.ConfigParser()
        capabilities.read_dict({"Capabilities": {"SupportedPhaseSelections": "P1,P1_P2,P1_P2_P3"}})
        self.assertEqual(
            _supported_phase_selections(capabilities["Capabilities"], {"P1": member, "P2": member, "P3": member}),
            ("P1", "P1_P2", "P1_P2_P3"),
        )
        capabilities = configparser.ConfigParser()
        capabilities.read_dict({"Capabilities": {"SupportedPhaseSelections": "P1,P1_P2"}})
        self.assertEqual(
            _supported_phase_selections(capabilities["Capabilities"], {"P1": member, "P2": member, "P3": member}),
            ("P1", "P1_P2"),
        )
        self.assertEqual(
            _supported_phase_selections({}, {"P1": member, "P2": member, "P3": member}),
            ("P1", "P1_P2", "P1_P2_P3"),
        )
        self.assertEqual(
            _supported_phase_selections({"SupportedPhaseSelections": "P1,P1_P2"}, {"P1": member, "P2": member, "P3": member}),
            ("P1", "P1_P2"),
        )
        self.assertEqual(
            _supported_phase_selections({"SupportedPhaseSelections": ""}, {"P1": member, "P2": member, "P3": member}),
            ("P1", "P1_P2", "P1_P2_P3"),
        )

    def test_switch_group_settings_loader_and_backend_init_contracts(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_dict(
            {
                "Members": {"P1": "phase1.ini", "P2": "phase2.ini", "P3": "phase3.ini"},
                "Capabilities": {"SupportedPhaseSelections": "P1,P1_P2"},
            }
        )
        service = self._service(MagicMock())
        member_p1 = SwitchGroupMember("P1", "template_switch", Path("/tmp/phase1.ini"))
        member_p2 = SwitchGroupMember("P2", "template_switch", Path("/tmp/phase2.ini"))
        member_p3 = SwitchGroupMember("P3", "template_switch", Path("/tmp/phase3.ini"))
        child_p1 = MagicMock()
        child_p2 = MagicMock()
        child_p3 = MagicMock()
        child_p1.capabilities.return_value = SwitchCapabilities(
            switching_mode="direct",
            supported_phase_selections=("P1",),
            requires_charge_pause_for_phase_change=False,
            max_direct_switch_power_w=2200.0,
        )
        child_p2.capabilities.return_value = SwitchCapabilities(
            switching_mode="direct",
            supported_phase_selections=("P1",),
            requires_charge_pause_for_phase_change=True,
            max_direct_switch_power_w=1500.0,
        )
        child_p3.capabilities.return_value = SwitchCapabilities(
            switching_mode="direct",
            supported_phase_selections=("P1",),
            requires_charge_pause_for_phase_change=False,
            max_direct_switch_power_w=1800.0,
        )
        with patch("venus_evcharger.backend.switch_group._config", return_value=parser) as load_config, patch(
            "venus_evcharger.backend.switch_group._phase_members",
            return_value={"P1": member_p1, "P2": member_p2, "P3": member_p3},
        ) as phase_members, patch(
            "venus_evcharger.backend.switch_group._child_switch_backend",
            side_effect=[child_p1, child_p2, child_p3],
        ) as child_backend:
            settings = load_switch_group_settings(
                service,
                " /tmp/switch-group.ini ",
                child_backend_factory=create_switch_backend,
            )

        load_config.assert_called_once_with("/tmp/switch-group.ini")
        phase_members.assert_called_once_with("/tmp/switch-group.ini", parser["Members"])
        self.assertEqual(
            child_backend.call_args_list,
            [
                call(service, member_p1, create_switch_backend),
                call(service, member_p2, create_switch_backend),
                call(service, member_p3, create_switch_backend),
            ],
        )
        self.assertEqual(settings.supported_phase_selections, ("P1", "P1_P2"))
        self.assertEqual(settings.phase_switch_targets, {"P1": ("P1",), "P1_P2": ("P1", "P2")})
        self.assertEqual(settings.switching_mode, "direct")
        self.assertTrue(settings.requires_charge_pause_for_phase_change)
        self.assertEqual(settings.max_direct_switch_power_w, 1500.0)

        invalid_child = MagicMock()
        invalid_child.capabilities.return_value = SwitchCapabilities(
            switching_mode="direct",
            supported_phase_selections=("P1", "P1_P2"),
            requires_charge_pause_for_phase_change=False,
            max_direct_switch_power_w=None,
        )
        with patch("venus_evcharger.backend.switch_group._config", return_value=parser), patch(
            "venus_evcharger.backend.switch_group._phase_members",
            return_value={"P1": member_p1, "P2": member_p2},
        ), patch(
            "venus_evcharger.backend.switch_group._child_switch_backend",
            side_effect=[child_p1, invalid_child],
        ):
            with self.assertRaisesRegex(ValueError, "^Switch group member P2 must expose single-phase support only$"):
                load_switch_group_settings(
                    service,
                    "/tmp/switch-group.ini",
                    child_backend_factory=create_switch_backend,
                )

        contactor_child = MagicMock()
        contactor_child.capabilities.return_value = SwitchCapabilities(
            switching_mode="contactor",
            supported_phase_selections=("P1",),
            requires_charge_pause_for_phase_change=False,
            max_direct_switch_power_w=999.0,
        )
        with patch("venus_evcharger.backend.switch_group._config", return_value=parser), patch(
            "venus_evcharger.backend.switch_group._phase_members",
            return_value={"P1": member_p1, "P2": member_p2},
        ), patch(
            "venus_evcharger.backend.switch_group._child_switch_backend",
            side_effect=[child_p1, contactor_child],
        ):
            contactor_settings = load_switch_group_settings(
                service,
                "/tmp/switch-group.ini",
                child_backend_factory=create_switch_backend,
            )
        self.assertEqual(contactor_settings.switching_mode, "contactor")
        self.assertIsNone(contactor_settings.max_direct_switch_power_w)

        backend_settings = SwitchGroupSettings(
            phase_members={"P1": member_p1, "P2": member_p2},
            phase_switch_targets={"P1": ("P1",), "P1_P2": ("P1", "P2")},
            supported_phase_selections=("P1", "P1_P2"),
            switching_mode="direct",
            requires_charge_pause_for_phase_change=True,
            max_direct_switch_power_w=2200.0,
        )
        with patch(
            "venus_evcharger.backend.switch_group.load_switch_group_settings",
            return_value=backend_settings,
        ) as load_settings, patch(
            "venus_evcharger.backend.switch_group._child_switch_backend",
            side_effect=[child_p1, child_p2, child_p1, child_p2],
        ) as init_child_backend:
            backend = SwitchGroupBackend(
                service,
                config_path=" /tmp/switch-group.ini ",
                child_backend_factory=create_switch_backend,
            )
            default_backend = SwitchGroupBackend(
                service,
                child_backend_factory=create_switch_backend,
            )

        self.assertIs(backend.service, service)
        self.assertEqual(backend.config_path, "/tmp/switch-group.ini")
        self.assertEqual(
            load_settings.call_args_list,
            [
                call(service, "/tmp/switch-group.ini", child_backend_factory=create_switch_backend),
                call(service, "", child_backend_factory=create_switch_backend),
            ],
        )
        self.assertEqual(
            init_child_backend.call_args_list,
            [
                call(service, member_p1, create_switch_backend),
                call(service, member_p2, create_switch_backend),
                call(service, member_p1, create_switch_backend),
                call(service, member_p2, create_switch_backend),
            ],
        )
        self.assertEqual(backend._selected_phase_selection, "P1")
        self.assertEqual(set(backend._members), {"P1", "P2"})
        self.assertEqual(default_backend.config_path, "")

    def test_switch_group_capabilities_returns_settings_values_exactly(self) -> None:
        backend = SwitchGroupBackend.__new__(SwitchGroupBackend)
        backend.settings = SwitchGroupSettings(
            phase_members={},
            phase_switch_targets={},
            supported_phase_selections=("P1", "P1_P2"),
            switching_mode="contactor",
            requires_charge_pause_for_phase_change=True,
            max_direct_switch_power_w=2200.0,
        )

        capabilities = backend.capabilities()

        self.assertEqual(capabilities.switching_mode, "contactor")
        self.assertEqual(capabilities.supported_phase_selections, ("P1", "P1_P2"))
        self.assertTrue(capabilities.requires_charge_pause_for_phase_change)
        self.assertEqual(capabilities.max_direct_switch_power_w, 2200.0)
