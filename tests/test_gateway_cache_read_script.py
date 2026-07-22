# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from io import StringIO
from pathlib import Path
from types import ModuleType

from tests.gateway_diagnostics_fixtures import gateway_diagnostics_snapshot
from venus_evcharger.ipc.gateway_diagnostics import encode_gateway_diagnostics

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
    def test_reads_selected_semantic_fields(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "gateway-diagnostics.json"
            snapshot_path.write_text(
                encode_gateway_diagnostics(gateway_diagnostics_snapshot()),
                encoding="utf-8",
            )
            lines = module.diagnostic_field_values(
                str(snapshot_path), ["operating_mode", "ac_power_w"]
            )
            self.assertEqual(len(lines), 2)
            self.assertIn("operating_mode=2 status=fresh", lines[0])
            self.assertIn("ac_power_w=1234.5 status=fresh", lines[1])

            output = StringIO()
            with redirect_stdout(output):
                result = module.main(
                    ["--snapshot", str(snapshot_path), "charging_enabled"]
                )
            self.assertEqual(result, 0)
            self.assertIn("charging_enabled=True", output.getvalue())

    def test_omitted_fields_print_complete_semantic_snapshot(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "gateway-diagnostics.json"
            snapshot_path.write_text(
                encode_gateway_diagnostics(gateway_diagnostics_snapshot()),
                encoding="utf-8",
            )
            lines = module.diagnostic_field_values(str(snapshot_path), [])
        self.assertEqual(len(lines), 10)
        self.assertTrue(lines[0].startswith("ac_power_w="))
        self.assertTrue(lines[-1].startswith("runtime_overrides_source="))

    def test_unknown_field_or_unreadable_snapshot_returns_diagnostic_exit(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "gateway-diagnostics.json"
            snapshot_path.write_text(
                encode_gateway_diagnostics(gateway_diagnostics_snapshot()),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unknown semantic"):
                module.diagnostic_field_values(str(snapshot_path), ["raw_transport_target"])

            output = StringIO()
            with redirect_stdout(output):
                result = module.main(
                    ["--snapshot", str(snapshot_path.with_name("missing")), "operating_mode"]
                )
            self.assertEqual(result, 2)
            self.assertIn("Unable to read gateway diagnostics", output.getvalue())


if __name__ == "__main__":
    unittest.main()
