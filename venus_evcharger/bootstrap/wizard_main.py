# SPDX-License-Identifier: GPL-3.0-or-later
"""Command-line orchestration for the setup wizard."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from venus_evcharger.bootstrap.wizard_capacity import (
    resolved_energy_capacity_overrides,
    resolved_energy_capacity_wh as _resolved_energy_capacity_wh_impl,
)
from venus_evcharger.bootstrap.wizard_cli import build_answers, build_parser, prompt_yes_no
from venus_evcharger.bootstrap.wizard_cli_output import result_text
from venus_evcharger.bootstrap.wizard_energy import merged_recommendation_prefixes
from venus_evcharger.bootstrap.wizard_guidance import probe_roles
from venus_evcharger.bootstrap.wizard_inventory_cli import run_inventory_editor
from venus_evcharger.bootstrap.wizard_inventory_support import (
    inventory_action_path,
    inventory_summary_text,
    load_inventory,
)
from venus_evcharger.bootstrap.wizard_models import WizardResult
from venus_evcharger.bootstrap.wizard_render import default_config_path, default_template_path
from venus_evcharger.bootstrap.wizard_runtime import configure_wallbox
from venus_evcharger.bootstrap.wizard_runtime_write import confirm_write, existing_output_paths


def resolve_live_check(namespace: argparse.Namespace) -> bool:
    """Resolve whether the CLI should perform optional live checks."""
    if namespace.live_check or getattr(namespace, "probe_roles", None):
        return True
    if namespace.non_interactive or namespace.yes:
        return False
    return prompt_yes_no("Run optional live connectivity checks now?", False)


def resolved_energy_capacity_wh(
    namespace: argparse.Namespace,
    recommendation_prefixes: tuple[str, ...],
) -> float | None:
    """Resolve one optional energy-capacity prompt for CLI execution."""
    return _resolved_energy_capacity_wh_impl(
        namespace,
        recommendation_prefixes,
        prompt_yes_no_fn=prompt_yes_no,
        input_fn=input,
    )


def run_wizard(namespace: argparse.Namespace) -> WizardResult:
    """Run one complete CLI wizard transaction."""
    answers, imported = build_answers(namespace)
    imported_from = imported.imported_from if imported is not None else None
    live_check = resolve_live_check(namespace)
    selected_probe_roles = probe_roles(namespace)
    recommendation_prefixes = merged_recommendation_prefixes(
        getattr(namespace, "energy_recommendation_prefix", None),
        getattr(namespace, "huawei_recommendation_prefix", None),
    )
    suggested_energy_capacity_wh = resolved_energy_capacity_wh(namespace, recommendation_prefixes)
    suggested_energy_capacity_overrides = resolved_energy_capacity_overrides(namespace)
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
    existing_files = existing_output_paths(Path(namespace.config_path), preview.generated_files)
    confirm_write(namespace, preview, existing_files)
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
    """Run the setup wizard CLI."""
    parser = build_parser(str(default_config_path()), str(default_template_path()))
    namespace = parser.parse_args(argv)
    try:
        if getattr(namespace, "inventory_action", None):
            print_inventory_action_result(namespace, run_inventory_editor(namespace))
            return 0
        result = run_wizard(namespace)
    except ValueError as exc:
        print_main_error(namespace, exc)
        return 2
    print_main_result(namespace, result)
    return 0


def print_inventory_action_result(namespace: argparse.Namespace, payload: dict[str, object]) -> None:
    """Print the result of one inventory-editor action."""
    if namespace.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    current_inventory_path = inventory_action_path(namespace)
    inventory = load_inventory(current_inventory_path)
    print(inventory_summary_text(current_inventory_path, inventory))


def print_main_error(namespace: argparse.Namespace, exc: ValueError) -> None:
    """Print one CLI error in text or JSON form."""
    if namespace.json:
        print(json.dumps({"error": str(exc), "code": "wizard-error"}, indent=2, sort_keys=True))
        return
    print(f"Error: {exc}", file=sys.stderr)


def print_main_result(namespace: argparse.Namespace, result: WizardResult) -> None:
    """Print one successful wizard result in text or JSON form."""
    if namespace.json:
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
        return
    print(result_text(result))
