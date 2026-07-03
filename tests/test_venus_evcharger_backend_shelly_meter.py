# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from venus_evcharger.backend.shelly_meter import (
    ShellyMeterBackend,
    _average_nonzero,
    _payload_float,
    _payload_value,
    _phase_values,
)


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class TestShellyWallboxBackendShellyMeter(unittest.TestCase):
    @staticmethod
    def _service(session: object) -> SimpleNamespace:
        return SimpleNamespace(
            session=session,
            host="192.168.1.10",
            username="",
            password="",
            use_digest_auth=False,
            shelly_request_timeout_seconds=2.0,
            pm_component="Switch",
            pm_id=0,
            phase="L1",
            max_current=16.0,
            _last_voltage=230.0,
        )

    def test_shelly_meter_uses_pm1_profile_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "meter.ini"
            path.write_text(
                "[Adapter]\nType=shelly_meter\nHost=192.168.1.50\nShellyProfile=pm1_meter_only\n",
                encoding="utf-8",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {
                    "apower": 920.0,
                    "current": 4.0,
                    "voltage": 230.0,
                    "aenergy": {"total": 1234.0},
                }
            )

            backend = ShellyMeterBackend(self._service(session), config_path=str(path))
            reading = backend.read_meter()

            self.assertEqual(backend.settings.profile_name, "pm1_meter_only")
            self.assertEqual(backend.settings.component, "PM1")
            self.assertEqual(reading.power_w, 920.0)
            self.assertEqual(reading.energy_kwh, 1.234)
            self.assertEqual(
                [call.kwargs["url"] for call in session.get.call_args_list],
                ["http://192.168.1.50/rpc/PM1.GetStatus?id=0"],
            )

    def test_shelly_meter_normalizes_em1_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "meter.ini"
            path.write_text(
                "[Adapter]\nType=shelly_meter\nHost=192.168.1.51\nShellyProfile=em1_meter_single_or_dual\n",
                encoding="utf-8",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {
                    "act_power": 1234.0,
                    "current": 5.4,
                    "voltage": 228.0,
                    "total_act_energy": 6789.0,
                }
            )

            backend = ShellyMeterBackend(self._service(session), config_path=str(path))
            reading = backend.read_meter()

            self.assertEqual(backend.settings.component, "EM1")
            self.assertEqual(reading.power_w, 1234.0)
            self.assertEqual(reading.current_a, 5.4)
            self.assertEqual(reading.voltage_v, 228.0)
            self.assertEqual(reading.energy_kwh, 6.789)
            self.assertEqual(
                [call.kwargs["url"] for call in session.get.call_args_list],
                ["http://192.168.1.51/rpc/EM1.GetStatus?id=0"],
            )

    def test_shelly_meter_normalizes_em_three_phase_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "meter.ini"
            path.write_text(
                "[Adapter]\nType=shelly_meter\nHost=192.168.1.52\nShellyProfile=em_3phase_profiled\n"
                "[Phase]\nMeasuredPhaseSelection=P1_P2_P3\n",
                encoding="utf-8",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {
                    "a_act_power": 1100.0,
                    "b_act_power": 1200.0,
                    "c_act_power": 1150.0,
                    "a_current": 4.8,
                    "b_current": 5.1,
                    "c_current": 5.0,
                    "a_voltage": 229.0,
                    "b_voltage": 230.0,
                    "c_voltage": 231.0,
                    "a_total_act_energy": 1000.0,
                    "b_total_act_energy": 2000.0,
                    "c_total_act_energy": 3000.0,
                }
            )

            backend = ShellyMeterBackend(self._service(session), config_path=str(path))
            reading = backend.read_meter()

            self.assertEqual(backend.settings.component, "EM")
            self.assertIsNone(reading.relay_on)
            self.assertEqual(reading.phase_selection, "P1_P2_P3")
            self.assertEqual(reading.power_w, 3450.0)
            self.assertAlmostEqual(reading.current_a or 0.0, 14.9, places=6)
            self.assertEqual(reading.voltage_v, 230.0)
            self.assertEqual(reading.energy_kwh, 6.0)
            self.assertEqual(reading.phase_powers_w, (1100.0, 1200.0, 1150.0))
            self.assertEqual(reading.phase_currents_a, (4.8, 5.1, 5.0))
            self.assertEqual(
                [call.kwargs["url"] for call in session.get.call_args_list],
                ["http://192.168.1.52/rpc/EM.GetStatus?id=0"],
            )

    def test_average_nonzero_handles_none_and_zero_only_inputs(self) -> None:
        self.assertIsNone(_average_nonzero(None))
        self.assertIsNone(_average_nonzero((0.0, 0.0, 0.0)))
        self.assertAlmostEqual(_average_nonzero((0.5, 0.0, 1.5)) or 0.0, 1.0)

    def test_payload_helpers_reject_empty_segments_and_skip_bad_values(self) -> None:
        payload = {"": 99.0, "outer": {"inner": "12.5"}, "bad": "nan"}

        self.assertIsNone(_payload_value(payload, ""))
        self.assertIsNone(_payload_value(payload, "."))
        self.assertEqual(_payload_value(payload, "outer.inner"), "12.5")
        self.assertEqual(_payload_float(payload, "missing", "bad", "outer.inner"), 12.5)

    def test_phase_values_preserve_explicit_zero_fill_for_missing_phases(self) -> None:
        self.assertEqual(_phase_values({"a_power": 2.0}, suffixes=("power",)), (2.0, 0.0, 0.0))
        self.assertEqual(_phase_values({"b_power": 3.0}, suffixes=("power",)), (0.0, 3.0, 0.0))
        self.assertEqual(_phase_values({"c_power": 4.0}, suffixes=("power",)), (0.0, 0.0, 4.0))
        self.assertIsNone(_phase_values({"total_power": 9.0}, suffixes=("power",)))

    def test_shelly_meter_rejects_switch_only_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "meter.ini"
            path.write_text(
                "[Adapter]\nType=shelly_meter\nHost=192.168.1.53\nShellyProfile=switch_1ch\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "not valid for meter backends"):
                ShellyMeterBackend(self._service(MagicMock()), config_path=str(path))

    def test_shelly_meter_reads_relay_output_when_status_exposes_it(self) -> None:
        session = MagicMock()
        session.get.return_value = _FakeResponse(
            {
                "output": False,
                "total_act_power": 750.0,
                "total_current": 3.25,
                "total_energy": 2500.0,
            }
        )
        service = self._service(session)
        service.phase = "L2"

        reading = ShellyMeterBackend(service).read_meter()

        self.assertIs(reading.relay_on, False)
        self.assertEqual(reading.power_w, 750.0)
        self.assertEqual(reading.current_a, 3.25)
        self.assertEqual(reading.energy_kwh, 2.5)
        self.assertEqual(reading.phase_powers_w, (0.0, 750.0, 0.0))
        self.assertEqual(reading.phase_currents_a, (0.0, 3.25, 0.0))

    def test_shelly_meter_uses_legacy_power_and_energy_fallback_paths(self) -> None:
        session = MagicMock()
        session.get.return_value = _FakeResponse(
            {
                "output": True,
                "power": 640.0,
                "current": 2.8,
                "total_energy": 12345.0,
            }
        )

        reading = ShellyMeterBackend(self._service(session)).read_meter()

        self.assertIs(reading.relay_on, True)
        self.assertEqual(reading.power_w, 640.0)
        self.assertEqual(reading.current_a, 2.8)
        self.assertEqual(reading.energy_kwh, 12.345)

    def test_shelly_meter_uses_phase_fallbacks_for_meter_families(self) -> None:
        session = MagicMock()
        session.get.return_value = _FakeResponse(
            {
                "a_apower": 100.0,
                "b_power": 200.0,
                "c_act_power": 300.0,
                "a_total_energy": 1000.0,
                "b_total_act_energy": 2000.0,
                "c_total_energy": 3000.0,
            }
        )

        reading = ShellyMeterBackend(self._service(session)).read_meter()

        self.assertEqual(reading.power_w, 600.0)
        self.assertIsNone(reading.current_a)
        self.assertEqual(reading.energy_kwh, 6.0)
        self.assertEqual(reading.phase_powers_w, (100.0, 200.0, 300.0))
        self.assertIsNone(reading.phase_currents_a)

    def test_shelly_meter_uses_configured_two_phase_projection_for_totals(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "meter.ini"
            path.write_text(
                "[Adapter]\nType=shelly_meter\nHost=192.168.1.54\nShellyProfile=pm1_meter_only\n"
                "[Phase]\nMeasuredPhaseSelection=P1_P2\n",
                encoding="utf-8",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse({"apower": 900.0, "current": 6.0})

            reading = ShellyMeterBackend(self._service(session), config_path=str(path)).read_meter()

            self.assertEqual(reading.phase_selection, "P1_P2")
            self.assertEqual(reading.phase_powers_w, (450.0, 450.0, 0.0))
            self.assertEqual(reading.phase_currents_a, (3.0, 3.0, 0.0))

    def test_shelly_meter_defaults_missing_totals_to_zero_on_default_line(self) -> None:
        session = MagicMock()
        session.get.return_value = _FakeResponse({"current": 0.0})
        service = self._service(session)
        delattr(service, "phase")

        reading = ShellyMeterBackend(service).read_meter()

        self.assertEqual(reading.power_w, 0.0)
        self.assertEqual(reading.phase_powers_w, (0.0, 0.0, 0.0))
        self.assertEqual(reading.phase_currents_a, (0.0, 0.0, 0.0))

    def test_shelly_meter_normalizes_single_phase_line_for_derived_vectors(self) -> None:
        session = MagicMock()
        backend = ShellyMeterBackend(self._service(session))

        backend.service.phase = "l1"
        self.assertEqual(backend._single_phase_line(), "L1")

        backend.service.phase = " L2 "
        self.assertEqual(backend._single_phase_line(), "L2")

        backend.service.phase = "L3"
        self.assertEqual(backend._single_phase_line(), "L3")

        backend.service.phase = "bad"
        self.assertEqual(backend._single_phase_line(), "L1")

        delattr(backend.service, "phase")
        self.assertEqual(backend._single_phase_line(), "L1")
