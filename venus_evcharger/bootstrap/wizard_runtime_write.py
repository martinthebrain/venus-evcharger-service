# SPDX-License-Identifier: GPL-3.0-or-later
"""Write-confirmation guards for the setup wizard runtime."""

from __future__ import annotations

from pathlib import Path

from venus_evcharger.bootstrap.wizard_cli import prompt_yes_no
from venus_evcharger.bootstrap.wizard_models import WizardResult


def existing_output_paths(config_path: Path, generated_files: tuple[str, ...]) -> tuple[str, ...]:
    existing = []
    for relative_path in generated_files:
        candidate = config_path.parent / relative_path
        if candidate.exists():
            existing.append(str(candidate))
    return tuple(existing)


def interactive_write_confirmed(preview: WizardResult, existing_files: tuple[str, ...]) -> bool:
    from venus_evcharger.bootstrap.wizard_cli_output import result_text

    print(result_text(preview))
    prompt = "Write config now and create backups of existing files?" if existing_files else "Write config now?"
    return prompt_yes_no(prompt, not bool(existing_files))


def non_interactive_write_allowed(namespace: object, existing_files: tuple[str, ...]) -> bool:
    if not getattr(namespace, "non_interactive"):
        return False
    if existing_files and not getattr(namespace, "force"):
        raise ValueError(
            "Refusing to overwrite existing files in --non-interactive mode without --force: "
            + ", ".join(existing_files)
        )
    return True


def skip_write_confirmation(namespace: object, existing_files: tuple[str, ...]) -> bool:
    if getattr(namespace, "dry_run"):
        return True
    if non_interactive_write_allowed(namespace, existing_files):
        return True
    return bool(getattr(namespace, "yes"))


def confirm_write(namespace: object, preview: WizardResult, existing_files: tuple[str, ...]) -> None:
    if skip_write_confirmation(namespace, existing_files):
        return
    if not interactive_write_confirmed(preview, existing_files):
        raise ValueError("Wizard write cancelled by user")
