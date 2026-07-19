# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import ModuleType


SCRIPT = Path(__file__).parents[1] / "scripts/dev/check_python_syntax_venus.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_python_syntax_venus", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load Venus syntax checker")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PythonSyntaxVenusTests(unittest.TestCase):
    def test_checks_files_and_directories_without_duplicates(self) -> None:
        module = _load_script()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            valid = root / "valid.py"
            nested = root / "nested"
            nested.mkdir()
            valid.write_text("answer = 42\n", encoding="utf-8")
            (nested / "also_valid.py").write_text("def value():\n    return 1\n", encoding="utf-8")
            (nested / "ignored.txt").write_text("not python", encoding="utf-8")

            self.assertEqual(module.check_python_syntax([str(root), str(valid)]), 0)
            self.assertEqual(len(module.python_files([str(root), str(valid)])), 2)

    def test_reports_invalid_source_and_invalid_selections(self) -> None:
        module = _load_script()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            invalid = root / "invalid.py"
            invalid.write_text("if True print('broken')\n", encoding="utf-8")
            empty = root / "empty"
            empty.mkdir()

            self.assertEqual(module.check_python_syntax([str(invalid)]), 1)
            with self.assertRaisesRegex(ValueError, "did not select"):
                module.check_python_syntax([str(empty)])
            with self.assertRaisesRegex(ValueError, "does not exist"):
                module.python_files([str(root / "missing")])

    def test_main_returns_usage_failure_for_missing_path(self) -> None:
        module = _load_script()
        self.assertEqual(module.main(["definitely-missing.py"]), 2)


if __name__ == "__main__":
    unittest.main()
