# SPDX-License-Identifier: GPL-3.0-or-later
"""Adapter-file rendering from the normalized wizard topology model."""

from __future__ import annotations

from venus_evcharger.bootstrap.wizard_adapters import (
    cerbo_gx_relay_switch_config,
    modbus_charger_config,
    native_charger_config,
    shelly_meter_config,
    shelly_switch_config,
    tasmota_meter_config,
    tasmota_switch_config,
    template_charger_config,
    template_meter_config,
    template_switch_config,
    template_switch_group_files,
    tuya_meter_config,
    tuya_switch_config,
)
from venus_evcharger.bootstrap.wizard_models import WizardAnswers
from venus_evcharger.bootstrap.wizard_support import base_url_from_input, host_from_input
from venus_evcharger.topology import EvChargerTopologyConfig


def render_adapter_files_from_topology(
    topology_config: EvChargerTopologyConfig,
    answers: WizardAnswers,
    role_hosts: dict[str, str],
) -> dict[str, str]:
    if topology_config.topology.type == "simple_relay":
        return _simple_relay_files(topology_config, answers, role_hosts)
    if topology_config.topology.type == "native_device":
        return _native_device_files(topology_config, answers, role_hosts)
    if topology_config.topology.type == "hybrid_topology":
        return _hybrid_topology_files(topology_config, answers, role_hosts)
    return {}


def _simple_relay_files(
    topology_config: EvChargerTopologyConfig,
    answers: WizardAnswers,
    role_hosts: dict[str, str],
) -> dict[str, str]:
    files: dict[str, str] = {}
    if topology_config.measurement is not None and topology_config.measurement.type == "external_meter":
        files["wizard-meter.ini"] = _measurement_config_text(answers, role_hosts)
    if topology_config.actuator is not None and topology_config.actuator.config_path is not None:
        files.update(_actuator_files(topology_config, role_hosts, answers))
    return files


def _native_device_files(
    topology_config: EvChargerTopologyConfig,
    answers: WizardAnswers,
    role_hosts: dict[str, str],
) -> dict[str, str]:
    charger = topology_config.charger
    if charger is None:
        return {}
    files = {
        "wizard-charger.ini": _charger_config_text(charger.type, answers, role_hosts),
    }
    if topology_config.measurement is not None and topology_config.measurement.type == "external_meter":
        files["wizard-meter.ini"] = _measurement_config_text(answers, role_hosts)
    return files


def _hybrid_topology_files(
    topology_config: EvChargerTopologyConfig,
    answers: WizardAnswers,
    role_hosts: dict[str, str],
) -> dict[str, str]:
    charger = topology_config.charger
    if charger is None:
        return {}
    files = {
        "wizard-charger.ini": _charger_config_text(charger.type, answers, role_hosts),
    }
    if topology_config.measurement is not None and topology_config.measurement.type == "external_meter":
        files["wizard-meter.ini"] = _measurement_config_text(answers, role_hosts)
    files.update(_actuator_files(topology_config, role_hosts, answers))
    return files


def _charger_config_text(charger_type: str, answers: WizardAnswers, role_hosts: dict[str, str]) -> str:
    charger_base_url = base_url_from_input(role_hosts.get("charger", answers.host_input))
    if charger_type == "template_charger":
        return template_charger_config(charger_base_url)
    if charger_type == "modbus_charger" and answers.charger_preset is None:
        return modbus_charger_config(
            answers.transport_kind,
            transport_host=answers.transport_host,
            transport_port=answers.transport_port,
            transport_device=answers.transport_device,
            transport_unit_id=answers.transport_unit_id,
        )
    return native_charger_config(
        charger_type,
        charger_base_url if charger_type != "modbus_charger" else "",
        charger_preset=answers.charger_preset,
        request_timeout_seconds=answers.request_timeout_seconds,
        transport_kind=answers.transport_kind,
        transport_host=answers.transport_host,
        transport_port=answers.transport_port,
        transport_device=answers.transport_device,
        transport_unit_id=answers.transport_unit_id,
    )


def _measurement_config_text(answers: WizardAnswers, role_hosts: dict[str, str]) -> str:
    meter_host = role_hosts.get("meter", answers.host_input)
    meter_base_url = base_url_from_input(meter_host)
    topology_preset = _measurement_topology_preset(answers)
    meter_family = _meter_family_for_preset(topology_preset)
    return _METER_CONFIG_RENDERERS[meter_family](meter_host, meter_base_url)


def _actuator_files(
    topology_config: EvChargerTopologyConfig,
    role_hosts: dict[str, str],
    answers: WizardAnswers,
) -> dict[str, str]:
    actuator = topology_config.actuator
    if actuator is None or actuator.type not in _ACTUATOR_RENDERERS:
        return {}
    return _ACTUATOR_RENDERERS[actuator.type](role_hosts.get("switch", answers.host_input), answers)


def _cerbo_actuator_files(_switch_host: str, answers: WizardAnswers) -> dict[str, str]:
    return {"wizard-switch.ini": cerbo_gx_relay_switch_config(answers.cerbo_relay_index, answers.cerbo_relay_contact_mode)}


def _switch_group_actuator_files(switch_host: str, answers: WizardAnswers) -> dict[str, str]:
    return template_switch_group_files(base_url_from_input(switch_host), answers.switch_group_supported_phase_selections)


def _template_switch_actuator_files(switch_host: str, _answers: WizardAnswers) -> dict[str, str]:
    return {"wizard-switch.ini": template_switch_config(base_url_from_input(switch_host), "/wizard/switch")}


def _shelly_switch_actuator_files(switch_host: str, _answers: WizardAnswers) -> dict[str, str]:
    return {"wizard-switch.ini": shelly_switch_config(host_from_input(switch_host))}


def _tuya_switch_actuator_files(switch_host: str, _answers: WizardAnswers) -> dict[str, str]:
    return {"wizard-switch.ini": tuya_switch_config(base_url_from_input(switch_host))}


def _tasmota_switch_actuator_files(switch_host: str, _answers: WizardAnswers) -> dict[str, str]:
    return {"wizard-switch.ini": tasmota_switch_config(base_url_from_input(switch_host))}


def _measurement_topology_preset(answers: WizardAnswers) -> str | None:
    if answers.topology_preset is not None:
        return answers.topology_preset
    return {"multi_adapter_topology": "template-stack"}.get(answers.profile)


def _template_meter_config_for_host(_meter_host: str, meter_base_url: str) -> str:
    return template_meter_config(meter_base_url)


def _shelly_meter_config_for_host(meter_host: str, _meter_base_url: str) -> str:
    return shelly_meter_config(host_from_input(meter_host))


def _tasmota_meter_config_for_host(_meter_host: str, meter_base_url: str) -> str:
    return tasmota_meter_config(meter_base_url)


def _tuya_meter_config_for_host(_meter_host: str, meter_base_url: str) -> str:
    return tuya_meter_config(meter_base_url)


_TUYA_METER_PRESETS = frozenset(
    {
        "tuya-io-template-charger",
        "tuya-io-modbus-charger",
        "tuya-meter-cerbo-relay",
        "tuya-meter-goe",
        "tuya-meter-modbus-charger",
    }
)
_TASMOTA_METER_PRESETS = frozenset(
    {
        "tasmota-io-template-charger",
        "tasmota-io-modbus-charger",
        "tasmota-meter-cerbo-relay",
        "tasmota-meter-goe",
        "tasmota-meter-modbus-charger",
    }
)
_TEMPLATE_METER_PRESETS = frozenset(
    {
        "template-stack",
        "template-meter-cerbo-relay",
        "template-meter-goe-switch-group",
    }
)


def _meter_family_for_preset(topology_preset: str | None) -> str:
    if topology_preset in _TEMPLATE_METER_PRESETS:
        return "template"
    if topology_preset in _TASMOTA_METER_PRESETS:
        return "tasmota"
    if topology_preset in _TUYA_METER_PRESETS:
        return "tuya"
    return "shelly"


_METER_CONFIG_RENDERERS = {
    "shelly": _shelly_meter_config_for_host,
    "tasmota": _tasmota_meter_config_for_host,
    "template": _template_meter_config_for_host,
    "tuya": _tuya_meter_config_for_host,
}

_ACTUATOR_RENDERERS = {
    "cerbo_gx_relay_switch": _cerbo_actuator_files,
    "switch_group": _switch_group_actuator_files,
    "template_switch": _template_switch_actuator_files,
    "shelly_switch": _shelly_switch_actuator_files,
    "shelly_contactor_switch": _shelly_switch_actuator_files,
    "tasmota_switch": _tasmota_switch_actuator_files,
    "tasmota_contactor_switch": _tasmota_switch_actuator_files,
    "tuya_switch": _tuya_switch_actuator_files,
    "tuya_contactor_switch": _tuya_switch_actuator_files,
}
