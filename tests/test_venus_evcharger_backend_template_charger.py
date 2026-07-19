# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from venus_evcharger.backend.template_charger import (
    TemplateChargerBackend,
    TemplateChargerSettings,
    _TemplateChargerCachedState,
    load_template_charger_settings,
)
from venus_evcharger.backend.template_charger_contract import (
    DEFAULT_CHARGER_PHASE_SELECTIONS,
    DEFAULT_CURRENT_JSON_TEMPLATE,
    DEFAULT_CURRENT_METHOD,
    DEFAULT_ENABLE_JSON_TEMPLATE,
    DEFAULT_ENABLE_METHOD,
    DEFAULT_PHASE_METHOD,
    DEFAULT_STATE_METHOD,
    CHARGER_FALSE_INT,
    CHARGER_FALSE_JSON,
    CHARGER_FALSE_TEXT,
    CHARGER_TRUE_INT,
    CHARGER_TRUE_JSON,
    CHARGER_TRUE_TEXT,
)
from venus_evcharger.backend.template_support import TemplateAuthSettings


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class TestShellyWallboxBackendTemplateCharger(unittest.TestCase):
    @staticmethod
    def _service(session: object) -> SimpleNamespace:
        return SimpleNamespace(
            session=session,
            shelly_request_timeout_seconds=2.0,
        )

    @staticmethod
    def _write_config(directory: str, content: str) -> str:
        path = Path(directory) / "template-charger.ini"
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_set_enabled_posts_rendered_enable_template(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[EnableRequest]\nMethod=POST\nUrl=/charger/enable\n"
                '[CurrentRequest]\nMethod=POST\nUrl=/charger/current\nJsonTemplate={"amps": $amps}\n',
            )
            session = MagicMock()
            session.post.return_value = _FakeResponse({})
            backend = TemplateChargerBackend(self._service(session), config_path=config_path)

            backend.set_enabled(True)

            session.post.assert_called_once_with(
                url="http://adapter.local/charger/enable",
                timeout=2.0,
                json={"enabled": True},
            )

    def test_set_enabled_posts_false_context_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[EnableRequest]\nMethod=POST\nUrl=/charger/enable/$enabled_text\n"
                'JsonTemplate={"json": $enabled_json, "int": "$enabled_int", "text": "$enabled_text"}\n'
                "[CurrentRequest]\nMethod=POST\nUrl=/charger/current\n",
            )
            session = MagicMock()
            session.post.return_value = _FakeResponse({})
            backend = TemplateChargerBackend(self._service(session), config_path=config_path)

            backend.set_enabled(False)

            session.post.assert_called_once_with(
                url="http://adapter.local/charger/enable/off",
                timeout=2.0,
                json={"json": False, "int": "0", "text": "off"},
            )

    def test_set_enabled_supports_custom_auth_header(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "AuthHeaderName=Authorization\nAuthHeaderValue=Bearer token\n"
                "[EnableRequest]\nMethod=POST\nUrl=/charger/enable\n"
                '[CurrentRequest]\nMethod=POST\nUrl=/charger/current\nJsonTemplate={"amps": $amps}\n',
            )
            session = MagicMock()
            session.post.return_value = _FakeResponse({})
            backend = TemplateChargerBackend(self._service(session), config_path=config_path)

            backend.set_enabled(True)

            session.post.assert_called_once_with(
                url="http://adapter.local/charger/enable",
                timeout=2.0,
                json={"enabled": True},
                headers={"Authorization": "Bearer token"},
            )

    def test_set_current_posts_rendered_current_template(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[EnableRequest]\nMethod=POST\nUrl=/charger/enable\n"
                '[CurrentRequest]\nMethod=PATCH\nUrl=/charger/current\nJsonTemplate={"amps": $amps}\n',
            )
            session = MagicMock()
            session.patch.return_value = _FakeResponse({})
            backend = TemplateChargerBackend(self._service(session), config_path=config_path)

            backend.set_current(13.5)

            session.patch.assert_called_once_with(
                url="http://adapter.local/charger/current",
                timeout=2.0,
                json={"amps": 13.5},
            )

    def test_set_phase_selection_posts_when_phase_request_is_configured(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n"
                "[EnableRequest]\nMethod=POST\nUrl=/charger/enable\n"
                '[CurrentRequest]\nMethod=POST\nUrl=/charger/current\nJsonTemplate={"amps": $amps}\n'
                '[PhaseRequest]\nMethod=PUT\nUrl=/charger/phase\n'
                'JsonTemplate={"phase_selection": "$phase_selection"}\n',
            )
            session = MagicMock()
            session.put.return_value = _FakeResponse({})
            backend = TemplateChargerBackend(self._service(session), config_path=config_path)

            backend.set_phase_selection("P1_P2_P3")

            session.put.assert_called_once_with(
                url="http://adapter.local/charger/phase",
                timeout=2.0,
                json={"phase_selection": "P1_P2_P3"},
            )

    def test_read_charger_state_reads_normalized_response_when_state_request_is_configured(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n"
                "[StateRequest]\nMethod=GET\nUrl=/charger/state?enabled=$enabled_int&amps=$amps&phase=$phase_selection\n"
                "[StateResponse]\nEnabledPath=data.enabled\nCurrentPath=data.current\n"
                "PhaseSelectionPath=data.phase_selection\nActualCurrentPath=data.actual_current\n"
                "PowerWattsPath=data.power_w\nEnergyKwhPath=data.energy_kwh\n"
                "StatusPath=data.status\nFaultPath=data.fault\n"
                "[EnableRequest]\nMethod=POST\nUrl=/charger/enable\n"
                '[CurrentRequest]\nMethod=POST\nUrl=/charger/current\nJsonTemplate={"amps": $amps}\n'
                '[PhaseRequest]\nMethod=PUT\nUrl=/charger/phase\n'
                'JsonTemplate={"phase_selection": "$phase_selection"}\n',
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {
                    "data": {
                        "enabled": True,
                        "current": 13.5,
                        "phase_selection": "P1_P2_P3",
                        "actual_current": 12.8,
                        "power_w": 2950.0,
                        "energy_kwh": 7.125,
                        "status": "charging",
                        "fault": "none",
                    }
                }
            )
            backend = TemplateChargerBackend(self._service(session), config_path=config_path)

            state = backend.read_charger_state()

            session.get.assert_called_once_with(
                url="http://adapter.local/charger/state?enabled=0&amps=0&phase=P1",
                timeout=2.0,
            )
            self.assertTrue(state.enabled)
            self.assertEqual(state.current_amps, 13.5)
            self.assertEqual(state.phase_selection, "P1_P2_P3")
            self.assertEqual(state.actual_current_amps, 12.8)
            self.assertEqual(state.power_w, 2950.0)
            self.assertEqual(state.energy_kwh, 7.125)
            self.assertEqual(state.status_text, "charging")
            self.assertEqual(state.fault_text, "none")
            self.assertIs(backend._enabled_state_cache, True)
            self.assertEqual(backend._current_amps_cache, 13.5)
            self.assertEqual(backend._phase_selection_cache, "P1_P2_P3")

    def test_read_charger_state_falls_back_to_command_cache_without_state_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
                "[EnableRequest]\nMethod=POST\nUrl=/charger/enable\n"
                '[CurrentRequest]\nMethod=POST\nUrl=/charger/current\nJsonTemplate={"amps": $amps}\n'
                '[PhaseRequest]\nMethod=PUT\nUrl=/charger/phase\n'
                'JsonTemplate={"phase_selection": "$phase_selection"}\n',
            )
            session = MagicMock()
            session.post.return_value = _FakeResponse({})
            session.put.return_value = _FakeResponse({})
            backend = TemplateChargerBackend(self._service(session), config_path=config_path)

            backend.set_enabled(True)
            backend.set_current(11.0)
            backend.set_phase_selection("P1_P2")
            state = backend.read_charger_state()

            self.assertTrue(state.enabled)
            self.assertEqual(state.current_amps, 11.0)
            self.assertEqual(state.phase_selection, "P1_P2")

    def test_multi_phase_template_charger_requires_phase_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
                "[EnableRequest]\nMethod=POST\nUrl=/charger/enable\n"
                '[CurrentRequest]\nMethod=POST\nUrl=/charger/current\nJsonTemplate={"amps": $amps}\n',
            )

            with self.assertRaises(ValueError) as error:
                TemplateChargerBackend(self._service(MagicMock()), config_path=config_path)
            self.assertEqual(
                str(error.exception),
                "Template charger backend with multi-phase support requires [PhaseRequest] Url",
            )

    def test_template_charger_helper_edges_cover_timeouts_state_fallbacks_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\nRequestTimeoutSeconds=0\n"
                "[EnableRequest]\nMethod=POST\nUrl=/charger/enable\n"
                "[CurrentRequest]\nMethod=POST\nUrl=/charger/current\n",
            )
            settings = load_template_charger_settings(self._service(MagicMock()), config_path)
            self.assertEqual(settings.timeout_seconds, 2.0)

        self.assertIsNone(TemplateChargerBackend._enabled_state(None))
        self.assertFalse(TemplateChargerBackend._enabled_state(0))
        self.assertTrue(TemplateChargerBackend._enabled_state(1))
        self.assertTrue(TemplateChargerBackend._enabled_state("enabled"))
        self.assertFalse(TemplateChargerBackend._enabled_state("disabled"))
        self.assertIsNone(TemplateChargerBackend._enabled_state("maybe"))
        self.assertIsNone(TemplateChargerBackend._optional_text(None))
        self.assertIsNone(TemplateChargerBackend._optional_text("  "))
        self.assertIsNone(TemplateChargerBackend._payload_float({}, None))
        self.assertEqual(
            TemplateChargerBackend._context(enabled=True, amps=13.5, phase_selection="P1_P2"),
            {
                "enabled_json": "true",
                "enabled_int": "1",
                "enabled_text": "on",
                "amps": "13.5",
                "phase_selection": "P1_P2",
            },
        )
        self.assertEqual(
            TemplateChargerBackend._context(enabled=False, amps=0.0, phase_selection="P1"),
            {
                "enabled_json": "false",
                "enabled_int": "0",
                "enabled_text": "off",
                "amps": "0",
                "phase_selection": "P1",
            },
        )
        self.assertEqual(
            TemplateChargerBackend._context(),
            {
                "enabled_json": "false",
                "enabled_int": "0",
                "enabled_text": "off",
                "amps": "0",
                "phase_selection": "P1",
            },
        )

        backend = TemplateChargerBackend.__new__(TemplateChargerBackend)
        backend.settings = TemplateChargerSettings(
            base_url="http://charger.local",
            auth_settings=TemplateAuthSettings("", "", False, None, None),
            timeout_seconds=2.0,
            supported_phase_selections=("P1",),
            state_method="GET",
            state_url=None,
            state_enabled_path=None,
            state_current_path=None,
            state_phase_selection_path=None,
            state_actual_current_path=None,
            state_power_watts_path=None,
            state_energy_kwh_path=None,
            state_status_path=None,
            state_fault_path=None,
            enable_method="POST",
            enable_url="/enable",
            enable_json_template=None,
            current_method="POST",
            current_url="/current",
            current_json_template=None,
            phase_method="POST",
            phase_url=None,
            phase_json_template=None,
        )
        cached = _TemplateChargerCachedState(enabled=True, current_amps=10.0, phase_selection="P1_P2")
        self.assertEqual(
            backend._state_context(cached),
            {
                "enabled_json": "true",
                "enabled_int": "1",
                "enabled_text": "on",
                "amps": "10",
                "phase_selection": "P1_P2",
            },
        )
        self.assertEqual(backend._payload_phase_selection({}, cached), "P1_P2")
        self.assertIsNone(backend._payload_text({}, None))
        state = backend._state_from_payload({"enabled": True}, cached)
        self.assertTrue(state.enabled)

        cached_disabled = _TemplateChargerCachedState(enabled=False, current_amps=9.0, phase_selection="P1")
        state = backend._state_from_payload({"enabled": True}, cached_disabled)
        self.assertFalse(state.enabled)

        with self.assertRaisesRegex(ValueError, "Unsupported charger current"):
            backend.set_current(-1.0)
        with self.assertRaisesRegex(ValueError, "Unsupported phase selection"):
            backend.set_phase_selection("P1_P2")
        backend.set_phase_selection("P1")
        self.assertEqual(
            backend._normalized_phase_selection("unknown", "P1", ("P1",)),
            "P1",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
                "[EnableRequest]\nMethod=POST\nUrl=/charger/enable\n"
                "[CurrentRequest]\nMethod=POST\nUrl=/charger/current\n"
                "[PhaseRequest]\nMethod=POST\nUrl=/charger/phase\n",
            )
            backend = TemplateChargerBackend(
                SimpleNamespace(session=MagicMock(), shelly_request_timeout_seconds=2.0, requested_phase_selection="P1_P2_P3"),
                config_path=config_path,
            )
            self.assertEqual(backend._phase_selection_cache, "P1")

    def test_template_charger_initial_caches_are_empty_and_service_fallbacks_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[EnableRequest]\nUrl=/charger/enable\n"
                "[CurrentRequest]\nUrl=/charger/current\n",
            )
            session = MagicMock()
            session.post.return_value = _FakeResponse({})
            service = SimpleNamespace(
                session=session,
                shelly_request_timeout_seconds=4.25,
                _adapter_auth_fallback_enabled=True,
                username="fallback-user",
                password="fallback-secret",
                use_digest_auth=False,
            )

            backend = TemplateChargerBackend(service, config_path=config_path)
            self.assertIsNone(backend._enabled_state_cache)
            self.assertIsNone(backend._current_amps_cache)
            backend.set_enabled(True)

            self.assertIs(backend._enabled_state_cache, True)
            self.assertEqual(backend.settings.timeout_seconds, 4.25)
            session.post.assert_called_once_with(
                url="http://adapter.local/charger/enable",
                timeout=4.25,
                json={"enabled": True},
                auth=("fallback-user", "fallback-secret"),
            )

    def test_template_charger_allows_zero_current_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[EnableRequest]\nUrl=/charger/enable\n"
                "[CurrentRequest]\nUrl=/charger/current\n",
            )
            session = MagicMock()
            session.post.return_value = _FakeResponse({})
            backend = TemplateChargerBackend(self._service(session), config_path=config_path)

            backend.set_current(0.0)

            self.assertEqual(backend._current_amps_cache, 0.0)
            session.post.assert_called_once_with(
                url="http://adapter.local/charger/current",
                timeout=2.0,
                json={"amps": 0},
            )

    def test_template_charger_requires_enable_and_current_urls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_enable = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[EnableRequest]\nMethod=POST\n"
                "[CurrentRequest]\nMethod=POST\nUrl=/charger/current\n",
            )
            with self.assertRaises(ValueError) as missing_enable_error:
                TemplateChargerBackend(self._service(MagicMock()), config_path=missing_enable)
            self.assertEqual(
                str(missing_enable_error.exception),
                "Template charger backend requires [EnableRequest] Url",
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            missing_current = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[EnableRequest]\nMethod=POST\nUrl=/charger/enable\n"
                "[CurrentRequest]\nMethod=POST\n",
            )
            with self.assertRaises(ValueError) as missing_current_error:
                TemplateChargerBackend(self._service(MagicMock()), config_path=missing_current)
            self.assertEqual(
                str(missing_current_error.exception),
                "Template charger backend requires [CurrentRequest] Url",
            )

    def test_template_charger_contract_defaults_are_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\n"
                "[EnableRequest]\nUrl=http://adapter.local/charger/enable\n"
                "[CurrentRequest]\nUrl=http://adapter.local/charger/current\n",
            )
            settings = load_template_charger_settings(self._service(MagicMock()), config_path)

            self.assertEqual(settings.base_url, "")
            self.assertEqual(settings.supported_phase_selections, DEFAULT_CHARGER_PHASE_SELECTIONS)
            self.assertEqual(settings.state_method, DEFAULT_STATE_METHOD)
            self.assertEqual(settings.enable_method, DEFAULT_ENABLE_METHOD)
            self.assertEqual(settings.current_method, DEFAULT_CURRENT_METHOD)
            self.assertEqual(settings.phase_method, DEFAULT_PHASE_METHOD)
            self.assertIsNone(settings.state_url)
            self.assertEqual(settings.enable_json_template, DEFAULT_ENABLE_JSON_TEMPLATE)
            self.assertEqual(settings.current_json_template, DEFAULT_CURRENT_JSON_TEMPLATE)
            self.assertIsNone(settings.phase_json_template)

    def test_template_charger_blank_phase_selection_uses_contract_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=\n"
                "[EnableRequest]\nUrl=/charger/enable\n"
                "[CurrentRequest]\nUrl=/charger/current\n",
            )
            settings = load_template_charger_settings(self._service(MagicMock()), config_path)

            self.assertEqual(settings.supported_phase_selections, DEFAULT_CHARGER_PHASE_SELECTIONS)

    def test_template_charger_invalid_methods_fall_back_to_contract_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
                "[StateRequest]\nMethod=DELETE\nUrl=/charger/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[EnableRequest]\nMethod=DELETE\nUrl=/charger/enable\n"
                "[CurrentRequest]\nMethod=DELETE\nUrl=/charger/current\n"
                "[PhaseRequest]\nMethod=DELETE\nUrl=/charger/phase\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse({"enabled": True})
            session.post.return_value = _FakeResponse({})
            backend = TemplateChargerBackend(self._service(session), config_path=config_path)

            state = backend.read_charger_state()
            backend.set_enabled(True)
            backend.set_current(12.0)
            backend.set_phase_selection("P1_P2")

            self.assertTrue(state.enabled)
            self.assertEqual(backend.settings.state_method, DEFAULT_STATE_METHOD)
            self.assertEqual(backend.settings.enable_method, DEFAULT_ENABLE_METHOD)
            self.assertEqual(backend.settings.current_method, DEFAULT_CURRENT_METHOD)
            self.assertEqual(backend.settings.phase_method, DEFAULT_PHASE_METHOD)
            session.get.assert_called_once_with(
                url="http://adapter.local/charger/state",
                timeout=2.0,
            )
            session.post.assert_any_call(
                url="http://adapter.local/charger/enable",
                timeout=2.0,
                json={"enabled": True},
            )
            session.post.assert_any_call(
                url="http://adapter.local/charger/current",
                timeout=2.0,
                json={"amps": 12},
            )
            session.post.assert_any_call(
                url="http://adapter.local/charger/phase",
                timeout=2.0,
                json={"phase_selection": "P1_P2"},
            )

    def test_template_charger_custom_methods_are_used_for_all_requests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
                "[StateRequest]\nMethod=PATCH\nUrl=/charger/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[EnableRequest]\nMethod=PUT\nUrl=/charger/enable\n"
                "[CurrentRequest]\nMethod=PATCH\nUrl=/charger/current\n"
                "[PhaseRequest]\nMethod=PUT\nUrl=/charger/phase\n",
            )
            session = MagicMock()
            session.patch.return_value = _FakeResponse({"enabled": True})
            session.put.return_value = _FakeResponse({})
            backend = TemplateChargerBackend(self._service(session), config_path=config_path)

            state = backend.read_charger_state()
            backend.set_enabled(True)
            backend.set_current(12.0)
            backend.set_phase_selection("P1_P2")

            self.assertTrue(state.enabled)
            session.patch.assert_any_call(
                url="http://adapter.local/charger/state",
                timeout=2.0,
            )
            session.put.assert_any_call(
                url="http://adapter.local/charger/enable",
                timeout=2.0,
                json={"enabled": True},
            )
            session.patch.assert_any_call(
                url="http://adapter.local/charger/current",
                timeout=2.0,
                json={"amps": 12},
            )
            session.put.assert_any_call(
                url="http://adapter.local/charger/phase",
                timeout=2.0,
                json={"phase_selection": "P1_P2"},
            )

    def test_read_charger_state_preserves_cached_phase_for_invalid_response_phase(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
                "[StateRequest]\nUrl=/charger/state\n"
                "[StateResponse]\nEnabledPath=enabled\nPhaseSelectionPath=phase\n"
                "[EnableRequest]\nUrl=/charger/enable\n"
                "[CurrentRequest]\nUrl=/charger/current\n"
                "[PhaseRequest]\nUrl=/charger/phase\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse({"enabled": True, "phase": "unknown"})
            service = SimpleNamespace(
                session=session,
                shelly_request_timeout_seconds=2.0,
                requested_phase_selection="P1_P2",
            )
            backend = TemplateChargerBackend(service, config_path=config_path)

            state = backend.read_charger_state()

            self.assertEqual(state.phase_selection, "P1_P2")
            self.assertEqual(backend._phase_selection_cache, "P1_P2")

    def test_template_charger_contract_literals_remain_stable(self) -> None:
        self.assertEqual(CHARGER_TRUE_JSON, "true")
        self.assertEqual(CHARGER_FALSE_JSON, "false")
        self.assertEqual(CHARGER_TRUE_INT, "1")
        self.assertEqual(CHARGER_FALSE_INT, "0")
        self.assertEqual(CHARGER_TRUE_TEXT, "on")
        self.assertEqual(CHARGER_FALSE_TEXT, "off")
