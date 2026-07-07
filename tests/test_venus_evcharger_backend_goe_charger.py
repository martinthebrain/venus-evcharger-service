# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from requests.auth import HTTPDigestAuth

from venus_evcharger.backend.goe_charger import (
    GoEChargerBackend,
    _goe_actual_current_amps,
    _goe_energy_kwh,
    _goe_fault_text,
    _goe_headers,
    _goe_nrg_values,
    _goe_optional_bool,
    _goe_optional_int,
    _goe_payload,
    _goe_phase_selection,
    _goe_power_w,
    _goe_query_value,
    _goe_rounded_current_setting,
    _goe_status_text,
    load_goe_charger_settings,
)


class _Response:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.raise_called = False

    def raise_for_status(self) -> None:
        self.raise_called = True

    def json(self) -> object:
        return self.payload


class _Session:
    def __init__(self, *responses: _Response) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def get(self, **kwargs: object) -> _Response:
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("unexpected go-e request")
        return self.responses.pop(0)


class TestGoEChargerBackend(unittest.TestCase):
    def _config(self, text: str) -> str:
        path = Path(tempfile.mkdtemp()) / "goe.ini"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def _service(self, session: _Session | None = None, **attrs: object) -> SimpleNamespace:
        payload = {"session": session or _Session()}
        payload.update(attrs)
        return SimpleNamespace(**payload)

    def test_settings_require_base_url_and_preserve_contract_fields(self) -> None:
        with self.assertRaises(ValueError) as missing_base_url:
            load_goe_charger_settings(self._service(), self._config("[Adapter]\nRequestTimeoutSeconds=3\n"))
        self.assertEqual(str(missing_base_url.exception), "go-e charger backend requires Adapter.BaseUrl")

        settings = load_goe_charger_settings(
            self._service(),
            self._config(
                "[Adapter]\n"
                "BaseUrl=http://goe.local/root/\n"
                "RequestTimeoutSeconds=3.5\n"
                "Username=user\n"
                "Password=secret\n"
                "DigestAuth=1\n"
                "AuthHeaderName=X-Token\n"
                "AuthHeaderValue=abc\n"
            ),
        )

        self.assertEqual(settings.base_url, "http://goe.local/root/")
        self.assertEqual(settings.timeout_seconds, 3.5)
        self.assertEqual(settings.supported_phase_selections, ("P1",))
        self.assertEqual(settings.state_url, "http://goe.local/root/api/status")
        self.assertEqual(settings.enable_url, "http://goe.local/root/api/set")
        self.assertEqual(settings.current_url, "http://goe.local/root/api/set")
        self.assertIsNone(settings.phase_url)
        self.assertEqual(settings.status_filter, "alw,amp,acu,car,err,eto,nrg,pnp")
        self.assertIsInstance(settings.auth_settings.use_digest_auth, bool)
        self.assertIsInstance(settings.auth_settings.username, str)
        self.assertIsInstance(settings.auth_settings.password, str)
        self.assertIsInstance(settings.auth_settings.auth_header_name, str)
        self.assertIsInstance(settings.auth_settings.auth_header_value, str)

    def test_settings_use_service_fallbacks_when_adapter_allows_them(self) -> None:
        settings = load_goe_charger_settings(
            self._service(
                shelly_request_timeout_seconds=6.5,
                _adapter_auth_fallback_enabled=True,
                username="service-user",
                password="service-secret",
                use_digest_auth=True,
            ),
            self._config("[Adapter]\nBaseUrl=http://goe.local\n"),
        )

        self.assertEqual(settings.timeout_seconds, 6.5)
        self.assertEqual(settings.auth_settings.username, "service-user")
        self.assertEqual(settings.auth_settings.password, "service-secret")
        self.assertIs(settings.auth_settings.use_digest_auth, True)

    def test_backend_constructor_passes_service_into_settings_loader(self) -> None:
        backend = GoEChargerBackend(
            self._service(
                shelly_request_timeout_seconds=6.5,
                _adapter_auth_fallback_enabled=True,
                username="service-user",
                password="service-secret",
                use_digest_auth=True,
            ),
            self._config("[Adapter]\nBaseUrl=http://goe.local\n"),
        )

        self.assertEqual(backend.settings.timeout_seconds, 6.5)
        self.assertEqual(backend.settings.auth_settings.username, "service-user")
        self.assertEqual(backend.settings.auth_settings.password, "service-secret")
        self.assertIs(backend.settings.auth_settings.use_digest_auth, True)

    def test_payload_and_scalar_normalizers_cover_local_and_cloud_shapes(self) -> None:
        self.assertEqual(_goe_payload(["not", "a", "dict"]), {})
        self.assertEqual(_goe_payload({"answer": 42}), {"answer": 42})
        self.assertEqual(_goe_payload({"data": {"answer": 42}}), {"answer": 42})
        self.assertEqual(_goe_payload({"data": "ignored", 7: "seven"}), {"data": "ignored", "7": "seven"})

        self.assertEqual(_goe_query_value(True), "true")
        self.assertEqual(_goe_query_value({"amp": 16}), '{"amp":16}')
        self.assertEqual(_goe_query_value([1, 2]), "[1,2]")
        self.assertIsNone(_goe_optional_int(None))
        self.assertIsNone(_goe_optional_int("bad"))
        self.assertEqual(_goe_optional_int("7.9"), 7)

        for value in (True, 1, 2, "1", "true", "on", "yes"):
            with self.subTest(value=value):
                self.assertIs(_goe_optional_bool(value), True)
        for value in (False, 0, "0", "false", "off", "no"):
            with self.subTest(value=value):
                self.assertIs(_goe_optional_bool(value), False)
        self.assertIsNone(_goe_optional_bool(None))
        self.assertIsNone(_goe_optional_bool("maybe"))

    def test_phase_measurement_energy_status_and_fault_contracts(self) -> None:
        self.assertEqual(_goe_phase_selection({}, "P1_P2"), "P1_P2")
        self.assertEqual(_goe_phase_selection({"pnp": 1}, "P1_P2"), "P1")
        self.assertEqual(_goe_phase_selection({"pnp": 2}, "P1"), "P1_P2")
        self.assertEqual(_goe_phase_selection({"pnp": 3}, "P1"), "P1_P2_P3")

        nrg = [230, 231, 232, 0, 64, 80, 72, 0, 0, 0, 0, 123]
        self.assertEqual(_goe_nrg_values({"nrg": nrg}), [float(item) for item in nrg])
        self.assertIsNone(_goe_nrg_values({"nrg": "bad"}))
        self.assertIsNone(_goe_nrg_values({"nrg": [1, "bad"]}))
        self.assertEqual(_goe_actual_current_amps({"nrg": nrg, "acu": 1}), 8.0)
        self.assertEqual(_goe_actual_current_amps({"nrg": [0, 0, 0, 0, 100, 80, 70]}), 10.0)
        self.assertEqual(_goe_actual_current_amps({"nrg": [0, 0, 0, 0, 40, 50, 90]}), 9.0)
        self.assertEqual(_goe_actual_current_amps({"nrg": [0, 0, 0, 0, 40, 50, 60, 200]}), 6.0)
        self.assertEqual(_goe_actual_current_amps({"acu": "6.5"}), 6.5)
        self.assertIsNone(_goe_actual_current_amps({}))
        self.assertEqual(_goe_power_w({"nrg": nrg}), 1230.0)
        self.assertIsNone(_goe_power_w({"nrg": nrg[:11]}))
        self.assertEqual(_goe_energy_kwh({"eto": 12345}), 12.345)
        self.assertIsNone(_goe_energy_kwh({"eto": "bad"}))
        self.assertEqual(_goe_status_text({"car": 2}), "charging")
        self.assertIsNone(_goe_status_text({"car": "bad"}))
        self.assertIsNone(_goe_fault_text({"err": 0, "car": 2}))
        self.assertEqual(_goe_fault_text({"err": 0, "car": 0}), "error")
        self.assertEqual(_goe_fault_text({"err": 0, "car": 5}), "error")
        self.assertEqual(_goe_fault_text({"err": 13}), "error-overtemp")
        self.assertEqual(_goe_fault_text({"err": 99}), "error-99")

    def test_rounded_current_contract(self) -> None:
        self.assertEqual(_goe_rounded_current_setting(5.5), 6)
        self.assertEqual(_goe_rounded_current_setting(6.49), 6)
        self.assertEqual(_goe_rounded_current_setting(6.5), 7)
        self.assertEqual(_goe_rounded_current_setting(32.49), 32)
        for amps in (5.49, 32.5):
            with self.subTest(amps=amps):
                with self.assertRaisesRegex(ValueError, "expected 6..32 A"):
                    _goe_rounded_current_setting(amps)

    def test_backend_uses_session_and_builds_request_kwargs_with_auth_and_headers(self) -> None:
        session = _Session()
        service = self._service(session)
        backend = GoEChargerBackend(
            service,
            self._config(
                "[Adapter]\n"
                "BaseUrl=http://goe.local\n"
                "RequestTimeoutSeconds=4\n"
                "Username=user\n"
                "Password=secret\n"
                "AuthHeaderName=X-Token\n"
                "AuthHeaderValue=abc\n"
            ),
        )

        kwargs = backend._request_kwargs("http://goe.local/api/status", params={"filter": "alw"})

        self.assertIs(backend.service, service)
        self.assertIs(backend._session, session)
        self.assertEqual(backend.config_path, backend.config_path.strip())
        self.assertEqual(kwargs["url"], "http://goe.local/api/status")
        self.assertEqual(kwargs["timeout"], 4.0)
        self.assertEqual(kwargs["params"], {"filter": "alw"})
        self.assertEqual(kwargs["auth"], ("user", "secret"))
        self.assertEqual(kwargs["headers"], {"X-Token": "abc"})
        self.assertEqual(_goe_headers(backend.settings.auth_settings), {"X-Token": "abc"})

        digest_backend = GoEChargerBackend(
            self._service(_Session()),
            self._config("[Adapter]\nBaseUrl=http://goe.local\nUsername=user\nPassword=secret\nDigestAuth=1\n"),
        )
        self.assertIsInstance(digest_backend._request_kwargs("http://goe.local")["auth"], HTTPDigestAuth)

        backend_without_session_attr = GoEChargerBackend(
            SimpleNamespace(),
            self._config("[Adapter]\nBaseUrl=http://goe.local\n"),
        )
        self.assertIsNotNone(backend_without_session_attr._session)

    def test_default_constructor_config_path_error_names_empty_path(self) -> None:
        with self.assertRaises(FileNotFoundError) as missing_config:
            GoEChargerBackend(self._service(_Session()))

        self.assertEqual(str(missing_config.exception), "template backend config not found: ")

    def test_status_payload_and_read_charger_state_contract(self) -> None:
        nrg = [230, 231, 232, 0, 64, 80, 72, 0, 0, 0, 0, 123]
        response = _Response({"data": {"alw": "true", "amp": 16, "acu": 6, "car": 2, "err": 0, "eto": 12345, "nrg": nrg, "pnp": 3}})
        session = _Session(response)
        backend = GoEChargerBackend(self._service(session), self._config("[Adapter]\nBaseUrl=http://goe.local\n"))

        state = backend.read_charger_state()

        self.assertTrue(response.raise_called)
        self.assertEqual(session.calls[0]["url"], "http://goe.local/api/status")
        self.assertEqual(session.calls[0]["params"], {"filter": "alw,amp,acu,car,err,eto,nrg,pnp"})
        self.assertIs(state.enabled, True)
        self.assertEqual(state.current_amps, 16.0)
        self.assertEqual(state.phase_selection, "P1_P2_P3")
        self.assertEqual(state.actual_current_amps, 8.0)
        self.assertEqual(state.power_w, 1230.0)
        self.assertEqual(state.energy_kwh, 12.345)
        self.assertEqual(state.status_text, "charging")
        self.assertIsNone(state.fault_text)
        self.assertEqual(backend._observed_phase_selection, "P1_P2_P3")

        fault_response = _Response({"data": {"alw": 1, "amp": 6, "car": 5, "err": 13}})
        fallback_phase_response = _Response({"data": {"alw": 1, "amp": 6, "car": 2, "err": 0}})
        fallback_backend = GoEChargerBackend(
            self._service(_Session(fault_response, fallback_phase_response)),
            self._config("[Adapter]\nBaseUrl=http://goe.local\n"),
        )
        fallback_backend._observed_phase_selection = "P1_P2"
        fault_state = fallback_backend.read_charger_state()
        self.assertEqual(fault_state.fault_text, "error-overtemp")
        fallback_state = fallback_backend.read_charger_state()
        self.assertEqual(fallback_state.phase_selection, "P1_P2")

    def test_set_value_enabled_current_and_rejection_contracts(self) -> None:
        ok_response = _Response({"frc": True})
        current_response = _Response({"amp": True})
        missing_ack_response = _Response({})
        rejected_false = _Response({"frc": False})
        rejected_text = _Response({"amp": "blocked"})
        session = _Session(ok_response, current_response, missing_ack_response, rejected_false, rejected_text)
        backend = GoEChargerBackend(self._service(session), self._config("[Adapter]\nBaseUrl=http://goe.local\n"))

        backend.set_enabled(True)
        backend.set_current(15.6)
        backend._set_value("custom", 1)
        with self.assertRaisesRegex(RuntimeError, "go-e charger rejected frc=True: False"):
            backend._set_value("frc", True)
        with self.assertRaisesRegex(RuntimeError, "go-e charger rejected amp=16: blocked"):
            backend._set_value("amp", 16)

        self.assertEqual(session.calls[0]["params"], {"frc": "2"})
        self.assertEqual(session.calls[0]["url"], "http://goe.local/api/set")
        self.assertEqual(session.calls[1]["params"], {"amp": "16"})
        self.assertEqual(session.calls[1]["url"], "http://goe.local/api/set")
        self.assertEqual(session.calls[2]["params"], {"custom": "1"})
        self.assertEqual(session.calls[2]["url"], "http://goe.local/api/set")
        self.assertTrue(ok_response.raise_called)
        self.assertTrue(current_response.raise_called)
        self.assertTrue(missing_ack_response.raise_called)

        off_backend = GoEChargerBackend(self._service(_Session(_Response({"frc": True}))), self._config("[Adapter]\nBaseUrl=http://goe.local\n"))
        off_backend.set_enabled(False)
        self.assertEqual(off_backend._session.calls[0]["params"], {"frc": "1"})

    def test_current_and_phase_write_validation(self) -> None:
        backend = GoEChargerBackend(self._service(_Session()), self._config("[Adapter]\nBaseUrl=http://goe.local\n"))

        for amps in (None, "bad", -1):
            with self.subTest(amps=amps):
                with self.assertRaisesRegex(ValueError, "Unsupported charger current"):
                    backend.set_current(amps)  # type: ignore[arg-type]
        with self.assertRaises(ValueError) as zero_current:
            backend.set_current(0)
        self.assertEqual(str(zero_current.exception), "Unsupported charger current '0'")
        with self.assertRaisesRegex(ValueError, "expected 6..32 A"):
            backend.set_current(1)

        backend.set_phase_selection("P1")
        backend.set_phase_selection(None)  # type: ignore[arg-type]
        backend._observed_phase_selection = "P1_P2"
        backend.set_phase_selection("P1_P2")
        with self.assertRaises(ValueError) as phase_error:
            backend.set_phase_selection("P1_P2_P3")
        self.assertEqual(
            str(phase_error.exception),
            "go-e charger backend does not support documented native phase switching",
        )


if __name__ == "__main__":
    unittest.main()
