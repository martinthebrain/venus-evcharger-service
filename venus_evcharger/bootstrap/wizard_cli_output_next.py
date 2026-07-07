# SPDX-License-Identifier: GPL-3.0-or-later
"""Next-step and setup-note sections for wizard result text."""

from __future__ import annotations

from pathlib import PurePosixPath

from venus_evcharger.bootstrap.wizard_models import WizardResult
from venus_evcharger.bootstrap.wizard_support import policy_mode_note, topology_uses_cerbo_relay

_METER_RELAY_PRESET_MARKERS = ("shelly", "tasmota", "tuya")


def result_setup_note_lines(result: WizardResult) -> list[str]:
    notes = [
        policy_mode_note(result.policy_mode),
        *_topology_setup_notes(result),
    ]
    return ["Setup notes:", *(f"  - {item}" for item in notes)]


def _topology_setup_notes(result: WizardResult) -> tuple[str, ...]:
    return tuple(
        message
        for condition, message in (
            (
                topology_uses_cerbo_relay(result.topology_preset),
                "Cerbo GX relay switching sets the Venus OS relay function to Manual before changing relay state.",
            ),
            (
                _is_meter_relay_setup(result.profile, result.topology_preset),
                "Meter/relay setups infer charging from power and energy deltas, not from vehicle communication.",
            ),
            (
                result.topology_preset is not None and "switch-group" in result.topology_preset,
                "External switch-group adapters own phase/contact switching only; the charger backend still owns charger control.",
            ),
            (
                _has_native_charger_backend(result.charger_backend),
                "Native charger backends can use charger-side status/control where the device supports it.",
            ),
        )
        if condition
    )


def _is_meter_relay_setup(profile: str, topology_preset: str | None) -> bool:
    if profile == "simple_relay" and topology_preset is None:
        return True
    if topology_preset is None:
        return False
    return any(marker in topology_preset for marker in _METER_RELAY_PRESET_MARKERS)


def _has_native_charger_backend(charger_backend: str | None) -> bool:
    return charger_backend in {"goe_charger", "modbus_charger", "simpleevse_charger", "smartevse_charger"}


def result_next_step_lines(result: WizardResult) -> list[str]:
    lines = ["Next steps:"]
    lines.extend(_optional_line(result.dry_run, "  - Review this preview, then rerun without --dry-run to write the files."))
    lines.extend(
        _optional_line(result.manual_review, "  - Review the Manual review items below before enabling unattended charging.")
    )
    lines.append(f"  - Validate the full setup: python3 -m venus_evcharger.backend.probe validate-wallbox {result.config_path}")
    lines.extend(
        _optional_line(
            _has_adapter_files(result),
            "  - Validate generated adapter files individually with: python3 -m venus_evcharger.backend.probe validate <adapter.ini>",
        )
    )
    lines.extend(_live_check_next_step_lines(result))
    return lines


def _optional_line(condition: object, line: str) -> tuple[str, ...]:
    return (line,) if condition else tuple()


def _live_check_next_step_lines(result: WizardResult) -> tuple[str, ...]:
    if result.live_check is None:
        return ("  - Optional: rerun the wizard with --live-check once the devices are reachable.",)
    return _optional_line(not result.live_check.get("ok"), "  - Fix the live connectivity issues above, then rerun with --live-check.")


def result_post_install_checklist_lines(result: WizardResult) -> list[str]:
    lines = [
        "Post-install checklist:",
        "  - In the Venus GUI, confirm Mode, StartStop, AutoStart, relay state, and measured charging power.",
        "  - Start with a safe manual test before relying on unattended Auto or Scheduled charging.",
    ]
    lines.extend(
        _optional_line(
            topology_uses_cerbo_relay(result.topology_preset),
            "  - For Cerbo relay setups, confirm Relay 1/2 and NO/NC wiring match the generated config.",
        )
    )
    lines.extend(
        _optional_line(
            _is_meter_relay_setup(result.profile, result.topology_preset),
            "  - For meter/relay setups, confirm session energy starts at zero after unplug/replug.",
        )
    )
    return lines


def _has_adapter_files(result: WizardResult) -> bool:
    return any(item.endswith(".ini") and item != _config_filename(result.config_path) for item in result.generated_files)


def _config_filename(config_path: str) -> str:
    return PurePosixPath(config_path).name
