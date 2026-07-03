# SPDX-License-Identifier: GPL-3.0-or-later
"""CLI, prompt, and answer-building helpers for the setup wizard."""

from __future__ import annotations

import argparse
import getpass
from pathlib import Path

from venus_evcharger.bootstrap.wizard_charger_presets import (
    CHARGER_PRESET_LABELS,
    CHARGER_PRESET_VALUES,
    apply_charger_preset_backend,
    relevant_charger_presets,
)
from venus_evcharger.bootstrap.wizard_choices import optional_choice
from venus_evcharger.bootstrap.wizard_cli_output import result_text
from venus_evcharger.bootstrap.wizard_cli_parser import build_parser
from venus_evcharger.bootstrap.wizard_guidance import (
    default_backend,
    prompt_role_hosts,
    prompt_topology_preset,
    resolved_primary_host,
    role_host_defaults,
    role_prompt_intro,
)
from venus_evcharger.bootstrap.wizard_import import ImportedWizardDefaults
from venus_evcharger.bootstrap.wizard_cli_imports import (
    clone_import_path as _clone_import_path_impl,
    empty_imported_defaults as _empty_imported_defaults_impl,
    resolve_import_path as _resolve_import_path_impl,
    resolve_imported_defaults,
    resume_import_path as _resume_import_path_impl,
)
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
from venus_evcharger.bootstrap.wizard_policy_guidance import policy_defaults, prompt_policy_defaults
from venus_evcharger.bootstrap.wizard_cli_non_interactive import (
    non_interactive_answers as _non_interactive_answers_impl,
    non_interactive_backend as _non_interactive_backend_impl,
    non_interactive_charger_preset as _non_interactive_charger_preset_impl,
    non_interactive_device_instance as _non_interactive_device_instance_impl,
    non_interactive_digest_auth as _non_interactive_digest_auth_impl,
    non_interactive_phase as _non_interactive_phase_impl,
    non_interactive_policy_mode as _non_interactive_policy_mode_impl,
    non_interactive_profile as _non_interactive_profile_impl,
    non_interactive_topology_preset as _non_interactive_topology_preset_impl,
    non_interactive_string as _non_interactive_string_impl,
    resolved_backend as _resolved_backend_impl,
)
from venus_evcharger.bootstrap.wizard_support import (
    NATIVE_CHARGER_VALUES,
    PHASE_SWITCH_CHARGER_VALUES,
    POLICY_VALUES,
    PROFILE_LABELS,
    PROFILE_VALUES,
    TOPOLOGY_PRESET_VALUES,
    TRANSPORT_VALUES,
    backend_requires_transport,
    host_from_input,
    topology_uses_cerbo_relay,
)
from venus_evcharger.bootstrap.wizard_transport_guidance import (
    SWITCH_GROUP_PHASE_LAYOUT_VALUES,
    non_interactive_transport_inputs,
    preset_specific_defaults,
    prompt_preset_specific_defaults,
    prompt_transport_inputs,
)

__all__ = ["build_answers", "build_parser", "prompt_yes_no", "result_text"]

_BackendPromptSpec = tuple[tuple[str, ...], WizardChargerBackend]


def _empty_imported_defaults() -> ImportedWizardDefaults:
    return _empty_imported_defaults_impl()


def _prompt_text(prompt: str, default: str) -> str:
    value = input(f"{prompt} [{default}]: ").strip()
    return value or default


def _prompt_password(default: str) -> str:
    if default and prompt_yes_no("Reuse imported password?", True):
        return default
    return getpass.getpass("Password: ")


def prompt_yes_no(prompt: str, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    value = input(f"{prompt} [{suffix}]: ").strip().lower()
    if not value:
        return default
    return value in ("y", "yes", "1", "true", "on")


def _choice_from_raw(raw: str, choices: tuple[str, ...]) -> str | None:
    if raw.isdigit():
        numeric = int(raw)
        if 1 <= numeric <= len(choices):
            return choices[numeric - 1]
    return raw if raw in choices else None


def _prompt_choice_input(default: str | None) -> str:
    return input(f"Select [{default or 1}]: ").strip()


def _prompt_choice(prompt: str, choices: tuple[str, ...], labels: dict[str, str] | None = None, default: str | None = None) -> str:
    print(prompt)
    for index, choice in enumerate(choices, start=1):
        label = labels.get(choice, choice) if labels is not None else choice
        print(f"  {index}. {label}")
    while True:
        resolved = _resolved_choice_input(_prompt_choice_input(default), choices, default)
        if resolved is not None:
            return resolved
        print("Invalid selection, please try again.")


def _resolved_choice_input(raw: str, choices: tuple[str, ...], default: str | None) -> str | None:
    if not raw and default is not None:
        return default
    return _choice_from_raw(raw, choices)


def _resume_import_path(namespace: argparse.Namespace) -> Path | None:
    return _resume_import_path_impl(namespace)


def _clone_import_path(namespace: argparse.Namespace) -> Path | None:
    return _clone_import_path_impl(namespace)


def _resolve_import_path(namespace: argparse.Namespace) -> Path | None:
    return _resolve_import_path_impl(namespace)


def _interactive_profile(namespace: argparse.Namespace, imported: ImportedWizardDefaults) -> WizardProfile:
    labels: dict[str, str] = {key: value for key, value in PROFILE_LABELS}
    raw_profile = namespace.profile or imported.profile or _prompt_choice("Choose the setup topology:", PROFILE_VALUES, labels, "simple_relay")
    return optional_choice(raw_profile, WIZARD_PROFILES, "profile") or "simple_relay"


def _interactive_backend(
    namespace: argparse.Namespace,
    profile: WizardProfile,
    imported: ImportedWizardDefaults,
    topology_preset: str | None,
) -> WizardChargerBackend | None:
    backend = optional_choice(namespace.charger_backend or default_backend(profile, imported), WIZARD_CHARGER_BACKENDS, "charger backend")
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
    raw_policy = namespace.policy_mode or _prompt_choice("Choose the initial policy mode:", POLICY_VALUES, default=imported.policy_mode or "manual")
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


def _interactive_answers(namespace: argparse.Namespace, imported: ImportedWizardDefaults | None) -> WizardAnswers:
    imported = imported or _empty_imported_defaults()
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


def _non_interactive_profile(namespace: argparse.Namespace, imported_defaults: ImportedWizardDefaults) -> WizardProfile:
    return _non_interactive_profile_impl(namespace, imported_defaults)


def _non_interactive_policy_mode(namespace: argparse.Namespace, imported_defaults: ImportedWizardDefaults) -> WizardPolicyMode:
    return _non_interactive_policy_mode_impl(namespace, imported_defaults)


def _non_interactive_digest_auth(namespace: argparse.Namespace, imported_defaults: ImportedWizardDefaults) -> bool:
    return _non_interactive_digest_auth_impl(namespace, imported_defaults)


def _non_interactive_answers(namespace: argparse.Namespace, imported: ImportedWizardDefaults | None) -> WizardAnswers:
    return _non_interactive_answers_impl(namespace, imported or _empty_imported_defaults())


def _non_interactive_topology_preset(namespace: argparse.Namespace, imported_defaults: ImportedWizardDefaults, profile: WizardProfile) -> str | None:
    return _non_interactive_topology_preset_impl(namespace, imported_defaults, profile)


def _non_interactive_backend(
    namespace: argparse.Namespace,
    imported: ImportedWizardDefaults | None,
    profile: WizardProfile,
    topology_preset: str | None,
) -> WizardChargerBackend | None:
    return _non_interactive_backend_impl(namespace, imported, profile, topology_preset)


def _resolved_backend(
    topology_preset: str | None,
    charger_preset: str | None,
    backend: WizardChargerBackend | None,
) -> WizardChargerBackend | None:
    return _resolved_backend_impl(topology_preset, charger_preset, backend)


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


def _non_interactive_charger_preset(
    namespace: argparse.Namespace,
    imported_defaults: ImportedWizardDefaults,
    backend: WizardChargerBackend | None,
) -> str | None:
    return _non_interactive_charger_preset_impl(namespace, imported_defaults, backend)


def _prompt_optional_choice(
    prompt: str,
    choices: tuple[str, ...],
    labels: dict[str, str],
    default: str | None,
) -> str | None:
    selected = _prompt_choice(prompt, choices, labels, default or "none")
    return None if selected == "none" else selected


def _non_interactive_device_instance(namespace: argparse.Namespace, imported_defaults: ImportedWizardDefaults) -> int:
    return _non_interactive_device_instance_impl(namespace, imported_defaults)


def _non_interactive_phase(namespace: argparse.Namespace, imported_defaults: ImportedWizardDefaults) -> str:
    return _non_interactive_phase_impl(namespace, imported_defaults)


def _non_interactive_string(namespace_value: str | None, imported_value: str | None) -> str:
    return _non_interactive_string_impl(namespace_value, imported_value)


def build_answers(namespace: argparse.Namespace) -> tuple[WizardAnswers, ImportedWizardDefaults | None]:
    imported = resolve_imported_defaults(namespace)
    answers = _non_interactive_answers(namespace, imported) if namespace.non_interactive else _interactive_answers(namespace, imported)
    return answers, imported
