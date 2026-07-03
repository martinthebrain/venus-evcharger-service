# SPDX-License-Identifier: GPL-3.0-or-later
"""Direct topology-model resolution for wizard-generated setups."""

from __future__ import annotations

from typing import TypedDict

from venus_evcharger.bootstrap.wizard_models import WizardAnswers
from venus_evcharger.topology import (
    ActuatorConfig,
    ActuatorType,
    ChargerConfig,
    ChargerType,
    EvChargerTopologyConfig,
    MeasurementConfig,
    PolicyConfig,
    TopologyConfig,
    validate_topology_config,
)


class _HybridExternalMeterOptions(TypedDict):
    actuator_type: ActuatorType
    switch_config_name: str
    charger_type: ChargerType


def build_wizard_topology_config(
    answers: WizardAnswers,
) -> EvChargerTopologyConfig:
    """Build one normalized topology config directly from wizard selections."""
    if answers.profile in {"simple_relay", "advanced_manual"}:
        return _simple_relay_topology(answers)
    if answers.profile == "native_device":
        return _native_charger_topology(answers)
    if answers.profile == "hybrid_topology":
        return _phase_switch_topology(answers)
    if answers.profile == "multi_adapter_topology":
        return _split_topology(answers)
    raise ValueError(f"unsupported wizard profile for topology mapping: {answers.profile}")


def _policy(answers: WizardAnswers) -> PolicyConfig:
    return PolicyConfig(mode=answers.policy_mode, phase=answers.phase)


def _simple_relay_topology(answers: WizardAnswers) -> EvChargerTopologyConfig:
    return validate_topology_config(
        EvChargerTopologyConfig(
            topology=TopologyConfig(type="simple_relay"),
            actuator=ActuatorConfig(type="shelly_contactor_switch", config_path=None),
            measurement=MeasurementConfig(type="actuator_native"),
            charger=None,
            policy=_policy(answers),
        )
    )


def _native_charger_topology(
    answers: WizardAnswers,
) -> EvChargerTopologyConfig:
    return validate_topology_config(
        EvChargerTopologyConfig(
            topology=TopologyConfig(type="native_device"),
            actuator=None,
            measurement=MeasurementConfig(type="charger_native"),
            charger=ChargerConfig(
                type=_charger_type_or_default(answers.charger_backend, "goe_charger"),
                config_path="wizard-charger.ini",
            ),
            policy=_policy(answers),
        )
    )


def _phase_switch_topology(
    answers: WizardAnswers,
) -> EvChargerTopologyConfig:
    return validate_topology_config(
        EvChargerTopologyConfig(
            topology=TopologyConfig(type="hybrid_topology"),
            actuator=ActuatorConfig(
                type="switch_group",
                config_path="wizard-switch-group.ini",
            ),
            measurement=MeasurementConfig(type="charger_native"),
            charger=ChargerConfig(
                type=_charger_type_or_default(answers.charger_backend, "simpleevse_charger"),
                config_path="wizard-charger.ini",
            ),
            policy=_policy(answers),
        )
    )


def _split_topology(
    answers: WizardAnswers,
) -> EvChargerTopologyConfig:
    topology_preset = answers.topology_preset or "template-stack"
    for resolver in _SPLIT_TOPOLOGY_RESOLVERS:
        topology = resolver(answers, topology_preset)
        if topology is not None:
            return topology
    raise ValueError(f"unsupported topology preset for topology mapping: {topology_preset}")


def _cerbo_relay_topology(answers: WizardAnswers, topology_preset: str) -> EvChargerTopologyConfig | None:
    if _cerbo_relay_meter_type(topology_preset) is None:
        return None
    return _cerbo_relay_external_meter_topology(answers, meter_config_name="wizard-meter.ini")


def _hybrid_external_topology(answers: WizardAnswers, topology_preset: str) -> EvChargerTopologyConfig | None:
    hybrid_external = _hybrid_external_meter_options(topology_preset)
    if hybrid_external is None:
        return None
    return _hybrid_external_meter_topology(answers, **hybrid_external)


def _native_external_topology(answers: WizardAnswers, topology_preset: str) -> EvChargerTopologyConfig | None:
    native_external = _native_external_meter_charger_type(topology_preset)
    if native_external is None:
        return None
    return _native_external_meter_topology(answers, charger_type=native_external)


def _hybrid_charger_topology(answers: WizardAnswers, topology_preset: str) -> EvChargerTopologyConfig | None:
    charger_native = _hybrid_charger_native_type(topology_preset)
    if charger_native is None:
        return None
    return _hybrid_charger_native_topology(answers, charger_type=charger_native)


_SPLIT_TOPOLOGY_RESOLVERS = (
    _cerbo_relay_topology,
    _hybrid_external_topology,
    _native_external_topology,
    _hybrid_charger_topology,
)


def _cerbo_relay_meter_type(topology_preset: str) -> str | None:
    """Return the meter adapter type for Cerbo relay presets."""
    return {
        "template-meter-cerbo-relay": "template_meter",
        "shelly-meter-cerbo-relay": "shelly_meter",
        "tasmota-meter-cerbo-relay": "tasmota_meter",
        "tuya-meter-cerbo-relay": "tuya_meter",
    }.get(topology_preset)


def _cerbo_relay_external_meter_topology(
    answers: WizardAnswers,
    *,
    meter_config_name: str,
) -> EvChargerTopologyConfig:
    return validate_topology_config(
        EvChargerTopologyConfig(
            topology=TopologyConfig(type="simple_relay"),
            actuator=ActuatorConfig(type="cerbo_gx_relay_switch", config_path="wizard-switch.ini"),
            measurement=MeasurementConfig(type="external_meter", config_path=meter_config_name),
            charger=None,
            policy=_policy(answers),
        )
    )


def _hybrid_external_meter_options(topology_preset: str) -> _HybridExternalMeterOptions | None:
    """Return hybrid external-meter builder options for one topology preset."""
    options: dict[str, _HybridExternalMeterOptions] = {
        "template-stack": {
            "actuator_type": "template_switch",
            "switch_config_name": "wizard-switch.ini",
            "charger_type": "template_charger",
        },
        "shelly-io-template-charger": {
            "actuator_type": "shelly_switch",
            "switch_config_name": "wizard-switch.ini",
            "charger_type": "template_charger",
        },
        "tuya-io-template-charger": {
            "actuator_type": "tuya_switch",
            "switch_config_name": "wizard-switch.ini",
            "charger_type": "template_charger",
        },
        "tasmota-io-template-charger": {
            "actuator_type": "tasmota_switch",
            "switch_config_name": "wizard-switch.ini",
            "charger_type": "template_charger",
        },
        "shelly-io-modbus-charger": {
            "actuator_type": "shelly_switch",
            "switch_config_name": "wizard-switch.ini",
            "charger_type": "modbus_charger",
        },
        "tuya-io-modbus-charger": {
            "actuator_type": "tuya_switch",
            "switch_config_name": "wizard-switch.ini",
            "charger_type": "modbus_charger",
        },
        "tasmota-io-modbus-charger": {
            "actuator_type": "tasmota_switch",
            "switch_config_name": "wizard-switch.ini",
            "charger_type": "modbus_charger",
        },
        "template-meter-goe-switch-group": {
            "actuator_type": "switch_group",
            "switch_config_name": "wizard-switch-group.ini",
            "charger_type": "goe_charger",
        },
        "shelly-meter-goe-switch-group": {
            "actuator_type": "switch_group",
            "switch_config_name": "wizard-switch-group.ini",
            "charger_type": "goe_charger",
        },
        "shelly-meter-modbus-switch-group": {
            "actuator_type": "switch_group",
            "switch_config_name": "wizard-switch-group.ini",
            "charger_type": "modbus_charger",
        },
    }
    return options.get(topology_preset)


def _native_external_meter_charger_type(topology_preset: str) -> ChargerType | None:
    """Return charger type for native-device external-meter presets."""
    charger_types: dict[str, ChargerType] = {
        "shelly-meter-goe": "goe_charger",
        "shelly-meter-modbus-charger": "modbus_charger",
        "tuya-meter-goe": "goe_charger",
        "tuya-meter-modbus-charger": "modbus_charger",
        "tasmota-meter-goe": "goe_charger",
        "tasmota-meter-modbus-charger": "modbus_charger",
    }
    return charger_types.get(topology_preset)


def _hybrid_charger_native_type(topology_preset: str) -> ChargerType | None:
    """Return charger type for hybrid charger-native presets."""
    charger_types: dict[str, ChargerType] = {
        "goe-external-switch-group": "goe_charger",
    }
    return charger_types.get(topology_preset)


def _native_external_meter_topology(
    answers: WizardAnswers,
    *,
    charger_type: ChargerType,
) -> EvChargerTopologyConfig:
    return validate_topology_config(
        EvChargerTopologyConfig(
            topology=TopologyConfig(type="native_device"),
            actuator=None,
            measurement=MeasurementConfig(
                type="external_meter",
                config_path="wizard-meter.ini",
            ),
            charger=ChargerConfig(
                type=charger_type,
                config_path="wizard-charger.ini",
            ),
            policy=_policy(answers),
        )
    )


def _hybrid_charger_native_topology(
    answers: WizardAnswers,
    *,
    charger_type: ChargerType,
) -> EvChargerTopologyConfig:
    return validate_topology_config(
        EvChargerTopologyConfig(
            topology=TopologyConfig(type="hybrid_topology"),
            actuator=ActuatorConfig(
                type="switch_group",
                config_path="wizard-switch-group.ini",
            ),
            measurement=MeasurementConfig(type="charger_native"),
            charger=ChargerConfig(
                type=charger_type,
                config_path="wizard-charger.ini",
            ),
            policy=_policy(answers),
        )
    )


def _hybrid_external_meter_topology(
    answers: WizardAnswers,
    *,
    actuator_type: ActuatorType,
    switch_config_name: str,
    charger_type: ChargerType,
) -> EvChargerTopologyConfig:
    return validate_topology_config(
        EvChargerTopologyConfig(
            topology=TopologyConfig(type="hybrid_topology"),
            actuator=ActuatorConfig(
                type=actuator_type,
                config_path=switch_config_name,
            ),
            measurement=MeasurementConfig(
                type="external_meter",
                config_path="wizard-meter.ini",
            ),
            charger=ChargerConfig(
                type=charger_type,
                config_path="wizard-charger.ini",
            ),
            policy=_policy(answers),
        )
    )


def _charger_type_or_default(value: ChargerType | None, default: ChargerType) -> ChargerType:
    """Return one wizard charger type while keeping topology literals typed."""
    return value or default
