# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact workflow contracts for the public energy probe facade."""

from __future__ import annotations

import io
import unittest
from dataclasses import replace
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from venus_evcharger.backend.modbus_transport_types import ModbusTransportSettings
from venus_evcharger.energy import probe


def _transport(**overrides: object) -> ModbusTransportSettings:
    baseline = ModbusTransportSettings(
        transport_kind="tcp",
        unit_id=1,
        timeout_seconds=2.0,
        host="192.0.2.1",
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


_FIELD: dict[str, object] = {
    "section": "SocRead",
    "register_type": "holding",
    "address": 10,
    "data_type": "uint16",
    "word_order": "big",
    "scale": 0.1,
}


class EnergyProbeFacadeContractTests(unittest.TestCase):
    def test_detect_pipeline_forwards_every_input_and_builds_exact_payload(self) -> None:
        parser = object()
        transport_section: dict[str, str] = {}
        plan = {"host": "192.0.2.5", "port_candidates": [502], "unit_id_candidates": [1]}
        base_transport = _transport()
        attempts = [{"ok": True, "host": "192.0.2.5"}]
        detected = {"ok": True, "host": "192.0.2.5"}
        details = {"platform": "MA"}
        with (
            patch.object(probe, "load_template_config", return_value=parser) as load_config,
            patch.object(probe, "_config_transport_section", return_value=transport_section) as transport,
            patch.object(probe, "_probe_plan", return_value=plan) as probe_plan,
            patch.object(probe, "load_modbus_transport_settings", return_value=base_transport) as load_transport,
            patch.object(probe, "_probe_field", return_value=_FIELD) as probe_field,
            patch.object(probe, "_probe_attempts", return_value=(attempts, detected)) as probe_attempts,
            patch.object(probe, "energy_source_profile_details", return_value=details) as profile_details,
        ):
            payload = probe.detect_modbus_energy_source(
                "config.ini",
                profile_name=" PROFILE ",
                host="override",
                port=1502,
                unit_id=7,
            )

        self.assertEqual(transport_section, {"Host": "192.0.2.5"})
        load_config.assert_called_once_with("config.ini")
        transport.assert_called_once_with(parser)
        probe_plan.assert_called_once_with(" PROFILE ", transport_section, host="override", port=1502, unit_id=7)
        load_transport.assert_called_once()
        self.assertIs(load_transport.call_args.args[0], parser)
        self.assertEqual(type(load_transport.call_args.args[1]), SimpleNamespace)
        probe_field.assert_called_once_with(parser, probe._field_settings)
        probe_attempts.assert_called_once_with(base_transport, plan, _FIELD)
        profile_details.assert_called_once_with(" PROFILE ")
        self.assertEqual(
            payload,
            {
                "config_path": "config.ini",
                "profile_name": "profile",
                "profile_details": details,
                "probe_plan": plan,
                "probe_field": _FIELD,
                "detected": detected,
                "attempts": attempts,
            },
        )

        no_host_transport: dict[str, str] = {}
        with (
            patch.object(probe, "load_template_config", return_value=parser),
            patch.object(probe, "_config_transport_section", return_value=no_host_transport),
            patch.object(probe, "_probe_plan", return_value={}),
            patch.object(probe, "load_modbus_transport_settings", return_value=base_transport),
            patch.object(probe, "_probe_field", return_value=_FIELD),
            patch.object(probe, "_probe_attempts", return_value=([], None)),
            patch.object(probe, "energy_source_profile_details", return_value={}),
        ):
            probe.detect_modbus_energy_source("config.ini")
        self.assertNotIn("Host", no_host_transport)

    def test_huawei_validation_handles_detection_failure_and_success_exactly(self) -> None:
        failed_detection: dict[str, object] = {"detected": None, "attempts": []}
        failed_recommendation = {"status": "incomplete"}
        with (
            patch.object(probe, "_huawei_detection", return_value=failed_detection) as detect,
            patch.object(probe, "_huawei_recommendation", return_value=failed_recommendation) as recommendation,
        ):
            failed = probe.validate_huawei_energy_source(
                "config.ini",
                profile_name=" PROFILE ",
                host="host",
                port=502,
                unit_id=1,
                source_id="source",
            )
        detect.assert_called_once_with("config.ini", "profile", host="host", port=502, unit_id=1)
        recommendation.assert_called_once_with(
            "profile",
            detection=failed_detection,
            required_fields_ok=False,
            meter_block_detected=False,
            source_id="source",
        )
        self.assertEqual(
            failed,
            {
                **failed_detection,
                "validation_ok": False,
                "field_results": [],
                "required_fields_ok": False,
                "meter_block_detected": False,
                "recommendation": failed_recommendation,
            },
        )

        detection = {"detected": {"host": "192.0.2.8", "port": 502, "unit_id": 2}}
        candidate = _transport(host="192.0.2.8", unit_id=2)
        parser = object()
        fields = [
            {"section": "SocRead", "required": True, "ok": True},
            {"section": "HuaweiMeterActivePowerRead", "required": False, "ok": True},
        ]
        success_recommendation = {"status": "ready"}
        with (
            patch.object(probe, "_huawei_detection", return_value=detection),
            patch.object(probe, "_huawei_candidate_transport", return_value=(candidate, parser)) as candidate_transport,
            patch.object(probe, "_validate_fields", return_value=fields) as validate_fields,
            patch.object(probe, "_huawei_recommendation", return_value=success_recommendation) as recommendation,
        ):
            success = probe.validate_huawei_energy_source("config.ini", profile_name="PROFILE", source_id="source")
        candidate_transport.assert_called_once_with("config.ini", detection["detected"])
        validate_fields.assert_called_once_with(candidate, parser, "profile", probe._field_settings, probe._attempt_probe)
        recommendation.assert_called_once_with(
            "profile",
            detection=detection,
            required_fields_ok=True,
            meter_block_detected=True,
            source_id="source",
        )
        self.assertEqual(
            success,
            {
                **detection,
                "validation_ok": True,
                "required_fields_ok": True,
                "meter_block_detected": True,
                "field_results": fields,
                "recommendation": success_recommendation,
            },
        )

    def test_detected_target_and_transport_overrides_preserve_missing_values(self) -> None:
        target: dict[str, str] = {"Port": "1502", "UnitId": "7"}
        probe._apply_detected_probe_target(target, {"host": " host ", "port": "502", "unit_id": "2"})
        self.assertEqual(target, {"Host": " host ", "Port": "502", "UnitId": "2"})

        missing_target: dict[str, str] = {}
        probe._apply_detected_probe_target(missing_target, {"host": None, "port": None, "unit_id": "invalid"})
        self.assertEqual(missing_target, {"Host": ""})

        base = _transport(host="base", port=1502, unit_id=7)
        self.assertEqual(
            probe._detected_candidate_transport(base, {"host": "detected", "port": "502", "unit_id": "2"}),
            _transport(host="detected", port=502, unit_id=2),
        )
        self.assertEqual(probe._detected_candidate_transport(base, {}), base)
        for value, expected in ((None, None), (True, None), ("2", 2), (2, 2), ("invalid", None)):
            with self.subTest(value=value):
                self.assertEqual(probe._optional_detected_int(value), expected)

        with patch.object(probe, "detect_modbus_energy_source", return_value={"detected": True}) as detect:
            self.assertEqual(
                probe._huawei_detection(
                    "config.ini",
                    "profile",
                    host="host",
                    port=1502,
                    unit_id=7,
                ),
                {"detected": True},
            )
        detect.assert_called_once_with(
            "config.ini",
            profile_name="profile",
            host="host",
            port=1502,
            unit_id=7,
        )

        parser = object()
        section: dict[str, str] = {}
        detected = {"host": "detected", "port": 502, "unit_id": 2}
        with (
            patch.object(probe, "load_template_config", return_value=parser) as load_config,
            patch.object(probe, "_config_transport_section", return_value=section) as transport_section,
            patch.object(probe, "load_modbus_transport_settings", return_value=base) as load_transport,
            patch.object(probe, "_probe_service", return_value="service") as probe_service,
            patch.object(probe, "_detected_candidate_transport", return_value=_transport()) as candidate,
        ):
            candidate_result = probe._huawei_candidate_transport("config.ini", detected)
        self.assertEqual(candidate_result, (_transport(), parser))
        load_config.assert_called_once_with("config.ini")
        transport_section.assert_called_once_with(parser)
        self.assertEqual(section, {"Host": "detected", "Port": "502", "UnitId": "2"})
        probe_service.assert_called_once_with()
        load_transport.assert_called_once_with(parser, "service")
        candidate.assert_called_once_with(base, detected)

    def test_field_result_helpers_distinguish_required_and_meter_sections(self) -> None:
        self.assertIs(probe._required_huawei_fields_ok([]), True)
        self.assertIs(probe._required_huawei_fields_ok([{"required": False, "ok": False}]), True)
        self.assertIs(probe._required_huawei_fields_ok([{"required": True, "ok": True}]), True)
        self.assertIs(probe._required_huawei_fields_ok([{"required": True, "ok": False}]), False)
        self.assertIs(probe._huawei_meter_block_detected([{"section": "HuaweiMeterPower", "ok": True}]), True)
        self.assertIs(probe._huawei_meter_block_detected([{"section": "MeterStatusRead", "ok": True}]), True)
        self.assertIs(probe._huawei_meter_block_detected([{"section": "SocRead", "ok": True}]), False)
        self.assertIs(probe._huawei_meter_block_detected([{"section": "HuaweiMeterPower", "ok": False}]), False)
        self.assertIs(probe._is_huawei_meter_result({"section": "HuaweiMeterPower"}), True)
        self.assertIs(probe._is_huawei_meter_result({"section": "MeterStatusRead"}), True)
        self.assertIs(probe._is_huawei_meter_result({"section": "SocRead"}), False)
        self.assertIs(probe._is_huawei_meter_result({}), False)

    def test_probe_attempt_success_error_and_field_type_contracts_are_exact(self) -> None:
        settings = _transport()
        client = MagicMock()
        client.read_scalar.return_value = 25
        with (
            patch.object(probe, "create_modbus_transport", return_value="transport") as create_transport,
            patch.object(probe, "ModbusClient", return_value=client) as client_type,
        ):
            success = probe._attempt_probe(settings, _FIELD)
        create_transport.assert_called_once_with(settings)
        client_type.assert_called_once_with("transport", 1, 2.0)
        client.read_scalar.assert_called_once_with("holding", 10, "uint16", "big")
        self.assertEqual(
            success,
            {"host": "192.0.2.1", "port": 502, "unit_id": 1, "ok": True, "raw_value": 25, "scaled_value": 2.5},
        )

        bool_client = MagicMock()
        bool_client.read_scalar.return_value = True
        with patch.object(probe, "create_modbus_transport", return_value="transport"), patch.object(
            probe,
            "ModbusClient",
            return_value=bool_client,
        ):
            self.assertEqual(probe._attempt_probe(settings, _FIELD)["scaled_value"], 0.1)
        bool_client.read_scalar.return_value = False
        with patch.object(probe, "create_modbus_transport", return_value="transport"), patch.object(
            probe,
            "ModbusClient",
            return_value=bool_client,
        ):
            self.assertEqual(probe._attempt_probe(settings, _FIELD)["scaled_value"], 0.0)

        with patch.object(probe, "create_modbus_transport", side_effect=TimeoutError("late")), patch.object(
            probe,
            "modbus_transport_issue_reason",
            return_value="timeout",
        ):
            error = probe._attempt_probe(settings, _FIELD)
        self.assertEqual(
            error,
            {"host": "192.0.2.1", "port": 502, "unit_id": 1, "ok": False, "reason": "timeout", "detail": "late"},
        )
        with patch.object(probe, "create_modbus_transport", side_effect=ValueError("bad")), patch.object(
            probe,
            "modbus_transport_issue_reason",
            return_value=None,
        ):
            self.assertEqual(probe._attempt_probe(settings, _FIELD)["reason"], "valueerror")

        self.assertEqual(probe._probe_int_field({"value": 3}, "value"), 3)
        self.assertEqual(probe._probe_float_field({"value": 2}, "value"), 2.0)
        self.assertEqual(probe._probe_text_field({"value": "text"}, "value"), "text")
        for function, value, expected_message in (
            (probe._probe_int_field, True, "must be int"),
            (probe._probe_int_field, 2.5, "must be int"),
            (probe._probe_float_field, True, "must be float"),
            (probe._probe_float_field, "2", "must be float"),
            (probe._probe_text_field, 2, "must be str"),
        ):
            with self.subTest(function=function.__name__, value=value), self.assertRaisesRegex(TypeError, expected_message):
                function({"value": value}, "value")

    def test_probe_plan_attempts_and_cli_dispatch_are_exact(self) -> None:
        transport = {"Host": "configured", "Port": "1502", "UnitId": "7", "SlaveId": "8"}
        with patch.object(probe, "energy_source_profile_probe_plan", return_value={"plan": True}) as plan:
            self.assertEqual(
                probe._probe_plan("profile", transport, host="override", port=502, unit_id=2),
                {"plan": True},
            )
        plan.assert_called_once_with("profile", configured_host="override", configured_port=502, configured_unit_id=2)

        with patch.object(probe, "energy_source_profile_probe_plan", return_value={}) as fallback_plan:
            probe._probe_plan("profile", transport, host="", port=None, unit_id=None)
        fallback_plan.assert_called_once_with(
            "profile",
            configured_host="configured",
            configured_port="1502",
            configured_unit_id="7",
        )
        slave_transport = {"SlaveId": "8"}
        with patch.object(probe, "energy_source_profile_probe_plan", return_value={}) as slave_plan:
            probe._probe_plan("profile", slave_transport, host="", port=None, unit_id=None)
        slave_plan.assert_called_once_with(
            "profile",
            configured_host="",
            configured_port=None,
            configured_unit_id="8",
        )

        candidates = (_transport(port=1), _transport(port=2), _transport(port=3))
        with (
            patch.object(probe, "_probe_candidates", return_value=candidates),
            patch.object(
                probe,
                "_attempt_probe",
                side_effect=(
                    {"ok": False, "port": 1},
                    {"ok": True, "port": 2},
                    {"ok": True, "port": 3},
                ),
            ) as attempt,
        ):
            attempts, detected = probe._probe_attempts(_transport(), {"plan": True}, _FIELD)
        self.assertEqual(attempts, [{"ok": False, "port": 1}, {"ok": True, "port": 2}])
        self.assertEqual(detected, {"ok": True, "port": 2})
        self.assertEqual(attempt.call_args_list, [call(candidates[0], _FIELD), call(candidates[1], _FIELD)])

        args = SimpleNamespace(
            command="detect-modbus-energy",
            config_path="config.ini",
            profile="profile",
            host="host",
            port=502,
            unit_id=1,
            source_id="source",
            emit="json",
            write_recommendation_prefix="",
        )
        with patch.object(probe, "detect_modbus_energy_source", return_value={"detected": True}) as detect:
            self.assertEqual(probe._command_payload(args), {"detected": True})
        detect.assert_called_once_with("config.ini", profile_name="profile", host="host", port=502, unit_id=1)
        args.command = "validate-huawei-energy"
        with patch.object(probe, "validate_huawei_energy_source", return_value={"valid": True}) as validate:
            self.assertEqual(probe._command_payload(args), {"valid": True})
        validate.assert_called_once_with(
            "config.ini",
            profile_name="profile",
            host="host",
            port=502,
            unit_id=1,
            source_id="source",
        )

        stdout = io.StringIO()
        with (
            patch.object(probe, "_command_payload", return_value={"step": 1}) as command_payload,
            patch.object(probe, "_payload_with_written_files", return_value={"step": 2}) as write_files,
            patch.object(probe, "_render_payload", return_value="rendered") as render,
            redirect_stdout(stdout),
        ):
            self.assertEqual(probe.main(["detect-modbus-energy", "config.ini"]), 0)
        parsed_args = command_payload.call_args.args[0]
        self.assertEqual(parsed_args.profile, "")
        self.assertEqual(parsed_args.host, "")
        self.assertIsNone(parsed_args.port)
        self.assertIsNone(parsed_args.unit_id)
        self.assertEqual(parsed_args.source_id, "huawei")
        self.assertEqual(parsed_args.emit, "json")
        self.assertEqual(parsed_args.write_recommendation_prefix, "")
        write_files.assert_called_once_with(parsed_args, {"step": 1})
        render.assert_called_once_with(parsed_args, {"step": 2})
        self.assertEqual(stdout.getvalue(), "rendered\n")

        with patch.object(probe, "_command_payload", return_value={}) as explicit_payload, redirect_stdout(io.StringIO()):
            probe.main(
                [
                    "detect-modbus-energy",
                    "config.ini",
                    "--port",
                    "1502",
                    "--unit-id",
                    "7",
                    "--source-id",
                    "custom",
                    "--emit",
                    "summary",
                    "--write-recommendation-prefix",
                    "prefix",
                ]
            )
        explicit_args = explicit_payload.call_args.args[0]
        self.assertEqual(explicit_args.port, 1502)
        self.assertEqual(explicit_args.unit_id, 7)
        self.assertEqual(explicit_args.source_id, "custom")
        self.assertEqual(explicit_args.emit, "summary")
        self.assertEqual(explicit_args.write_recommendation_prefix, "prefix")

        with self.assertRaises(SystemExit):
            probe.main(["unsupported", "config.ini"])
        with self.assertRaises(SystemExit):
            probe.main(["detect-modbus-energy", "config.ini", "--emit", "unsupported"])
        help_output = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stdout(help_output):
            probe.main(["--help"])
        self.assertIn("Probe external energy-source connector configs", help_output.getvalue())

        unsupported = SimpleNamespace(command="unsupported")
        with self.assertRaises(ValueError) as raised:
            probe._command_payload(unsupported)
        self.assertEqual(str(raised.exception), "Unsupported energy probe command 'unsupported'")

        empty_args = SimpleNamespace(
            config_path="config.ini",
            profile=None,
            host=None,
            port=None,
            unit_id=None,
            source_id=None,
        )
        with patch.object(probe, "detect_modbus_energy_source", return_value={}) as empty_detect:
            probe._detect_command_payload(empty_args)
        empty_detect.assert_called_once_with("config.ini", profile_name="", host="", port=None, unit_id=None)
        with patch.object(probe, "validate_huawei_energy_source", return_value={}) as empty_validate:
            probe._validate_huawei_command_payload(empty_args)
        empty_validate.assert_called_once_with(
            "config.ini",
            profile_name="",
            host="",
            port=None,
            unit_id=None,
            source_id="huawei",
        )


if __name__ == "__main__":
    unittest.main()
