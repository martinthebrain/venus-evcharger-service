# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional setup wizard for initial wallbox configuration."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from venus_evcharger.backend.factory import build_service_backends
from venus_evcharger.backend.probe import probe_meter_backend, probe_switch_backend, read_charger_backend
from venus_evcharger.bootstrap.wizard_cli import build_answers, build_parser, prompt_yes_no
from venus_evcharger.bootstrap.wizard_cli_output import result_text
from venus_evcharger.bootstrap.wizard_capacity import (
    resolved_energy_capacity_overrides as _resolved_energy_capacity_overrides,
    resolved_energy_capacity_wh as _resolved_energy_capacity_wh_impl,
)
from venus_evcharger.bootstrap.wizard_energy import merged_recommendation_prefixes
from venus_evcharger.bootstrap.wizard_guidance import compatibility_warnings, probe_roles
from venus_evcharger.bootstrap.wizard_inventory_cli import run_inventory_editor
from venus_evcharger.bootstrap.wizard_inventory_support import (
    inventory_action_path,
    inventory_summary_text,
    load_inventory,
)
from venus_evcharger.bootstrap.wizard_models import WizardAnswers, WizardResult, WizardTransportKind
from venus_evcharger.bootstrap.wizard_render import (
    answer_defaults,
    append_backends,
    default_config_path,
    default_template_path,
    materialize_rendered_setup,
    redact_sensitive_rendered_setup,
    replace_assignment,
    render_wizard_config,
    sensitive_defaults_from_config_text,
    upsert_default_assignments,
    write_generated_files,
)
from venus_evcharger.bootstrap.wizard_runtime import (
    _existing_output_paths,
    _live_connectivity_payload_with_hooks,
    configure_wallbox as _runtime_configure_wallbox,
)

_replace_assignment = replace_assignment
_append_backends = append_backends
_write_generated_files = write_generated_files


def _resolve_live_check(namespace: argparse.Namespace) -> bool:
    if namespace.live_check or getattr(namespace, "probe_roles", None):
        return True
    if namespace.non_interactive or namespace.yes:
        return False
    return prompt_yes_no("Run optional live connectivity checks now?", False)


def _live_connectivity_payload(
    main_path: Path,
    selected_roles: tuple[str, ...] | None,
    secret_defaults: dict[str, str] | None = None,
) -> dict[str, object]:
    """Run live connectivity checks through patchable module-level hook targets."""
    return _live_connectivity_payload_with_hooks(
        main_path,
        selected_roles,
        secret_defaults=secret_defaults,
        build_backends_fn=build_service_backends,
        probe_meter_fn=probe_meter_backend,
        probe_switch_fn=probe_switch_backend,
        read_charger_fn=read_charger_backend,
    )


def _live_check_rendered_setup(
    config_text: str,
    adapter_files: dict[str, str],
    config_name: str,
    selected_roles: tuple[str, ...] | None,
) -> dict[str, object]:
    """Materialize one rendered setup and probe it through patchable module-level hooks."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        redacted_config_text, redacted_adapter_files = redact_sensitive_rendered_setup(config_text, adapter_files)
        main_path = materialize_rendered_setup(redacted_config_text, temp_path, redacted_adapter_files, config_name)
        return _live_connectivity_payload(main_path, selected_roles, sensitive_defaults_from_config_text(config_text))


def _resolved_energy_capacity_wh(
    namespace: argparse.Namespace,
    recommendation_prefixes: tuple[str, ...],
) -> float | None:
    """Resolve one capacity prompt through patchable module-level prompt hooks."""
    return _resolved_energy_capacity_wh_impl(
        namespace,
        recommendation_prefixes,
        prompt_yes_no_fn=prompt_yes_no,
        input_fn=input,
    )


def _interactive_write_confirmed(preview: WizardResult, existing_files: tuple[str, ...]) -> bool:
    print(result_text(preview))
    prompt = "Write config now and create backups of existing files?" if existing_files else "Write config now?"
    return prompt_yes_no(prompt, not bool(existing_files))


def _non_interactive_write_allowed(namespace: object, existing_files: tuple[str, ...]) -> bool:
    if not getattr(namespace, "non_interactive"):
        return False
    if existing_files and not getattr(namespace, "force"):
        raise ValueError(
            "Refusing to overwrite existing files in --non-interactive mode without --force: "
            + ", ".join(existing_files)
        )
    return True


def _skip_write_confirmation(namespace: object, existing_files: tuple[str, ...]) -> bool:
    if getattr(namespace, "dry_run"):
        return True
    if _non_interactive_write_allowed(namespace, existing_files):
        return True
    return bool(getattr(namespace, "yes"))


def _confirm_write(namespace: object, preview: WizardResult, existing_files: tuple[str, ...]) -> None:
    if _skip_write_confirmation(namespace, existing_files):
        return
    if not _interactive_write_confirmed(preview, existing_files):
        raise ValueError("Wizard write cancelled by user")


def configure_wallbox(
    answers: WizardAnswers,
    *,
    config_path: Path,
    template_path: Path,
    dry_run: bool = False,
    live_check: bool = False,
    selected_probe_roles: tuple[str, ...] | None = None,
    imported_from: str | None = None,
    energy_recommendation_prefix: str | tuple[str, ...] | list[str] | None = None,
    huawei_recommendation_prefix: str | tuple[str, ...] | list[str] | None = None,
    apply_suggested_energy_merge: bool = False,
    suggested_energy_capacity_wh: float | None = None,
    suggested_energy_capacity_overrides: dict[str, float] | None = None,
) -> WizardResult:
    """Configure one setup and keep module-level live-check hooks patchable in tests."""
    return _runtime_configure_wallbox(
        answers,
        config_path=config_path,
        template_path=template_path,
        dry_run=dry_run,
        live_check=live_check,
        selected_probe_roles=selected_probe_roles,
        imported_from=imported_from,
        energy_recommendation_prefix=energy_recommendation_prefix,
        huawei_recommendation_prefix=huawei_recommendation_prefix,
        apply_suggested_energy_merge=apply_suggested_energy_merge,
        suggested_energy_capacity_wh=suggested_energy_capacity_wh,
        suggested_energy_capacity_overrides=suggested_energy_capacity_overrides,
        live_check_runner=_live_check_rendered_setup,
    )


def _run_wizard(namespace: argparse.Namespace) -> WizardResult:
    answers, imported = build_answers(namespace)
    imported_from = imported.imported_from if imported is not None else None
    live_check = _resolve_live_check(namespace)
    selected_probe_roles = probe_roles(namespace)
    recommendation_prefixes = merged_recommendation_prefixes(
        getattr(namespace, "energy_recommendation_prefix", None),
        getattr(namespace, "huawei_recommendation_prefix", None),
    )
    suggested_energy_capacity_wh = _resolved_energy_capacity_wh(namespace, recommendation_prefixes)
    suggested_energy_capacity_overrides = _resolved_energy_capacity_overrides(namespace)
    preview = configure_wallbox(
        answers,
        config_path=Path(namespace.config_path),
        template_path=Path(namespace.template_path),
        dry_run=True,
        live_check=live_check,
        selected_probe_roles=selected_probe_roles,
        imported_from=imported_from,
        energy_recommendation_prefix=recommendation_prefixes,
        huawei_recommendation_prefix=recommendation_prefixes,
        apply_suggested_energy_merge=getattr(namespace, "apply_energy_merge", False),
        suggested_energy_capacity_wh=suggested_energy_capacity_wh,
        suggested_energy_capacity_overrides=suggested_energy_capacity_overrides,
    )
    existing_files = _existing_output_paths(Path(namespace.config_path), preview.generated_files)
    _confirm_write(namespace, preview, existing_files)
    if namespace.dry_run:
        return preview
    return configure_wallbox(
        answers,
        config_path=Path(namespace.config_path),
        template_path=Path(namespace.template_path),
        dry_run=False,
        live_check=live_check,
        selected_probe_roles=selected_probe_roles,
        imported_from=imported_from,
        energy_recommendation_prefix=recommendation_prefixes,
        huawei_recommendation_prefix=recommendation_prefixes,
        apply_suggested_energy_merge=getattr(namespace, "apply_energy_merge", False),
        suggested_energy_capacity_wh=suggested_energy_capacity_wh,
        suggested_energy_capacity_overrides=suggested_energy_capacity_overrides,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser(str(default_config_path()), str(default_template_path()))
    namespace = parser.parse_args(argv)
    try:
        if getattr(namespace, "inventory_action", None):
            _print_inventory_action_result(namespace, run_inventory_editor(namespace))
            return 0
        result = _run_wizard(namespace)
    except ValueError as exc:
        _print_main_error(namespace, exc)
        return 2
    _print_main_result(namespace, result)
    return 0


def _print_inventory_action_result(namespace: argparse.Namespace, payload: dict[str, object]) -> None:
    """Print the result of one inventory-editor action."""
    if namespace.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    current_inventory_path = inventory_action_path(namespace)
    inventory = load_inventory(current_inventory_path)
    print(inventory_summary_text(current_inventory_path, inventory))


def _print_main_error(namespace: argparse.Namespace, exc: ValueError) -> None:
    """Print one CLI error in text or JSON form."""
    if getattr(namespace, "json", False):
        print(json.dumps({"error": str(exc)}, indent=2, sort_keys=True))
        return
    print(f"Error: {exc}", file=sys.stderr)


def _print_main_result(namespace: argparse.Namespace, result: WizardResult) -> None:
    """Print one successful wizard result in text or JSON form."""
    if namespace.json:
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
        return
    print(result_text(result))


__all__ = [
    "WizardAnswers",
    "configure_wallbox",
    "default_template_path",
    "default_config_path",
    "main",
    "_live_connectivity_payload",
    "_live_check_rendered_setup",
    "_replace_assignment",
    "_append_backends",
    "_write_generated_files",
    "_non_interactive_write_allowed",
    "_confirm_write",
    "probe_meter_backend",
    "probe_switch_backend",
    "read_charger_backend",
    "prompt_yes_no",
    "_interactive_write_confirmed",
]


if __name__ == "__main__":
    raise SystemExit(main())
