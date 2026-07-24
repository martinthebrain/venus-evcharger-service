# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact contracts for the command-json energy connector."""

from __future__ import annotations

import unittest
from configparser import ConfigParser
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.energy.connectors_command import (
    CommandJsonEnergySourceSettings,
    _build_command_json_energy_source_snapshot,
    _command_args,
    _command_confidence,
    _command_json_energy_source_settings,
    _command_online,
    _command_soc_value,
    _command_source_name,
    _command_timeout_seconds,
    _command_usable_capacity_wh,
    _validate_command_json_energy_source_settings,
)
from venus_evcharger.energy.models import EnergySourceDefinition, EnergySourceSnapshot


def _settings(**overrides: object) -> CommandJsonEnergySourceSettings:
    baseline = CommandJsonEnergySourceSettings(
        command=("helper", "--once"),
        timeout_seconds=2.5,
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


class EnergyConnectorsCommandContractTests(unittest.TestCase):
    def test_snapshot_builder_maps_every_field_exactly(self) -> None:
        source = EnergySourceDefinition(
            source_id="source",
            role="hybrid-inverter",
            connector_type="command_json",
            config_path="helper.ini",
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

        self.assertEqual(
            _build_command_json_energy_source_snapshot(source, 123.5, settings, payload),
            EnergySourceSnapshot(
                source_id="source",
                role="hybrid-inverter",
                service_name="helper",
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

        defaults = _settings(
            usable_capacity_wh_path=None,
            online_path=None,
            confidence_path=None,
            operating_mode_path=None,
        )
        snapshot = _build_command_json_energy_source_snapshot(source, 124.5, defaults, payload)
        self.assertEqual(snapshot.usable_capacity_wh, 5000.0)
        self.assertIs(snapshot.online, True)
        self.assertEqual(snapshot.confidence, 1.0)
        self.assertEqual(snapshot.operating_mode, "")

    def test_scalar_boundaries_and_source_name_fallbacks_are_exact(self) -> None:
        settings = _settings()
        for value, expected in ((-0.1, None), (0.0, 0.0), (100.0, 100.0), (100.1, None), (None, None)):
            with self.subTest(soc=value):
                self.assertEqual(_command_soc_value({"data": {"soc": value}}, settings), expected)

        source = EnergySourceDefinition(source_id="source", usable_capacity_wh=5000.0)
        for value, expected in ((None, 5000.0), (-1.0, None), (0.0, None), (0.5, 0.5)):
            with self.subTest(capacity=value):
                self.assertEqual(
                    _command_usable_capacity_wh({"data": {"capacity": value}}, settings, source),
                    expected,
                )

        self.assertIs(_command_online({"data": {"online": False}}, settings), False)
        self.assertIs(_command_online({"data": {"online": True}}, settings), True)
        self.assertIs(_command_online({}, _settings(online_path=None)), True)
        self.assertEqual(_command_confidence({"data": {"confidence": 0.25}}, settings), 0.25)
        self.assertEqual(_command_confidence({}, _settings(confidence_path=None)), 1.0)

        self.assertEqual(
            _command_source_name(EnergySourceDefinition(source_id="id", service_name="service"), settings), "service"
        )
        self.assertEqual(_command_source_name(EnergySourceDefinition(source_id="id"), settings), "helper")
        self.assertEqual(
            _command_source_name(
                EnergySourceDefinition(source_id="id", config_path="config.ini"), _settings(command=())
            ),
            "config.ini",
        )
        self.assertEqual(_command_source_name(EnergySourceDefinition(source_id="id"), _settings(command=())), "id")

    def test_timeout_and_argument_precedence_is_exact(self) -> None:
        runtime = SimpleNamespace(shelly_request_timeout_seconds=2.5)
        self.assertEqual(_command_timeout_seconds(runtime, {}, {}), 2.5)
        self.assertEqual(_command_timeout_seconds(runtime, {"RequestTimeoutSeconds": "3.5"}, {}), 3.5)
        self.assertEqual(
            _command_timeout_seconds(
                runtime,
                {"RequestTimeoutSeconds": "3.5"},
                {"TimeoutSeconds": "4.5"},
            ),
            4.5,
        )
        for value in (None, "invalid", "0", "-1"):
            with self.subTest(invalid_timeout=value):
                self.assertEqual(_command_timeout_seconds(runtime, {}, {"TimeoutSeconds": value}), 2.5)
        self.assertEqual(_command_timeout_seconds(runtime, {}, {"TimeoutSeconds": "0.5"}), 0.5)
        self.assertEqual(_command_timeout_seconds(SimpleNamespace(), {}, {}), 2.0)
        self.assertEqual(_command_timeout_seconds(SimpleNamespace(shelly_request_timeout_seconds=0), {}, {}), 2.0)
        limited_runtime = _TimeoutRuntime(0.4)
        self.assertEqual(
            _command_timeout_seconds(
                limited_runtime,
                {"RequestTimeoutSeconds": "3.5"},
                {"TimeoutSeconds": "4.5"},
            ),
            0.4,
        )
        self.assertEqual(limited_runtime.requests, [4.5])

        self.assertEqual(_command_args({}), ())
        self.assertEqual(_command_args({"Args": "  "}), ())
        self.assertEqual(
            _command_args({"Args": 'python3 "helper script.py" --once'}),
            ("python3", "helper script.py", "--once"),
        )

    def test_settings_loader_maps_every_section_and_caches_by_trimmed_path(self) -> None:
        parser = ConfigParser()
        parser.read_dict(
            {
                "Adapter": {"RequestTimeoutSeconds": "3.5"},
                "Command": {"Args": 'python3 "helper script.py" --once', "TimeoutSeconds": "4.5"},
                "Response": {
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
        runtime = SimpleNamespace(shelly_request_timeout_seconds=2.0)
        source = EnergySourceDefinition(source_id="source", config_path=" config.ini ")
        with (
            patch(
                "venus_evcharger.energy.connectors_command.load_template_config",
                return_value=parser,
            ) as load_config,
            patch(
                "venus_evcharger.energy.connectors_command._command_timeout_seconds",
                return_value=4.5,
            ) as timeout,
            patch(
                "venus_evcharger.energy.connectors_command._validate_command_json_energy_source_settings",
            ) as validate,
        ):
            loaded = _command_json_energy_source_settings(runtime, source)
            cached = _command_json_energy_source_settings(runtime, source)

        self.assertIs(cached, loaded)
        load_config.assert_called_once_with("config.ini")
        timeout.assert_called_once_with(runtime, parser["Adapter"], parser["Command"])
        validate.assert_called_once_with(source, loaded)
        self.assertEqual(
            loaded,
            CommandJsonEnergySourceSettings(
                command=("python3", "helper script.py", "--once"),
                timeout_seconds=4.5,
                soc_path="result.soc",
                usable_capacity_wh_path="result.capacity",
                battery_power_path="result.battery",
                ac_power_path="result.ac",
                pv_input_power_path="result.pv",
                grid_interaction_path="result.grid",
                operating_mode_path="result.mode",
                online_path="result.online",
                confidence_path="result.confidence",
            ),
        )
        self.assertEqual(
            runtime._energy_connector_runtime_state.caches,
            {"command_json.settings": {"config.ini": loaded}},
        )

        missing_path = EnergySourceDefinition(source_id="missing", config_path="  ")
        with self.assertRaises(ValueError) as raised:
            _command_json_energy_source_settings(SimpleNamespace(), missing_path)
        self.assertEqual(
            str(raised.exception),
            "Energy source 'missing' requires ConfigPath for command_json connector",
        )

    def test_validation_requires_command_and_one_readable_value(self) -> None:
        source = EnergySourceDefinition(source_id="source")
        _validate_command_json_energy_source_settings(source, _settings())
        with self.assertRaisesRegex(ValueError, "requires \\[Command\\] Args"):
            _validate_command_json_energy_source_settings(source, _settings(command=()))

        empty = _settings(
            soc_path=None,
            usable_capacity_wh_path=None,
            battery_power_path=None,
            ac_power_path=None,
            pv_input_power_path=None,
            grid_interaction_path=None,
        )
        with self.assertRaisesRegex(ValueError, "requires at least one Response path"):
            _validate_command_json_energy_source_settings(source, empty)
        _validate_command_json_energy_source_settings(
            EnergySourceDefinition(source_id="source", usable_capacity_wh=1.0),
            empty,
        )


if __name__ == "__main__":
    unittest.main()
