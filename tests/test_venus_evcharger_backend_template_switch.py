# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.backend.template_switch import (
    TemplateSwitchBackend,
    force_contactor_switch_settings,
    load_template_switch_settings,
    _normalize_switching_mode,
)
from venus_evcharger.backend.template_switch_contract import (
    DEFAULT_COMMAND_METHOD,
    DEFAULT_ENABLED_PATH,
    DEFAULT_PHASE_JSON_TEMPLATE,
    DEFAULT_PHASE_METHOD,
    DEFAULT_STATE_METHOD,
    DEFAULT_SWITCH_PHASE_SELECTIONS,
    DEFAULT_SWITCHING_MODE,
    SWITCH_FALSE_INT,
    SWITCH_FALSE_JSON,
    SWITCH_FALSE_TEXT,
    SWITCH_TRUE_INT,
    SWITCH_TRUE_JSON,
    SWITCH_TRUE_TEXT,
)


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class TestShellyWallboxBackendTemplateSwitch(unittest.TestCase):
    @staticmethod
    def _service(session: object) -> SimpleNamespace:
        return SimpleNamespace(
            session=session,
            requested_phase_selection="P1",
            shelly_request_timeout_seconds=2.0,
        )

    @staticmethod
    def _write_config(directory: str, content: str) -> str:
        path = Path(directory) / "template-switch.ini"
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_read_switch_state_uses_normalized_state_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n"
                "[StateRequest]\nMethod=GET\nUrl=/switch/state?enabled=$enabled_int&phase=$phase_selection\n"
                "[StateResponse]\nEnabledPath=data.enabled\nPhaseSelectionPath=data.phase_selection\n"
                "[CommandRequest]\nMethod=POST\nUrl=/switch/control\n"
                "[PhaseRequest]\nMethod=PUT\nUrl=/switch/phase\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {"data": {"enabled": True, "phase_selection": "P1_P2_P3"}}
            )
            backend = TemplateSwitchBackend(self._service(session), config_path=config_path)

            state = backend.read_switch_state()

            self.assertTrue(state.enabled)
            self.assertEqual(state.phase_selection, "P1_P2_P3")
            session.get.assert_called_once_with(
                url="http://adapter.local/switch/state?enabled=0&phase=P1",
                timeout=2.0,
            )

    def test_read_switch_state_exposes_optional_feedback_and_interlock_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1\nSwitchingMode=contactor\n"
                "[StateRequest]\nMethod=GET\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=data.enabled\nFeedbackClosedPath=data.feedback_closed\nInterlockOkPath=data.interlock_ok\n"
                "[CommandRequest]\nMethod=POST\nUrl=/switch/control\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {"data": {"enabled": True, "feedback_closed": False, "interlock_ok": True}}
            )
            backend = TemplateSwitchBackend(self._service(session), config_path=config_path)

            state = backend.read_switch_state()

            self.assertTrue(state.enabled)
            self.assertEqual(state.phase_selection, "P1")
            self.assertIs(state.feedback_closed, False)
            self.assertIs(state.interlock_ok, True)

    def test_read_switch_state_supports_digest_auth_and_custom_header(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "Username=user\nPassword=secret\nDigestAuth=1\n"
                "AuthHeaderName=Authorization\nAuthHeaderValue=Bearer token\n"
                "[Capabilities]\nSupportedPhaseSelections=P1\n"
                "[StateRequest]\nMethod=GET\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=data.enabled\n"
                "[CommandRequest]\nMethod=POST\nUrl=/switch/control\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse({"data": {"enabled": True}})
            with patch("venus_evcharger.backend.template_support.HTTPDigestAuth", return_value="digest-auth") as digest_auth:
                backend = TemplateSwitchBackend(self._service(session), config_path=config_path)

                state = backend.read_switch_state()

            self.assertTrue(state.enabled)
            digest_auth.assert_called_once_with("user", "secret")
            session.get.assert_called_once_with(
                url="http://adapter.local/switch/state",
                timeout=2.0,
                auth="digest-auth",
                headers={"Authorization": "Bearer token"},
            )

    def test_set_enabled_posts_rendered_json_template(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[StateRequest]\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nMethod=POST\nUrl=/switch/control\n"
                'JsonTemplate={"enabled": $enabled_json, "phase_selection": "$phase_selection"}\n',
            )
            session = MagicMock()
            session.post.return_value = _FakeResponse({})
            backend = TemplateSwitchBackend(self._service(session), config_path=config_path)

            backend.set_enabled(True)

            session.post.assert_called_once_with(
                url="http://adapter.local/switch/control",
                timeout=2.0,
                json={"enabled": True, "phase_selection": "P1"},
            )

    def test_set_enabled_posts_false_context_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[StateRequest]\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nMethod=POST\nUrl=/switch/control/$enabled_text\n"
                'JsonTemplate={"json": $enabled_json, "int": "$enabled_int", "text": "$enabled_text"}\n',
            )
            session = MagicMock()
            session.post.return_value = _FakeResponse({})
            backend = TemplateSwitchBackend(self._service(session), config_path=config_path)

            backend.set_enabled(False)

            session.post.assert_called_once_with(
                url="http://adapter.local/switch/control/off",
                timeout=2.0,
                json={"json": False, "int": "0", "text": "off"},
            )

    def test_set_phase_selection_posts_when_phase_request_is_configured(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
                "[StateRequest]\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nMethod=POST\nUrl=/switch/control\n"
                "[PhaseRequest]\nMethod=PUT\nUrl=/switch/phase/$enabled_int\n"
                'JsonTemplate={"enabled": "$enabled_text", "phase_selection": "$phase_selection"}\n',
            )
            session = MagicMock()
            session.put.return_value = _FakeResponse({})
            backend = TemplateSwitchBackend(self._service(session), config_path=config_path)

            backend.set_phase_selection("P1_P2")

            session.put.assert_called_once_with(
                url="http://adapter.local/switch/phase/0",
                timeout=2.0,
                json={"enabled": "off", "phase_selection": "P1_P2"},
            )

    def test_multi_phase_template_switch_requires_phase_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
                "[StateRequest]\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nMethod=POST\nUrl=/switch/control\n",
            )

            with self.assertRaises(ValueError):
                TemplateSwitchBackend(self._service(MagicMock()), config_path=config_path)

    def test_template_switch_helper_edges_cover_defaults_invalid_states_and_fallbacks(self) -> None:
        self.assertEqual(_normalize_switching_mode("weird", "contactor"), "contactor")
        self.assertEqual(
            TemplateSwitchBackend._context(True, "P1_P2"),
            {
                "enabled_json": "true",
                "enabled_int": "1",
                "enabled_text": "on",
                "phase_selection": "P1_P2",
            },
        )
        self.assertEqual(
            TemplateSwitchBackend._context(False, "P1"),
            {
                "enabled_json": "false",
                "enabled_int": "0",
                "enabled_text": "off",
                "phase_selection": "P1",
            },
        )
        for token in ("1", "true", "on", "yes", "enabled"):
            self.assertTrue(TemplateSwitchBackend._enabled_state(token), token)
        for token in ("0", "false", "off", "no", "disabled"):
            self.assertFalse(TemplateSwitchBackend._enabled_state(token), token)
        self.assertFalse(TemplateSwitchBackend._enabled_state(0))
        self.assertTrue(TemplateSwitchBackend._enabled_state(1))
        with self.assertRaisesRegex(ValueError, "Unsupported enabled-state value"):
            TemplateSwitchBackend._enabled_state("maybe")

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\nRequestTimeoutSeconds=0\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
                "[StateRequest]\nMethod=GET\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=data.enabled\nPhaseSelectionPath=data.phase_selection\n"
                "[CommandRequest]\nMethod=POST\nUrl=/switch/control\n"
                "[PhaseRequest]\nMethod=PUT\nUrl=/switch/phase\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse({"data": {"enabled": True, "phase_selection": "P1_P2_P3"}})
            backend = TemplateSwitchBackend(self._service(session), config_path=config_path)

            self.assertEqual(backend.settings.timeout_seconds, 2.0)
            state = backend.read_switch_state()
            self.assertEqual(state.phase_selection, "P1")
            self.assertEqual(backend._selected_phase_selection, "P1")
            with self.assertRaisesRegex(ValueError, "Unsupported phase selection"):
                backend.set_phase_selection("P1_P2_P3")

        with tempfile.TemporaryDirectory() as temp_dir:
            missing_state = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[StateRequest]\nMethod=GET\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nMethod=POST\nUrl=/switch/control\n",
            )
            with self.assertRaises(ValueError) as missing_state_error:
                TemplateSwitchBackend(self._service(MagicMock()), config_path=missing_state)
            self.assertEqual(
                str(missing_state_error.exception),
                "Template switch backend requires [StateRequest] Url",
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            missing_command = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[StateRequest]\nMethod=GET\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nMethod=POST\n",
            )
            with self.assertRaises(ValueError) as missing_command_error:
                TemplateSwitchBackend(self._service(MagicMock()), config_path=missing_command)
            self.assertEqual(
                str(missing_command_error.exception),
                "Template switch backend requires [CommandRequest] Url",
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            missing_enabled = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[StateRequest]\nMethod=GET\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=\n"
                "[CommandRequest]\nMethod=POST\nUrl=/switch/control\n",
            )
            with self.assertRaises(ValueError) as missing_enabled_error:
                TemplateSwitchBackend(self._service(MagicMock()), config_path=missing_enabled)
            self.assertEqual(
                str(missing_enabled_error.exception),
                "Template switch backend requires [StateResponse] EnabledPath",
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            missing_phase = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
                "[StateRequest]\nMethod=GET\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nMethod=POST\nUrl=/switch/control\n",
            )
            with self.assertRaises(ValueError) as missing_phase_error:
                TemplateSwitchBackend(self._service(MagicMock()), config_path=missing_phase)
            self.assertEqual(
                str(missing_phase_error.exception),
                "Template switch backend with multi-phase support requires [PhaseRequest] Url",
            )

    def test_template_switch_init_keeps_service_path_timeout_and_requested_phase(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\nRequestTimeoutSeconds=3.5\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
                "[StateRequest]\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nUrl=/switch/control\n"
                "[PhaseRequest]\nUrl=/switch/phase\n",
            )
            service = SimpleNamespace(
                session=MagicMock(),
                requested_phase_selection="P1_P2",
                shelly_request_timeout_seconds=2.0,
            )

            backend = TemplateSwitchBackend(service, config_path=f" {config_path} ")

            self.assertIs(backend.service, service)
            self.assertEqual(backend.config_path, config_path)
            self.assertEqual(backend.timeout_seconds, 3.5)
            self.assertEqual(backend._selected_phase_selection, "P1_P2")

    def test_template_switch_init_defaults_to_first_supported_phase_without_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
                "[StateRequest]\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nUrl=/switch/control\n"
                "[PhaseRequest]\nUrl=/switch/phase\n",
            )
            service = SimpleNamespace(
                session=MagicMock(),
                shelly_request_timeout_seconds=2.0,
            )

            backend = TemplateSwitchBackend(service, config_path=config_path)

            self.assertEqual(backend._selected_phase_selection, "P1")

    def test_template_switch_default_config_path_is_part_of_constructor_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[StateRequest]\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nUrl=/switch/control\n",
            )
            service = self._service(MagicMock())
            settings = load_template_switch_settings(service, config_path)

            with patch(
                "venus_evcharger.backend.template_switch.load_template_switch_settings",
                return_value=settings,
            ) as load_settings:
                backend = TemplateSwitchBackend(service)

            load_settings.assert_called_once_with(service, "")
            self.assertEqual(backend.config_path, "")
            self.assertEqual(backend._selected_phase_selection, "P1")

    def test_template_switch_uses_service_auth_fallback_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[StateRequest]\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nUrl=/switch/control\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse({"enabled": True})
            service = SimpleNamespace(
                session=session,
                requested_phase_selection="P1",
                shelly_request_timeout_seconds=2.0,
                _adapter_auth_fallback_enabled=True,
                username="fallback-user",
                password="fallback-secret",
                use_digest_auth=False,
            )

            backend = TemplateSwitchBackend(service, config_path=config_path)
            state = backend.read_switch_state()

            self.assertTrue(state.enabled)
            session.get.assert_called_once_with(
                url="http://adapter.local/switch/state",
                timeout=2.0,
                auth=("fallback-user", "fallback-secret"),
            )

    def test_template_switch_uses_service_timeout_fallback_when_adapter_timeout_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[StateRequest]\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nUrl=/switch/control\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse({"enabled": True})
            service = SimpleNamespace(
                session=session,
                requested_phase_selection="P1",
                shelly_request_timeout_seconds=4.25,
            )

            backend = TemplateSwitchBackend(service, config_path=config_path)
            state = backend.read_switch_state()

            self.assertTrue(state.enabled)
            self.assertEqual(backend.settings.timeout_seconds, 4.25)
            session.get.assert_called_once_with(
                url="http://adapter.local/switch/state",
                timeout=4.25,
            )

    def test_template_switch_contract_defaults_are_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\n"
                "[StateRequest]\nUrl=http://adapter.local/switch/state\n"
                "[StateResponse]\n"
                "[CommandRequest]\nUrl=http://adapter.local/switch/control\n",
            )
            settings = load_template_switch_settings(self._service(MagicMock()), config_path)

            self.assertEqual(settings.base_url, "")
            self.assertEqual(settings.supported_phase_selections, DEFAULT_SWITCH_PHASE_SELECTIONS)
            self.assertEqual(settings.switching_mode, DEFAULT_SWITCHING_MODE)
            self.assertEqual(settings.state_method, DEFAULT_STATE_METHOD)
            self.assertEqual(settings.command_method, DEFAULT_COMMAND_METHOD)
            self.assertEqual(settings.phase_method, DEFAULT_PHASE_METHOD)
            self.assertEqual(settings.state_enabled_path, DEFAULT_ENABLED_PATH)
            self.assertIsNone(settings.command_json_template)
            self.assertIsNone(settings.phase_url)
            self.assertIsNone(settings.phase_json_template)
            self.assertIsNone(settings.state_phase_selection_path)
            self.assertIsNone(settings.state_feedback_closed_path)
            self.assertIsNone(settings.state_interlock_ok_path)
            self.assertFalse(settings.requires_charge_pause_for_phase_change)
            self.assertIsNone(settings.max_direct_switch_power_w)

    def test_template_switch_blank_phase_selection_uses_contract_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=\n"
                "[StateRequest]\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nUrl=/switch/control\n",
            )
            settings = load_template_switch_settings(self._service(MagicMock()), config_path)

            self.assertEqual(settings.supported_phase_selections, DEFAULT_SWITCH_PHASE_SELECTIONS)

    def test_template_switch_relative_urls_require_base_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\n"
                "[StateRequest]\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nUrl=http://adapter.local/switch/control\n",
            )

            with self.assertRaisesRegex(ValueError, "requires Adapter.BaseUrl"):
                load_template_switch_settings(self._service(MagicMock()), config_path)

    def test_template_switch_phase_default_template_and_capability_flags(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\n"
                "SupportedPhaseSelections=P1,P1_P2\n"
                "SwitchingMode=contactor\n"
                "RequiresChargePauseForPhaseChange=1\n"
                "MaxDirectSwitchPowerWatts=9000\n"
                "[StateRequest]\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nUrl=/switch/control\n"
                "[PhaseRequest]\nUrl=/switch/phase\n",
            )
            session = MagicMock()
            session.post.return_value = _FakeResponse({})
            backend = TemplateSwitchBackend(self._service(session), config_path=config_path)

            self.assertEqual(backend.settings.phase_json_template, DEFAULT_PHASE_JSON_TEMPLATE)
            self.assertEqual(backend.settings.max_direct_switch_power_w, None)
            capabilities = backend.capabilities()
            self.assertEqual(capabilities.switching_mode, "contactor")
            self.assertEqual(capabilities.supported_phase_selections, ("P1", "P1_P2"))
            self.assertTrue(capabilities.requires_charge_pause_for_phase_change)
            self.assertIsNone(capabilities.max_direct_switch_power_w)

            backend.set_phase_selection("P1_P2")

            session.post.assert_called_once_with(
                url="http://adapter.local/switch/phase",
                timeout=2.0,
                json={"phase_selection": "P1_P2"},
            )

    def test_template_switch_invalid_methods_fall_back_to_contract_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
                "[StateRequest]\nMethod=DELETE\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nMethod=DELETE\nUrl=/switch/control\n"
                "[PhaseRequest]\nMethod=DELETE\nUrl=/switch/phase\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse({"enabled": True})
            session.post.return_value = _FakeResponse({})
            backend = TemplateSwitchBackend(self._service(session), config_path=config_path)

            state = backend.read_switch_state()
            backend.set_enabled(True)
            backend.set_phase_selection("P1_P2")

            self.assertTrue(state.enabled)
            self.assertEqual(backend.settings.state_method, DEFAULT_STATE_METHOD)
            self.assertEqual(backend.settings.command_method, DEFAULT_COMMAND_METHOD)
            self.assertEqual(backend.settings.phase_method, DEFAULT_PHASE_METHOD)
            session.get.assert_called_once_with(
                url="http://adapter.local/switch/state",
                timeout=2.0,
            )
            session.post.assert_any_call(
                url="http://adapter.local/switch/control",
                timeout=2.0,
            )
            session.post.assert_any_call(
                url="http://adapter.local/switch/phase",
                timeout=2.0,
                json={"phase_selection": "P1_P2"},
            )

    def test_template_switch_custom_methods_are_used_for_all_requests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
                "[StateRequest]\nMethod=PATCH\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nMethod=PUT\nUrl=/switch/control\n"
                "[PhaseRequest]\nMethod=PATCH\nUrl=/switch/phase\n",
            )
            session = MagicMock()
            session.patch.return_value = _FakeResponse({"enabled": True})
            session.put.return_value = _FakeResponse({})
            backend = TemplateSwitchBackend(self._service(session), config_path=config_path)

            state = backend.read_switch_state()
            backend.set_enabled(True)
            backend.set_phase_selection("P1_P2")

            self.assertTrue(state.enabled)
            session.patch.assert_any_call(
                url="http://adapter.local/switch/state",
                timeout=2.0,
            )
            session.put.assert_called_once_with(
                url="http://adapter.local/switch/control",
                timeout=2.0,
            )
            session.patch.assert_any_call(
                url="http://adapter.local/switch/phase",
                timeout=2.0,
                json={"phase_selection": "P1_P2"},
            )

    def test_read_switch_state_preserves_current_phase_without_response_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
                "[StateRequest]\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nUrl=/switch/control\n"
                "[PhaseRequest]\nUrl=/switch/phase\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse({"enabled": True})
            service = SimpleNamespace(
                session=session,
                requested_phase_selection="P1_P2",
                shelly_request_timeout_seconds=2.0,
            )
            backend = TemplateSwitchBackend(service, config_path=config_path)

            state = backend.read_switch_state()

            self.assertEqual(state.phase_selection, "P1_P2")
            self.assertEqual(backend._selected_phase_selection, "P1_P2")

    def test_read_switch_state_preserves_current_phase_for_invalid_response_phase(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
                "[StateRequest]\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\nPhaseSelectionPath=phase\n"
                "[CommandRequest]\nUrl=/switch/control\n"
                "[PhaseRequest]\nUrl=/switch/phase\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse({"enabled": True, "phase": "unknown"})
            service = SimpleNamespace(
                session=session,
                requested_phase_selection="P1_P2",
                shelly_request_timeout_seconds=2.0,
            )
            backend = TemplateSwitchBackend(service, config_path=config_path)

            state = backend.read_switch_state()

            self.assertEqual(state.phase_selection, "P1_P2")
            self.assertEqual(backend._selected_phase_selection, "P1_P2")

    def test_force_contactor_switch_settings_removes_direct_power_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nMaxDirectSwitchPowerWatts=2300\n"
                "[StateRequest]\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nUrl=/switch/control\n",
            )
            backend = TemplateSwitchBackend(self._service(MagicMock()), config_path=config_path)

            self.assertEqual(backend.settings.switching_mode, "direct")
            self.assertEqual(backend.settings.max_direct_switch_power_w, 2300.0)
            self.assertEqual(backend.capabilities().max_direct_switch_power_w, 2300.0)

            force_contactor_switch_settings(backend)

            self.assertEqual(backend.settings.switching_mode, "contactor")
            self.assertIsNone(backend.settings.max_direct_switch_power_w)

    def test_template_switch_set_phase_selection_updates_cache_without_request_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1\n"
                "[StateRequest]\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nMethod=POST\nUrl=/switch/control\n",
            )
            backend = TemplateSwitchBackend(self._service(MagicMock()), config_path=config_path)

            backend.set_phase_selection("P1")

            self.assertEqual(backend._selected_phase_selection, "P1")

    def test_template_switch_contract_literals_remain_stable(self) -> None:
        self.assertEqual(SWITCH_TRUE_JSON, "true")
        self.assertEqual(SWITCH_FALSE_JSON, "false")
        self.assertEqual(SWITCH_TRUE_INT, "1")
        self.assertEqual(SWITCH_FALSE_INT, "0")
        self.assertEqual(SWITCH_TRUE_TEXT, "on")
        self.assertEqual(SWITCH_FALSE_TEXT, "off")
