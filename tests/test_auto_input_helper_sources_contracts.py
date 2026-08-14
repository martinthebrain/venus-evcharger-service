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
            "pv": MeasuredValue(1200.0, 99.0, "fresh", 0.9, ("pv-ac-a", "pv-dc-a"), observed_monotonic=99.0),
            "grid": MeasuredValue(-300.0, 98.0, "stale", 0.8, ("grid-primary",), observed_monotonic=98.0),
            "battery": MeasuredValue(72.5, 97.0, "fresh", 0.75, ("battery-a", "battery-b"), observed_monotonic=97.0),
            "battery_power": MeasuredValue(-1400.0, 96.0, "fresh", 0.9, ("battery-a",), observed_monotonic=96.0),
        }
        with patch("venus_evcharger.inputs.helper.sources.time.time", return_value=100.0), patch(
            "venus_evcharger.inputs.helper.sources.time.monotonic",
            return_value=100.0,
        ):
            self.sources.prepare_cycle()
            self.assertEqual(self.sources.pv_power(), 1200.0)
            self.assertEqual(self.sources.grid_power(), -300.0)
            battery = self.sources.battery_snapshot()
        self.assertEqual(self.gateway.input_refreshes, 1)
        self.assertEqual(self.sources.observed_at("pv"), 99.0)
        self.assertEqual(self.sources.observed_at("grid"), 98.0)
        self.assertEqual(self.sources.observed_at("battery"), 97.0)
        self.assertEqual(self.sources.observed_monotonic("pv"), 99.0)
        self.assertEqual(self.sources.observed_monotonic("grid"), 98.0)
        self.assertEqual(self.sources.observed_monotonic("battery"), 97.0)
        self.assertEqual(battery["battery_soc"], 72.5)
        self.assertEqual(battery["battery_source_count"], 1)
        self.assertEqual(battery["battery_average_confidence"], 0.75)
        self.assertEqual(battery["battery_combined_charge_power_w"], 1400.0)
        self.assertEqual(battery["battery_combined_discharge_power_w"], 0.0)
        self.assertEqual(battery["battery_combined_net_power_w"], -1400.0)
        self.assertEqual(self.gateway.requests, [])

    def test_missing_error_and_expired_values_request_only_semantic_scopes(self) -> None:
        self.gateway.measurements = {
            "pv": MeasuredValue(None, 0.0, "unknown", 0.0, observed_monotonic=0.0),
            "grid": MeasuredValue(10.0, 1.0, "stale", 0.5, observed_monotonic=1.0),
            "battery": MeasuredValue(120.0, 99.0, "fresh", 1.0, observed_monotonic=99.0),
        }
        with patch("venus_evcharger.inputs.helper.sources.time.time", return_value=100.0), patch(
            "venus_evcharger.inputs.helper.sources.time.monotonic",
            return_value=100.0,
        ):
            self.sources.prepare_cycle()
            self.assertIsNone(self.sources.pv_power())
            self.assertIsNone(self.sources.grid_power())
            battery = self.sources.battery_snapshot()
            self.assertIsNone(battery["battery_soc"])
            self.assertIsNone(battery["battery_combined_soc"])
            self.assertEqual(battery["battery_source_count"], 0)
            self.assertEqual(battery["battery_sources"], [])
        self.assertEqual([request[0] for request in self.gateway.requests], ["pv", "grid", "battery"])
        self.assertTrue(all(request[2] for request in self.gateway.requests))

    def test_gateway_only_mode_projects_power_and_handles_a_missing_battery(self) -> None:
        settings = replace(
            helper_settings(),
            gateway_energy_source=None,
            energy_sources=(),
        )
        sources = AutoInputSources(settings, self.gateway)
        self.gateway.measurements = {
            "battery": MeasuredValue(
                55.0,
                99.0,
                "fresh",
                1.0,
                ("battery",),
                observed_monotonic=99.0,
            ),
            "battery_power": MeasuredValue(
                -750.0,
                99.0,
                "fresh",
                1.0,
                ("battery",),
                observed_monotonic=99.0,
            ),
        }
        with patch("venus_evcharger.inputs.helper.sources.time.time", return_value=100.0), patch(
            "venus_evcharger.inputs.helper.sources.time.monotonic",
            return_value=100.0,
        ):
            sources.prepare_cycle()
            available = sources.battery_snapshot()
        self.assertEqual(available["battery_soc"], 55.0)
        self.assertEqual(available["battery_combined_net_power_w"], -750.0)

        self.gateway.measurements = {}
        with patch("venus_evcharger.inputs.helper.sources.time.time", return_value=101.0), patch(
            "venus_evcharger.inputs.helper.sources.time.monotonic",
            return_value=101.0,
        ):
            sources.prepare_cycle()
            missing = sources.battery_snapshot()
        self.assertEqual(missing, empty_battery_snapshot())
        self.assertEqual(self.gateway.requests[-1][0], "battery")

    def test_non_positive_gateway_capacity_metadata_is_not_projected(self) -> None:
        self.gateway.measurements = {
            "battery": MeasuredValue(
                98.0,
                99.0,
                "fresh",
                1.0,
                ("battery",),
                observed_monotonic=99.0,
            ),
            "battery_capacity_wh": MeasuredValue(
                0.0,
                99.0,
                "fresh",
                1.0,
                ("capacity-wh",),
                observed_monotonic=99.0,
            ),
            "battery_capacity_ah": MeasuredValue(
                -100.0,
                99.0,
                "fresh",
                1.0,
                ("capacity-ah",),
                observed_monotonic=99.0,
            ),
            "battery_voltage": MeasuredValue(
                0.0,
                99.0,
                "fresh",
                1.0,
                ("voltage",),
                observed_monotonic=99.0,
            ),
        }
        with patch("venus_evcharger.inputs.helper.sources.time.time", return_value=100.0), patch(
            "venus_evcharger.inputs.helper.sources.time.monotonic",
            return_value=100.0,
        ):
            self.sources.prepare_cycle()
            snapshot = self.sources.battery_snapshot()

        self.assertIsNone(snapshot["battery_combined_usable_capacity_wh"])

    def test_unprepared_and_non_data_scopes_are_safe(self) -> None:
        self.assertEqual(self.sources._cycle_monotonic, 0.0)
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
        gateway = ProjectedEnergyValue(
            100.0,
            10.0,
            "victron",
            1.0,
            observed_monotonic=10.0,
        )
        external = ProjectedEnergyValue(
            200.0,
            9.0,
            "huawei",
            0.8,
            observed_monotonic=9.0,
        )
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
            observed_monotonic=10.0,
        )
        stale_external = ProjectedEnergyValue(
            200.0,
            9.0,
            "huawei",
            1.0,
            "stale",
            observed_monotonic=9.0,
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

    def test_epoch_jump_is_ignored_but_future_monotonic_observation_is_rejected(self) -> None:
        gateway = FakeEnergyGateway()
        gateway.measurements["pv"] = MeasuredValue(
            500.0,
            101.0,
            "fresh",
            1.0,
            observed_monotonic=99.0,
        )
        sources = AutoInputSources(helper_settings(), gateway)
        with patch(
            "venus_evcharger.inputs.helper.sources.time.time",
            return_value=100.0,
        ), patch(
            "venus_evcharger.inputs.helper.sources.time.monotonic",
            return_value=100.0,
        ):
            sources.prepare_cycle()
            self.assertEqual(sources.pv_power(), 500.0)

        gateway.measurements["pv"] = replace(
            gateway.measurements["pv"],
            observed_monotonic=101.0,
        )
        with patch(
            "venus_evcharger.inputs.helper.sources.time.time",
            return_value=200.0,
        ), patch(
            "venus_evcharger.inputs.helper.sources.time.monotonic",
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
        with patch("venus_evcharger.inputs.helper.sources.time.time", return_value=100.0), patch(
            "venus_evcharger.inputs.helper.sources.time.monotonic",
            return_value=100.0,
        ):
            sources.prepare_cycle()
            self.assertIsNone(sources.pv_power())
        self.assertEqual(self.gateway.requests, [])

if __name__ == "__main__":
    unittest.main()
