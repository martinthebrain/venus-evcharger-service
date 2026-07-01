# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest


def _load_mutation_audit_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "dev" / "run_mutation_audit.py"
    script_dir = str(script.parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    spec = importlib.util.spec_from_file_location("run_mutation_audit_under_test", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mutation_audit = _load_mutation_audit_module()


class MutationAuditScriptTests(unittest.TestCase):
    def test_parse_counts_treats_no_tests_as_attention_result(self) -> None:
        counts = mutation_audit._parse_counts(
            """
            2 killed
            1 survived
            3 no tests
            mutant-id: suspicious
            another-id: no tests
            """
        )

        self.assertEqual(counts["killed"], 2)
        self.assertEqual(counts["survived"], 1)
        self.assertEqual(counts["suspicious"], 1)
        self.assertEqual(counts["no_tests"], 4)
        self.assertEqual(mutation_audit._status_from_counts(counts), "needs_attention")

    def test_survivor_names_extracts_only_survived_mutants(self) -> None:
        self.assertEqual(
            mutation_audit._survivor_names(
                """
                package.module.x_func__mutmut_1: survived
                package.module.x_func__mutmut_2: killed
                package.module.x_func__mutmut_3: no tests
                package.module.x_func__mutmut_4: survived
                """
            ),
            [
                "package.module.x_func__mutmut_1",
                "package.module.x_func__mutmut_4",
            ],
        )

    def test_target_status_promotes_missing_test_mapping_to_attention(self) -> None:
        counts = dict.fromkeys(mutation_audit.RESULT_WORDS, 0)

        status = mutation_audit._target_status(
            1,
            0,
            counts,
            log_text="mutmut could not find any test case for any mutant",
        )

        self.assertEqual(status, "needs_attention")

    def test_exit_code_fails_on_no_tests_unless_no_fail_requested(self) -> None:
        result = SimpleNamespace(status="needs_attention")

        self.assertEqual(mutation_audit._exit_code([result], no_fail=False), 1)
        self.assertEqual(mutation_audit._exit_code([result], no_fail=True), 0)

    def test_exit_code_accepts_explicitly_not_applicable_contract_modules(self) -> None:
        result = SimpleNamespace(status="not_applicable")

        self.assertEqual(mutation_audit._exit_code([result], no_fail=False), 0)

    def test_default_mutation_config_contains_update_cycle_contract_tests(self) -> None:
        config = mutation_audit._mutmut_config_toml("venus_evcharger/update/learning_signature.py")

        self.assertIn('only_mutate = ["venus_evcharger/update/learning_signature.py"]', config)
        self.assertIn('"tests/test_venus_evcharger_update_cycle_controller.py"', config)
        self.assertIn('"tests/venus_evcharger_update_cycle_controller_cases_quindenary.py"', config)

    def test_constant_only_modules_are_reported_as_not_applicable(self) -> None:
        path = Path(__file__).resolve().parents[1] / "venus_evcharger/update/software_update_contracts.py"

        self.assertTrue(mutation_audit._is_constant_only_module(path))
        self.assertEqual(
            mutation_audit._not_applicable_mutation_target(
                Path(__file__).resolve().parents[1],
                "venus_evcharger/update/software_update_contracts.py",
            ),
            "constant-only module; mutation coverage belongs to consuming runtime modules",
        )

    def test_non_constant_modules_stay_mutation_targets(self) -> None:
        path = Path(__file__).resolve().parents[1] / "venus_evcharger/update/software_update_state.py"

        self.assertFalse(mutation_audit._is_constant_only_module(path))
        self.assertIsNone(
            mutation_audit._not_applicable_mutation_target(
                Path(__file__).resolve().parents[1],
                "venus_evcharger/update/software_update_state.py",
            )
        )

    def test_survivor_verification_uses_mutmut_virtualenv_when_available(self) -> None:
        command = mutation_audit._survivor_verification_test_command(["/tmp/python", "-m", "mutmut"])

        self.assertEqual(command[:4], ["/tmp/python", "-m", "pytest", "-q"])
        self.assertIn("tests/venus_evcharger_update_cycle_controller_cases_tertiary.py", command)

    def test_mutation_audit_lock_rejects_concurrent_runs_in_same_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            with mutation_audit.audit_support.mutmut_audit_lock(repo):
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    with mutation_audit.audit_support.mutmut_audit_lock(repo):
                        pass

            with mutation_audit.audit_support.mutmut_audit_lock(repo):
                self.assertTrue((repo / mutation_audit.audit_support.LOCK_FILENAME).exists())


if __name__ == "__main__":
    unittest.main()
