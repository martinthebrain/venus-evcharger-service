# SPDX-License-Identifier: GPL-3.0-or-later
"""Config-to-snapshot scenarios for external helper energy sources."""

from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from typing import TypeGuard
from unittest.mock import MagicMock, patch

from tests.support.auto_input_helper import FakeEnergyGateway, MemoryWriter
from venus_evcharger.energy.models import (
    EnergyClusterSnapshot,
    EnergyLearningProfile,
    EnergySourceDefinition,
    EnergySourceSnapshot,
)
from venus_evcharger.inputs.helper.config_runtime import load_auto_input_helper_settings
from venus_evcharger.inputs.helper.external_contracts import (
    ExternalPollingPolicy,
    ExternalSourcePoll,
    ProjectedEnergyValue,
    PvProjectionPolicy,
)
from venus_evcharger.inputs.helper.external_sources import (
    ConfiguredEnergySources,
    _battery_payload,
    _external_source_payload,
    _forecast_cluster_payload,
    _gateway_source_payload,
    _source_payloads,
)
from venus_evcharger.inputs.helper.snapshot import SnapshotStore
from venus_evcharger.inputs.helper.sources import AutoInputSources
from venus_evcharger.inputs.helper.sources import (
    _contributing_measurement_status,
    _measurement_observed_at,
    _projected_observed_at,
    _projection_confidence_rank,
    _projection_quality,
    _projection_status_rank,
    _select_pv_projection,
    _source_scope,
    _valid_gateway_observation_time,
    empty_battery_snapshot,
    gateway_battery_snapshot,
)
from venus_evcharger.ipc.energy import MeasuredValue


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self._encoded = json.dumps(payload).encode("utf-8")
        self.headers = {"Content-Length": str(len(self._encoded))}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self.payload

    def iter_content(self, chunk_size: int) -> tuple[bytes, ...]:
        return tuple(
            self._encoded[offset : offset + chunk_size]
            for offset in range(0, len(self._encoded), chunk_size)
        )

    def close(self) -> None:
        return None


class _Session:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.urls: list[str] = []

    def get(self, *, url: str, timeout: float, **_kwargs: object) -> _Response:
        self.urls.append(f"{url}|{timeout}")
        return _Response(self.payload)


def _is_object_dict(value: object) -> TypeGuard[dict[object, object]]:
    return isinstance(value, dict)


def _is_string_object_dict(value: object) -> TypeGuard[dict[str, object]]:
    return _is_object_dict(value) and all(isinstance(key, str) for key in value)


def _is_object_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _projection_poll(snapshot: EnergySourceSnapshot) -> ExternalSourcePoll:
    return ExternalSourcePoll(
        snapshot=snapshot,
        contributing=True,
        poll_status="success",
        measurement_status="fresh",
        attempted_at=10.0,
        observed_at=9.0,
        next_poll_at=11.0,
        age_seconds=1.0,
        consecutive_failures=0,
        last_error="",
    )


class ExternalEnergySourceScenarioTests(unittest.TestCase):
    def test_http_huawei_primary_is_fused_with_victron_gateway_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connector_path = root / "huawei.ini"
            connector_path.write_text(
                """[Adapter]
BaseUrl=http://huawei.local
RequestTimeoutSeconds=0.5
[EnergyRequest]
Method=GET
Url=/energy
[EnergyResponse]
SocPath=battery.soc
UsableCapacityWhPath=battery.capacity_wh
BatteryPowerPath=battery.power_w
PvInputPowerPath=solar.pv_w
GridInteractionPath=meter.grid_w
OnlinePath=online
ConfidencePath=confidence
""",
                encoding="utf-8",
            )
            config_path = root / "config.ini"
            config_path.write_text(
                f"""[DEFAULT]
AutoInputPollIntervalMs=1000
AutoBatteryPollIntervalMs=1000
DbusGatewayMaxAgeSeconds=10
AutoEnergySources=victron,huawei
AutoEnergySource.victron.Profile=dbus-battery
AutoEnergySource.victron.UsableCapacityWh=5000
AutoEnergySource.huawei.Profile=template-http-hybrid
AutoEnergySource.huawei.ConfigPath={connector_path}
AutoPvSourcePolicy=external_preferred
AutoPvExternalSource=huawei
AutoGridFusionEnabled=1
AutoGridFusionPrimarySource=huawei
AutoGridFusionBackupSource=victron
AutoGridFusionPrimaryMaxAgeSeconds=10
AutoGridFusionBackupMaxAgeSeconds=10
AutoGridFusionFailoverSamples=1
AutoGridFusionRecoverySamples=1
AutoGridFusionFailoverHoldSeconds=0
""",
                encoding="utf-8",
            )
            settings = load_auto_input_helper_settings(
                str(config_path),
                str(root / "snapshot.json"),
                None,
                1,
                "scenario",
            )
            gateway = FakeEnergyGateway()
            gateway.measurements = {
                "pv": MeasuredValue(None, 0.0, "unknown", 0.0, ()),
                "grid": MeasuredValue(250.0, 99.0, "fresh", 1.0, ("system",)),
                "battery": MeasuredValue(60.0, 99.0, "fresh", 0.9, ("battery",)),
            }
            session = _Session(
                {
                    "battery": {"soc": 80.0, "capacity_wh": 10000.0, "power_w": -1200.0},
                    "solar": {"pv_w": 3200.0},
                    "meter": {"grid_w": -450.0},
                    "online": True,
                    "confidence": 0.8,
                }
            )
            sources = AutoInputSources(settings, gateway, connector_session=session)
            writer = MemoryWriter()
            store = SnapshotStore(settings, sources, writer, lambda: False)

            with patch("venus_evcharger.inputs.helper.sources.time.time", return_value=100.0):
                snapshot = store.collect(now=100.0)

        self.assertEqual(session.urls, ["http://huawei.local/energy|0.5"])
        self.assertEqual(
            tuple(source.source_id for source in settings.energy_sources),
            ("huawei",),
        )
        self.assertEqual(snapshot["pv_power"], 3200.0)
        self.assertEqual(snapshot["pv_captured_at"], 100.0)
        self.assertEqual(snapshot["pv_status"], "ok")
        battery_soc = snapshot["battery_soc"]
        combined_soc = snapshot["battery_combined_soc"]
        assert isinstance(battery_soc, float)
        assert isinstance(combined_soc, float)
        self.assertAlmostEqual(battery_soc, 73.33333333333333)
        self.assertAlmostEqual(combined_soc, 73.33333333333333)
        self.assertEqual(snapshot["battery_combined_usable_capacity_wh"], 15000.0)
        self.assertEqual(snapshot["battery_combined_charge_power_w"], 1200.0)
        self.assertEqual(snapshot["battery_source_count"], 2)
        self.assertEqual(snapshot["battery_online_source_count"], 2)
        raw_sources = snapshot["battery_sources"]
        assert _is_object_list(raw_sources)
        battery_sources: list[dict[str, object]] = []
        for source in raw_sources:
            assert _is_string_object_dict(source)
            battery_sources.append(source)
        self.assertEqual(
            [source["source_id"] for source in battery_sources],
            ["huawei", "victron"],
        )
        self.assertEqual(battery_sources[1]["usable_capacity_wh"], 5000.0)
        self.assertEqual(battery_sources[1]["battery_chemistry"], "lfp")
        self.assertEqual(snapshot["grid_power"], -450.0)
        self.assertEqual(snapshot["grid_selected_source_id"], "huawei")
        self.assertEqual(snapshot["grid_fusion_state"], "primary")
        learning = snapshot["battery_learning_profiles"]
        assert _is_string_object_dict(learning)
        self.assertEqual(set(learning), {"huawei"})
        huawei_learning = learning["huawei"]
        assert _is_string_object_dict(huawei_learning)
        self.assertEqual(huawei_learning["sample_count"], 1)

    def test_external_failure_stays_visible_while_gateway_soc_remains_available(self) -> None:
        source = """[DEFAULT]
AutoEnergySources=external
AutoEnergySource.external.Type=command_json
AutoEnergySource.external.ConfigPath=/missing.ini
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.ini"
            config_path.write_text(source, encoding="utf-8")
            settings = load_auto_input_helper_settings(str(config_path), None, None, 1, "failure")
            gateway = FakeEnergyGateway()
            gateway.measurements["battery"] = MeasuredValue(55.0, 100.0, "fresh", 1.0, ("battery",))
            sources = AutoInputSources(settings, gateway)
            with patch("venus_evcharger.inputs.helper.sources.time.time", return_value=100.0):
                sources.prepare_cycle()
                snapshot = sources.battery_snapshot()

        self.assertEqual(snapshot["battery_soc"], 55.0)
        self.assertEqual(snapshot["battery_source_count"], 1)
        self.assertEqual(snapshot["battery_online_source_count"], 1)
        raw_sources = snapshot["battery_sources"]
        assert _is_object_list(raw_sources)
        external_source, gateway_source = raw_sources
        assert _is_string_object_dict(external_source)
        assert _is_string_object_dict(gateway_source)
        self.assertFalse(external_source["online"])
        self.assertFalse(external_source["contributing"])
        self.assertEqual(external_source["poll_status"], "failed")
        self.assertEqual(external_source["measurement_status"], "missing")
        self.assertEqual(external_source["captured_at"], None)
        self.assertEqual(external_source["attempted_at"], 100.0)
        self.assertTrue(gateway_source["online"])


class ExternalEnergyPayloadContractTests(unittest.TestCase):
    def test_empty_gateway_battery_snapshot_has_an_exact_independent_schema(self) -> None:
        expected: dict[str, object] = {
            "battery_soc": None,
            "battery_combined_soc": None,
            "battery_combined_usable_capacity_wh": None,
            "battery_combined_charge_power_w": None,
            "battery_combined_discharge_power_w": None,
            "battery_combined_net_power_w": None,
            "battery_combined_ac_power_w": None,
            "battery_combined_pv_input_power_w": None,
            "battery_combined_grid_interaction_w": None,
            "battery_headroom_charge_w": None,
            "battery_headroom_discharge_w": None,
            "expected_near_term_export_w": None,
            "expected_near_term_import_w": None,
            "battery_discharge_balance_mode": "",
            "battery_discharge_balance_target_distribution_mode": "",
            "battery_discharge_balance_error_w": None,
            "battery_discharge_balance_max_abs_error_w": None,
            "battery_discharge_balance_total_discharge_w": None,
            "battery_discharge_balance_eligible_source_count": 0,
            "battery_discharge_balance_active_source_count": 0,
            "battery_discharge_balance_control_candidate_count": 0,
            "battery_discharge_balance_control_ready_count": 0,
            "battery_discharge_balance_supported_control_source_count": 0,
            "battery_discharge_balance_experimental_control_source_count": 0,
            "battery_average_confidence": None,
            "battery_source_count": 0,
            "battery_online_source_count": 0,
            "battery_valid_soc_source_count": 0,
            "battery_battery_source_count": 0,
            "battery_hybrid_inverter_source_count": 0,
            "battery_inverter_source_count": 0,
            "battery_sources": [],
            "battery_learning_profiles": {},
        }

        first = empty_battery_snapshot()
        second = empty_battery_snapshot()

        self.assertEqual(first, expected)
        self.assertEqual(tuple(first), tuple(expected))
        self.assertIsNot(first["battery_sources"], second["battery_sources"])
        self.assertIsNot(
            first["battery_learning_profiles"],
            second["battery_learning_profiles"],
        )

    def test_gateway_battery_snapshot_overlays_only_gateway_measurement_fields(self) -> None:
        expected = empty_battery_snapshot()
        expected.update(
            {
                "battery_soc": 61.5,
                "battery_combined_soc": 61.5,
                "battery_average_confidence": 0.75,
                "battery_source_count": 3,
                "battery_online_source_count": 3,
                "battery_valid_soc_source_count": 3,
                "battery_battery_source_count": 3,
            }
        )

        self.assertEqual(
            gateway_battery_snapshot(61.5, confidence=0.75, source_count=3),
            expected,
        )
        minimum = gateway_battery_snapshot(0.0, confidence=0.0, source_count=0)
        self.assertEqual(minimum["battery_soc"], 0.0)
        self.assertEqual(minimum["battery_average_confidence"], 0.0)
        self.assertEqual(minimum["battery_source_count"], 1)
        self.assertEqual(minimum["battery_online_source_count"], 1)
        self.assertEqual(minimum["battery_valid_soc_source_count"], 1)
        self.assertEqual(minimum["battery_battery_source_count"], 1)
        defaults = gateway_battery_snapshot(50.0)
        self.assertEqual(defaults["battery_average_confidence"], 1.0)
        self.assertEqual(defaults["battery_source_count"], 1)

    def test_projection_ranking_and_timestamp_primitives_have_closed_boundaries(self) -> None:
        fresh_low = ProjectedEnergyValue(1.0, 10.0, "fresh-low", 0.499, "fresh")
        fresh_high = ProjectedEnergyValue(2.0, 9.0, "fresh-high", 0.5, "fresh")
        stale_high = ProjectedEnergyValue(3.0, 8.0, "stale-high", 1.0, "stale")

        self.assertEqual(_projection_status_rank(fresh_low), 1)
        self.assertEqual(_projection_status_rank(stale_high), 0)
        self.assertEqual(_projection_confidence_rank(fresh_low), 0)
        self.assertEqual(_projection_confidence_rank(fresh_high), 1)
        self.assertEqual(_projection_quality(fresh_low), (1, 0))
        self.assertEqual(_projection_quality(fresh_high), (1, 1))
        self.assertEqual(_projection_quality(stale_high), (0, 1))
        self.assertIs(
            _select_pv_projection(
                fresh_low,
                stale_high,
                PvProjectionPolicy("external_preferred"),
            ),
            fresh_low,
        )
        self.assertIs(
            _select_pv_projection(
                fresh_low,
                fresh_high,
                PvProjectionPolicy("gateway_preferred"),
            ),
            fresh_high,
        )

        for status, expected in (
            ("fresh", True),
            ("stale", True),
            ("unknown", False),
            ("expired", False),
            ("", False),
        ):
            with self.subTest(status=status):
                self.assertIs(_contributing_measurement_status(status), expected)
        for observed_at, current, expected in (
            (0.0, 10.0, False),
            (0.001, 10.0, True),
            (10.0, 10.0, True),
            (10.001, 10.0, False),
            (float("nan"), 10.0, False),
            (float("inf"), 10.0, False),
        ):
            with self.subTest(observed_at=observed_at):
                self.assertIs(
                    _valid_gateway_observation_time(observed_at, current),
                    expected,
                )
        self.assertIsNone(_projected_observed_at(None))
        self.assertEqual(_projected_observed_at(fresh_high), 9.0)
        self.assertIsNone(_measurement_observed_at(None))
        self.assertIsNone(
            _measurement_observed_at(MeasuredValue(1.0, 0.0, "unknown", 1.0))
        )
        self.assertEqual(
            _measurement_observed_at(MeasuredValue(1.0, 0.001, "fresh", 1.0)),
            0.001,
        )
        self.assertEqual(_source_scope("grid"), "grid")
        self.assertEqual(_source_scope("pv"), "all")
        self.assertEqual(_source_scope("battery"), "all")
        self.assertEqual(_source_scope("unknown"), "all")

    def test_cycle_orchestration_reuses_one_poll_and_one_projection_chain(self) -> None:
        definition = EnergySourceDefinition("external", "battery", "command_json", "/source.ini")
        external = EnergySourceSnapshot(
            "external", "battery", "external-service", soc=44.0, online=True, captured_at=19.0
        )
        gateway_source = EnergySourceSnapshot(
            "victron", "battery", "gateway-service", soc=55.0, online=True, captured_at=18.0
        )
        poll = _projection_poll(external)
        gateway_value = MeasuredValue(55.0, 18.0, "fresh", 1.0)
        cluster = EnergyClusterSnapshot(combined_soc=50.0)
        profiles = {"external": EnergyLearningProfile("external", sample_count=1)}
        summary, balance, control, forecast = {"summary": 1}, {"balance": 2}, {"control": 3}, {"forecast": 4}
        source_payloads, battery = [{"source_id": "external"}], {"battery_soc": 50.0}
        pv = ProjectedEnergyValue(100.0, 19.0, "external", 0.8)
        aggregate = MagicMock(return_value=cluster)
        gateway_projection = MagicMock(return_value=gateway_source)
        learning = MagicMock(return_value=profiles)
        learning_summary = MagicMock(return_value=summary)
        balance_metrics = MagicMock(return_value=balance)
        control_metrics = MagicMock(return_value=control)
        energy_forecast = MagicMock(return_value=forecast)
        selected_soc = MagicMock(return_value=(50.0, 18.0))
        payload_projection = MagicMock(return_value=source_payloads)
        battery_projection = MagicMock(return_value=battery)
        pv_projection = MagicMock(return_value=pv)
        scheduler = MagicMock()
        scheduler.poll.return_value = (poll,)

        with patch("venus_evcharger.inputs.helper.external_sources.ExternalSourceScheduler", return_value=scheduler), patch.multiple(
            "venus_evcharger.inputs.helper.external_sources",
            aggregate_energy_sources=aggregate,
            _gateway_battery_source=gateway_projection,
            update_energy_learning_profiles=learning,
            summarize_energy_learning_profiles=learning_summary,
            derive_discharge_balance_metrics=balance_metrics,
            derive_discharge_control_metrics=control_metrics,
            derive_energy_forecast=energy_forecast,
            _selected_soc=selected_soc,
            _source_payloads=payload_projection,
            _battery_payload=battery_projection,
        ):
            with patch(
                "venus_evcharger.inputs.helper.external_sources.external_pv_projection",
                pv_projection,
            ):
                sources = ConfiguredEnergySources(
                    (definition,),
                    use_combined_soc=False,
                    request_timeout_seconds=1.0,
                    polling_policy=ExternalPollingPolicy(),
                    pv_policy=PvProjectionPolicy("external_only", "external"),
                )
                cycle = sources.collect_cycle(gateway_value, 20.0)

        scheduler.poll.assert_called_once_with(20.0)
        gateway_projection.assert_called_once_with(gateway_value, "victron", None)
        aggregate.assert_called_once_with((external, gateway_source))
        learning.assert_called_once_with({}, (external,), 20.0)
        learning_summary.assert_called_once_with(profiles)
        balance_metrics.assert_called_once_with((external,), profiles)
        control_metrics.assert_called_once_with((external,), {"external": definition})
        energy_forecast.assert_called_once_with(_forecast_cluster_payload(cluster), summary)
        selected_soc.assert_called_once_with(cluster, (external,), gateway_source, False)
        payload_projection.assert_called_once_with((poll,), gateway_source, balance, control)
        battery_projection.assert_called_once_with(
            50.0, cluster.as_dict(), forecast, balance, control, source_payloads, profiles
        )
        pv_projection.assert_called_once_with((poll,), "external")
        self.assertEqual((cycle.battery, cycle.pv, cycle.battery_observed_at, cycle.polls), (battery, pv, 18.0, (poll,)))

    def test_forecast_projection_has_an_exact_transport_neutral_schema(self) -> None:
        cluster = EnergyClusterSnapshot(
            combined_soc=1.0,
            combined_charge_power_w=2.0,
            combined_discharge_power_w=3.0,
            combined_charge_limit_power_w=4.0,
            combined_discharge_limit_power_w=5.0,
            combined_grid_interaction_w=6.0,
        )

        self.assertEqual(
            _forecast_cluster_payload(cluster),
            {
                "battery_combined_soc": 1.0,
                "battery_combined_charge_power_w": 2.0,
                "battery_combined_discharge_power_w": 3.0,
                "battery_combined_charge_limit_power_w": 4.0,
                "battery_combined_discharge_limit_power_w": 5.0,
                "battery_combined_grid_interaction_w": 6.0,
            },
        )

    def test_source_payloads_preserve_order_diagnostics_and_metric_precedence(self) -> None:
        external = EnergySourceSnapshot(
            source_id="external",
            role="battery",
            service_name="external-service",
            soc=44.0,
            online=True,
            confidence=0.7,
            captured_at=9.0,
        )
        gateway = EnergySourceSnapshot(
            source_id="gateway",
            role="battery",
            service_name="gateway-service",
            soc=55.0,
            online=True,
            confidence=0.8,
            captured_at=8.0,
        )
        poll = _projection_poll(external)
        external_expected = poll.payload()
        external_expected.update({"shared": "control", "balance": 1, "control": 2})
        gateway_expected = dict(gateway.as_dict())
        gateway_expected.update(
            {
                "contributing": True,
                "poll_status": "semantic_gateway",
                "measurement_status": "fresh",
                "attempted_at": None,
                "observed_at": 8.0,
                "next_poll_at": 0.0,
                "age_seconds": None,
                "consecutive_failures": 0,
                "last_error": "",
            }
        )

        self.assertEqual(
            _external_source_payload(
                poll,
                {"external": {"shared": "balance", "balance": 1}},
                {"external": {"shared": "control", "control": 2}},
            ),
            external_expected,
        )
        self.assertEqual(_gateway_source_payload(gateway), gateway_expected)
        self.assertEqual(
            _source_payloads(
                (poll,),
                gateway,
                {"sources": {"external": {"shared": "balance", "balance": 1}}},
                {"sources": {"external": {"shared": "control", "control": 2}}},
            ),
            [external_expected, gateway_expected],
        )

    def test_battery_payload_has_exact_values_and_defaults(self) -> None:
        cluster = {
            "combined_soc": 1.0,
            "combined_usable_capacity_wh": 2.0,
            "combined_charge_power_w": 3.0,
            "combined_discharge_power_w": 4.0,
            "combined_net_battery_power_w": 5.0,
            "combined_ac_power_w": 6.0,
            "combined_pv_input_power_w": 7.0,
            "combined_grid_interaction_w": 8.0,
            "average_confidence": 9.0,
            "source_count": 10,
            "online_source_count": 11,
            "valid_soc_source_count": 12,
            "battery_source_count": 13,
            "hybrid_inverter_source_count": 14,
            "inverter_source_count": 15,
        }
        forecast = {
            "battery_headroom_charge_w": 16.0,
            "battery_headroom_discharge_w": 17.0,
            "expected_near_term_export_w": 18.0,
            "expected_near_term_import_w": 19.0,
        }
        balance = {
            "mode": "balanced",
            "target_distribution_mode": "capacity",
            "error_w": 20.0,
            "max_abs_error_w": 21.0,
            "total_discharge_w": 22.0,
            "eligible_source_count": 23,
            "active_source_count": 24,
        }
        control = {
            "control_candidate_count": 25,
            "control_ready_count": 26,
            "supported_control_source_count": 27,
            "experimental_control_source_count": 28,
        }
        sources: list[dict[str, object]] = [{"source_id": "external"}]
        profile = EnergyLearningProfile(source_id="external", sample_count=29)
        expected = {
            "battery_soc": 99.0,
            "battery_combined_soc": 1.0,
            "battery_combined_usable_capacity_wh": 2.0,
            "battery_combined_charge_power_w": 3.0,
            "battery_combined_discharge_power_w": 4.0,
            "battery_combined_net_power_w": 5.0,
            "battery_combined_ac_power_w": 6.0,
            "battery_combined_pv_input_power_w": 7.0,
            "battery_combined_grid_interaction_w": 8.0,
            "battery_headroom_charge_w": 16.0,
            "battery_headroom_discharge_w": 17.0,
            "expected_near_term_export_w": 18.0,
            "expected_near_term_import_w": 19.0,
            "battery_discharge_balance_mode": "balanced",
            "battery_discharge_balance_target_distribution_mode": "capacity",
            "battery_discharge_balance_error_w": 20.0,
            "battery_discharge_balance_max_abs_error_w": 21.0,
            "battery_discharge_balance_total_discharge_w": 22.0,
            "battery_discharge_balance_eligible_source_count": 23,
            "battery_discharge_balance_active_source_count": 24,
            "battery_discharge_balance_control_candidate_count": 25,
            "battery_discharge_balance_control_ready_count": 26,
            "battery_discharge_balance_supported_control_source_count": 27,
            "battery_discharge_balance_experimental_control_source_count": 28,
            "battery_average_confidence": 9.0,
            "battery_source_count": 10,
            "battery_online_source_count": 11,
            "battery_valid_soc_source_count": 12,
            "battery_battery_source_count": 13,
            "battery_hybrid_inverter_source_count": 14,
            "battery_inverter_source_count": 15,
            "battery_sources": sources,
            "battery_learning_profiles": {"external": profile.as_dict()},
        }

        self.assertEqual(
            _battery_payload(99.0, cluster, forecast, balance, control, sources, {"external": profile}),
            expected,
        )
        default_payload = _battery_payload(None, {}, {}, {}, {}, [], {})
        none_fields = tuple(key for key, value in expected.items() if isinstance(value, (float, str)))
        zero_fields = tuple(key for key, value in expected.items() if isinstance(value, int))
        expected_defaults = dict.fromkeys(none_fields, None)
        expected_defaults.update(dict.fromkeys(zero_fields, 0))
        expected_defaults.update(
            {
                "battery_soc": None,
                "battery_sources": [],
                "battery_learning_profiles": {},
            }
        )
        self.assertEqual(default_payload, expected_defaults)


if __name__ == "__main__":
    unittest.main()
