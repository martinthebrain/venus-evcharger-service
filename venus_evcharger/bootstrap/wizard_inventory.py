# SPDX-License-Identifier: GPL-3.0-or-later
"""Device inventory generation from wizard answers and normalized topology."""

from __future__ import annotations

from dataclasses import asdict
from urllib.parse import urlparse

from venus_evcharger.bootstrap.wizard_models import WizardAnswers
from venus_evcharger.inventory import (
    DeviceCapability,
    DeviceInstance,
    DeviceInventory,
    DeviceProfile,
    PhaseLabel,
    RoleBinding,
    RoleBindingMember,
    SwitchingMode,
    render_device_inventory_config,
)
from venus_evcharger.topology import EvChargerTopologyConfig

_THREE_PHASE_SCOPE: tuple[PhaseLabel, ...] = ("L1", "L2", "L3")


def build_wizard_inventory(
    answers: WizardAnswers,
    role_hosts: dict[str, str],
    topology_config: EvChargerTopologyConfig,
) -> DeviceInventory:
    """Build one reusable local device inventory from one wizard configuration."""
    profile_map: dict[str, DeviceProfile] = {}
    devices: list[DeviceInstance] = []
    bindings: list[RoleBinding] = []
    phase_scope = _phase_scope(answers.phase)
    _populate_actuator_inventory(profile_map, devices, bindings, answers, role_hosts, topology_config, phase_scope)
    _populate_measurement_inventory(profile_map, devices, bindings, answers, role_hosts, topology_config, phase_scope)
    _populate_charger_inventory(profile_map, devices, bindings, answers, role_hosts, topology_config, phase_scope)
    return DeviceInventory(
        profiles=tuple(profile_map.values()),
        devices=tuple(devices),
        bindings=tuple(bindings),
    )

def _populate_actuator_inventory(
    profile_map: dict[str, DeviceProfile],
    devices: list[DeviceInstance],
    bindings: list[RoleBinding],
    answers: WizardAnswers,
    role_hosts: dict[str, str],
    topology_config: EvChargerTopologyConfig,
    phase_scope: tuple[PhaseLabel, ...],
) -> None:
    if topology_config.actuator is None:
        return
    if topology_config.actuator.type == "switch_group":
        bindings.append(
            _switch_group_binding(
                profile_map,
                devices,
                role_hosts.get("switch", answers.host_input),
                _switch_group_phase_scope(answers.switch_group_supported_phase_selections),
            )
        )
        return
    switch_profile_id = _profile_id("switch", topology_config.actuator.type)
    profile_map[switch_profile_id] = DeviceProfile(
        id=switch_profile_id,
        label=_profile_label(topology_config.actuator.type),
        capabilities=(
            DeviceCapability(
                id="switch",
                kind="switch",
                adapter_type=topology_config.actuator.type,
                supported_phases=phase_scope,
                switching_mode=_switching_mode(topology_config.actuator.type),
                supports_feedback=_switch_supports_feedback(topology_config.actuator.type),
                supports_phase_selection=_supports_phase_selection(phase_scope),
            ),
        ),
    )
    devices.append(
        DeviceInstance(
            id="switch_device",
            profile_id=switch_profile_id,
            label="Switch device",
            endpoint=_switch_endpoint(topology_config.actuator.type, answers, role_hosts),
        )
    )
    bindings.append(
        RoleBinding(
            id="actuation",
            role="actuation",
            label="Actuation",
            phase_scope=phase_scope,
            members=(RoleBindingMember(device_id="switch_device", capability_id="switch", phases=phase_scope),),
        )
    )


def _populate_measurement_inventory(
    profile_map: dict[str, DeviceProfile],
    devices: list[DeviceInstance],
    bindings: list[RoleBinding],
    answers: WizardAnswers,
    role_hosts: dict[str, str],
    topology_config: EvChargerTopologyConfig,
    phase_scope: tuple[PhaseLabel, ...],
) -> None:
    measurement_type = _measurement_type(topology_config)
    if measurement_type is None:
        return
    if _uses_actuator_native_measurement(measurement_type, topology_config):
        bindings.append(_actuator_native_measurement_binding(profile_map, devices, answers, topology_config, phase_scope))
        return
    if _uses_standalone_meter(measurement_type):
        _append_standalone_meter(profile_map, devices, bindings, answers, role_hosts, measurement_type, phase_scope)
        return
    if measurement_type == "charger_native":
        bindings.append(_charger_native_measurement_binding(phase_scope))


def _measurement_type(topology_config: EvChargerTopologyConfig) -> str | None:
    """Return the active measurement type or ``None`` when no measurement role is configured."""
    measurement = topology_config.measurement
    if measurement is None or measurement.type == "none":
        return None
    return measurement.type


def _uses_actuator_native_measurement(
    measurement_type: str,
    topology_config: EvChargerTopologyConfig,
) -> bool:
    """Return whether measurement reuses the configured actuator role."""
    return measurement_type == "actuator_native" and topology_config.actuator is not None


def _uses_standalone_meter(measurement_type: str) -> bool:
    """Return whether measurement requires a dedicated meter inventory entry."""
    return measurement_type in {"external_meter", "fixed_reference", "learned_reference"}


def _charger_native_measurement_binding(
    phase_scope: tuple[PhaseLabel, ...],
) -> RoleBinding:
    """Return the inventory binding for charger-native measurement."""
    return RoleBinding(
        id="measurement",
        role="measurement",
        label="Measurement",
        phase_scope=phase_scope,
        members=(RoleBindingMember(device_id="charger_device", capability_id="meter", phases=phase_scope),),
    )


def _actuator_native_measurement_binding(
    profile_map: dict[str, DeviceProfile],
    devices: list[DeviceInstance],
    answers: WizardAnswers,
    topology_config: EvChargerTopologyConfig,
    phase_scope: tuple[PhaseLabel, ...],
) -> RoleBinding:
    assert topology_config.actuator is not None
    if topology_config.actuator.type == "switch_group":
        profile_id = _profile_id("switch", "switch_group_member")
        group_profile = profile_map[profile_id]
        profile_map[profile_id] = DeviceProfile(
            id=group_profile.id,
            label=group_profile.label,
            capabilities=(
                *group_profile.capabilities,
                DeviceCapability(
                    id="meter",
                    kind="meter",
                    adapter_type=group_profile.capabilities[0].adapter_type,
                    supported_phases=_THREE_PHASE_SCOPE,
                    measures_power=True,
                    measures_energy=True,
                ),
            ),
        )
        return _group_measurement_binding(
            devices=devices,
            binding_id="measurement",
            label="Measurement",
            phase_scope=_switch_group_phase_scope(answers.switch_group_supported_phase_selections),
        )
    profile_id = _profile_id("switch", topology_config.actuator.type)
    switch_profile = profile_map[profile_id]
    profile_map[profile_id] = DeviceProfile(
        id=switch_profile.id,
        label=switch_profile.label,
        capabilities=(
            *switch_profile.capabilities,
            DeviceCapability(
                id="meter",
                kind="meter",
                adapter_type=topology_config.actuator.type,
                supported_phases=phase_scope,
                measures_power=True,
                measures_energy=True,
            ),
        ),
    )
    return RoleBinding(
        id="measurement",
        role="measurement",
        label="Measurement",
        phase_scope=phase_scope,
        members=(RoleBindingMember(device_id="switch_device", capability_id="meter", phases=phase_scope),),
    )


def _append_standalone_meter(
    profile_map: dict[str, DeviceProfile],
    devices: list[DeviceInstance],
    bindings: list[RoleBinding],
    answers: WizardAnswers,
    role_hosts: dict[str, str],
    measurement_type: str,
    phase_scope: tuple[PhaseLabel, ...],
) -> None:
    meter_profile_id = _profile_id("meter", measurement_type)
    profile_map[meter_profile_id] = DeviceProfile(
        id=meter_profile_id,
        label=_measurement_profile_label(measurement_type),
        capabilities=(
            DeviceCapability(
                id="meter",
                kind="meter",
                adapter_type=_measurement_adapter_type(answers, measurement_type),
                supported_phases=phase_scope,
                measures_power=True,
                measures_energy=measurement_type != "fixed_reference",
            ),
        ),
    )
    devices.append(
        DeviceInstance(
            id="meter_device",
            profile_id=meter_profile_id,
            label="Meter device",
            endpoint=_endpoint(role_hosts.get("meter", answers.host_input)),
        )
    )
    bindings.append(
        RoleBinding(
            id="measurement",
            role="measurement",
            label="Measurement",
            phase_scope=phase_scope,
            members=(RoleBindingMember(device_id="meter_device", capability_id="meter", phases=phase_scope),),
        )
    )


def _populate_charger_inventory(
    profile_map: dict[str, DeviceProfile],
    devices: list[DeviceInstance],
    bindings: list[RoleBinding],
    answers: WizardAnswers,
    role_hosts: dict[str, str],
    topology_config: EvChargerTopologyConfig,
    phase_scope: tuple[PhaseLabel, ...],
) -> None:
    if topology_config.charger is None:
        return
    charger_profile_id = _profile_id("charger", topology_config.charger.type)
    profile_map[charger_profile_id] = DeviceProfile(
        id=charger_profile_id,
        label=_profile_label(topology_config.charger.type),
        capabilities=_charger_capabilities(topology_config, phase_scope),
    )
    devices.append(
        DeviceInstance(
            id="charger_device",
            profile_id=charger_profile_id,
            label="Charger device",
            endpoint=_endpoint(role_hosts.get("charger", answers.host_input)),
        )
    )
    bindings.append(
        RoleBinding(
            id="charger",
            role="charger",
            label="Charger",
            phase_scope=phase_scope,
            members=(RoleBindingMember(device_id="charger_device", capability_id="charger", phases=phase_scope),),
        )
    )


def _charger_capabilities(
    topology_config: EvChargerTopologyConfig,
    phase_scope: tuple[PhaseLabel, ...],
) -> tuple[DeviceCapability, ...]:
    assert topology_config.charger is not None
    capabilities = [
        DeviceCapability(
            id="charger",
            kind="charger",
            adapter_type=topology_config.charger.type,
            supported_phases=phase_scope,
            supports_phase_selection=_supports_phase_selection(phase_scope),
        )
    ]
    if topology_config.measurement is not None and topology_config.measurement.type == "charger_native":
        capabilities.append(
            DeviceCapability(
                id="meter",
                kind="meter",
                adapter_type=topology_config.charger.type,
                supported_phases=phase_scope,
                measures_power=True,
                measures_energy=True,
            )
        )
    return tuple(capabilities)


def inventory_payload(inventory: DeviceInventory) -> dict[str, object]:
    """Return one JSON-ready payload for one generated inventory."""
    return asdict(inventory)


def inventory_text(
    answers: WizardAnswers,
    role_hosts: dict[str, str],
    topology_config: EvChargerTopologyConfig,
) -> str:
    """Render one inventory sidecar from one wizard state."""
    return render_device_inventory_config(build_wizard_inventory(answers, role_hosts, topology_config))


def _switch_group_binding(
    profile_map: dict[str, DeviceProfile],
    devices: list[DeviceInstance],
    switch_host_input: str,
    phase_scope: tuple[PhaseLabel, ...],
) -> RoleBinding:
    profile_id = _profile_id("switch", "switch_group_member")
    profile_map[profile_id] = DeviceProfile(
        id=profile_id,
        label="Switch group member",
        capabilities=(
            DeviceCapability(
                id="switch",
                kind="switch",
                adapter_type="template_switch",
                supported_phases=_THREE_PHASE_SCOPE,
                switching_mode="contactor",
                supports_feedback=True,
            ),
        ),
    )
    members: list[RoleBindingMember] = []
    for phase_label in phase_scope:
        device_id = f"switch_{phase_label.lower()}"
        devices.append(
            DeviceInstance(
                id=device_id,
                profile_id=profile_id,
                label=f"Switch {phase_label}",
                endpoint=_phase_endpoint(switch_host_input, phase_label),
            )
        )
        members.append(
            RoleBindingMember(
                device_id=device_id,
                capability_id="switch",
                phases=(phase_label,),
            )
        )
    return RoleBinding(
        id="actuation",
        role="actuation",
        label="Actuation",
        phase_scope=phase_scope,
        members=tuple(members),
    )


def _group_measurement_binding(
    *,
    devices: list[DeviceInstance],
    binding_id: str,
    label: str,
    phase_scope: tuple[PhaseLabel, ...],
) -> RoleBinding:
    members = tuple(
        RoleBindingMember(
            device_id=f"switch_{phase_label.lower()}",
            capability_id="meter",
            phases=(phase_label,),
        )
        for phase_label in phase_scope
        if any(device.id == f"switch_{phase_label.lower()}" for device in devices)
    )
    return RoleBinding(
        id=binding_id,
        role="measurement",
        label=label,
        phase_scope=phase_scope,
        members=members,
    )


def _profile_id(prefix: str, adapter_type: str) -> str:
    return f"{prefix}_{adapter_type}".replace("-", "_")


def _profile_label(adapter_type: str) -> str:
    return adapter_type.replace("_", " ").title()


def _measurement_profile_label(measurement_type: str) -> str:
    if measurement_type == "fixed_reference":
        return "Fixed reference meter"
    if measurement_type == "learned_reference":
        return "Learned reference meter"
    return "Meter device"


def _measurement_adapter_type(answers: WizardAnswers, measurement_type: str) -> str:
    if measurement_type in {"fixed_reference", "learned_reference"}:
        return measurement_type
    if answers.topology_preset is None:
        return "shelly_meter"
    return _adapter_type_for_topology_preset(answers.topology_preset)


def _adapter_type_for_topology_preset(topology_preset: str) -> str:
    if topology_preset in {"template-stack", "template-meter-cerbo-relay", "template-meter-goe-switch-group"}:
        return "template_meter"
    if topology_preset.startswith("tasmota-"):
        return "tasmota_meter"
    if topology_preset.startswith("tuya-"):
        return "tuya_meter"
    return "shelly_meter"


def _phase_scope(phase: str) -> tuple[PhaseLabel, ...]:
    normalized = phase.strip().upper()
    if normalized == "3P":
        return _THREE_PHASE_SCOPE
    if normalized == "L2":
        return ("L2",)
    if normalized == "L3":
        return ("L3",)
    return ("L1",)


def _switch_group_phase_scope(supported_phase_selections: str) -> tuple[PhaseLabel, ...]:
    normalized = supported_phase_selections.strip().upper()
    if "P1_P2_P3" in normalized:
        return _THREE_PHASE_SCOPE
    if "P1_P2" in normalized:
        return ("L1", "L2")
    return ("L1",)


def _switching_mode(adapter_type: str) -> SwitchingMode:
    contactor_types = {
        "cerbo_gx_relay_switch",
        "shelly_contactor_switch",
        "tasmota_contactor_switch",
        "tuya_contactor_switch",
    }
    return "contactor" if adapter_type in contactor_types else "direct"


def _supports_phase_selection(phase_scope: tuple[PhaseLabel, ...]) -> bool:
    return len(phase_scope) > 1


def _switch_supports_feedback(adapter_type: str) -> bool:
    return adapter_type != "cerbo_gx_relay_switch"


def _switch_endpoint(adapter_type: str, answers: WizardAnswers, role_hosts: dict[str, str]) -> str | None:
    if adapter_type == "cerbo_gx_relay_switch":
        return f"local://cerbo-gx/relay/{int(answers.cerbo_relay_index)}"
    return _endpoint(role_hosts.get("switch", answers.host_input))


def _endpoint(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _phase_endpoint(switch_host_input: str, phase_label: str) -> str:
    parsed = urlparse(switch_host_input)
    if parsed.scheme:
        base = switch_host_input.rstrip("/")
    else:
        base = f"http://{switch_host_input.rstrip('/')}"
    suffix = {"L1": "/wizard/phase1", "L2": "/wizard/phase2", "L3": "/wizard/phase3"}[phase_label]
    return f"{base}{suffix}"
