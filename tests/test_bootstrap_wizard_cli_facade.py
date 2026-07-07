# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import argparse
import unittest
from unittest.mock import Mock, patch

from venus_evcharger.bootstrap import wizard_cli
from tests.wizard_branch_runtime_cases_common import _imported_defaults


class BootstrapWizardCliFacadeTests(unittest.TestCase):
    def test_build_answers_passes_imported_defaults_to_interactive_builder(self) -> None:
        imported = _imported_defaults(imported_from="/tmp/import.json")
        expected_answers = Mock()
        namespace = argparse.Namespace(non_interactive=False)

        with (
            patch("venus_evcharger.bootstrap.wizard_cli.resolve_imported_defaults", return_value=imported),
            patch("venus_evcharger.bootstrap.wizard_cli.interactive_answers", return_value=expected_answers) as interactive,
            patch("venus_evcharger.bootstrap.wizard_cli.non_interactive_answers") as non_interactive,
        ):
            answers, returned_imported = wizard_cli.build_answers(namespace)

        self.assertIs(answers, expected_answers)
        self.assertIs(returned_imported, imported)
        interactive.assert_called_once_with(namespace, imported)
        non_interactive.assert_not_called()

    def test_build_answers_uses_empty_defaults_for_noninteractive_without_import(self) -> None:
        expected_answers = Mock()
        namespace = argparse.Namespace(non_interactive=True)

        with (
            patch("venus_evcharger.bootstrap.wizard_cli.resolve_imported_defaults", return_value=None),
            patch("venus_evcharger.bootstrap.wizard_cli.empty_imported_defaults", return_value="empty") as empty_defaults,
            patch("venus_evcharger.bootstrap.wizard_cli.non_interactive_answers", return_value=expected_answers) as non_interactive,
            patch("venus_evcharger.bootstrap.wizard_cli.interactive_answers") as interactive,
        ):
            answers, returned_imported = wizard_cli.build_answers(namespace)

        self.assertIs(answers, expected_answers)
        self.assertIsNone(returned_imported)
        empty_defaults.assert_called_once_with()
        non_interactive.assert_called_once_with(namespace, "empty")
        interactive.assert_not_called()


if __name__ == "__main__":
    unittest.main()
