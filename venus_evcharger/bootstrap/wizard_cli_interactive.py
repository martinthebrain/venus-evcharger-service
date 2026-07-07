# SPDX-License-Identifier: GPL-3.0-or-later
"""Interactive answer-building helpers for the setup wizard CLI."""

from __future__ import annotations

import argparse

from venus_evcharger.bootstrap.wizard_charger_presets import (
    CHARGER_PRESET_LABELS,
    apply_charger_preset_backend,
    relevant_charger_presets,
)
from venus_evcharger.bootstrap.wizard_choices import optional_choice
from venus_evcharger.bootstrap.wizard_cli_imports import empty_imported_defaults
from venus_evcharger.bootstrap.wizard_cli_prompts import (
    _prompt_choice,
    _prompt_optional_choice,
    _prompt_password,
    _prompt_text,
    prompt_yes_no,
)
from venus_evcharger.bootstrap.wizard_guidance import (
    default_backend,
    prompt_role_hosts,
    prompt_topology_preset,
    resolved_primary_host,
    role_prompt_intro,
)
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
from venus_evcharger.bootstrap.wizard_policy_guidance import prompt_policy_defaults
from venus_evcharger.bootstrap.wizard_support import (
    NATIVE_CHARGER_VALUES,
    PHASE_SWITCH_CHARGER_VALUES,
    POLICY_VALUES,
    PROFILE_LABELS,
    PROFILE_VALUES,
    backend_requires_transport,
    host_from_input,
    topology_uses_cerbo_relay,
)
from venus_evcharger.bootstrap.wizard_transport_guidance import prompt_preset_specific_defaults, prompt_transport_inputs

_BackendPromptSpec = tuple[tuple[str, ...], WizardChargerBackend]


def _interactive_profile(namespace: argparse.Namespace, imported: ImportedWizardDefaults) -> WizardProfile:
    labels: dict[str, str] = {key: value for key, value in PROFILE_LABELS}
    raw_profile = namespace.profile or imported.profile or _prompt_choice(
        "Choose the setup topology:",
        PROFILE_VALUES,
        labels,
        "simple_relay",
    )
    return optional_choice(raw_profile, WIZARD_PROFILES, "profile") or "simple_relay"


def _interactive_backend(
    namespace: argparse.Namespace,
    profile: WizardProfile,
    imported: ImportedWizardDefaults,
    topology_preset: str | None,
) -> WizardChargerBackend | None:
    del topology_preset
    backend = optional_choice(
        namespace.charger_backend or default_backend(profile, imported),
        WIZARD_CHARGER_BACKENDS,
        "charger backend",
    )
    if namespace.charger_backend is None:
        backend = _interactive_backend_choice(profile, backend)
    return backend


def _interactive_backend_choice(profile: WizardProfile, backend: WizardChargerBackend | None) -> WizardChargerBackend | None:
    prompt_spec = _backend_prompt_spec(profile)
    if prompt_spec is None:
        return backend
    choices, default_backend_value = prompt_spec
    default = backend if backend is not None else default_backend_value
    raw_backend = _prompt_choice("Choose the charger backend:", choices, default=default)
    return optional_choice(raw_backend, WIZARD_CHARGER_BACKENDS, "charger backend") or default_backend_value


def _backend_prompt_spec(profile: WizardProfile) -> _BackendPromptSpec | None:
    if profile == "native_device":
        return NATIVE_CHARGER_VALUES, "goe_charger"
    if profile == "hybrid_topology":
        return PHASE_SWITCH_CHARGER_VALUES, "simpleevse_charger"
    return None


def _interactive_auth_inputs(namespace: argparse.Namespace, imported: ImportedWizardDefaults) -> tuple[bool, str, str]:
    digest_auth = _interactive_digest_auth(namespace, imported)
    username = _interactive_username(namespace, imported, digest_auth)
    password = _interactive_password(namespace, imported, digest_auth)
    return digest_auth, username, password


def _interactive_digest_auth(namespace: argparse.Namespace, imported: ImportedWizardDefaults) -> bool:
    if namespace.digest_auth:
        return True
    return prompt_yes_no("Does this setup require authentication?", bool(imported.digest_auth)) if namespace.digest_auth is False else False


def _interactive_username(namespace: argparse.Namespace, imported: ImportedWizardDefaults, digest_auth: bool) -> str:
    username = namespace.username or imported.username or ""
    if _should_prompt_username(namespace, digest_auth):
        return _prompt_text("Username", username or "admin")
    return username


def _interactive_password(namespace: argparse.Namespace, imported: ImportedWizardDefaults, digest_auth: bool) -> str:
    password = namespace.password or imported.password or ""
    if digest_auth and namespace.password is None:
        return _prompt_password(password)
    return password


def _should_prompt_username(namespace: argparse.Namespace, digest_auth: bool) -> bool:
    return digest_auth and namespace.username is None


def _interactive_policy_mode(namespace: argparse.Namespace, imported: ImportedWizardDefaults) -> WizardPolicyMode:
    raw_policy = namespace.policy_mode or _prompt_choice(
        "Choose the initial policy mode:",
        POLICY_VALUES,
        default=imported.policy_mode or "manual",
    )
    return optional_choice(raw_policy, WIZARD_POLICY_MODES, "policy mode") or "manual"


def _interactive_device_instance(namespace: argparse.Namespace, imported: ImportedWizardDefaults) -> int:
    if namespace.device_instance is not None:
        return int(namespace.device_instance)
    return int(_prompt_text("DeviceInstance", str(imported.device_instance or 60)))


def _interactive_cerbo_relay_inputs(namespace: argparse.Namespace, topology_preset: str | None) -> tuple[int, str]:
    if not topology_uses_cerbo_relay(topology_preset):
        return 0, "NO"
    relay_index = getattr(namespace, "cerbo_relay_index", None)
    if relay_index is None:
        relay_index = int(_prompt_choice("Choose the Cerbo GX relay:", ("0", "1"), {"0": "Relay 1", "1": "Relay 2"}, "0"))
    contact_mode = getattr(namespace, "cerbo_relay_contact_mode", None) or _prompt_choice(
        "Which contact is wired?",
        ("NO", "NC"),
        {"NO": "NO, normally open, recommended fail-off", "NC": "NC, normally closed"},
        "NO",
    )
    return int(relay_index), str(contact_mode).strip().upper()


def _interactive_phase(namespace: argparse.Namespace, imported: ImportedWizardDefaults) -> str:
    return namespace.phase or _prompt_choice("Choose the phase baseline:", ("L1", "L2", "L3", "3P"), default=imported.phase or "L1")


def interactive_answers(namespace: argparse.Namespace, imported: ImportedWizardDefaults | None) -> WizardAnswers:
    imported = imported or empty_imported_defaults()
    profile = _interactive_profile(namespace, imported)
    shared_host = namespace.host or imported.host_input or _prompt_text("Primary host or IP", "192.168.1.50")
    topology_preset = _interactive_topology_preset(namespace, imported, profile)
    backend = _interactive_backend(namespace, profile, imported, topology_preset)
    charger_preset = _interactive_charger_preset(namespace, imported, backend)
    backend = _resolved_backend(topology_preset, charger_preset, backend)
    intro = role_prompt_intro(profile, topology_preset)
    if intro:
        print(intro)
    meter_host, switch_host, charger_host = prompt_role_hosts(
        namespace,
        imported,
        profile,
        topology_preset,
        shared_host,
        prompt_text=_prompt_text,
    )
    host_input = resolved_primary_host(namespace, imported, meter_host, switch_host, charger_host)
    transport_kind, transport_host, transport_port, transport_device, transport_unit_id = _interactive_transport_inputs(
        backend,
        charger_preset,
        host_input,
        imported,
    )
    digest_auth, username, password = _interactive_auth_inputs(namespace, imported)
    policy_mode = _interactive_policy_mode(namespace, imported)
    request_timeout_seconds, switch_group_phase_layout = prompt_preset_specific_defaults(
        namespace,
        imported,
        profile=profile,
        backend=backend,
        topology_preset=topology_preset,
        charger_preset=charger_preset,
        prompt_choice=_prompt_choice,
        prompt_text=_prompt_text,
    )
    cerbo_relay_index, cerbo_relay_contact_mode = _interactive_cerbo_relay_inputs(namespace, topology_preset)
    (
        auto_start_surplus_watts,
        auto_stop_surplus_watts,
        auto_min_soc,
        auto_resume_soc,
        scheduled_enabled_days,
        scheduled_latest_end_time,
        scheduled_night_current_amps,
    ) = prompt_policy_defaults(policy_mode, imported, namespace, prompt_text=_prompt_text)
    return WizardAnswers(
        profile=profile,
        host_input=host_input,
        meter_host_input=meter_host,
        switch_host_input=switch_host,
        charger_host_input=charger_host,
        device_instance=_interactive_device_instance(namespace, imported),
        phase=_interactive_phase(namespace, imported),
        policy_mode=policy_mode,
        digest_auth=digest_auth,
        username=username,
        password=password,
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


def _interactive_topology_preset(namespace: argparse.Namespace, imported: ImportedWizardDefaults, profile: WizardProfile) -> str | None:
    topology_preset = namespace.topology_preset or imported.topology_preset
    if profile == "multi_adapter_topology" and topology_preset is None:
        return prompt_topology_preset(_prompt_choice, imported.topology_preset or "template-stack")
    return topology_preset


def _interactive_transport_inputs(
    backend: WizardChargerBackend | None,
    charger_preset: str | None,
    host_input: str,
    imported: ImportedWizardDefaults,
) -> tuple[WizardTransportKind, str, int, str, int]:
    if backend_requires_transport(backend):
        return prompt_transport_inputs(
            backend,
            charger_preset,
            host_input,
            imported,
            prompt_choice=_prompt_choice,
            prompt_text=_prompt_text,
        )
    return "serial_rtu", host_from_input(host_input), 502, "/dev/ttyUSB0", 1


def _resolved_backend(
    topology_preset: str | None,
    charger_preset: str | None,
    backend: WizardChargerBackend | None,
) -> WizardChargerBackend | None:
    del topology_preset
    return apply_charger_preset_backend(charger_preset, backend)


def _interactive_charger_preset(
    namespace: argparse.Namespace,
    imported: ImportedWizardDefaults,
    backend: WizardChargerBackend | None,
) -> str | None:
    options = relevant_charger_presets(backend)
    selected_preset = _validated_namespace_charger_preset(namespace.charger_preset, options, backend)
    if selected_preset is not None:
        return selected_preset
    if not options:
        return None
    labels = _charger_preset_labels()
    default = imported.charger_preset if imported.charger_preset in options else None
    return _prompt_optional_choice("Choose an optional device preset:", ("none", *options), labels, default)


def _validated_namespace_charger_preset(
    charger_preset: str | None,
    options: tuple[str, ...],
    backend: WizardChargerBackend | None,
) -> str | None:
    """Return one CLI-provided charger preset after backend compatibility validation."""
    if charger_preset is None:
        return None
    if charger_preset not in options:
        raise ValueError(f"--charger-preset {charger_preset} is not supported for backend {backend or 'none'}")
    return charger_preset


def _charger_preset_labels() -> dict[str, str]:
    """Return choice labels for optional charger presets."""
    return {"none": "Generic backend mapping", **{key: value for key, value in CHARGER_PRESET_LABELS}}


_interactive_answers = interactive_answers
