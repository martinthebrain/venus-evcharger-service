# SPDX-License-Identifier: GPL-3.0-or-later
"""Public setup-wizard CLI facade."""

from __future__ import annotations

import argparse

from venus_evcharger.bootstrap.wizard_cli_imports import empty_imported_defaults, resolve_imported_defaults
from venus_evcharger.bootstrap.wizard_cli_interactive import interactive_answers
from venus_evcharger.bootstrap.wizard_cli_non_interactive import non_interactive_answers
from venus_evcharger.bootstrap.wizard_cli_output import result_text
from venus_evcharger.bootstrap.wizard_cli_parser import build_parser
from venus_evcharger.bootstrap.wizard_cli_prompts import prompt_yes_no
from venus_evcharger.bootstrap.wizard_import import ImportedWizardDefaults
from venus_evcharger.bootstrap.wizard_models import WizardAnswers

__all__ = ["build_answers", "build_parser", "prompt_yes_no", "result_text"]


def build_answers(namespace: argparse.Namespace) -> tuple[WizardAnswers, ImportedWizardDefaults | None]:
    imported = resolve_imported_defaults(namespace)
    if namespace.non_interactive:
        answers = non_interactive_answers(namespace, imported or empty_imported_defaults())
    else:
        answers = interactive_answers(namespace, imported)
    return answers, imported
