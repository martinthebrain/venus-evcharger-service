# SPDX-License-Identifier: GPL-3.0-or-later
"""Transport and preset-specific prompt helpers for the setup wizard."""

from __future__ import annotations

from typing import Callable

from venus_evcharger.bootstrap.wizard_charger_presets import preset_transport_port, preset_transport_unit_id
from venus_evcharger.bootstrap.wizard_choices import optional_choice
from venus_evcharger.bootstrap.wizard_import import ImportedWizardDefaults
from venus_evcharger.bootstrap.wizard_models import WIZARD_TRANSPORT_KINDS, WizardChargerBackend, WizardProfile, WizardTransportKind
from venus_evcharger.bootstrap.wizard_support import TRANSPORT_VALUES, default_transport_kind, host_from_input

SWITCH_GROUP_PHASE_LAYOUT_VALUES = ("P1,P1_P2,P1_P2_P3", "P1,P1_P2_P3")
GOE_REQUEST_TIMEOUT_DEFAULT = 2.0
_PHASE_LAYOUT_PRESETS = {"goe-external-switch-group"}
_SWITCH_GROUP_PRESET_FRAGMENT = "switch-group"

PromptText = Callable[[str, str], str]
PromptChoice = Callable[[str, tuple[str, ...], dict[str, str] | None, str | None], str]


def prompt_transport_inputs(
    backend: WizardChargerBackend | None,
    charger_preset: str | None,
    host_input: str,
    imported: ImportedWizardDefaults,
    *,
    prompt_choice: PromptChoice,
    prompt_text: PromptText,
) -> tuple[WizardTransportKind, str, int, str, int]:
    default_kind = _required_transport_kind(imported.transport_kind or default_transport_kind(backend))
    raw_transport_kind = prompt_choice("Choose the transport:", TRANSPORT_VALUES, None, default_kind)
    transport_kind = _required_transport_kind(raw_transport_kind)
    defaults = _transport_defaults(imported, host_input, charger_preset, transport_kind)
    if transport_kind == "tcp":
        return _prompt_tcp_transport(prompt_text, transport_kind, defaults)
    return _prompt_serial_transport(prompt_text, host_input, transport_kind, defaults)


def _transport_defaults(
    imported: ImportedWizardDefaults,
    host_input: str,
    charger_preset: str | None,
    transport_kind: WizardTransportKind,
) -> tuple[str, str, str, str]:
    return (
        _transport_host_default(imported, host_input),
        _transport_port_default(imported, charger_preset, transport_kind),
        _transport_device_default(imported),
        _transport_unit_id_default(imported, charger_preset),
    )


def _transport_host_default(imported: ImportedWizardDefaults, host_input: str) -> str:
    """Return the default transport host for prompted transport configuration."""
    return imported.transport_host or host_from_input(host_input)


def _transport_port_default(
    imported: ImportedWizardDefaults,
    charger_preset: str | None,
    transport_kind: WizardTransportKind,
) -> str:
    """Return the default transport TCP port as text."""
    return str(imported.transport_port or preset_transport_port(charger_preset, transport_kind) or 502)


def _transport_device_default(imported: ImportedWizardDefaults) -> str:
    """Return the default serial transport device path."""
    return imported.transport_device or "/dev/ttyUSB0"


def _transport_unit_id_default(imported: ImportedWizardDefaults, charger_preset: str | None) -> str:
    """Return the default Modbus unit identifier as text."""
    return str(imported.transport_unit_id or preset_transport_unit_id(charger_preset) or 1)


def _prompt_tcp_transport(
    prompt_text: PromptText,
    transport_kind: WizardTransportKind,
    defaults: tuple[str, str, str, str],
) -> tuple[WizardTransportKind, str, int, str, int]:
    default_host, default_port, default_device, default_unit_id = defaults
    return (
        transport_kind,
        prompt_text("Modbus TCP host", default_host),
        int(prompt_text("Modbus TCP port", default_port)),
        default_device,
        int(prompt_text("Modbus unit id", default_unit_id)),
    )


def _prompt_serial_transport(
    prompt_text: PromptText,
    host_input: str,
    transport_kind: WizardTransportKind,
    defaults: tuple[str, str, str, str],
) -> tuple[WizardTransportKind, str, int, str, int]:
    _, default_port, default_device, default_unit_id = defaults
    return (
        transport_kind,
        host_from_input(host_input),
        int(default_port),
        prompt_text("Serial device", default_device),
        int(prompt_text("Modbus unit id", default_unit_id)),
    )


def _namespace_int(namespace: object, key: str, default: int) -> int:
    value = getattr(namespace, key)
    return int(value if value is not None else default)


def _required_transport_kind(raw_value: object) -> WizardTransportKind:
    transport_kind = optional_choice(raw_value, WIZARD_TRANSPORT_KINDS, "transport")
    if transport_kind is None:
        raise ValueError("Transport must not be empty")
    return transport_kind


def non_interactive_transport_inputs(
    namespace: object,
    backend: WizardChargerBackend | None,
    charger_preset: str | None,
    host_input: str,
    imported: ImportedWizardDefaults,
) -> tuple[WizardTransportKind, str, int, str, int]:
    transport_kind = _non_interactive_transport_kind(namespace, imported, backend)
    transport_host = _non_interactive_transport_host(namespace, imported, host_input)
    transport_device = _non_interactive_transport_device(namespace, imported)
    default_port = imported.transport_port or preset_transport_port(charger_preset, transport_kind) or 502
    default_unit_id = imported.transport_unit_id or preset_transport_unit_id(charger_preset) or 1
    return (
        transport_kind,
        transport_host,
        _namespace_int(namespace, "transport_port", default_port),
        transport_device,
        _namespace_int(namespace, "transport_unit_id", default_unit_id),
    )


def _non_interactive_transport_kind(
    namespace: object,
    imported: ImportedWizardDefaults,
    backend: WizardChargerBackend | None,
) -> WizardTransportKind:
    return _required_transport_kind(getattr(namespace, "transport") or imported.transport_kind or default_transport_kind(backend))


def _non_interactive_transport_host(namespace: object, imported: ImportedWizardDefaults, host_input: str) -> str:
    return getattr(namespace, "transport_host") or imported.transport_host or host_from_input(host_input)


def _non_interactive_transport_device(namespace: object, imported: ImportedWizardDefaults) -> str:
    return getattr(namespace, "transport_device") or imported.transport_device or "/dev/ttyUSB0"


def preset_specific_defaults(
    namespace: object,
    imported: ImportedWizardDefaults,
    *,
    backend: WizardChargerBackend | None,
    topology_preset: str | None,
    charger_preset: str | None = None,
) -> tuple[float | None, str]:
    timeout = _request_timeout_seconds(namespace, imported, backend)
    phase_layout = getattr(namespace, "switch_group_phase_layout") or imported.switch_group_phase_layout or SWITCH_GROUP_PHASE_LAYOUT_VALUES[0]
    if not _supports_phase_layout(topology_preset):
        phase_layout = SWITCH_GROUP_PHASE_LAYOUT_VALUES[0]
    return timeout, str(phase_layout)


def _request_timeout_seconds(
    namespace: object,
    imported: ImportedWizardDefaults,
    backend: WizardChargerBackend | None,
) -> float | None:
    timeout = getattr(namespace, "request_timeout_seconds")
    if timeout is not None:
        return float(timeout)
    if imported.request_timeout_seconds is not None:
        return float(imported.request_timeout_seconds)
    return GOE_REQUEST_TIMEOUT_DEFAULT if backend == "goe_charger" else None


def _supports_phase_layout(topology_preset: str | None) -> bool:
    if topology_preset is None:
        return False
    return topology_preset in _PHASE_LAYOUT_PRESETS or _SWITCH_GROUP_PRESET_FRAGMENT in topology_preset


def prompt_preset_specific_defaults(
    namespace: object,
    imported: ImportedWizardDefaults,
    *,
    profile: WizardProfile,
    backend: WizardChargerBackend | None,
    topology_preset: str | None,
    charger_preset: str | None = None,
    prompt_choice: PromptChoice,
    prompt_text: PromptText,
) -> tuple[float | None, str]:
    request_timeout_seconds, phase_layout = preset_specific_defaults(
        namespace,
        imported,
        backend=backend,
        topology_preset=topology_preset,
        charger_preset=charger_preset,
    )
    if backend == "goe_charger" and getattr(namespace, "request_timeout_seconds") is None:
        request_timeout_seconds = float(prompt_text("go-e request timeout seconds", f"{request_timeout_seconds or GOE_REQUEST_TIMEOUT_DEFAULT:g}"))
    if _should_prompt_phase_layout(profile, topology_preset, namespace):
        phase_layout = prompt_choice(
            "Choose the external phase-switch layout:",
            SWITCH_GROUP_PHASE_LAYOUT_VALUES,
            {
                "P1,P1_P2,P1_P2_P3": "1P -> 2P -> 3P staged switching",
                "P1,P1_P2_P3": "1P -> 3P switching only",
            },
            phase_layout,
        )
    return request_timeout_seconds, phase_layout


def _should_prompt_phase_layout(profile: WizardProfile, topology_preset: str | None, namespace: object) -> bool:
    return (
        profile in {"hybrid_topology", "multi_adapter_topology"}
        and _supports_phase_layout(topology_preset)
        and getattr(namespace, "switch_group_phase_layout") is None
    )
