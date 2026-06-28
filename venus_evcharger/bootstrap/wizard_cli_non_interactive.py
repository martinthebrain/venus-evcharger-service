# SPDX-License-Identifier: GPL-3.0-or-later
"""Non-interactive answer builders for the setup wizard CLI."""

from __future__ import annotations

import argparse

from venus_evcharger.bootstrap.wizard_charger_presets import apply_charger_preset_backend, relevant_charger_presets
from venus_evcharger.bootstrap.wizard_choices import optional_choice
from venus_evcharger.bootstrap.wizard_guidance import default_backend, resolved_primary_host, role_host_defaults
from venus_evcharger.bootstrap.wizard_import import ImportedWizardDefaults
from venus_evcharger.bootstrap.wizard_models import (
    WIZARD_CHARGER_BACKENDS,
    WIZARD_POLICY_MODES,
    WIZARD_PROFILES,
    WizardAnswers,
    WizardChargerBackend,
    WizardPolicyMode,
    WizardProfile,
    WizardTransportKind,
)
from venus_evcharger.bootstrap.wizard_policy_guidance import policy_defaults
from venus_evcharger.bootstrap.wizard_support import host_from_input, topology_uses_cerbo_relay
from venus_evcharger.bootstrap.wizard_support import (
    backend_requires_transport,
)
from venus_evcharger.bootstrap.wizard_transport_guidance import (
    non_interactive_transport_inputs,
    preset_specific_defaults,
)

def non_interactive_profile(namespace: argparse.Namespace, imported_defaults: ImportedWizardDefaults) -> WizardProfile:
    profile = optional_choice(namespace.profile or imported_defaults.profile, WIZARD_PROFILES, "profile")
    if profile is None:
        raise ValueError("--profile is required in --non-interactive mode unless --import-config/--clone-current provides one")
    return profile


def non_interactive_policy_mode(namespace: argparse.Namespace, imported_defaults: ImportedWizardDefaults) -> WizardPolicyMode:
    return optional_choice(namespace.policy_mode or imported_defaults.policy_mode, WIZARD_POLICY_MODES, "policy mode") or "manual"


def non_interactive_digest_auth(namespace: argparse.Namespace, imported_defaults: ImportedWizardDefaults) -> bool:
    if namespace.digest_auth:
        return True
    if imported_defaults.digest_auth is not None:
        return bool(imported_defaults.digest_auth)
    return False


def non_interactive_topology_preset(
    namespace: argparse.Namespace,
    imported_defaults: ImportedWizardDefaults,
    profile: WizardProfile,
) -> str | None:
    topology_preset = namespace.topology_preset or imported_defaults.topology_preset
    if profile == "multi_adapter_topology":
        return topology_preset or "template-stack"
    return topology_preset


def non_interactive_backend(
    namespace: argparse.Namespace,
    imported: ImportedWizardDefaults | None,
    profile: WizardProfile,
    topology_preset: str | None,
) -> WizardChargerBackend | None:
    _ = topology_preset
    return optional_choice(namespace.charger_backend or default_backend(profile, imported), WIZARD_CHARGER_BACKENDS, "charger backend")


def resolved_backend(
    topology_preset: str | None,
    charger_preset: str | None,
    backend: WizardChargerBackend | None,
) -> WizardChargerBackend | None:
    from venus_evcharger.bootstrap.wizard_guidance import apply_topology_preset_backend

    return apply_topology_preset_backend(topology_preset, backend, charger_preset)


def non_interactive_charger_preset(
    namespace: argparse.Namespace,
    imported_defaults: ImportedWizardDefaults,
    backend: WizardChargerBackend | None,
) -> str | None:
    charger_preset = namespace.charger_preset or imported_defaults.charger_preset
    if charger_preset is None:
        return None
    if charger_preset not in relevant_charger_presets(apply_charger_preset_backend(charger_preset, backend)):
        raise ValueError(f"--charger-preset {charger_preset} is not supported for backend {backend or 'none'}")
    return charger_preset


def non_interactive_device_instance(namespace: argparse.Namespace, imported_defaults: ImportedWizardDefaults) -> int:
    return int(namespace.device_instance if namespace.device_instance is not None else (imported_defaults.device_instance or 60))


def non_interactive_phase(namespace: argparse.Namespace, imported_defaults: ImportedWizardDefaults) -> str:
    return namespace.phase or imported_defaults.phase or "L1"


def non_interactive_string(namespace_value: str | None, imported_value: str | None) -> str:
    return namespace_value or imported_value or ""


def non_interactive_cerbo_relay_inputs(namespace: argparse.Namespace, topology_preset: str | None) -> tuple[int, str]:
    if not topology_uses_cerbo_relay(topology_preset):
        return 0, "NO"
    relay_index = _non_interactive_relay_index(namespace)
    contact_mode = _non_interactive_contact_mode(namespace)
    return relay_index, contact_mode


def _non_interactive_relay_index(namespace: argparse.Namespace) -> int:
    raw_value = getattr(namespace, "cerbo_relay_index", None)
    relay_index = int(raw_value if raw_value is not None else 0)
    if relay_index not in (0, 1):
        raise ValueError("--cerbo-relay-index must be 0 or 1")
    return relay_index


def _non_interactive_contact_mode(namespace: argparse.Namespace) -> str:
    contact_mode = str(getattr(namespace, "cerbo_relay_contact_mode", None) or "NO").strip().upper()
    if contact_mode not in {"NO", "NC"}:
        raise ValueError("--cerbo-relay-contact-mode must be NO or NC")
    return contact_mode


def _non_interactive_transport_answers(
    namespace: argparse.Namespace,
    imported_defaults: ImportedWizardDefaults,
    *,
    backend: WizardChargerBackend | None,
    charger_preset: str | None,
    host_input: str,
    topology_preset: str | None,
) -> tuple[WizardTransportKind, str, int, str, int, float | None, str]:
    transport_kind, transport_host, transport_port, transport_device, transport_unit_id = (
        non_interactive_transport_inputs(
            namespace,
            backend,
            charger_preset,
            host_input,
            imported_defaults,
        )
    )
    request_timeout_seconds, switch_group_phase_layout = preset_specific_defaults(
        namespace,
        imported_defaults,
        backend=backend,
        topology_preset=topology_preset,
        charger_preset=charger_preset,
    )
    return (
        transport_kind,
        transport_host,
        transport_port,
        transport_device,
        transport_unit_id,
        request_timeout_seconds,
        switch_group_phase_layout,
    )


def _non_interactive_policy_answers(
    namespace: argparse.Namespace,
    imported_defaults: ImportedWizardDefaults,
) -> tuple[
    WizardPolicyMode,
    float | None,
    float | None,
    float | None,
    float | None,
    str | None,
    str | None,
    float | None,
]:
    policy_mode = non_interactive_policy_mode(namespace, imported_defaults)
    (
        auto_start_surplus_watts,
        auto_stop_surplus_watts,
        auto_min_soc,
        auto_resume_soc,
        scheduled_enabled_days,
        scheduled_latest_end_time,
        scheduled_night_current_amps,
    ) = policy_defaults(policy_mode, imported_defaults, namespace)
    return (
        policy_mode,
        auto_start_surplus_watts,
        auto_stop_surplus_watts,
        auto_min_soc,
        auto_resume_soc,
        scheduled_enabled_days,
        scheduled_latest_end_time,
        scheduled_night_current_amps,
    )


def _effective_transport_answers(
    backend: WizardChargerBackend | None,
    host_input: str,
    transport_kind: WizardTransportKind,
    transport_host: str,
    transport_port: int,
    transport_device: str,
    transport_unit_id: int,
) -> tuple[WizardTransportKind, str, int, str, int]:
    if backend_requires_transport(backend):
        return (
            transport_kind,
            transport_host,
            transport_port,
            transport_device,
            transport_unit_id,
        )
    return ("serial_rtu", host_from_input(host_input), 502, "/dev/ttyUSB0", 1)


def non_interactive_answers(
    namespace: argparse.Namespace,
    imported_defaults: ImportedWizardDefaults,
) -> WizardAnswers:
    profile = non_interactive_profile(namespace, imported_defaults)
    shared_host = namespace.host or imported_defaults.host_input or "192.168.1.50"
    topology_preset = non_interactive_topology_preset(namespace, imported_defaults, profile)
    backend = non_interactive_backend(namespace, imported_defaults, profile, topology_preset)
    charger_preset = non_interactive_charger_preset(namespace, imported_defaults, backend)
    backend = resolved_backend(topology_preset, charger_preset, backend)
    meter_host, switch_host, charger_host = role_host_defaults(namespace, imported_defaults, profile, topology_preset, shared_host)
    host_input = resolved_primary_host(namespace, imported_defaults, meter_host, switch_host, charger_host)
    (
        transport_kind,
        transport_host,
        transport_port,
        transport_device,
        transport_unit_id,
        request_timeout_seconds,
        switch_group_phase_layout,
    ) = _non_interactive_transport_answers(
        namespace,
        imported_defaults,
        backend=backend,
        charger_preset=charger_preset,
        host_input=host_input,
        topology_preset=topology_preset,
    )
    (
        policy_mode,
        auto_start_surplus_watts,
        auto_stop_surplus_watts,
        auto_min_soc,
        auto_resume_soc,
        scheduled_enabled_days,
        scheduled_latest_end_time,
        scheduled_night_current_amps,
    ) = _non_interactive_policy_answers(namespace, imported_defaults)
    transport_kind, transport_host, transport_port, transport_device, transport_unit_id = (
        _effective_transport_answers(
            backend,
            host_input,
            transport_kind,
            transport_host,
            transport_port,
            transport_device,
            transport_unit_id,
        )
    )
    cerbo_relay_index, cerbo_relay_contact_mode = non_interactive_cerbo_relay_inputs(namespace, topology_preset)
    return WizardAnswers(
        profile=profile,
        host_input=host_input,
        meter_host_input=meter_host,
        switch_host_input=switch_host,
        charger_host_input=charger_host,
        device_instance=non_interactive_device_instance(namespace, imported_defaults),
        phase=non_interactive_phase(namespace, imported_defaults),
        policy_mode=policy_mode,
        digest_auth=non_interactive_digest_auth(namespace, imported_defaults),
        username=non_interactive_string(namespace.username, imported_defaults.username),
        password=non_interactive_string(namespace.password, imported_defaults.password),
        topology_preset=topology_preset,
        charger_backend=backend,
        charger_preset=charger_preset,
        request_timeout_seconds=request_timeout_seconds,
        cerbo_relay_index=cerbo_relay_index,
        cerbo_relay_contact_mode=cerbo_relay_contact_mode,
        switch_group_supported_phase_selections=switch_group_phase_layout,
        auto_start_surplus_watts=auto_start_surplus_watts,
        auto_stop_surplus_watts=auto_stop_surplus_watts,
        auto_min_soc=auto_min_soc,
        auto_resume_soc=auto_resume_soc,
        scheduled_enabled_days=scheduled_enabled_days,
        scheduled_latest_end_time=scheduled_latest_end_time,
        scheduled_night_current_amps=scheduled_night_current_amps,
        transport_kind=transport_kind,
        transport_host=transport_host,
        transport_port=transport_port,
        transport_device=transport_device,
        transport_unit_id=transport_unit_id,
    )
