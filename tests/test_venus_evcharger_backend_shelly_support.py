# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.backend.shelly_support import (
    ShellyBackendBase,
    _config,
    _mapping_path_value,
    _optional_signal_readback_settings,
    _parse_switch_channel_ids,
    _phase_switch_targets,
    _switch_channel_id,
    normalize_switching_mode,
    phase_currents_for_selection,
    phase_powers_for_selection,
    resolve_shelly_profile,
    validate_shelly_profile_role,
)
from venus_evcharger.backend.shelly_io_types import is_shelly_io_host, require_shelly_io_host


class TestShellyWallboxBackendShellySupport(unittest.TestCase):
    @staticmethod
    def _section(name: str, values: dict[str, object] | None = None) -> configparser.SectionProxy:
        parser = configparser.ConfigParser()
        parser.read_dict({name: {key: str(value) for key, value in (values or {}).items()}})
        return parser[name]

    def test_shelly_support_scalar_helpers_cover_validation_edges(self) -> None:
        self.assertEqual(normalize_switching_mode("weird", "contactor"), "contactor")
        self.assertEqual(_parse_switch_channel_ids("", (5,)), (5,))
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

        self.assertEqual(phase_powers_for_selection(2000.0, "P1_P2"), (1000.0, 1000.0, 0.0))
        self.assertEqual(phase_currents_for_selection(12.0, "P1_P2", "L3"), (6.0, 6.0, 0.0))
        self.assertEqual(phase_powers_for_selection(900.0, "P1", "L3"), (0.0, 0.0, 900.0))

    def test_shelly_support_profile_and_path_helpers_cover_errors(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported ShellyProfile"):
            resolve_shelly_profile("unknown")
        with self.assertRaisesRegex(ValueError, "is not valid for switch backends"):
            validate_shelly_profile_role("pm1_meter", "switch")
        self.assertEqual(_config("").sections(), [])
        with self.assertRaises(FileNotFoundError):
            _config("/definitely/missing.ini")

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

    def test_shelly_backend_base_auth_and_rpc_helpers_cover_remaining_branches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "shelly.ini"
            config_path.write_text(
                "[Adapter]\nHost=192.168.1.20\nUsername=user\nPassword=secret\nDigestAuth=1\n",
                encoding="utf-8",
            )
            session = MagicMock()
            response = MagicMock()
            response.json.return_value = {}
            session.get.return_value = response
            backend = ShellyBackendBase(SimpleNamespace(session=session), str(config_path))

            with patch("venus_evcharger.backend.shelly_support.HTTPDigestAuth", return_value="digest-auth"):
                self.assertEqual(backend._auth(), "digest-auth")
                self.assertEqual(backend._rpc_url("Switch.GetStatus"), "http://192.168.1.20/rpc/Switch.GetStatus")
                backend._request_json("http://192.168.1.20/rpc/Test")
                session.get.assert_called_once_with(
                    url="http://192.168.1.20/rpc/Test",
                    timeout=2.0,
                    auth="digest-auth",
                )

            backend.settings = SimpleNamespace(username="user", password="secret", use_digest_auth=False, timeout_seconds=2.0)
            self.assertEqual(backend._auth(), ("user", "secret"))

    def test_shelly_backend_base_can_reset_transport_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "shelly.ini"
            config_path.write_text("[Adapter]\nHost=192.168.1.20\n", encoding="utf-8")
            old_session = MagicMock()
            new_session = MagicMock()
            backend = ShellyBackendBase(SimpleNamespace(session=old_session), str(config_path))

            backend.reset_transport_session(new_session)

            old_session.close.assert_called_once_with()
            self.assertIs(backend._session, new_session)

    def test_shelly_io_host_contract_validates_core_service_boundary(self) -> None:
        service = SimpleNamespace(
            session=MagicMock(),
            host="192.168.1.20",
            pm_component="switch",
            pm_id=0,
            _relay_command_lock=MagicMock(),
            _worker_stop_event=MagicMock(),
            _time_now=lambda: 123.0,
            _request=lambda _url: {},
            rpc_call=lambda _method, **_params: {},
            _peek_pending_relay_command=lambda: (None, None),
            _clear_pending_relay_command=lambda _relay_on: None,
        )

        self.assertTrue(is_shelly_io_host(service))
        self.assertIs(require_shelly_io_host(service), service)

    def test_shelly_io_host_contract_reports_missing_members(self) -> None:
        with self.assertRaisesRegex(TypeError, "_request"):
            require_shelly_io_host(SimpleNamespace(session=MagicMock()))
