# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.readback_store import InMemoryReadbackStore
from venus_evcharger.backend.shelly_support import (
    ShellyBackendBase,
    _config,
    _derived_max_direct_switch_power_w,
    _mapping_path_value,
    _optional_signal_readback_settings,
    _parse_switch_channel_ids,
    _phase_switch_targets,
    _resolved_max_direct_switch_power_w,
    _resolved_phase_selection,
    _resolved_shelly_component,
    _resolved_switching_mode,
    _resolved_timeout_seconds,
    _supported_phase_selections,
    _switch_channel_id,
    load_shelly_backend_settings,
    normalize_switching_mode,
    parse_phase_selection_list,
    phase_currents_for_selection,
    phase_powers_for_selection,
    resolve_shelly_profile,
    validate_shelly_profile_role,
)
from venus_evcharger.backend.shelly_support_phase import _channel_id_tokens
from venus_evcharger.backend.shelly_io_requests import ShellyRequestClient
from venus_evcharger.backend.shelly_io_ports import (
    is_shelly_capability_host,
    is_shelly_lifecycle_host,
    is_shelly_readback_cache_host,
    is_shelly_readback_host,
    is_shelly_request_host,
    is_shelly_runtime_host,
    is_shelly_transport_host,
    is_shelly_worker_host,
    require_shelly_capability_host,
    require_shelly_lifecycle_host,
    require_shelly_readback_cache_host,
    require_shelly_readback_host,
    require_shelly_request_host,
    require_shelly_runtime_host,
    require_shelly_transport_host,
    require_shelly_worker_host,
)
from venus_evcharger.backend.shelly_io_types import (
    ShellyPmStatus,
    is_charger_state_backend,
    is_closeable,
    is_enable_backend,
    is_meter_backend,
    is_phase_selection_backend,
    is_settable_event,
    is_switch_capabilities_backend,
    is_switch_state_backend,
    is_transport_session_reset_backend,
    normalize_phase_value,
    normalize_supported_phase_tuple,
)
from venus_evcharger.backend.shelly_io_worker_status import (
    _copy_float_field,
    _copy_known_status_energy,
    _copy_known_status_phase_fields,
    _copy_known_status_scalars,
    _numeric_triplet,
    _numeric_value,
    _phase_tuple,
    _phase_tuple_candidate,
    _phase_tuple_items,
    _set_apower,
    _set_current,
    _set_voltage,
    local_pm_status_payload,
)


class _CloseableSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class TestShellyWallboxBackendShellySupport(unittest.TestCase):
    @staticmethod
    def _section(name: str, values: dict[str, object] | None = None) -> configparser.SectionProxy:
        parser = configparser.ConfigParser()
        parser.read_dict({name: {key: str(value) for key, value in (values or {}).items()}})
        return parser[name]

    def test_shelly_support_scalar_helpers_cover_validation_edges(self) -> None:
        self.assertEqual(normalize_switching_mode("weird", "contactor"), "contactor")
        self.assertEqual(parse_phase_selection_list("", ("P1_P2",)), ("P1_P2",))
        self.assertEqual(_channel_id_tokens(None), ())
        self.assertEqual(_parse_switch_channel_ids("", (5,)), (5,))
        self.assertEqual(_parse_switch_channel_ids(None, (5,)), (5,))
        self.assertEqual(_parse_switch_channel_ids("0,0,1", (9,)), (0, 1))
        self.assertIsNone(_switch_channel_id(""))
        with self.assertRaisesRegex(ValueError, "Invalid Shelly switch channel id 'x'"):
            _switch_channel_id("x")
        with self.assertRaisesRegex(ValueError, "Invalid Shelly switch channel id '-1'"):
            _switch_channel_id("-1")

        phase_map = self._section("PhaseMap", {"Bogus": "0"})
        with self.assertRaisesRegex(ValueError, "Unsupported PhaseMap key"):
            _phase_switch_targets(phase_map, 0, ("P1",))

        phase_map = self._section("PhaseMap", {"P1_P2": "1,2"})
        self.assertEqual(_phase_switch_targets(phase_map, 0, ("P1",))["P1"], (0,))

        phase_map = self._section("PhaseMap", {"P1_P2": "2", "P1": "4"})
        self.assertEqual(_phase_switch_targets(phase_map, 0, ("P1",)), {"P1": (4,)})
        phase_map = self._section("PhaseMap", {"P1": ""})
        self.assertEqual(_phase_switch_targets(phase_map, 3, ("P1",)), {"P1": (3,)})

        self.assertEqual(phase_powers_for_selection(2000.0, "P1_P2"), (1000.0, 1000.0, 0.0))
        self.assertEqual(phase_powers_for_selection(900.0, "P1_P2_P3"), (300.0, 300.0, 300.0))
        self.assertEqual(phase_powers_for_selection(900.0, "P1"), (900.0, 0.0, 0.0))
        self.assertEqual(phase_powers_for_selection(900.0, "P1", None), (900.0, 0.0, 0.0))
        self.assertEqual(phase_powers_for_selection(900.0, "P1", "bad-line"), (900.0, 0.0, 0.0))
        self.assertEqual(phase_powers_for_selection(900.0, "P1", " l2 "), (0.0, 900.0, 0.0))
        self.assertEqual(phase_currents_for_selection(12.0, "P1_P2", "L3"), (6.0, 6.0, 0.0))
        self.assertEqual(phase_currents_for_selection(12.0, "P1_P2_P3"), (4.0, 4.0, 4.0))
        self.assertEqual(phase_currents_for_selection(9.0, "P1"), (9.0, 0.0, 0.0))
        self.assertEqual(phase_currents_for_selection(9.0, "P1", None), (9.0, 0.0, 0.0))
        self.assertEqual(phase_currents_for_selection(9.0, "P1", "bad-line"), (9.0, 0.0, 0.0))
        self.assertEqual(phase_currents_for_selection(9.0, "P1", " l2 "), (0.0, 9.0, 0.0))
        self.assertEqual(phase_currents_for_selection(9.0, "P1", " l3 "), (0.0, 0.0, 9.0))
        self.assertEqual(phase_powers_for_selection(900.0, "P1", "L3"), (0.0, 0.0, 900.0))

    def test_shelly_support_profile_and_path_helpers_cover_errors(self) -> None:
        self.assertIsNone(resolve_shelly_profile(None))
        combined_profile = resolve_shelly_profile("switch_1ch_with_pm")
        self.assertIsNotNone(combined_profile)
        self.assertEqual(combined_profile.roles, ("switch", "meter"))
        validate_shelly_profile_role(None, "switch")
        validate_shelly_profile_role("pm1_meter", " METER ")

        expected_profiles = (
            "em1_meter,em1_meter_single_or_dual,em_3phase_profiled,em_meter,"
            "pm1_meter,pm1_meter_only,switch_1ch,switch_1ch_with_pm,"
            "switch_multi_or_plug,switch_or_cover_profile"
        )
        with self.assertRaises(ValueError) as unsupported_profile:
            resolve_shelly_profile("unknown")
        self.assertEqual(
            str(unsupported_profile.exception),
            f"Unsupported ShellyProfile 'unknown' (supported: {expected_profiles})",
        )

        with self.assertRaises(ValueError) as invalid_role:
            validate_shelly_profile_role("pm1_meter", "switch")
        self.assertEqual(
            str(invalid_role.exception),
            "ShellyProfile 'pm1_meter' is not valid for switch backends (supported roles: meter)",
        )
        with self.assertRaises(ValueError) as invalid_multi_role:
            validate_shelly_profile_role("switch_1ch_with_pm", "charger")
        self.assertEqual(
            str(invalid_multi_role.exception),
            "ShellyProfile 'switch_1ch_with_pm' is not valid for charger backends "
            "(supported roles: switch,meter)",
        )
        self.assertEqual(_config("").sections(), [])
        with self.assertRaises(FileNotFoundError) as missing_config:
            _config("/definitely/missing.ini")
        self.assertEqual(missing_config.exception.args, ("/definitely/missing.ini",))

        section = self._section("Feedback", {"Component": "Input", "ValuePath": ""})
        with self.assertRaisesRegex(ValueError, "requires ValuePath"):
            _optional_signal_readback_settings(section)

        section = self._section("Feedback", {"Component": "Temperature"})
        settings = _optional_signal_readback_settings(section)
        self.assertIsNotNone(settings)
        self.assertEqual(settings.value_path, "state")
        section = self._section("Feedback", {"Component": "Switch"})
        settings = _optional_signal_readback_settings(section)
        self.assertIsNotNone(settings)
        self.assertEqual(settings.value_path, "output")

        with self.assertRaisesRegex(ValueError, "Missing Shelly signal response path"):
            _mapping_path_value({"outer": {}}, "outer.missing")
        self.assertEqual(_mapping_path_value({"outer": {"x": 1}}, "outer..x"), 1)

    def test_optional_signal_readback_defaults_are_part_of_the_contract(self) -> None:
        section = self._section("Feedback", {"ValuePath": "state", "Invert": "1"})

        settings = _optional_signal_readback_settings(section)

        self.assertIsNotNone(settings)
        self.assertEqual(settings.component, "Input")
        self.assertEqual(settings.device_id, 0)
        self.assertEqual(settings.value_path, "state")
        self.assertTrue(settings.invert)

        custom = _optional_signal_readback_settings(
            section,
            default_component="Switch",
            default_id=2,
        )
        self.assertIsNotNone(custom)
        self.assertEqual(custom.component, "Switch")
        self.assertEqual(custom.device_id, 2)

        blank_component = self._section("Feedback", {"Component": "", "ValuePath": "output"})
        blank_settings = _optional_signal_readback_settings(
            blank_component,
            default_component="Switch",
            default_id=5,
        )
        self.assertIsNotNone(blank_settings)
        self.assertEqual(blank_settings.component, "Switch")
        self.assertEqual(blank_settings.device_id, 5)
        self.assertEqual(blank_settings.value_path, "output")
        self.assertFalse(blank_settings.invert)

        trimmed = self._section("Feedback", {"Component": " Switch ", "Id": " 8 ", "ValuePath": " output ", "Invert": "1"})
        trimmed_settings = _optional_signal_readback_settings(trimmed)
        self.assertIsNotNone(trimmed_settings)
        self.assertEqual(trimmed_settings.component, "Switch")
        self.assertEqual(trimmed_settings.device_id, 8)
        self.assertEqual(trimmed_settings.value_path, "output")
        self.assertTrue(trimmed_settings.invert)

        with self.assertRaises(ValueError):
            _optional_signal_readback_settings(self._section("Feedback", {"Id": "bad"}))

    def test_shelly_settings_use_service_defaults_without_legacy_config(self) -> None:
        service = SimpleNamespace(
            host="192.0.2.20",
            pm_component="PM1",
            pm_id=3,
            phase="P1_P2",
            max_current=10.0,
            _last_voltage=230.0,
            shelly_request_timeout_seconds=4.5,
            username="service-user",
            password="service-password",
            use_digest_auth=True,
        )

        settings = load_shelly_backend_settings(service)

        self.assertIsNone(settings.profile_name)
        self.assertEqual(settings.host, "192.0.2.20")
        self.assertEqual(settings.component, "PM1")
        self.assertEqual(settings.device_id, 3)
        self.assertEqual(settings.timeout_seconds, 4.5)
        self.assertEqual(settings.username, "service-user")
        self.assertEqual(settings.password, "service-password")
        self.assertTrue(settings.use_digest_auth)
        self.assertEqual(settings.phase_selection, "P1_P2")
        self.assertEqual(settings.switching_mode, "direct")
        self.assertEqual(settings.supported_phase_selections, ("P1",))
        self.assertFalse(settings.requires_charge_pause_for_phase_change)
        self.assertEqual(settings.max_direct_switch_power_w, 2300.0)
        self.assertEqual(settings.phase_switch_targets, {"P1": (3,)})
        self.assertIsNone(settings.feedback_readback)
        self.assertIsNone(settings.interlock_readback)

    def test_shelly_settings_minimal_service_defaults_are_stable(self) -> None:
        settings = load_shelly_backend_settings(SimpleNamespace())

        self.assertEqual(settings.host, "")
        self.assertEqual(settings.component, "Switch")
        self.assertEqual(settings.device_id, 0)
        self.assertEqual(settings.username, "")
        self.assertEqual(settings.password, "")
        self.assertFalse(settings.use_digest_auth)
        self.assertEqual(settings.phase_selection, "P1")
        self.assertEqual(settings.phase_switch_targets, {"P1": (0,)})

    def test_shelly_profile_default_phase_overrides_service_default_phase(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "shelly.ini"
            config_path.write_text("[Adapter]\nShellyProfile=em_meter\n", encoding="utf-8")

            settings = load_shelly_backend_settings(
                SimpleNamespace(phase="P1", use_digest_auth=True),
                str(config_path),
            )

        self.assertEqual(settings.component, "EM")
        self.assertEqual(settings.device_id, 0)
        self.assertEqual(settings.phase_selection, "P1_P2_P3")
        self.assertTrue(settings.use_digest_auth)

    def test_shelly_profile_without_phase_default_uses_service_phase(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "shelly.ini"
            config_path.write_text("[Adapter]\nShellyProfile=switch_1ch\n", encoding="utf-8")

            settings = load_shelly_backend_settings(SimpleNamespace(phase="P1_P2"), str(config_path))

        self.assertEqual(settings.component, "Switch")
        self.assertEqual(settings.phase_selection, "P1_P2")

    def test_shelly_settings_config_overrides_are_a_single_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "shelly.ini"
            config_path.write_text(
                "\n".join(
                    (
                        "[Adapter]",
                        "ShellyProfile=pm1_meter",
                        "Host=192.0.2.30",
                        "Component=EM1",
                        "Id=4",
                        "RequestTimeoutSeconds=7.5",
                        "Username=config-user",
                        "Password=config-password",
                        "DigestAuth=0",
                        "[Phase]",
                        "MeasuredPhaseSelection=P1_P2_P3",
                        "[Capabilities]",
                        "SwitchingMode=contactor",
                        "SupportedPhaseSelections=P1,P1_P2",
                        "RequiresChargePauseForPhaseChange=1",
                        "MaxDirectSwitchPowerWatts=3456",
                        "[PhaseMap]",
                        "P1=8",
                        "P1_P2=8,9",
                        "[Feedback]",
                        "Component=Switch",
                        "Id=6",
                        "ValuePath=output",
                        "Invert=1",
                        "[Interlock]",
                        "Component=Input",
                        "Id=7",
                        "ValuePath=status.enabled",
                        "Invert=0",
                        "",
                    )
                ),
                encoding="utf-8",
            )
            service = SimpleNamespace(
                host="service-host",
                pm_component="PM1",
                pm_id=1,
                phase="P1",
                max_current=10.0,
                _last_voltage=230.0,
                shelly_request_timeout_seconds=2.0,
                username="service-user",
                password="service-password",
                use_digest_auth=True,
            )

            settings = load_shelly_backend_settings(service, str(config_path))

        self.assertEqual(settings.profile_name, "pm1_meter")
        self.assertEqual(settings.host, "192.0.2.30")
        self.assertEqual(settings.component, "EM1")
        self.assertEqual(settings.device_id, 4)
        self.assertEqual(settings.timeout_seconds, 7.5)
        self.assertEqual(settings.username, "config-user")
        self.assertEqual(settings.password, "config-password")
        self.assertFalse(settings.use_digest_auth)
        self.assertEqual(settings.phase_selection, "P1_P2_P3")
        self.assertEqual(settings.switching_mode, "contactor")
        self.assertEqual(settings.supported_phase_selections, ("P1", "P1_P2"))
        self.assertTrue(settings.requires_charge_pause_for_phase_change)
        self.assertIsNone(settings.max_direct_switch_power_w)
        self.assertEqual(settings.phase_switch_targets, {"P1": (8,), "P1_P2": (8, 9)})
        self.assertIsNotNone(settings.feedback_readback)
        self.assertEqual(settings.feedback_readback.component, "Switch")
        self.assertEqual(settings.feedback_readback.device_id, 6)
        self.assertEqual(settings.feedback_readback.value_path, "output")
        self.assertTrue(settings.feedback_readback.invert)
        self.assertIsNotNone(settings.interlock_readback)
        self.assertEqual(settings.interlock_readback.component, "Input")
        self.assertEqual(settings.interlock_readback.device_id, 7)
        self.assertEqual(settings.interlock_readback.value_path, "status.enabled")
        self.assertFalse(settings.interlock_readback.invert)

    def test_shelly_resolvers_preserve_service_and_config_precedence(self) -> None:
        adapter = self._section("Adapter", {"Component": "", "RequestTimeoutSeconds": "6.0"})
        capabilities = self._section(
            "Capabilities",
            {
                "SwitchingMode": "contactor",
                "SupportedPhaseSelections": "P1,P1_P2",
                "MaxDirectSwitchPowerWatts": "5000",
            },
        )
        phase = self._section("Phase", {"MeasuredPhase": "P1_P2"})
        service = SimpleNamespace(pm_component="PM1", shelly_request_timeout_seconds=3.0, max_current=10.0, _last_voltage=230.0)

        self.assertEqual(_resolved_shelly_component(adapter, None, service), "Switch")
        self.assertEqual(_resolved_shelly_component(self._section("Adapter", {}), None, SimpleNamespace()), "Switch")
        self.assertEqual(_resolved_timeout_seconds(adapter, service), 6.0)
        self.assertEqual(_resolved_switching_mode(capabilities, "direct"), "contactor")
        self.assertEqual(_resolved_switching_mode(self._section("Capabilities", {"SwitchingMode": "invalid"}), "contactor"), "contactor")
        self.assertEqual(_supported_phase_selections(capabilities), ("P1", "P1_P2"))
        self.assertEqual(_resolved_phase_selection(phase, "P1"), "P1_P2")
        self.assertIsNone(_resolved_max_direct_switch_power_w(service, capabilities, "contactor"))

        invalid_phase = self._section("Phase", {"MeasuredPhaseSelection": "invalid", "MeasuredPhase": "also-invalid"})
        self.assertEqual(_resolved_phase_selection(invalid_phase, "P1_P2"), "P1_P2")

        empty_supported = self._section("Capabilities", {"SupportedPhaseSelections": ""})
        self.assertEqual(_supported_phase_selections(empty_supported), ("P1",))

        direct_capabilities = self._section("Capabilities", {"MaxDirectSwitchPowerWatts": "5000"})
        self.assertEqual(_resolved_max_direct_switch_power_w(service, direct_capabilities, "direct"), 5000.0)

        empty_capabilities = self._section("Capabilities", {})
        self.assertEqual(_resolved_max_direct_switch_power_w(service, empty_capabilities, "direct"), 2300.0)
        self.assertEqual(_derived_max_direct_switch_power_w(SimpleNamespace(max_current=0.5, _last_voltage=230.0)), 115.0)
        self.assertEqual(_derived_max_direct_switch_power_w(SimpleNamespace(max_current=10.0, _last_voltage=0.5)), 5.0)
        self.assertIsNone(_derived_max_direct_switch_power_w(SimpleNamespace(max_current=0.0, _last_voltage=230.0)))
        self.assertIsNone(_derived_max_direct_switch_power_w(SimpleNamespace(max_current=10.0, _last_voltage=0.0)))
        self.assertIsNone(_derived_max_direct_switch_power_w(SimpleNamespace(max_current=None, _last_voltage=230.0)))

    def test_shelly_backend_base_auth_and_rpc_helpers_cover_remaining_branches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "shelly.ini"
            config_path.write_text(
                "[Adapter]\nHost=192.0.2.20\nUsername=user\nPassword=secret\nDigestAuth=1\n",
                encoding="utf-8",
            )
            session = MagicMock()
            response = MagicMock()
            response.json.return_value = {}
            session.get.return_value = response
            backend = ShellyBackendBase(SimpleNamespace(session=session), str(config_path))

            with patch("venus_evcharger.backend.shelly_support.HTTPDigestAuth", return_value="digest-auth") as auth_mock:
                self.assertEqual(backend._auth(), "digest-auth")
                auth_mock.assert_called_once_with("user", "secret")
                self.assertEqual(backend._rpc_url("Switch.GetStatus"), "http://192.0.2.20/rpc/Switch.GetStatus")
                backend._request_json("http://192.0.2.20/rpc/Test")
                session.get.assert_called_once_with(
                    url="http://192.0.2.20/rpc/Test",
                    timeout=2.0,
                    auth="digest-auth",
                )
                self.assertEqual(
                    backend._rpc_url("Switch.Set", {"id": 0, "on": True, "brightness": 12.5}),
                    "http://192.0.2.20/rpc/Switch.Set?id=0&on=true&brightness=12.5",
                )

            backend.settings = SimpleNamespace(username="user", password="secret", use_digest_auth=False, timeout_seconds=2.0)
            self.assertEqual(backend._auth(), ("user", "secret"))

            backend.settings = SimpleNamespace(username="", password="secret", use_digest_auth=True, timeout_seconds=2.0)
            self.assertIsNone(backend._auth())
            backend.settings = SimpleNamespace(username="user", password="", use_digest_auth=True, timeout_seconds=2.0)
            self.assertIsNone(backend._auth())

            response.json.return_value = ["not", "a", "dict"]
            with self.assertRaises(ValueError) as error_context:
                backend._request_json("http://192.0.2.20/rpc/Test")
            self.assertEqual(str(error_context.exception), "Shelly RPC response must be a JSON object")

            response.json.return_value = {1: "one", "two": 2}
            self.assertEqual(backend._request_json("http://192.0.2.20/rpc/Test"), {"1": "one", "two": 2})

            backend.settings = SimpleNamespace(component="Switch", device_id=0, timeout_seconds=2.0)
            backend._rpc_call = MagicMock(return_value={"output": True})
            self.assertEqual(backend._component_status(" Switch ", "3"), {"output": True})
            backend._rpc_call.assert_called_once_with("Switch.GetStatus", id=3)

            backend._component_status = MagicMock(return_value={"status": {"enabled": "1"}})
            signal = SimpleNamespace(component="Input", device_id=4, value_path="status.enabled", invert=False)
            self.assertTrue(backend._signal_readback_flag(signal))
            backend._component_status.assert_called_once_with("Input", 4)
            backend._component_status = MagicMock(return_value={"output": 1})
            inverted_signal = SimpleNamespace(component="Switch", device_id=1, value_path="output", invert=True)
            self.assertFalse(backend._signal_readback_flag(inverted_signal))
            self.assertIsNone(backend._signal_readback_flag(None))

    def test_shelly_backend_base_init_defaults_are_contractual(self) -> None:
        service = SimpleNamespace(
            session=None,
            host="192.0.2.40",
            pm_component="Switch",
            pm_id=0,
            phase="P1",
            max_current=10.0,
            _last_voltage=230.0,
            shelly_request_timeout_seconds=2.0,
            username="",
            password="",
            use_digest_auth=False,
        )

        with patch("venus_evcharger.backend.shelly_support.requests.Session", return_value="new-session"):
            backend = ShellyBackendBase(service)

        self.assertIs(backend.service, service)
        self.assertEqual(backend.config_path, "")
        self.assertEqual(backend.settings.host, "192.0.2.40")
        self.assertEqual(backend.settings.switching_mode, "direct")
        self.assertEqual(backend.settings.supported_phase_selections, ("P1",))
        self.assertEqual(backend._session, "new-session")

    def test_shelly_backend_base_init_creates_session_when_service_has_no_session_attr(self) -> None:
        service = SimpleNamespace(
            host="192.0.2.40",
            pm_component="Switch",
            pm_id=0,
            phase="P1",
            max_current=10.0,
            _last_voltage=230.0,
            shelly_request_timeout_seconds=2.0,
            username="",
            password="",
            use_digest_auth=False,
        )

        with patch("venus_evcharger.backend.shelly_support.requests.Session", return_value="created-session"):
            backend = ShellyBackendBase(service)

        self.assertIs(backend.service, service)
        self.assertEqual(backend._session, "created-session")

    def test_shelly_backend_base_strips_config_path_before_loading(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "shelly.ini"
            config_path.write_text("[Adapter]\nHost=192.0.2.41\n", encoding="utf-8")
            service = SimpleNamespace(session=MagicMock(), max_current=10.0, _last_voltage=230.0)

            backend = ShellyBackendBase(service, f"  {config_path}  ")

        self.assertEqual(backend.config_path, str(config_path))
        self.assertEqual(backend.settings.host, "192.0.2.41")

    def test_shelly_response_and_phase_status_contracts_cover_invalid_and_valid_edges(self) -> None:
        with self.assertRaisesRegex(ValueError, "Shelly response must be a JSON object"):
            ShellyRequestClient._json_object(["not", "a", "dict"])

        pm_status: dict[str, object] = {}
        _copy_known_status_phase_fields(
            {
                "_phase_selection": "P1_P2",
                "_phase_powers_w": (1200, 800.0, 0),
                "_phase_currents_a": [5, 3.5, 0],
            },
            pm_status,
        )
        self.assertEqual(pm_status["_phase_selection"], "P1_P2")
        self.assertEqual(pm_status["_phase_powers_w"], (1200.0, 800.0, 0.0))
        self.assertEqual(pm_status["_phase_currents_a"], (5.0, 3.5, 0.0))
        self.assertIsNone(_phase_tuple(("bad", 1, 2)))
        self.assertFalse(_numeric_triplet((1, True, 3)))

    def test_shelly_worker_local_pm_payload_copies_only_known_valid_fields(self) -> None:
        raw_status = {
            "output": 0,
            "apower": 123.5,
            "current": 4,
            "voltage": 229.5,
            "aenergy": {"total": 987},
            "_pm_confirmed": "",
            "_phase_selection": "P1_P2",
            "_phase_powers_w": [100.0, 200, 0],
            "_phase_currents_a": (1, 2.5, 0.0),
            "ignored": "value",
        }

        self.assertEqual(
            local_pm_status_payload(raw_status),
            {
                "output": False,
                "apower": 123.5,
                "current": 4.0,
                "voltage": 229.5,
                "aenergy": {"total": 987.0},
                "_pm_confirmed": False,
                "_phase_selection": "P1_P2",
                "_phase_powers_w": (100.0, 200.0, 0.0),
                "_phase_currents_a": (1.0, 2.5, 0.0),
            },
        )

        invalid_status = {
            "output": None,
            "apower": True,
            "current": "5",
            "voltage": None,
            "aenergy": {"total": "bad"},
            "_pm_confirmed": None,
            "_phase_powers_w": [100.0, "bad", 0],
            "_phase_currents_a": (1, 2),
        }
        self.assertEqual(local_pm_status_payload(invalid_status), {"aenergy": {"total": 0.0}})

    def test_shelly_worker_payload_copy_helpers_cover_direct_edges(self) -> None:
        pm_status: ShellyPmStatus = {}
        _copy_known_status_scalars(
            {
                "output": True,
                "apower": 1.0,
                "current": 2.0,
                "voltage": 3.0,
                "_pm_confirmed": True,
            },
            pm_status,
        )
        self.assertEqual(
            pm_status,
            {
                "output": True,
                "apower": 1.0,
                "current": 2.0,
                "voltage": 3.0,
                "_pm_confirmed": True,
            },
        )

        energy_status: ShellyPmStatus = {}
        _copy_known_status_energy({"aenergy": {"total": 12.5}}, energy_status)
        self.assertEqual(energy_status, {"aenergy": {"total": 12.5}})
        _copy_known_status_energy({"aenergy": "bad"}, energy_status)
        self.assertEqual(energy_status, {"aenergy": {"total": 12.5}})

        scalar_status: ShellyPmStatus = {}
        _copy_float_field({"apower": 1.25}, scalar_status, "apower")
        _copy_float_field({"current": 2}, scalar_status, "current")
        _copy_float_field({"voltage": 230.0}, scalar_status, "voltage")
        _copy_float_field({"unknown": 99.0}, scalar_status, "unknown")
        _copy_float_field({"apower": False}, scalar_status, "apower")
        self.assertEqual(scalar_status, {"apower": 1.25, "current": 2.0, "voltage": 230.0})

        _set_apower(scalar_status, 5.0)
        _set_current(scalar_status, 6.0)
        _set_voltage(scalar_status, 231.0)
        self.assertEqual(scalar_status, {"apower": 5.0, "current": 6.0, "voltage": 231.0})

    def test_shelly_worker_phase_tuple_helpers_cover_shape_and_numeric_contracts(self) -> None:
        self.assertEqual(_phase_tuple([1, 2.5, 0]), (1.0, 2.5, 0.0))
        self.assertEqual(_phase_tuple_items((1, 2, 3)), (1, 2, 3))
        self.assertEqual(_phase_tuple_candidate([1, 2, 3]), (1, 2, 3))
        self.assertIsNone(_phase_tuple_candidate((1, 2)))
        self.assertIsNone(_phase_tuple_candidate("123"))
        self.assertIsNone(_phase_tuple_items((1, object(), 3)))
        self.assertTrue(_numeric_triplet((1, 2.0, 3)))
        self.assertTrue(_numeric_value(0.0))
        self.assertFalse(_numeric_value(True))
        self.assertFalse(_numeric_value("0"))

    def test_shelly_backend_base_can_reset_transport_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "shelly.ini"
            config_path.write_text("[Adapter]\nHost=192.0.2.20\n", encoding="utf-8")
            old_session = MagicMock()
            new_session = MagicMock()
            backend = ShellyBackendBase(SimpleNamespace(session=old_session), str(config_path))

            backend.reset_transport_session(new_session)

            old_session.close.assert_called_once_with()
            self.assertIs(backend._session, new_session)

            old_session.close.reset_mock()
            backend.reset_transport_session(new_session)
            old_session.close.assert_not_called()
            self.assertIs(backend._session, new_session)

            close_less_session = object()
            backend._session = close_less_session
            backend.reset_transport_session(new_session)
            self.assertIs(backend._session, new_session)

            strict_old_session = _CloseableSession()
            backend._session = strict_old_session
            backend.reset_transport_session(new_session)
            self.assertTrue(strict_old_session.closed)
            self.assertIs(backend._session, new_session)

            uninitialized_backend = object.__new__(ShellyBackendBase)
            uninitialized_backend.reset_transport_session(new_session)
            self.assertIs(uninitialized_backend._session, new_session)

    def test_shelly_io_component_ports_validate_independently(self) -> None:
        readback_store = InMemoryReadbackStore()
        runtime = SimpleNamespace(
            ensure_worker_state=MagicMock(),
            update_worker_snapshot=MagicMock(),
            mark_failure=MagicMock(),
            warning_throttled=MagicMock(),
            mark_recovery=MagicMock(),
            worker_snapshot=MagicMock(return_value={}),
            ensure_auto_input_helper=MagicMock(),
            source_retry_ready=MagicMock(return_value=True),
            source_retry_remaining=MagicMock(return_value=0),
            delay_source_retry=MagicMock(),
        )
        auto = SimpleNamespace(
            mark_relay_changed=MagicMock(),
            mode_uses_auto_logic=MagicMock(return_value=False),
        )
        service = SimpleNamespace(
            session=MagicMock(),
            use_digest_auth=False,
            username="",
            password="",
            host="192.0.2.20",
            pm_component="switch",
            pm_id=0,
            _worker_session=MagicMock(),
            _readback_store=readback_store,
            time_now=lambda: 1.0,
            auto_shelly_soft_fail_seconds=10.0,
            requested_phase_selection="P1",
            active_phase_selection="P1",
            runtime=runtime,
            auto=auto,
        )
        contracts = (
            (is_shelly_request_host, require_shelly_request_host),
            (is_shelly_capability_host, require_shelly_capability_host),
            (is_shelly_runtime_host, require_shelly_runtime_host),
            (is_shelly_readback_cache_host, require_shelly_readback_cache_host),
            (is_shelly_readback_host, require_shelly_readback_host),
            (is_shelly_transport_host, require_shelly_transport_host),
            (is_shelly_worker_host, require_shelly_worker_host),
            (is_shelly_lifecycle_host, require_shelly_lifecycle_host),
        )
        for predicate, require in contracts:
            with self.subTest(require=require.__name__):
                self.assertTrue(predicate(service))
                self.assertIs(require(service), service)
                self.assertFalse(predicate(SimpleNamespace()))
                with self.assertRaises(TypeError):
                    require(SimpleNamespace())

    def test_shelly_io_type_guard_contracts_are_explicit(self) -> None:
        guard_cases = (
            (is_meter_backend, "read_meter"),
            (is_enable_backend, "set_enabled"),
            (is_phase_selection_backend, "set_phase_selection"),
            (is_switch_state_backend, "read_switch_state"),
            (is_switch_capabilities_backend, "capabilities"),
            (is_charger_state_backend, "read_charger_state"),
            (is_transport_session_reset_backend, "reset_transport_session"),
            (is_closeable, "close"),
            (is_settable_event, "set"),
        )
        for guard, method_name in guard_cases:
            with self.subTest(method_name=method_name):
                self.assertFalse(guard(None))
                self.assertFalse(guard(SimpleNamespace(**{method_name: None})))
                self.assertFalse(guard(SimpleNamespace(**{method_name: "not-callable"})))
                self.assertTrue(guard(SimpleNamespace(**{method_name: lambda *args, **kwargs: None})))

    def test_shelly_io_phase_normalizers_forward_defaults_explicitly(self) -> None:
        self.assertEqual(normalize_phase_value("p1_p2"), "P1_P2")
        self.assertEqual(normalize_phase_value("bad"), "P1")
        self.assertEqual(normalize_phase_value("bad", "P1_P2_P3"), "P1_P2_P3")
        self.assertEqual(normalize_supported_phase_tuple("P1_P2,P1_P2_P3"), ("P1_P2", "P1_P2_P3"))
        self.assertEqual(normalize_supported_phase_tuple("", ("P1_P2",)), ("P1_P2",))


__all__ = [name for name in globals() if not name.startswith("__")]
