# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from dataclasses import replace
from typing import cast
from unittest.mock import patch

from tests.support.auto_input_helper import FakeEnergyGateway, helper_settings
from venus_evcharger.energy.models import EnergySourceDefinition, EnergySourceSnapshot
from venus_evcharger.energy.read_steps import EnergySourceReadStep, completed_read
from venus_evcharger.inputs.helper.external_contracts import (
    ExternalPollingPolicy,
    ProjectedEnergyValue,
    PvProjectionPolicy,
    PvSourcePolicyName,
)
from venus_evcharger.inputs.helper.sources import (
    AutoInputSources,
    _select_pv_projection,
    empty_battery_snapshot,
    gateway_battery_snapshot,
)
from venus_evcharger.ipc.energy import MeasuredValue


class AutoInputHelperSourceContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway = FakeEnergyGateway()
        self.sources = AutoInputSources(helper_settings(), self.gateway)

    def test_one_cycle_projects_all_semantic_measurements(self) -> None:
        self.gateway.measurements = {
            "pv": MeasuredValue(1200.0, 99.0, "fresh", 0.9, ("pv-ac-a", "pv-dc-a")),
            "grid": MeasuredValue(-300.0, 98.0, "stale", 0.8, ("grid-primary",)),
            "battery": MeasuredValue(72.5, 97.0, "fresh", 0.75, ("battery-a", "battery-b")),
            "battery_power": MeasuredValue(-1400.0, 96.0, "fresh", 0.9, ("battery-a",)),
        }
        with patch("venus_evcharger.inputs.helper.sources.time.time", return_value=100.0):
            self.sources.prepare_cycle()
            self.assertEqual(self.sources.pv_power(), 1200.0)
            self.assertEqual(self.sources.grid_power(), -300.0)
            battery = self.sources.battery_snapshot()
        self.assertEqual(self.gateway.input_refreshes, 1)
        self.assertEqual(self.sources.observed_at("pv"), 99.0)
        self.assertEqual(self.sources.observed_at("grid"), 98.0)
        self.assertEqual(self.sources.observed_at("battery"), 96.0)
        self.assertEqual(battery["battery_soc"], 72.5)
        self.assertEqual(battery["battery_source_count"], 2)
        self.assertEqual(battery["battery_average_confidence"], 0.75)
        self.assertEqual(battery["battery_combined_charge_power_w"], 1400.0)
        self.assertEqual(battery["battery_combined_discharge_power_w"], 0.0)
        self.assertEqual(battery["battery_combined_net_power_w"], -1400.0)
        self.assertEqual(self.gateway.requests, [])

    def test_missing_error_and_expired_values_request_only_semantic_scopes(self) -> None:
        self.gateway.measurements = {
            "pv": MeasuredValue(None, 0.0, "unknown", 0.0),
            "grid": MeasuredValue(10.0, 1.0, "stale", 0.5),
            "battery": MeasuredValue(120.0, 99.0, "fresh", 1.0),
        }
        with patch("venus_evcharger.inputs.helper.sources.time.time", return_value=100.0):
            self.sources.prepare_cycle()
            self.assertIsNone(self.sources.pv_power())
            self.assertIsNone(self.sources.grid_power())
            self.assertEqual(self.sources.battery_snapshot(), empty_battery_snapshot())
        self.assertEqual([request[0] for request in self.gateway.requests], ["pv", "grid", "battery"])
        self.assertTrue(all(request[2] for request in self.gateway.requests))

    def test_unprepared_and_non_data_scopes_are_safe(self) -> None:
        self.assertIs(self.sources._gateway_battery_power, None)
        self.assertIsNone(self.sources.pv_power())
        self.assertIsNone(self.sources.observed_at("unknown"))
        self.assertEqual(self.gateway.requests[0][0], "pv")

    def test_gateway_battery_power_signs_include_an_exact_zero_boundary(self) -> None:
        idle = gateway_battery_snapshot(50.0, net_power_w=0.0)
        discharging = gateway_battery_snapshot(50.0, net_power_w=500.0)

        self.assertEqual(idle["battery_combined_charge_power_w"], 0.0)
        self.assertEqual(idle["battery_combined_discharge_power_w"], 0.0)
        self.assertEqual(discharging["battery_combined_charge_power_w"], 0.0)
        self.assertEqual(discharging["battery_combined_discharge_power_w"], 500.0)

    def test_pv_selection_policy_has_explicit_primary_and_fallback_order(self) -> None:
        gateway = ProjectedEnergyValue(100.0, 10.0, "victron", 1.0)
        external = ProjectedEnergyValue(200.0, 9.0, "huawei", 0.8)
        cases = {
            "gateway_only": gateway,
            "gateway_preferred": gateway,
            "external_preferred": external,
            "external_only": external,
        }
        for policy_name, expected in cases.items():
            with self.subTest(policy=policy_name):
                policy = PvProjectionPolicy(name=cast(PvSourcePolicyName, policy_name))
                self.assertEqual(_select_pv_projection(gateway, external, policy), expected)
        self.assertIsNone(
            _select_pv_projection(None, None, PvProjectionPolicy(name="external_only"))
        )

    def test_pv_selection_prefers_freshness_then_confidence_before_policy_order(self) -> None:
        fresh_gateway = ProjectedEnergyValue(
            100.0,
            10.0,
            "victron",
            0.2,
            "fresh",
        )
        stale_external = ProjectedEnergyValue(
            200.0,
            9.0,
            "huawei",
            1.0,
            "stale",
        )
        self.assertIs(
            _select_pv_projection(
                fresh_gateway,
                stale_external,
                PvProjectionPolicy(name="external_preferred"),
            ),
            fresh_gateway,
        )

        high_confidence_external = replace(
            fresh_gateway,
            value=300.0,
            source_id="huawei",
            confidence=0.8,
        )
        self.assertIs(
            _select_pv_projection(
                fresh_gateway,
                high_confidence_external,
                PvProjectionPolicy(name="gateway_preferred"),
            ),
            high_confidence_external,
        )
        equal_quality_external = replace(
            fresh_gateway,
            value=400.0,
            source_id="huawei",
        )
        self.assertIs(
            _select_pv_projection(
                fresh_gateway,
                equal_quality_external,
                PvProjectionPolicy(name="gateway_preferred"),
            ),
            fresh_gateway,
        )

    def test_future_gateway_observation_requests_refresh(self) -> None:
        gateway = FakeEnergyGateway()
        gateway.measurements["pv"] = MeasuredValue(
            500.0,
            101.0,
            "fresh",
            1.0,
        )
        sources = AutoInputSources(helper_settings(), gateway)
        with patch(
            "venus_evcharger.inputs.helper.sources.time.time",
            return_value=100.0,
        ):
            sources.prepare_cycle()
            self.assertIsNone(sources.pv_power())
        self.assertEqual(gateway.requests[-1][0], "pv")

    def test_external_battery_read_before_prepare_is_empty_and_requests_gateway(self) -> None:
        definition = EnergySourceDefinition(
            source_id="external",
            role="battery",
            connector_type="command_json",
            config_path="/external.ini",
        )
        settings = replace(
            helper_settings(),
            energy_sources=(definition,),
            external_polling_policy=ExternalPollingPolicy(),
        )

        def reader(
            _runtime: object,
            _source: EnergySourceDefinition,
            _now: float,
        ) -> EnergySourceReadStep:
            raise AssertionError("unprepared source must not poll")

        sources = AutoInputSources(settings, self.gateway, energy_source_reader=reader)
        self.assertEqual(sources.battery_snapshot(), empty_battery_snapshot())
        self.assertEqual(self.gateway.requests[0][0], "battery")

    def test_external_only_missing_pv_uses_scheduler_retry_without_gateway_refresh(self) -> None:
        definition = EnergySourceDefinition(
            source_id="external",
            role="battery",
            connector_type="command_json",
            config_path="/external.ini",
        )
        settings = replace(
            helper_settings(),
            energy_sources=(definition,),
            external_polling_policy=ExternalPollingPolicy(),
            pv_projection_policy=PvProjectionPolicy(name="external_only"),
        )

        def reader(
            _runtime: object,
            _source: EnergySourceDefinition,
            now: float,
        ) -> EnergySourceReadStep:
            return completed_read(EnergySourceSnapshot(
                source_id="external",
                role="battery",
                service_name="external",
                soc=50.0,
                online=True,
                captured_at=now,
            ))

        sources = AutoInputSources(settings, self.gateway, energy_source_reader=reader)
        with patch("venus_evcharger.inputs.helper.sources.time.time", return_value=100.0):
            sources.prepare_cycle()
            self.assertIsNone(sources.pv_power())
        self.assertEqual(self.gateway.requests, [])

if __name__ == "__main__":
    unittest.main()
