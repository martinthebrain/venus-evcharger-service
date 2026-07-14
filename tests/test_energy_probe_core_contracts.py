# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact contracts for Modbus energy probe planning helpers."""

from __future__ import annotations

import unittest
from configparser import ConfigParser
from dataclasses import replace
from unittest.mock import MagicMock, call

from venus_evcharger.backend.modbus_transport_types import ModbusTransportSettings
from venus_evcharger.energy import probe_core


def _transport(**overrides: object) -> ModbusTransportSettings:
    baseline = ModbusTransportSettings(
        transport_kind="tcp",
        unit_id=1,
        timeout_seconds=2.0,
        host="base-host",
        port=502,
        device=None,
        baudrate=9600,
        bytesize=8,
        parity="N",
        stopbits=1,
        serial_port_owner="none",
        serial_port_owner_stop_command=None,
        serial_port_owner_start_command=None,
        serial_retry_count=0,
        serial_retry_delay_seconds=0.0,
    )
    return replace(baseline, **overrides)


class EnergyProbeCoreContractTests(unittest.TestCase):
    def test_probe_service_transport_section_and_field_selection_are_exact(self) -> None:
        service = probe_core._probe_service()
        self.assertEqual(vars(service), {"shelly_request_timeout_seconds": 2.0})

        parser = ConfigParser()
        parser["DEFAULT"]["Host"] = "default-host"
        self.assertIs(probe_core._config_transport_section(parser), parser["DEFAULT"])
        parser.add_section("Transport")
        parser["Transport"]["Host"] = "transport-host"
        self.assertIs(probe_core._config_transport_section(parser), parser["Transport"])

        selected = {"section": "BatteryPowerRead"}
        field_settings = MagicMock(side_effect=(None, selected))
        self.assertIs(probe_core._probe_field(parser, field_settings), selected)
        self.assertEqual(
            field_settings.call_args_list,
            [call(parser, "SocRead"), call(parser, "BatteryPowerRead")],
        )
        with self.assertRaises(ValueError) as raised:
            probe_core._probe_field(parser, MagicMock(return_value=None))
        self.assertEqual(str(raised.exception), "Energy probe requires at least one Modbus read section")

    def test_field_settings_parse_explicit_defaults_and_missing_values(self) -> None:
        parser = ConfigParser()
        parser.read_dict(
            {
                "Explicit": {
                    "Address": " 42 ",
                    "RegisterType": " INPUT ",
                    "DataType": " INT32 ",
                    "WordOrder": " LITTLE ",
                    "Scale": " -0.5 ",
                },
                "Defaults": {"Address": "7", "Scale": "  "},
                "MissingAddress": {"Address": "  "},
            }
        )
        self.assertEqual(
            probe_core._field_settings(parser, "Explicit"),
            {
                "section": "Explicit",
                "register_type": "input",
                "address": 42,
                "data_type": "int32",
                "word_order": "little",
                "scale": -0.5,
            },
        )
        self.assertEqual(
            probe_core._field_settings(parser, "Defaults"),
            {
                "section": "Defaults",
                "register_type": "holding",
                "address": 7,
                "data_type": "uint16",
                "word_order": "big",
                "scale": 1.0,
            },
        )
        self.assertIsNone(probe_core._field_settings(parser, "MissingAddress"))
        self.assertIsNone(probe_core._field_settings(parser, "MissingSection"))
        self.assertEqual(probe_core._section_text({}, "missing"), "")
        self.assertEqual(probe_core._section_text({}, "missing", "fallback"), "fallback")
        self.assertEqual(probe_core._section_text({"value": "  "}, "value", "fallback"), "fallback")

    def test_validate_fields_preserves_order_required_flags_and_huawei_extras(self) -> None:
        parser = ConfigParser()
        transport = _transport()
        fields = {
            "SocRead": {"section": "SocRead", "address": 1},
            "AcPowerRead": {"section": "AcPowerRead", "address": 2},
        }
        field_settings = MagicMock(side_effect=lambda _parser, section: fields.get(section))
        attempt = MagicMock(side_effect=lambda _transport, field: {"ok": field["address"] == 1})
        results = probe_core._validate_fields(transport, parser, "generic", field_settings, attempt)
        self.assertEqual(
            results,
            [
                {"ok": True, "section": "SocRead", "required": True},
                {"ok": False, "section": "AcPowerRead", "required": True},
            ],
        )
        self.assertEqual(
            field_settings.call_args_list,
            [call(parser, section) for section in probe_core._FIELD_PROBE_SECTIONS],
        )
        self.assertEqual(attempt.call_args_list, [call(transport, fields["SocRead"]), call(transport, fields["AcPowerRead"])])

        huawei_attempt = MagicMock(return_value={"ok": True})
        huawei = probe_core._validate_fields(
            transport,
            parser,
            "huawei_ma_native_lan",
            MagicMock(return_value=None),
            huawei_attempt,
        )
        self.assertEqual(len(huawei), len(probe_core._HUAWEI_METER_FIELDS))
        self.assertEqual([entry["section"] for entry in huawei], [field["section"] for field in probe_core._HUAWEI_METER_FIELDS])
        self.assertTrue(all(entry["required"] is False for entry in huawei))
        self.assertEqual(
            huawei_attempt.call_args_list,
            [call(transport, field) for field in probe_core._HUAWEI_METER_FIELDS],
        )

    def test_candidate_cross_product_and_scalar_normalizers_are_exact(self) -> None:
        base = _transport()
        candidates = probe_core._probe_candidates(
            base,
            {
                "host": [" host-a ", "host-b"],
                "port_candidates": [1502, "2502", None],
                "unit_id_candidates": [2, "3"],
            },
        )
        self.assertEqual(len(candidates), 8)
        self.assertEqual(
            [(item.host, item.port, item.unit_id) for item in candidates],
            [
                (host, port, unit)
                for host in ("host-a", "host-b")
                for port in (1502, 2502)
                for unit in (2, 3)
            ],
        )
        self.assertEqual(probe_core._probe_candidates(base, {}), (base,))
        serial = _transport(transport_kind="serial_rtu", host=None, port=None)
        self.assertEqual(probe_core._probe_candidates(serial, {}), (serial,))
        serial_candidates = probe_core._probe_candidates(
            serial,
            {"unit_id_candidates": [4, 7]},
        )
        self.assertEqual([candidate.unit_id for candidate in serial_candidates], [4, 7])
        with self.assertRaises(ValueError) as raised:
            probe_core._probe_candidates(_transport(host=None), {})
        self.assertEqual(str(raised.exception), "Energy probe requires a host candidate for TCP/UDP Modbus detection")

        self.assertEqual(probe_core._text_candidates([None, " a ", "", "b"]), ["a", "b"])
        self.assertEqual(probe_core._int_candidates([None, "2", "invalid"]), [2])
        self.assertEqual(probe_core._int_candidates([]), [])
        self.assertEqual(probe_core._probe_default_ports(_transport(port=None), []), [])
        probe_core._validate_probe_host_candidates(["host"])
        with self.assertRaisesRegex(ValueError, "requires a host candidate"):
            probe_core._validate_probe_host_candidates([])
        for value, expected in ((None, None), (True, None), (False, None), ("", None), (" 2 ", 2), (2, 2), ("invalid", None)):
            with self.subTest(value=value):
                self.assertEqual(probe_core._probe_int_value(value), expected)
        for value, expected in ((None, None), ("", None), (" text ", "text"), (2, "2")):
            with self.subTest(text=value):
                self.assertEqual(probe_core._optional_probe_text(value), expected)
        self.assertEqual(probe_core._normalized_probe_text(" VALUE ", "fallback"), "value")
        self.assertEqual(probe_core._normalized_probe_text("", "fallback"), "fallback")


if __name__ == "__main__":
    unittest.main()
