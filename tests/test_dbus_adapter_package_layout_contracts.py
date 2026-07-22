# SPDX-License-Identifier: GPL-3.0-or-later
"""Architecture contracts for the responsibility-oriented adapter package."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO / "venus_evcharger" / "dbus_adapter"
EXPECTED_AREAS = {"health", "process", "publication", "read", "write"}
ALLOWED_PACKAGE_EXPORTS = {
    Path("venus_evcharger/dbus_adapter/publication/__init__.py"): {
        ("registry", "GatewayPublicationRegistry"),
    },
}
DOCUMENTATION_PATHS = (REPO / "DBUS_GATEWAY.md", REPO / "DBUS_INTROSPECTION.md")
RETIRED_IMPORT_PREFIX = "venus_evcharger.dbus_adapter_"
RETIRED_PATH_PREFIX = "venus_evcharger/dbus_adapter_"


class DbusAdapterPackageLayoutContractTests(unittest.TestCase):
    def test_legacy_top_level_adapter_modules_are_absent(self) -> None:
        self.assertEqual(sorted((REPO / "venus_evcharger").glob("dbus_adapter_*.py")), [])

    def test_responsibility_areas_are_explicit_packages(self) -> None:
        areas = {path.name for path in PACKAGE_ROOT.iterdir() if path.is_dir() and not path.name.startswith("__")}
        self.assertEqual(areas, EXPECTED_AREAS)
        for area in EXPECTED_AREAS:
            self.assertTrue((PACKAGE_ROOT / area / "__init__.py").is_file())

    def test_package_initializers_expose_only_declared_public_roles(self) -> None:
        for path in sorted(PACKAGE_ROOT.rglob("__init__.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            exports = {
                (node.module or "", alias.name)
                for node in tree.body
                if isinstance(node, ast.ImportFrom)
                for alias in node.names
            }
            self.assertEqual(
                exports,
                ALLOWED_PACKAGE_EXPORTS.get(path.relative_to(REPO), set()),
                str(path.relative_to(REPO)),
            )
            self.assertFalse(
                any(isinstance(node, ast.Import) for node in tree.body),
                str(path.relative_to(REPO)),
            )

    def test_python_imports_do_not_reference_retired_module_names(self) -> None:
        paths = sorted((REPO / "venus_evcharger").rglob("*.py"))
        paths.extend(sorted((REPO / "scripts").rglob("*.py")))
        paths.extend(sorted((REPO / "tests").rglob("*.py")))
        paths.append(REPO / "venus_evcharger_dbus_adapter.py")
        failures: list[str] = []
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                module_names = self._imported_modules(node)
                failures.extend(
                    f"{path.relative_to(REPO)}:{node.lineno}: {module}"
                    for module in module_names
                    if module.startswith(RETIRED_IMPORT_PREFIX)
                )
        self.assertEqual(failures, [])

    def test_documentation_uses_only_canonical_adapter_paths(self) -> None:
        failures = [
            str(path.relative_to(REPO))
            for path in DOCUMENTATION_PATHS
            if RETIRED_IMPORT_PREFIX in path.read_text(encoding="utf-8")
            or RETIRED_PATH_PREFIX in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(failures, [])

    @staticmethod
    def _imported_modules(node: ast.AST) -> list[str]:
        if isinstance(node, ast.Import):
            return [alias.name for alias in node.names]
        if isinstance(node, ast.ImportFrom) and node.module:
            return [node.module]
        return []


if __name__ == "__main__":
    unittest.main()
