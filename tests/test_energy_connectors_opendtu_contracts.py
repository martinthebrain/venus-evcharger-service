# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact contracts for the OpenDTU energy connector."""

from __future__ import annotations

import unittest
from configparser import ConfigParser
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.backend.template_support import TemplateAuthSettings
from venus_evcharger.energy import connectors_opendtu as opendtu
from venus_evcharger.energy import connectors_opendtu_payload as payloads
from venus_evcharger.energy.models import EnergySourceDefinition, EnergySourceSnapshot

_AUTH = TemplateAuthSettings("user", "password", False, None, None)


def _settings(**overrides: object) -> opendtu.OpenDtuEnergySourceSettings:
    baseline = opendtu.OpenDtuEnergySourceSettings(
        base_url="http://opendtu.local",
        auth_settings=_AUTH,
        timeout_seconds=2.5,
        status_url="http://opendtu.local/status",
        inverter_status_url="http://opendtu.local/inverter?serial=${serial}",
        serial_filter=("A", "B"),
        max_data_age_seconds=600.0,
    )
    return replace(baseline, **overrides)


def _inverter(
    serial: object = "A",
    *,
    reachable: bool = True,
    producing: bool = True,
    data_age: object = 5.0,
    ac_power: object = 100.0,
    dc_powers: tuple[object, ...] = (60.0, 40.0),
) -> dict[str, object]:
    return {
        "serial": serial,
        "reachable": reachable,
        "producing": producing,
        "data_age": data_age,
        "AC": {"0": {"Power": {"v": ac_power}}},
        "DC": {str(index): {"Power": {"v": value}} for index, value in enumerate(dc_powers)},
    }


class _TimeoutRuntime:
    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        self.requests: list[float] = []

    def bounded_request_timeout_seconds(self, configured_seconds: float) -> float:
        self.requests.append(configured_seconds)
        return self.timeout_seconds


class EnergyConnectorsOpenDtuContractTests(unittest.TestCase):
    def test_http_boundary_forwards_runtime_settings_method_and_url(self) -> None:
        runtime = object()
        settings = _settings()
        client = object()
        with patch.object(opendtu, "TemplateHttpBackendBase", return_value=client) as client_type:
            self.assertIs(opendtu._opendtu_snapshot_client(runtime, settings), client)
        client_type.assert_called_once_with(runtime, 2.5, auth_settings=_AUTH)

        request_client = MagicMock()
        request_client._perform_request.return_value = {"inverters": []}
        self.assertEqual(opendtu._opendtu_snapshot_payload(request_client, settings), {"inverters": []})
        request_client._perform_request.assert_called_once_with("GET", "http://opendtu.local/status")

    def test_confidence_and_online_filter_boundaries_are_exact(self) -> None:
        fresh = _inverter("fresh", data_age=600.0)
        stale = _inverter("stale", data_age=600.1)
        unreachable = _inverter("offline", reachable=False)
        self.assertEqual(payloads.opendtu_online_inverters((fresh, stale, unreachable), 600.0), (fresh,))
        self.assertEqual(payloads.opendtu_snapshot_confidence((), 600.0, False), (False, 0.0))
        self.assertEqual(
            payloads.opendtu_snapshot_confidence((fresh, stale, unreachable), 600.0, False),
            (True, 1.0 / 3.0),
        )
        self.assertEqual(payloads.opendtu_snapshot_confidence((fresh,), 600.0, False), (True, 1.0))
        self.assertEqual(
            payloads.opendtu_snapshot_confidence((stale, unreachable), 600.0, False),
            (False, 0.0),
        )
        self.assertEqual(
            payloads.opendtu_snapshot_confidence((stale, unreachable), 600.0, True),
            (True, 1.0),
        )

    def test_completed_snapshot_aggregates_only_online_fresh_inverters(self) -> None:
        source = EnergySourceDefinition(
            source_id="pv",
            role="inverter",
            physical_id="roof-array",
        )
        settings = _settings()
        payload = {"payload": True}
        fresh = _inverter("A", ac_power=123.0, dc_powers=(80.0, 65.0))
        stale = _inverter("B", data_age=601.0, ac_power=900.0, dc_powers=(800.0,))
        inverters = (fresh, stale)
        with (
            patch.object(opendtu, "_energy_source_allows_unreachable_idle", return_value=True) as allows_idle,
            patch.object(opendtu, "_opendtu_plausible_idle_snapshot", return_value=False) as plausible_idle,
            patch.object(opendtu, "_opendtu_snapshot_confidence", return_value=(True, 0.75)) as confidence,
        ):
            snapshot = opendtu._opendtu_completed_snapshot(
                source,
                settings,
                payload,
                inverters,
                99.5,
            )

        self.assertEqual(
            snapshot,
            EnergySourceSnapshot(
                source_id="pv",
                role="inverter",
                service_name="http://opendtu.local",
                physical_id="roof-array",
                ac_power_w=123.0,
                pv_input_power_w=145.0,
                operating_mode="producing",
                online=True,
                confidence=0.75,
                captured_at=99.5,
            ),
        )
        allows_idle.assert_called_once_with(source)
        plausible_idle.assert_called_once_with(
            payload,
            inverters,
            ac_power_w=123.0,
            pv_input_power_w=145.0,
            max_data_age_seconds=600.0,
            allow_unreachable_idle=True,
        )
        confidence.assert_called_once_with(inverters, 600.0, False)

        with (
            patch.object(opendtu, "_energy_source_allows_unreachable_idle", return_value=False),
            patch.object(opendtu, "_opendtu_plausible_idle_snapshot", return_value=False),
            patch.object(opendtu, "_opendtu_snapshot_confidence", return_value=(True, 1.0)),
        ):
            idle = opendtu._opendtu_completed_snapshot(
                source,
                settings,
                payload,
                (_inverter(producing=False, ac_power=0.0, dc_powers=(0.0,)),),
                100.0,
            )
        self.assertEqual(idle.operating_mode, "idle")

    def test_timeout_age_source_name_and_settings_contracts_are_exact(self) -> None:
        self.assertEqual(opendtu._section_text({}, "missing"), "")
        self.assertEqual(opendtu._section_text({}, "missing", "fallback"), "fallback")
        self.assertEqual(opendtu._section_text({"value": "  "}, "value", "fallback"), "fallback")
        self.assertEqual(opendtu._section_text({"value": " text "}, "value", "fallback"), "text")
        runtime = SimpleNamespace(shelly_request_timeout_seconds=2.5)
        self.assertEqual(opendtu._opendtu_timeout_seconds(runtime, {}), 2.5)
        self.assertEqual(opendtu._opendtu_timeout_seconds(runtime, {"RequestTimeoutSeconds": "0.5"}), 0.5)
        for value in (None, "invalid", "0", "-1"):
            with self.subTest(timeout=value):
                self.assertEqual(opendtu._opendtu_timeout_seconds(runtime, {"RequestTimeoutSeconds": value}), 2.5)
        self.assertEqual(opendtu._opendtu_timeout_seconds(SimpleNamespace(), {}), 2.0)
        limited_runtime = _TimeoutRuntime(0.4)
        self.assertEqual(
            opendtu._opendtu_timeout_seconds(
                limited_runtime,
                {"RequestTimeoutSeconds": "3.5"},
            ),
            0.4,
        )
        self.assertEqual(limited_runtime.requests, [3.5])

        for value, expected in ((None, 600.0), ("invalid", 600.0), ("-1", 600.0), ("0", 0.0), ("0.5", 0.5)):
            with self.subTest(max_age=value):
                self.assertEqual(opendtu._opendtu_max_data_age_seconds({"MaxDataAgeSeconds": value}), expected)

        settings = _settings()
        self.assertEqual(
            opendtu._opendtu_source_name(EnergySourceDefinition(source_id="id", service_name="service"), settings),
            "service",
        )
        self.assertEqual(
            opendtu._opendtu_source_name(EnergySourceDefinition(source_id="id"), settings), "http://opendtu.local"
        )
        blank = _settings(base_url="")
        self.assertEqual(
            opendtu._opendtu_source_name(EnergySourceDefinition(source_id="id", config_path="cfg"), blank), "cfg"
        )
        self.assertEqual(opendtu._opendtu_source_name(EnergySourceDefinition(source_id="id"), blank), "id")

        parser = ConfigParser()
        parser.read_dict(
            {
                "Adapter": {"BaseUrl": " http://host/base ", "RequestTimeoutSeconds": "3.5"},
                "OpenDTU": {
                    "StatusUrl": "/status",
                    "InverterStatusUrl": "/detail?serial=${serial}",
                    "InverterSerials": " A, B ",
                    "MaxDataAgeSeconds": "45",
                },
            }
        )
        source = EnergySourceDefinition(source_id="source", config_path=" config.ini ")
        runtime = SimpleNamespace()
        with (
            patch.object(opendtu, "load_template_config", return_value=parser) as load_config,
            patch.object(opendtu, "load_template_auth_settings", return_value=_AUTH) as load_auth,
            patch.object(opendtu, "_opendtu_timeout_seconds", return_value=3.5) as timeout,
            patch.object(opendtu, "_validate_opendtu_energy_source_settings") as validate,
        ):
            loaded = opendtu._opendtu_energy_source_settings(runtime, source)
            cached = opendtu._opendtu_energy_source_settings(runtime, source)
        self.assertIs(cached, loaded)
        load_config.assert_called_once_with("config.ini")
        load_auth.assert_called_once_with(parser["Adapter"])
        timeout.assert_called_once_with(runtime, parser["Adapter"])
        self.assertEqual(
            loaded,
            opendtu.OpenDtuEnergySourceSettings(
                "http://host/base",
                _AUTH,
                3.5,
                "http://host/base/status",
                "http://host/base/detail?serial=${serial}",
                ("A", "B"),
                45.0,
            ),
        )
        validate.assert_called_once_with(source, loaded)
        self.assertEqual(
            runtime._energy_connector_runtime_state.caches,
            {"opendtu.settings": {"config.ini": loaded}},
        )

        defaults_parser = ConfigParser()
        defaults_parser.read_dict({"Adapter": {"BaseUrl": "http://defaults"}, "OpenDTU": {}})
        defaults_source = EnergySourceDefinition(source_id="defaults", config_path="defaults.ini")
        with patch.object(opendtu, "load_template_config", return_value=defaults_parser):
            defaults = opendtu._opendtu_energy_source_settings(SimpleNamespace(), defaults_source)
        self.assertEqual(defaults.base_url, "http://defaults")
        self.assertEqual(defaults.status_url, "http://defaults/api/livedata/status")
        self.assertEqual(
            defaults.inverter_status_url,
            "http://defaults/api/livedata/status?inv=${serial}",
        )
        self.assertEqual(defaults.serial_filter, ())

        with self.assertRaises(ValueError) as raised:
            opendtu._opendtu_energy_source_settings(SimpleNamespace(), EnergySourceDefinition(source_id="missing"))
        self.assertEqual(
            str(raised.exception), "Energy source 'missing' requires ConfigPath for opendtu_http connector"
        )

    def test_status_and_each_detail_are_separate_unique_inverter_steps(self) -> None:
        settings = _settings()
        client = MagicMock()
        complete = _inverter("A")
        old_shape = {"serial": "B", "reachable": True, "producing": True}
        payload: dict[str, object] = {"inverters": [complete, old_shape, "invalid"]}
        client._perform_request.side_effect = (
            payload,
            {"inverters": [_inverter("B", ac_power=222.0)]},
        )

        progress = opendtu._opendtu_start_read(client, settings)
        self.assertEqual(progress.inverters, {"A": complete})
        self.assertEqual(progress.detail_serials, ("B",))
        self.assertEqual(progress.next_detail_index, 0)
        opendtu._opendtu_continue_read(client, settings, progress)
        self.assertEqual(progress.inverters["B"], _inverter("B", ac_power=222.0))
        self.assertEqual(progress.next_detail_index, 1)
        self.assertEqual(client._perform_request.call_count, 2)
        self.assertEqual(
            client._perform_request.call_args_list[0].args,
            ("GET", "http://opendtu.local/status"),
        )
        self.assertEqual(
            client._perform_request.call_args_list[1].args,
            ("GET", "http://opendtu.local/inverter?serial=${serial}"),
        )
        self.assertEqual(
            client._perform_request.call_args_list[1].kwargs,
            {"context": {"serial": "B"}},
        )

    def test_failed_status_or_detail_step_discards_partial_progress(self) -> None:
        source = EnergySourceDefinition(
            source_id="pv",
            connector_type="opendtu_http",
            config_path="source.ini",
        )
        runtime = SimpleNamespace()
        client = MagicMock()
        client._perform_request.side_effect = TimeoutError("offline")
        with (
            patch.object(
                opendtu,
                "_opendtu_energy_source_settings",
                return_value=_settings(),
            ),
            patch.object(opendtu, "_opendtu_snapshot_client", return_value=client),
            self.assertRaisesRegex(TimeoutError, "offline"),
        ):
            opendtu._opendtu_energy_source_step(runtime, source, 10.0)
        self.assertEqual(
            runtime._energy_connector_runtime_state.caches["opendtu.progress"],
            {},
        )

    def test_energy_source_step_resumes_one_detail_and_completes(self) -> None:
        source = EnergySourceDefinition(
            source_id="pv",
            connector_type="opendtu_http",
            config_path="source.ini",
        )
        runtime = SimpleNamespace()
        client = MagicMock()
        client._perform_request.side_effect = (
            {
                "inverters": [
                    {"serial": "A", "reachable": True, "producing": True}
                ]
            },
            {"inverters": [_inverter("A")]},
        )
        with (
            patch.object(
                opendtu,
                "_opendtu_energy_source_settings",
                return_value=_settings(serial_filter=("A",)),
            ),
            patch.object(opendtu, "_opendtu_snapshot_client", return_value=client),
        ):
            pending = opendtu._opendtu_energy_source_step(
                runtime,
                source,
                10.0,
            )
            completed = opendtu._opendtu_energy_source_step(
                runtime,
                source,
                11.0,
            )

        self.assertFalse(pending.complete)
        self.assertTrue(completed.complete)
        assert completed.snapshot is not None
        self.assertEqual(completed.snapshot.captured_at, 11.0)
        self.assertEqual(completed.snapshot.ac_power_w, 100.0)
        self.assertEqual(client._perform_request.call_count, 2)
        self.assertEqual(
            runtime._energy_connector_runtime_state.caches["opendtu.progress"],
            {},
        )

    def test_completed_snapshot_normalizes_plausible_night_idle_to_zero(self) -> None:
        snapshot = opendtu._opendtu_completed_snapshot(
            EnergySourceDefinition(source_id="pv", role="inverter"),
            _settings(serial_filter=("A",)),
            {"hints": {"radio_problem": False}},
            (
                {
                    "serial": "A",
                    "reachable": False,
                    "producing": False,
                },
            ),
            12.0,
        )

        self.assertTrue(snapshot.online)
        self.assertEqual(snapshot.ac_power_w, 0.0)
        self.assertEqual(snapshot.operating_mode, "idle")

    def test_duplicate_serials_and_ambiguous_details_fail_closed(self) -> None:
        complete = _inverter("A")
        duplicate = _inverter("A", ac_power=200.0)
        ignored = _inverter("B")
        payload: dict[str, object] = {
            "inverters": [complete, duplicate, ignored, "invalid"],
        }
        self.assertEqual(
            payloads.opendtu_unique_raw_inverters(payload, ("A",)),
            (),
        )
        self.assertEqual(payloads.opendtu_filtered_raw_inverters([complete, ignored, None], ("A",)), (complete,))
        self.assertIs(payloads.opendtu_matches_serial_filter(complete, ()), True)
        self.assertIs(payloads.opendtu_matches_serial_filter(complete, ("A",)), True)
        self.assertIs(payloads.opendtu_matches_serial_filter(complete, ("B",)), False)
        self.assertIs(payloads.opendtu_matches_serial_filter({"serial": None}, ("None",)), False)
        for detail_payload in (
            {},
            {"inverters": []},
            {"inverters": ["invalid"]},
            {"inverters": [complete, duplicate]},
        ):
            with self.subTest(payload=detail_payload), self.assertRaises(ValueError):
                payloads.opendtu_detail_inverter(detail_payload, "A")
        self.assertEqual(
            payloads.opendtu_detail_inverter({"inverters": [complete]}, "A"),
            complete,
        )

        first = _inverter("A", ac_power=100.0, dc_powers=(60.0, 40.0))
        second = _inverter("B", ac_power=50.0, dc_powers=(30.0, None))
        inverters = (first, second)
        self.assertEqual(payloads.opendtu_summed_ac_power(inverters), 150.0)
        self.assertEqual(payloads.opendtu_total_dc_power(inverters), 130.0)
        self.assertIs(payloads.opendtu_any_producing(inverters), True)
        self.assertIsNone(payloads.opendtu_ac_power({}))
        self.assertIsNone(payloads.opendtu_dc_power({}))
        self.assertEqual(
            payloads.opendtu_dc_power({"DC": {"invalid": "skip", "valid": {"Power": {"v": 7.0}}}}),
            7.0,
        )
        self.assertIsNone(payloads.opendtu_metric_value({}, "Power"))

    def test_idle_online_and_profile_policy_truth_tables_are_exact(self) -> None:
        idle = ({"serial": "A", "reachable": False, "producing": False},)
        payload: dict[str, object] = {"hints": {"radio_problem": False}}
        self.assertIs(
            payloads.opendtu_plausible_idle_snapshot(
                payload,
                idle,
                ac_power_w=0.5,
                pv_input_power_w=None,
                max_data_age_seconds=600.0,
                allow_unreachable_idle=True,
            ),
            True,
        )
        false_cases = (
            (payload, idle, 0.0, 0.0, False),
            (payload, (), 0.0, 0.0, True),
            (payload, (_inverter(),), 0.0, 0.0, True),
            (payload, (_inverter(producing=False),), 0.0, 0.0, True),
            (payload, idle, 0.6, 0.0, True),
            (payload, idle, 0.0, -0.6, True),
            ({"hints": {"radio_problem": True}}, idle, 0.0, 0.0, True),
            (payload, (_inverter(reachable=True, producing=False, data_age=700.0),), 0.0, 0.0, True),
        )
        for candidate_payload, candidate_inverters, candidate_ac_power, candidate_pv_power, allow_idle in false_cases:
            with self.subTest(
                inverters=candidate_inverters,
                ac_power=candidate_ac_power,
                pv_power=candidate_pv_power,
                allow_idle=allow_idle,
            ):
                self.assertIs(
                    payloads.opendtu_plausible_idle_snapshot(
                        candidate_payload,
                        candidate_inverters,
                        ac_power_w=candidate_ac_power,
                        pv_input_power_w=candidate_pv_power,
                        max_data_age_seconds=600.0,
                        allow_unreachable_idle=allow_idle,
                    ),
                    False,
                )

        self.assertIs(payloads.opendtu_unreachable_idle_stub(idle[0]), True)
        self.assertIs(payloads.opendtu_unreachable_idle_stub(_inverter()), False)
        self.assertIs(payloads.opendtu_unreachable_idle_stub(_inverter(reachable=False, producing=True)), False)
        self.assertIs(payloads.opendtu_inverter_online(_inverter(data_age=None), 600.0), False)
        self.assertIs(payloads.opendtu_inverter_online(_inverter(data_age=-0.1), 600.0), False)
        self.assertIs(payloads.opendtu_inverter_online(_inverter(data_age=0.0), 600.0), True)
        self.assertIs(payloads.opendtu_inverter_online(_inverter(data_age=600.0), 600.0), True)
        self.assertIs(payloads.opendtu_inverter_online(_inverter(data_age=600.1), 600.0), False)
        self.assertIs(payloads.opendtu_inverter_online(_inverter(reachable=False), 600.0), False)

        self.assertIs(
            payloads.energy_source_allows_unreachable_idle(EnergySourceDefinition(source_id="pv", role="inverter")),
            True,
        )
        self.assertIs(
            payloads.energy_source_allows_unreachable_idle(
                EnergySourceDefinition(source_id="hybrid", role="hybrid-inverter")
            ),
            False,
        )
        profile = SimpleNamespace(idle_unreachable_policy="allow_plausible_idle")
        profile_source = EnergySourceDefinition(source_id="profile", profile_name="custom-profile")
        with patch.object(payloads, "resolve_energy_source_profile", return_value=profile) as resolve_profile:
            self.assertIs(payloads.energy_source_allows_unreachable_idle(profile_source), True)
        resolve_profile.assert_called_once_with("custom-profile")

    def test_settings_validation_requires_status_url(self) -> None:
        source = EnergySourceDefinition(source_id="source")
        opendtu._validate_opendtu_energy_source_settings(source, _settings())
        with self.assertRaises(ValueError) as raised:
            opendtu._validate_opendtu_energy_source_settings(source, _settings(status_url=""))
        self.assertEqual(str(raised.exception), "Energy source 'source' requires OpenDTU.StatusUrl or Adapter.BaseUrl")


if __name__ == "__main__":
    unittest.main()
