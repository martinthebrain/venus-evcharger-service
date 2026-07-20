# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from io import StringIO
from pathlib import Path
from types import ModuleType

SCRIPT = Path("scripts/ops/gateway_cache_read.py")


def _load_module() -> ModuleType:
    loader = SourceFileLoader("gateway_cache_read_under_test", str(SCRIPT))
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


class GatewayCacheReadScriptTests(unittest.TestCase):
    def test_reads_present_values_and_reports_missing_paths(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = Path(temp_dir) / "cache.json"
            cache.write_text(
                json.dumps(
                    {
                        "values": {
                            "path:service/Mode": {"value": 2, "status": "fresh", "age_s": 0.25},
                        }
                    }
                ),
                encoding="utf-8",
            )
            lines, missing = module.cached_path_values(str(cache), "service", ["/Mode", "/Missing"])
            self.assertEqual(lines, ["/Mode=2 status=fresh age_s=0.25"])
            self.assertEqual(missing, ["/Missing"])

            output = StringIO()
            with redirect_stdout(output):
                result = module.main(["--cache", str(cache), "--service", "service", "/Mode", "/Missing"])
            self.assertEqual(result, 1)
            self.assertIn("/Missing=<missing>", output.getvalue())

    def test_invalid_or_unreadable_cache_returns_diagnostic_exit(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            invalid = Path(temp_dir) / "invalid.json"
            invalid.write_text("[]", encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                result = module.main(["--cache", str(invalid), "/Mode"])
            self.assertEqual(result, 2)
            self.assertIn("no values object", output.getvalue())

            broken = invalid.with_name("broken.json")
            broken.write_text("{", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                module.cached_path_values(str(broken), "service", ["/Mode"])


if __name__ == "__main__":
    unittest.main()
