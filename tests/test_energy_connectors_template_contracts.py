# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact contracts for the template HTTP energy connector."""

from __future__ import annotations

import unittest
from configparser import ConfigParser
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.backend.template_support import TemplateAuthSettings
from venus_evcharger.energy import connectors_template as template
from venus_evcharger.energy.models import EnergySourceDefinition, EnergySourceSnapshot

_AUTH = TemplateAuthSettings("user", "password", False, None, None)


def _settings(**overrides: object) -> template.TemplateHttpEnergySourceSettings:
    baseline = template.TemplateHttpEnergySourceSettings(
        base_url="http://energy.local",
        auth_settings=_AUTH,
        timeout_seconds=2.5,
        request_method="POST",
        request_url="http://energy.local/state",
        soc_path="data.soc",
        usable_capacity_wh_path="data.capacity",
        battery_power_path="data.battery",
        ac_power_path="data.ac",
        pv_input_power_path="data.pv",
        grid_interaction_path="data.grid",
        operating_mode_path="data.mode",
        online_path="data.online",
        confidence_path="data.confidence",
    )
    return replace(baseline, **overrides)


class _TimeoutRuntime:
    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        self.requests: list[float] = []

    def bounded_request_timeout_seconds(self, configured_seconds: float) -> float:
        self.requests.append(configured_seconds)
        return self.timeout_seconds


class EnergyConnectorsTemplateContractTests(unittest.TestCase):
    def test_snapshot_pipeline_maps_every_field_exactly(self) -> None:
        runtime = object()
        owner = SimpleNamespace(service=runtime)
        source = EnergySourceDefinition(
            source_id="source",
            role="hybrid-inverter",
            physical_id="battery-bank-a",
            physical_priority=7,
            usable_capacity_wh=5000.0,
        )
        settings = _settings()
        payload: dict[str, object] = {
            "data": {
                "soc": 61.5,
                "capacity": 8400.0,
                "battery": -1200.0,
                "ac": 2300.0,
                "pv": 1700.0,
                "grid": -300.0,
                "mode": " support ",
                "online": False,
                "confidence": 0.65,
            }
        }
        with (
            patch.object(template, "_template_http_energy_source_settings", return_value=settings) as load_settings,
            patch.object(template, "_template_http_payload", return_value=payload) as load_payload,
        ):
            snapshot = template._template_http_energy_source_snapshot(owner, source, 123.5)
        self.assertEqual(
            snapshot,
            EnergySourceSnapshot(
                source_id="source",
                role="hybrid-inverter",
                service_name="http://energy.local",
                physical_id="battery-bank-a",
                physical_priority=7,
                battery_chemistry="lfp",
                soc=61.5,
                usable_capacity_wh=8400.0,
                net_battery_power_w=-1200.0,
                ac_power_w=2300.0,
                pv_input_power_w=1700.0,
                grid_interaction_w=-300.0,
                operating_mode="support",
                online=False,
                confidence=0.65,
                captured_at=123.5,
            ),
        )
        load_settings.assert_called_once_with(runtime, source)
        load_payload.assert_called_once_with(runtime, settings)

        defaults = _settings(
            usable_capacity_wh_path=None,
            operating_mode_path=None,
            online_path=None,
            confidence_path=None,
        )
        with (
            patch.object(template, "_template_http_energy_source_settings", return_value=defaults),
            patch.object(template, "_template_http_payload", return_value=payload),
        ):
            default_snapshot = template._template_http_energy_source_snapshot(owner, source, 124.5)
        self.assertEqual(default_snapshot.usable_capacity_wh, 5000.0)
        self.assertEqual(default_snapshot.operating_mode, "")
        self.assertIs(default_snapshot.online, True)
        self.assertEqual(default_snapshot.confidence, 1.0)

    def test_http_boundary_and_scalar_boundaries_are_exact(self) -> None:
        runtime = object()
        settings = _settings()
        backend = MagicMock()
        backend._perform_request.return_value = {"result": True}
        with patch.object(template, "TemplateHttpBackendBase", return_value=backend) as backend_type:
            self.assertEqual(template._template_http_payload(runtime, settings), {"result": True})
        backend_type.assert_called_once_with(runtime, 2.5, auth_settings=_AUTH)
        backend._perform_request.assert_called_once_with("POST", "http://energy.local/state")

        for value, expected in ((-0.1, None), (0.0, 0.0), (100.0, 100.0), (100.1, None), (None, None)):
            with self.subTest(soc=value):
                self.assertEqual(template._template_soc_value({"data": {"soc": value}}, settings), expected)
        source = EnergySourceDefinition(source_id="source", usable_capacity_wh=5000.0)
        for value, expected in ((None, 5000.0), (-1.0, None), (0.0, None), (0.5, 0.5)):
            with self.subTest(capacity=value):
                self.assertEqual(
                    template._template_usable_capacity_wh({"data": {"capacity": value}}, settings, source),
                    expected,
                )
        self.assertIs(template._template_online({"data": {"online": False}}, settings), False)
        self.assertIs(template._template_online({"data": {"online": True}}, settings), True)
        self.assertIs(template._template_online({}, _settings(online_path=None)), True)
        self.assertEqual(template._template_confidence({"data": {"confidence": 0.25}}, settings), 0.25)
        self.assertEqual(template._template_confidence({}, _settings(confidence_path=None)), 1.0)

    def test_source_name_timeout_and_section_text_fallbacks_are_exact(self) -> None:
        settings = _settings()
        self.assertEqual(
            template._template_source_name(EnergySourceDefinition(source_id="id", service_name="service"), settings),
            "service",
        )
        self.assertEqual(
            template._template_source_name(EnergySourceDefinition(source_id="id"), settings), "http://energy.local"
        )
        blank = _settings(base_url="")
        self.assertEqual(
            template._template_source_name(EnergySourceDefinition(source_id="id", config_path="cfg"), blank), "cfg"
        )
        self.assertEqual(template._template_source_name(EnergySourceDefinition(source_id="id"), blank), "id")

        runtime = SimpleNamespace(shelly_request_timeout_seconds=2.5)
        self.assertEqual(template._template_timeout_seconds(runtime, {}), 2.5)
        self.assertEqual(template._template_timeout_seconds(runtime, {"RequestTimeoutSeconds": "0.5"}), 0.5)
        for value in (None, "invalid", "0", "-1"):
            with self.subTest(timeout=value):
                self.assertEqual(template._template_timeout_seconds(runtime, {"RequestTimeoutSeconds": value}), 2.5)
        self.assertEqual(template._template_timeout_seconds(SimpleNamespace(), {}), 2.0)
        limited_runtime = _TimeoutRuntime(0.4)
        self.assertEqual(
            template._template_timeout_seconds(
                limited_runtime,
                {"RequestTimeoutSeconds": "3.5"},
            ),
            0.4,
        )
        self.assertEqual(limited_runtime.requests, [3.5])

        self.assertEqual(template._section_text({}, "missing"), "")
        self.assertEqual(template._section_text({}, "missing", "fallback"), "fallback")
        self.assertEqual(template._section_text({"value": "  "}, "value", "fallback"), "fallback")
        self.assertEqual(template._section_text({"value": " text "}, "value", "fallback"), "text")

    def test_settings_loader_maps_every_section_and_caches(self) -> None:
        parser = ConfigParser()
        parser.read_dict(
            {
                "Adapter": {"BaseUrl": " http://host/base ", "RequestTimeoutSeconds": "3.5"},
                "EnergyRequest": {"Method": " post ", "Url": "/state"},
                "EnergyResponse": {
                    "SocPath": "result.soc",
                    "UsableCapacityWhPath": "result.capacity",
                    "BatteryPowerPath": "result.battery",
                    "AcPowerPath": "result.ac",
                    "PvInputPowerPath": "result.pv",
                    "GridInteractionPath": "result.grid",
                    "OperatingModePath": "result.mode",
                    "OnlinePath": "result.online",
                    "ConfidencePath": "result.confidence",
                },
            }
        )
        runtime = SimpleNamespace()
        source = EnergySourceDefinition(source_id="source", config_path=" config.ini ")
        with (
            patch.object(template, "load_template_config", return_value=parser) as load_config,
            patch.object(template, "load_template_auth_settings", return_value=_AUTH) as load_auth,
            patch.object(template, "_template_timeout_seconds", return_value=3.5) as timeout,
            patch.object(template, "_validate_template_http_energy_source_settings") as validate,
        ):
            loaded = template._template_http_energy_source_settings(runtime, source)
            cached = template._template_http_energy_source_settings(runtime, source)
        self.assertIs(cached, loaded)
        load_config.assert_called_once_with("config.ini")
        load_auth.assert_called_once_with(parser["Adapter"])
        timeout.assert_called_once_with(runtime, parser["Adapter"])
        self.assertEqual(
            loaded,
            template.TemplateHttpEnergySourceSettings(
                "http://host/base",
                _AUTH,
                3.5,
                "POST",
                "http://host/base/state",
                "result.soc",
                "result.capacity",
                "result.battery",
                "result.ac",
                "result.pv",
                "result.grid",
                "result.mode",
                "result.online",
                "result.confidence",
            ),
        )
        validate.assert_called_once_with(source, loaded)
        self.assertEqual(
            runtime._energy_connector_runtime_state.caches,
            {"template_http.settings": {"config.ini": loaded}},
        )

        invalid_method_parser = ConfigParser()
        invalid_method_parser.read_dict(
            {
                "Adapter": {"BaseUrl": "http://invalid-method"},
                "EnergyRequest": {"Method": "invalid", "Url": "/state"},
                "EnergyResponse": {"SocPath": "soc"},
            }
        )
        invalid_source = EnergySourceDefinition(source_id="invalid", config_path="invalid.ini")
        with patch.object(template, "load_template_config", return_value=invalid_method_parser):
            invalid_method = template._template_http_energy_source_settings(SimpleNamespace(), invalid_source)
        self.assertEqual(invalid_method.request_method, "GET")

        with self.assertRaises(ValueError) as raised:
            template._template_http_energy_source_settings(
                SimpleNamespace(), EnergySourceDefinition(source_id="missing")
            )
        self.assertEqual(
            str(raised.exception), "Energy source 'missing' requires ConfigPath for template_http connector"
        )

    def test_validation_requires_url_and_one_readable_value(self) -> None:
        source = EnergySourceDefinition(source_id="source")
        template._validate_template_http_energy_source_settings(source, _settings())
        with self.assertRaises(ValueError) as missing_url:
            template._validate_template_http_energy_source_settings(source, _settings(request_url=""))
        self.assertEqual(str(missing_url.exception), "Energy source 'source' requires [EnergyRequest] Url")
        empty = _settings(
            soc_path=None,
            usable_capacity_wh_path=None,
            battery_power_path=None,
            ac_power_path=None,
            pv_input_power_path=None,
            grid_interaction_path=None,
        )
        with self.assertRaises(ValueError) as no_response:
            template._validate_template_http_energy_source_settings(source, empty)
        self.assertEqual(
            str(no_response.exception),
            "Energy source 'source' requires at least one readable EnergyResponse path or UsableCapacityWh",
        )
        template._validate_template_http_energy_source_settings(
            EnergySourceDefinition(source_id="source", usable_capacity_wh=1.0),
            empty,
        )


if __name__ == "__main__":
    unittest.main()
