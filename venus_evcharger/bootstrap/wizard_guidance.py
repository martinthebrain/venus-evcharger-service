# SPDX-License-Identifier: GPL-3.0-or-later
"""Prompt guidance, compatibility warnings, and shared wizard defaults."""

from __future__ import annotations

from typing import Callable

from venus_evcharger.bootstrap.wizard_charger_presets import apply_charger_preset_backend
from venus_evcharger.bootstrap.wizard_import import ImportedWizardDefaults
from venus_evcharger.bootstrap.wizard_models import WIZARD_CHARGER_BACKENDS, WizardChargerBackend
from venus_evcharger.bootstrap.wizard_support import (
    PROFILE_ROLE_HOSTS,
    TOPOLOGY_PRESET_LABELS,
    TOPOLOGY_PRESET_BACKENDS,
    TOPOLOGY_PRESET_INTROS,
    TOPOLOGY_RESPONSIBILITY_SUMMARIES,
    TOPOLOGY_PRESET_VALUES,
    TOPOLOGY_ROLE_HOSTS,
    host_from_input,
)

PromptText = Callable[[str, str], str]
PromptChoice = Callable[[str, tuple[str, ...], dict[str, str] | None, str | None], str]

_PROFILE_PROMPT_INTROS: dict[str, str] = {
    "native_device": "This setup only needs the charger endpoint.",
    "hybrid_topology": "This setup needs one charger endpoint and one external phase-switch endpoint.",
}
_DEFAULT_BACKENDS: dict[str, WizardChargerBackend] = {
    "native_device": "goe_charger",
    "hybrid_topology": "simpleevse_charger",
}
_PHASE_LAYOUT_PRESETS = {"goe-external-switch-group"}
_SWITCH_GROUP_PRESET_FRAGMENT = "switch-group"


def prompt_topology_preset(prompt_choice: PromptChoice, default: str) -> str:
    labels = _topology_choice_labels()
    return prompt_choice("Choose the topology preset:", TOPOLOGY_PRESET_VALUES, labels, default)


def _topology_choice_labels() -> dict[str, str]:
    return {
        key: f"{value} - {TOPOLOGY_RESPONSIBILITY_SUMMARIES.get(key, key)}"
        for key, value in TOPOLOGY_PRESET_LABELS
    }


def relevant_role_hosts(profile: str, topology_preset: str | None) -> tuple[str, ...]:
    if profile == "multi_adapter_topology":
        return TOPOLOGY_ROLE_HOSTS.get(topology_preset or "", ())
    if profile == "simple_relay":
        return ()
    return PROFILE_ROLE_HOSTS.get(profile, ())


def role_prompt_intro(profile: str, topology_preset: str | None) -> str | None:
    if profile == "multi_adapter_topology":
        return TOPOLOGY_PRESET_INTROS.get(
            topology_preset or "",
            "This topology uses separate adapter roles. We will ask for each role endpoint separately.",
        )
    return _PROFILE_PROMPT_INTROS.get(profile)


def role_prompt_label(role: str, topology_preset: str | None) -> str:
    if role == "meter":
        return "Meter endpoint (host or full BaseUrl)"
    if role == "switch":
        if topology_preset and _SWITCH_GROUP_PRESET_FRAGMENT in topology_preset:
            return "External phase-switch endpoint (host or full BaseUrl)"
        return "Switch endpoint (host or full BaseUrl)"
    return "Charger endpoint (host or full BaseUrl)"


def role_host_defaults(
    namespace: object,
    imported: ImportedWizardDefaults,
    profile: str,
    topology_preset: str | None,
    shared_host: str,
) -> tuple[str | None, str | None, str | None]:
    relevant = set(relevant_role_hosts(profile, topology_preset))
    role_values = {
        role: getattr(namespace, f"{role}_host") or getattr(imported, f"{role}_host_input") or (shared_host if role in relevant else None)
        for role in ("meter", "switch", "charger")
    }
    return role_values["meter"], role_values["switch"], role_values["charger"]


def resolved_primary_host(
    namespace: object,
    imported: ImportedWizardDefaults,
    meter_host: str | None,
    switch_host: str | None,
    charger_host: str | None,
) -> str:
    for candidate in (getattr(namespace, "host"), imported.host_input, charger_host, meter_host, switch_host):
        if candidate:
            return str(candidate)
    return "192.168.1.50"


def prompt_role_hosts(
    namespace: object,
    imported: ImportedWizardDefaults,
    profile: str,
    topology_preset: str | None,
    shared_host: str,
    *,
    prompt_text: PromptText,
) -> tuple[str | None, str | None, str | None]:
    relevant = set(relevant_role_hosts(profile, topology_preset))
    role_defaults = _prompted_role_defaults(namespace, imported, profile, topology_preset, shared_host, relevant, prompt_text)
    return _filtered_role_defaults(role_defaults, relevant)


def _role_defaults_map(
    namespace: object,
    imported: ImportedWizardDefaults,
    profile: str,
    topology_preset: str | None,
    shared_host: str,
) -> dict[str, str | None]:
    return dict(
        zip(
            ("meter", "switch", "charger"),
            role_host_defaults(namespace, imported, profile, topology_preset, shared_host),
        )
    )


def _prompted_role_defaults(
    namespace: object,
    imported: ImportedWizardDefaults,
    profile: str,
    topology_preset: str | None,
    shared_host: str,
    relevant: set[str],
    prompt_text: PromptText,
) -> dict[str, str | None]:
    role_defaults = _role_defaults_map(namespace, imported, profile, topology_preset, shared_host)
    for role in sorted(relevant):
        if getattr(namespace, f"{role}_host") is None:
            role_defaults[role] = prompt_text(role_prompt_label(role, topology_preset), role_defaults[role] or shared_host)
    return role_defaults


def _filtered_role_defaults(role_defaults: dict[str, str | None], relevant: set[str]) -> tuple[str | None, str | None, str | None]:
    return (
        role_defaults["meter"] if "meter" in relevant else None,
        role_defaults["switch"] if "switch" in relevant else None,
        role_defaults["charger"] if "charger" in relevant else None,
    )


def default_backend(profile: str, imported: ImportedWizardDefaults | None) -> WizardChargerBackend | None:
    imported_backend = imported.charger_backend if imported is not None else None
    return imported_backend or _DEFAULT_BACKENDS.get(profile)


def apply_topology_preset_backend(
    topology_preset: str | None,
    backend: WizardChargerBackend | None,
    charger_preset: str | None = None,
) -> WizardChargerBackend | None:
    resolved_backend = _resolved_topology_preset_backend(topology_preset, backend)
    return apply_charger_preset_backend(charger_preset, resolved_backend)


def _resolved_topology_preset_backend(
    topology_preset: str | None,
    backend: WizardChargerBackend | None,
) -> WizardChargerBackend | None:
    """Return the backend implied by one topology preset, falling back to the explicit backend."""
    if topology_preset is None:
        return backend
    topology_backend = TOPOLOGY_PRESET_BACKENDS.get(topology_preset)
    if topology_backend is None:
        return backend
    return _known_charger_backend(topology_backend)


def _known_charger_backend(raw_backend: str) -> WizardChargerBackend:
    for backend in WIZARD_CHARGER_BACKENDS:
        if raw_backend == backend:
            return backend
    raise ValueError(f"Unsupported topology backend: {raw_backend}")


def compatibility_warnings(
    *,
    profile: str,
    topology_preset: str | None,
    charger_backend: str | None,
    primary_host_input: str,
    role_hosts: dict[str, str],
    transport_kind: str,
    transport_host: str,
    switch_group_supported_phase_selections: str,
    charger_preset: str | None = None,
) -> tuple[str, ...]:
    shared_roles = sorted(role for role, value in role_hosts.items() if value == primary_host_input)
    warning_items = (
        _shared_endpoint_warning(profile, role_hosts),
        _switch_group_warning(topology_preset, shared_roles),
        _charger_host_warning(charger_backend, transport_kind, transport_host, primary_host_input, role_hosts),
        _charger_preset_warning(charger_preset),
        _phase_layout_warning(switch_group_supported_phase_selections),
    )
    return tuple(item for item in warning_items if item is not None)


def _shared_endpoint_warning(profile: str, role_hosts: dict[str, str]) -> str | None:
    if profile == "multi_adapter_topology" and len(role_hosts) > 1 and len(set(role_hosts.values())) == 1:
        return "Multiple topology roles resolve to the same shared endpoint; verify that this combined-host layout is intentional."
    return None


def _switch_group_warning(topology_preset: str | None, shared_roles: list[str]) -> str | None:
    if topology_preset and (_SWITCH_GROUP_PRESET_FRAGMENT in topology_preset or topology_preset in _PHASE_LAYOUT_PRESETS) and "switch" in shared_roles:
        return "This switch_group preset is using the shared primary endpoint for the external phase switch; verify that the switch adapter is really colocated there."
    return None


def _charger_host_warning(
    charger_backend: str | None,
    transport_kind: str,
    transport_host: str,
    primary_host_input: str,
    role_hosts: dict[str, str],
) -> str | None:
    if _modbus_primary_transport_warning(charger_backend, transport_kind, transport_host, primary_host_input):
        return "The Modbus TCP charger currently uses the primary service host as transport host; confirm charger address and unit id."
    if charger_backend == "goe_charger" and "charger" not in role_hosts:
        return "The go-e preset fell back to the shared primary endpoint; set an explicit charger endpoint if the charger lives elsewhere."
    return None


def _modbus_primary_transport_warning(
    charger_backend: str | None,
    transport_kind: str,
    transport_host: str,
    primary_host_input: str,
) -> bool:
    return (
        charger_backend == "modbus_charger"
        and transport_kind == "tcp"
        and transport_host == host_from_input(primary_host_input)
    )


def _phase_layout_warning(switch_group_supported_phase_selections: str) -> str | None:
    if switch_group_supported_phase_selections == "P1,P1_P2_P3":
        return "The external phase switch is configured for 1P -> 3P switching only; make sure a 2-phase step is intentionally unavailable."
    return None


def _charger_preset_warning(charger_preset: str | None) -> str | None:
    if charger_preset == "cfos-power-brain-modbus":
        return "The cFos preset only writes charging_enable, charging_cur_limit, and relay_select regularly; avoid adding periodic writes to other cFos registers because they persist to flash."
    if charger_preset == "openwb-modbus-secondary":
        return "The openWB Modbus preset expects the charger in secondary Modbus mode. If you enable the openWB heartbeat, keep this service polling continuously so the heartbeat does not expire."
    return None


def probe_roles(namespace: object) -> tuple[str, ...] | None:
    selected = tuple(getattr(namespace, "probe_roles") or ())
    return selected or None
