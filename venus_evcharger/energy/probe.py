# SPDX-License-Identifier: GPL-3.0-or-later
"""CLI helpers to actively probe Modbus-backed external energy-source configs."""

from __future__ import annotations

import argparse
import configparser
from dataclasses import replace
from typing import Mapping, MutableMapping, cast

from venus_evcharger.backend.modbus_client import ModbusClient
from venus_evcharger.backend.modbus_transport import create_modbus_transport
from venus_evcharger.backend.modbus_transport_config import load_modbus_transport_settings, modbus_transport_issue_reason
from venus_evcharger.backend.modbus_transport_types import ModbusTransportSettings
from venus_evcharger.backend.template_support import load_template_config
from venus_evcharger.energy.numeric import optional_int

from .probe_cli import _payload_with_written_files, _render_payload
from .probe_core import (
    _config_transport_section,
    _field_settings,
    _probe_candidates,
    _probe_field,
    _probe_service,
    _validate_fields,
)
from .probe_huawei import _huawei_recommendation
from .profiles import energy_source_profile_details, energy_source_profile_probe_plan


ENERGY_PROBE_READ_ERRORS = (OSError, RuntimeError, TypeError, ValueError)


def detect_modbus_energy_source(
    config_path: str,
    *,
    profile_name: str = "",
    host: str = "",
    port: int | None = None,
    unit_id: int | None = None,
) -> dict[str, object]:
    """Actively test one Modbus energy config against candidate endpoints."""
    parser = load_template_config(config_path)
    transport = _config_transport_section(parser)
    probe_plan = _probe_plan(
        profile_name,
        transport,
        host=host,
        port=port,
        unit_id=unit_id,
    )
    default_host = str(probe_plan.get("host", "")).strip()
    if default_host and not transport.get("Host", "").strip():
        transport["Host"] = default_host
    base_transport = load_modbus_transport_settings(parser, _probe_service())
    field = _probe_field(parser, _field_settings)
    attempts, detected = _probe_attempts(base_transport, probe_plan, field)
    return {
        "config_path": config_path,
        "profile_name": str(profile_name).strip().lower(),
        "profile_details": dict(energy_source_profile_details(profile_name)),
        "probe_plan": dict(probe_plan),
        "probe_field": dict(field),
        "detected": detected,
        "attempts": attempts,
    }


def validate_huawei_energy_source(
    config_path: str,
    *,
    profile_name: str,
    host: str = "",
    port: int | None = None,
    unit_id: int | None = None,
    source_id: str = "huawei",
) -> dict[str, object]:
    """Validate one Huawei-backed energy config against a reachable endpoint."""
    normalized_profile = str(profile_name).strip().lower()
    detection = _huawei_detection(
        config_path,
        normalized_profile,
        host=host,
        port=port,
        unit_id=unit_id,
    )
    detected = detection.get("detected")
    if not isinstance(detected, Mapping):
        return _invalid_huawei_validation_payload(
            normalized_profile,
            detection,
            source_id,
        )
    candidate_transport, parser = _huawei_candidate_transport(config_path, detected)
    field_results = _validate_fields(candidate_transport, parser, normalized_profile, _field_settings, _attempt_probe)
    required_fields_ok = _required_huawei_fields_ok(field_results)
    meter_block_detected = _huawei_meter_block_detected(field_results)
    return _huawei_validation_payload(
        normalized_profile,
        detection,
        field_results=field_results,
        required_fields_ok=required_fields_ok,
        meter_block_detected=meter_block_detected,
        source_id=source_id,
    )


def _huawei_detection(
    config_path: str,
    normalized_profile: str,
    *,
    host: str,
    port: int | None,
    unit_id: int | None,
) -> dict[str, object]:
    return detect_modbus_energy_source(
        config_path,
        profile_name=normalized_profile,
        host=host,
        port=port,
        unit_id=unit_id,
    )


def _invalid_huawei_validation_payload(
    normalized_profile: str,
    detection: Mapping[str, object],
    source_id: str,
) -> dict[str, object]:
    return {
        **detection,
        "validation_ok": False,
        "field_results": [],
        "required_fields_ok": False,
        "meter_block_detected": False,
        "recommendation": _huawei_recommendation(
            normalized_profile,
            detection=detection,
            required_fields_ok=False,
            meter_block_detected=False,
            source_id=source_id,
        ),
    }

def _huawei_candidate_transport(
    config_path: str,
    detected: Mapping[str, object],
) -> tuple[ModbusTransportSettings, configparser.ConfigParser]:
    parser = load_template_config(config_path)
    transport = _config_transport_section(parser)
    _apply_detected_probe_target(transport, detected)
    base_transport = load_modbus_transport_settings(parser, _probe_service())
    return _detected_candidate_transport(base_transport, detected), parser


def _apply_detected_probe_target(transport: MutableMapping[str, str], detected: Mapping[str, object]) -> None:
    transport["Host"] = str(detected.get("host", "") or "")
    detected_port = _optional_detected_int(detected.get("port"))
    if detected_port is not None:
        transport["Port"] = str(detected_port)
    detected_unit_id = _optional_detected_int(detected.get("unit_id"))
    if detected_unit_id is not None:
        transport["UnitId"] = str(detected_unit_id)


def _detected_candidate_transport(
    base_transport: ModbusTransportSettings,
    detected: Mapping[str, object],
) -> ModbusTransportSettings:
    detected_port = _optional_detected_int(detected.get("port"))
    detected_unit_id = _optional_detected_int(detected.get("unit_id"))
    return replace(
        base_transport,
        host=str(detected.get("host", "") or base_transport.host),
        port=detected_port if detected_port is not None else base_transport.port,
        unit_id=detected_unit_id if detected_unit_id is not None else base_transport.unit_id,
    )


def _optional_detected_int(value: object) -> int | None:
    return optional_int(value)


def _required_huawei_fields_ok(field_results: list[dict[str, object]]) -> bool:
    required_results = [result for result in field_results if bool(result.get("required"))]
    return all(bool(result.get("ok")) for result in required_results)


def _huawei_meter_block_detected(field_results: list[dict[str, object]]) -> bool:
    for result in field_results:
        if _is_huawei_meter_result(result) and bool(result.get("ok")):
            return True
    return False


def _is_huawei_meter_result(result: Mapping[str, object]) -> bool:
    section = str(result.get("section", ""))
    return section.startswith("HuaweiMeter") or section == "MeterStatusRead"


def _huawei_validation_payload(
    normalized_profile: str,
    detection: Mapping[str, object],
    *,
    field_results: list[dict[str, object]],
    required_fields_ok: bool,
    meter_block_detected: bool,
    source_id: str,
) -> dict[str, object]:
    return {
        **detection,
        "validation_ok": required_fields_ok,
        "required_fields_ok": required_fields_ok,
        "meter_block_detected": meter_block_detected,
        "field_results": field_results,
        "recommendation": _huawei_recommendation(
            normalized_profile,
            detection=detection,
            required_fields_ok=required_fields_ok,
            meter_block_detected=meter_block_detected,
            source_id=source_id,
        ),
    }


def _attempt_probe(
    transport_settings: ModbusTransportSettings,
    field: dict[str, object],
) -> dict[str, object]:
    address = cast(int, field["address"])
    scale = cast(float, field["scale"])
    register_type = cast(str, field["register_type"])
    data_type = cast(str, field["data_type"])
    word_order = cast(str, field["word_order"])
    try:
        transport = create_modbus_transport(transport_settings)
        client = ModbusClient(transport, transport_settings.unit_id, transport_settings.timeout_seconds)
        raw_value = client.read_scalar(register_type, address, data_type, word_order)
        numeric_value = float(raw_value) if not isinstance(raw_value, bool) else (1.0 if raw_value else 0.0)
        return {
            "host": transport_settings.host,
            "port": transport_settings.port,
            "unit_id": transport_settings.unit_id,
            "ok": True,
            "raw_value": raw_value,
            "scaled_value": numeric_value * scale,
        }
    except ENERGY_PROBE_READ_ERRORS as error:
        return {
            "host": transport_settings.host,
            "port": transport_settings.port,
            "unit_id": transport_settings.unit_id,
            "ok": False,
            "reason": modbus_transport_issue_reason(error) or error.__class__.__name__.lower(),
            "detail": str(error),
        }


def _probe_plan(
    profile_name: str,
    transport: Mapping[str, object],
    *,
    host: str,
    port: int | None,
    unit_id: int | None,
) -> dict[str, object]:
    return dict(
        energy_source_profile_probe_plan(
            profile_name,
            configured_host=host or transport.get("Host", ""),
            configured_port=port if port is not None else transport.get("Port", ""),
            configured_unit_id=unit_id if unit_id is not None else transport.get("UnitId", transport.get("SlaveId", "")),
        )
    )


def _probe_attempts(
    base_transport: ModbusTransportSettings,
    probe_plan: Mapping[str, object],
    field: dict[str, object],
) -> tuple[list[dict[str, object]], dict[str, object] | None]:
    attempts: list[dict[str, object]] = []
    for candidate in _probe_candidates(base_transport, probe_plan):
        attempt = _attempt_probe(candidate, field)
        attempts.append(attempt)
        if attempt["ok"]:
            return attempts, dict(attempt)
    return attempts, None


def main(argv: list[str] | None = None) -> int:
    """Run the energy probe CLI."""
    parser = argparse.ArgumentParser(description="Probe external energy-source connector configs")
    parser.add_argument("command", choices=("detect-modbus-energy", "validate-huawei-energy"))
    parser.add_argument("config_path")
    parser.add_argument("--profile", default="")
    parser.add_argument("--host", default="")
    parser.add_argument("--port", type=int)
    parser.add_argument("--unit-id", type=int)
    parser.add_argument("--source-id", default="huawei")
    parser.add_argument("--emit", choices=("json", "ini", "wizard-hint", "summary"), default="json")
    parser.add_argument("--write-recommendation-prefix", default="")
    args = parser.parse_args(argv)
    payload = _command_payload(args)
    payload = _payload_with_written_files(args, payload)
    print(_render_payload(args, payload))
    return 0


def _command_payload(args: argparse.Namespace) -> dict[str, object]:
    if args.command == "detect-modbus-energy":
        return _detect_command_payload(args)
    if args.command == "validate-huawei-energy":
        return _validate_huawei_command_payload(args)
    raise ValueError(f"Unsupported energy probe command '{args.command}'")


def _detect_command_payload(args: argparse.Namespace) -> dict[str, object]:
    return detect_modbus_energy_source(
        args.config_path,
        profile_name=str(args.profile or ""),
        host=str(args.host or ""),
        port=args.port,
        unit_id=args.unit_id,
    )


def _validate_huawei_command_payload(args: argparse.Namespace) -> dict[str, object]:
    return validate_huawei_energy_source(
        args.config_path,
        profile_name=str(args.profile or ""),
        host=str(args.host or ""),
        port=args.port,
        unit_id=args.unit_id,
        source_id=str(args.source_id or "huawei"),
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
