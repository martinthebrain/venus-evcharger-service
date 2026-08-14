# SPDX-License-Identifier: GPL-3.0-or-later
"""Direct tests for the repository architecture-contract checker."""

from __future__ import annotations

import ast
import runpy
import sys
import tempfile
import unittest
from collections.abc import Generator, Mapping
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DEV = Path(__file__).resolve().parents[1] / "scripts/dev"
if str(SCRIPTS_DEV) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DEV))

from scripts.dev import architecture_suppression_contracts as suppressions
from scripts.dev import check_architecture_contracts as architecture


def _write(root: Path, relative_path: str, source: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


@contextmanager
def _temporary_repo(files: Mapping[str, str]) -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        for relative_path, source in files.items():
            _write(root, relative_path, source)
        yield root


def _suppression_failure(path: str, line: int, reason: str, source: str = "") -> str:
    detail = f": {source}" if source else ""
    return f"{path}:{line}: {reason}{detail}"


class ArchitectureContractsScriptTests(unittest.TestCase):
    def assert_suppression_failures(self, files: Mapping[str, str], expected: list[str]) -> None:
        with _temporary_repo(files) as root:
            self.assertEqual(suppressions.check_suppression_contracts(root), expected)

    def test_forbidden_suppression_markers_are_reported_exactly(self) -> None:
        self.assert_suppression_failures(
            {
                "runtime.py": (
                    "a = value  # type: ignore[arg-type]\n"
                    "b = value  # TYPE :IGNORE\n"
                    "c = 1  # pragma: no mutate\n"
                    "for item in values:  # pragma:no branch\n    consume(item)\n"
                )
            },
            [
                _suppression_failure("runtime.py", 1, "type ignore suppressions are forbidden"),
                _suppression_failure("runtime.py", 2, "type ignore suppressions are forbidden"),
                _suppression_failure("runtime.py", 3, "mutation suppressions are forbidden"),
                _suppression_failure("runtime.py", 4, "branch coverage suppressions are forbidden"),
            ],
        )

    def test_noqa_allowlist_is_exact_by_path_code_and_call_site(self) -> None:
        self.assert_suppression_failures(
            {
                "tests/test_shard.py": (
                    "from tests.test_shard_support import *  # noqa: F401,F403\n"
                ),
                "tests/venus_evcharger_helpers_support.py": (
                    "import package  # noqa: E402\n"
                    "from package import Contract  # noqa: E402\n"
                ),
                "venus_evcharger/control/http_api.py": (
                    "def do_GET(self) -> None:  # noqa: N802\n    pass\n"
                ),
                "tests/support/dbus_gateway_adapter_harness.py": (
                    "def GetValue(self) -> object:  # noqa: N802 - DBus spelling\n"
                    "    return 1\n"
                ),
                "scripts/dev/pi_gateway_release_gate_shelly.py": (
                    "value = urlopen(url)  # noqa: S310\n"
                ),
            },
            [],
        )
        rejected = {
            "tests/blanket.py": "import package  # noqa\n",
            "tests/not_a_shard.py": "from package import *  # noqa: F401,F403\n",
            "tests/other.py": "def GetValue(self):  # noqa: N802\n    pass\n",
            "venus_evcharger/control/http_api.py": (
                "value = 1  # noqa: N802\n"
                "def do_GET(self):  # noqa: n802\n    pass\n"
            ),
            "venus_evcharger/energy/connectors.py": (
                "value = subprocess.run(command)  # noqa: S603\n"
            ),
            "venus_evcharger/other.py": "value = urlopen(url)  # noqa: S310\n",
            "scripts/dev/pi_gateway_release_gate_shelly.py": (
                "value = urlopen(url)  # noqa: S310,S603\n"
            ),
        }
        rejected_cases = (
            (
                "scripts/dev/pi_gateway_release_gate_shelly.py",
                1,
                "value = urlopen(url)  # noqa: S310,S603",
            ),
            ("tests/blanket.py", 1, "import package  # noqa"),
            ("tests/not_a_shard.py", 1, "from package import *  # noqa: F401,F403"),
            ("tests/other.py", 1, "def GetValue(self):  # noqa: N802"),
            ("venus_evcharger/control/http_api.py", 1, "value = 1  # noqa: N802"),
            ("venus_evcharger/control/http_api.py", 2, "def do_GET(self):  # noqa: n802"),
            (
                "venus_evcharger/energy/connectors.py",
                1,
                "value = subprocess.run(command)  # noqa: S603",
            ),
            ("venus_evcharger/other.py", 1, "value = urlopen(url)  # noqa: S310"),
        )
        expected = [
            _suppression_failure(path, line, "unapproved noqa suppression", source)
            for path, line, source in rejected_cases
        ]
        self.assert_suppression_failures(rejected, expected)

    def test_no_cover_allowlist_is_derived_from_structure(self) -> None:
        valid = {
            "venus_evcharger/ports/example.py": (
                "import typing\n"
                "from typing import Protocol, TYPE_CHECKING, TypeVar\n"
                "T = TypeVar('T')\n"
                "class Port(Protocol):  # pragma: no cover\n"
                "    def read(self) -> int: ...  # pragma: no cover\n"
                "    def concrete(self) -> int:\n        return 1\n"
                "class Qualified(typing.Protocol):  # pragma: no cover\n    pass\n"
                "class Generic(Protocol[T]):  # pragma: no cover\n    pass\n"
                "if TYPE_CHECKING:\n"
                "    from package import Contract  # pragma: no cover\n"
                "if typing.TYPE_CHECKING:\n"
                "    import other_contract  # pragma: no cover\n"
            ),
            "tool.py": "if __name__ == '__main__':  # pragma: no cover\n    main()\n",
            "venus_evcharger/dbus_adapter/process/loop.py": (
                "class DbusAdapterLoop:\n"
                "    def run(self) -> None:  # pragma: no cover - Venus loop\n"
                "        execute()\n"
            ),
        }
        self.assert_suppression_failures(valid, [])
        invalid = {
            "broken.py": "if True print('broken')  # pragma: no cover\n",
            "tool.py": (
                "if __name__ == '__main__' == expected:  # pragma: no cover\n    main()\n"
            ),
            "venus_evcharger/example.py": (
                "class Concrete:\n"
                "    def read(self) -> int: ...  # pragma: no cover\n"
                "class Invalid(factory()):  # pragma: no cover\n    pass\n"
                "from package import Contract  # pragma: no cover\n"
            ),
            "venus_evcharger/dbus_adapter/process/other.py": (
                "class DbusAdapterLoop:\n"
                "    def run(self) -> None:  # pragma: no cover - Venus loop\n"
                "        execute()\n"
            ),
        }
        expected = [
            _suppression_failure(
                "broken.py",
                1,
                "unapproved coverage suppression",
                "if True print('broken')  # pragma: no cover",
            ),
            _suppression_failure(
                "tool.py",
                1,
                "unapproved coverage suppression",
                "if __name__ == '__main__' == expected:  # pragma: no cover",
            ),
            _suppression_failure(
                "venus_evcharger/dbus_adapter/process/other.py",
                2,
                "unapproved coverage suppression",
                "def run(self) -> None:  # pragma: no cover - Venus loop",
            ),
            _suppression_failure(
                "venus_evcharger/example.py",
                2,
                "unapproved coverage suppression",
                "def read(self) -> int: ...  # pragma: no cover",
            ),
            _suppression_failure(
                "venus_evcharger/example.py",
                3,
                "unapproved coverage suppression",
                "class Invalid(factory()):  # pragma: no cover",
            ),
            _suppression_failure(
                "venus_evcharger/example.py",
                5,
                "unapproved coverage suppression",
                "from package import Contract  # pragma: no cover",
            ),
        ]
        self.assert_suppression_failures(invalid, expected)

    def test_documented_runtime_exception_requires_a_reason(self) -> None:
        source = (
            "class DbusAdapterLoop:\n"
            "    def run(self) -> None:  # pragma: no cover\n"
            "        execute()\n"
        )
        documented = {"venus_evcharger/dbus_adapter/process/loop.py": {"DbusAdapterLoop.run": ""}}
        with patch.object(suppressions, "_DOCUMENTED_NO_COVER_CALLABLES", documented):
            self.assert_suppression_failures(
                {"venus_evcharger/dbus_adapter/process/loop.py": source},
                [
                    _suppression_failure(
                        "venus_evcharger/dbus_adapter/process/loop.py",
                        2,
                        "unapproved coverage suppression",
                        "def run(self) -> None:  # pragma: no cover",
                    )
                ],
            )

    def test_suppression_ast_helpers_have_exact_contracts(self) -> None:
        classes = ast.parse(
            "class Named(Protocol): pass\n"
            "class Qualified(typing.Protocol): pass\n"
            "class Generic(Protocol[int]): pass\n"
            "class Invalid(factory()): pass\n"
        ).body
        nodes = [node for node in classes if isinstance(node, ast.ClassDef)]
        self.assertEqual(
            [suppressions._base_name(node.bases[0]) for node in nodes],
            ["Protocol", "Protocol", "Protocol", ""],
        )
        functions = ast.parse(
            "def empty(): ...\n"
            "def concrete():\n    return 1\n"
            "def multiple():\n    ...\n    ...\n"
            "def literal(): 1\n"
        ).body
        methods = [node for node in functions if isinstance(node, ast.FunctionDef)]
        self.assertEqual(
            [suppressions._is_ellipsis_body(node) for node in methods],
            [True, False, False, False],
        )
        self.assertEqual(list(suppressions._line_range(methods[1])), [2, 3])
        valid = ast.parse("if __name__ == '__main__': pass\n").body[0]
        chained = ast.parse("if __name__ == '__main__' == expected: pass\n").body[0]
        assert isinstance(valid, ast.If) and isinstance(valid.test, ast.Compare)
        assert isinstance(chained, ast.If) and isinstance(chained.test, ast.Compare)
        comparison = suppressions._single_comparison(valid.test)
        self.assertIsNotNone(comparison)
        assert comparison is not None
        self.assertIsInstance(comparison[0], ast.Eq)
        self.assertTrue(suppressions._is_dunder_name(valid.test.left))
        self.assertTrue(suppressions._is_main_literal(comparison[1]))
        self.assertIsNone(suppressions._single_comparison(chained.test))
        self.assertFalse(suppressions._is_dunder_name(ast.Constant("__name__")))
        self.assertFalse(suppressions._is_main_literal(ast.Name(id="value")))
        tree = ast.parse("class DbusAdapterLoop:\n    def run(self): pass\n")
        class_node = tree.body[0]
        assert isinstance(class_node, ast.ClassDef)
        self.assertEqual(
            suppressions._class_documented_callable_lines(
                class_node, {"DbusAdapterLoop.run": "hardware loop"}
            ),
            {2},
        )
        self.assertEqual(suppressions._method_name("def GetValue(self):"), "GetValue")
        self.assertEqual(suppressions._method_name("value = 1"), "")

    def test_suppression_scanner_scope_and_encoding_are_explicit(self) -> None:
        included = (
            "venus_evcharger/runtime.py",
            "tests/test_runtime.py",
            "scripts/dev/check.py",
            "scripts/ops/probe.py",
            "entrypoint.py",
        )
        with _temporary_repo({path: "# source\n" for path in included}) as root:
            _write(root, "docs/example.py", "# outside project Python roots\n")
            paths = [
                path.relative_to(root).as_posix()
                for path in suppressions._suppression_scan_paths(root)
            ]
            self.assertEqual(paths, sorted(included))
            with patch.object(Path, "read_text", autospec=True, return_value="# source\n") as reader:
                self.assertEqual(suppressions.check_suppression_contracts(root), [])
        self.assertEqual(reader.call_count, len(included))
        reader.assert_any_call(root / "entrypoint.py", encoding="utf-8")

    def test_file_patterns_report_locations_and_can_be_retired(self) -> None:
        relative = "venus_evcharger/dbus_adapter/write/legacy.py"
        patterns = {
            "venus_evcharger/dbus_adapter/write": architecture.FORBIDDEN_FILE_PATTERNS[
                "venus_evcharger/dbus_adapter/write"
            ]
        }
        with _temporary_repo({relative: "\nclass Legacy(DbusWriteSchedulerCore):\n    pass\n"}) as root:
            path = root / relative
            with patch.object(architecture, "REPO", root), patch.object(
                architecture, "FORBIDDEN_FILE_PATTERNS", patterns
            ):
                self.assertEqual(
                    architecture._check_forbidden_patterns(),
                    [
                        f"{relative}:2: retired write-scheduler inheritance roles: "
                        "DbusWriteSchedulerCore"
                    ],
                )
                path.write_text("class WriteCommandQueue:\n    pass\n", encoding="utf-8")
                self.assertEqual(architecture._check_forbidden_patterns(), [])

    def test_expected_class_bases_detect_changed_missing_and_valid_classes(self) -> None:
        relative = "venus_evcharger/dbus_adapter/process/adapter.py"
        expected = {relative: {"DbusAdapter": ()}}
        source = "VALUE = 1\nclass DbusAdapter(LegacyRole):\n    pass\n"
        with _temporary_repo({relative: source}) as root:
            path = root / relative
            with patch.object(architecture, "REPO", root), patch.object(
                architecture, "EXPECTED_CLASS_BASES", expected
            ):
                self.assertEqual(
                    architecture._check_expected_class_bases(),
                    [
                        f"{relative}:2: DbusAdapter direct bases changed "
                        "from () to ('LegacyRole',)"
                    ],
                )
                path.write_text("class DbusAdapter:\n    pass\n", encoding="utf-8")
                self.assertEqual(architecture._check_expected_class_bases(), [])
                path.write_text("class Other:\n    pass\n", encoding="utf-8")
                self.assertEqual(
                    architecture._check_expected_class_bases(),
                    [f"{relative}: expected class DbusAdapter is missing"],
                )

    def test_multiple_inheritance_requires_an_exact_documented_exception(self) -> None:
        relative = "venus_evcharger/example.py"
        source = "class Combined(First[int], module.Second):\n    pass\n"
        with _temporary_repo({relative: source}) as root, patch.object(
            architecture, "REPO", root
        ):
            no_allowed: dict[
                str, dict[str, architecture._AllowedMultipleInheritance]
            ] = {}
            with patch.object(architecture, "ALLOWED_MULTIPLE_INHERITANCE", no_allowed):
                self.assertEqual(
                    architecture._check_multiple_inheritance_contract(),
                    [
                        f"{relative}:1: unexpected multiple inheritance "
                        "on Combined: First[int], module.Second"
                    ],
                )
            allowed = {
                relative: {
                    "Combined": architecture._AllowedMultipleInheritance(
                        bases=("First[int]", "module.Second"),
                        reason="Explicit test composition.",
                    )
                }
            }
            with patch.object(architecture, "ALLOWED_MULTIPLE_INHERITANCE", allowed):
                self.assertEqual(architecture._check_multiple_inheritance_contract(), [])
            allowed[relative]["Combined"] = architecture._AllowedMultipleInheritance(
                bases=("First", "Third"), reason="Mismatched exception."
            )
            with patch.object(architecture, "ALLOWED_MULTIPLE_INHERITANCE", allowed):
                failures = architecture._check_multiple_inheritance_contract()
            self.assertIn("allowed multiple inheritance on Combined changed", failures[0])

    def test_repository_boundary_checks_reject_owned_details(self) -> None:
        files = {
            "DBUS_GATEWAY.md": "GatewayClient.request_raw_value\n",
            "runtime.py": "allowed = 'compileall'\n",
            "retired.py": "",
            "venus_evcharger/dbus_adapter_legacy.py": "",
            "venus_evcharger/consumer.py": (
                "from venus_evcharger.dbus_gateway_surface import Value\n"
                "legacy = 'dbus_venus_surface'\n"
            ),
            "venus_evcharger/dbus_gateway_owner.py": (
                "from venus_evcharger.dbus_gateway_surface import Value\n"
            ),
        }
        with _temporary_repo(files) as root, patch.object(architecture, "REPO", root):
            with patch.object(
                architecture,
                "FORBIDDEN_SUBSTRINGS",
                {"runtime.py": ("compileall", "py_compile")},
            ), patch.object(architecture, "RETIRED_STATE_MODULES", ("retired.py", "absent.py")):
                self.assertEqual(
                    architecture._check_forbidden_substrings(),
                    ["runtime.py: contains forbidden compatibility marker 'compileall'"],
                )
                self.assertEqual(
                    architecture._check_retired_state_modules(),
                    ["retired.py: retired state-controller module must remain absent"],
                )
            self.assertEqual(
                architecture._check_dbus_adapter_layout(),
                [
                    "venus_evcharger/dbus_adapter_legacy.py: fragmented adapter modules "
                    "belong under venus_evcharger/dbus_adapter/"
                ],
            )
            self.assertEqual(
                architecture._check_gateway_surface_boundary(),
                [
                    "venus_evcharger/consumer.py: legacy dbus_venus_surface import/name is forbidden",
                    "venus_evcharger/consumer.py: import Venus surface contracts through "
                    "venus_evcharger.dbus_gateway",
                ],
            )

    def test_required_gateway_contract_symbols_are_structural(self) -> None:
        relative = "venus_evcharger/ipc/contract.py"
        source = "LIMIT = 1\nclass Queue:\n    def accept(self):\n        transport.commit()\n"
        required = {relative: frozenset(("LIMIT", "Queue", "accept", "commit"))}
        with _temporary_repo({relative: source}) as root, patch.object(
            architecture, "REPO", root
        ), patch.object(architecture, "REQUIRED_GATEWAY_CONTRACT_SYMBOLS", required):
            path = root / relative
            self.assertEqual(architecture._check_required_gateway_contracts(), [])
            path.write_text("class Queue:\n    pass\n", encoding="utf-8")
            self.assertEqual(
                architecture._check_required_gateway_contracts(),
                [
                    f"{relative}: required gateway contract symbol 'LIMIT' is missing",
                    f"{relative}: required gateway contract symbol 'accept' is missing",
                    f"{relative}: required gateway contract symbol 'commit' is missing",
                ],
            )
        self.assertEqual(architecture._check_required_gateway_contracts(), [])

    def test_main_reports_failures_and_success(self) -> None:
        checks = (
            "_check_forbidden_substrings _check_forbidden_patterns _check_retired_state_modules "
            "_check_expected_class_bases _check_multiple_inheritance_contract "
            "_check_gateway_surface_boundary _check_dbus_adapter_layout "
            "_check_required_gateway_contracts check_suppression_contracts "
            "check_command_mailbox_contracts check_gateway_operation_contracts "
            "check_gateway_read_contracts"
        ).split()
        stderr = StringIO()
        with ExitStack() as stack:
            for name in checks:
                stack.enter_context(patch.object(architecture, name, return_value=[]))
            stack.enter_context(
                patch.object(
                    architecture,
                    "_check_expected_class_bases",
                    return_value=["adapter.py: base changed"],
                )
            )
            with redirect_stderr(stderr):
                self.assertEqual(architecture.main(), 1)
        self.assertIn("Architecture contract violations found", stderr.getvalue())
        self.assertIn("adapter.py: base changed", stderr.getvalue())
        stdout = StringIO()
        with ExitStack() as stack:
            for name in checks:
                stack.enter_context(patch.object(architecture, name, return_value=[]))
            with redirect_stdout(stdout):
                self.assertEqual(architecture.main(), 0)
        self.assertEqual(stdout.getvalue(), "Architecture contracts passed.\n")

        with redirect_stdout(StringIO()), self.assertRaises(SystemExit) as stopped:
            runpy.run_path(str(architecture.__file__), run_name="__main__")
        self.assertEqual(stopped.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
