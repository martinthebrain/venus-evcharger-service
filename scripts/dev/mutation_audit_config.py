# SPDX-License-Identifier: GPL-3.0-or-later
"""Mutmut configuration helpers for the optional mutation audit."""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterator
from pathlib import Path

if __package__:
    from .mutation_audit_targets import DEFAULT_TEST_SELECTION
else:
    from mutation_audit_targets import DEFAULT_TEST_SELECTION

_FOCUSED_TEST_SELECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("scripts/dev/", ("tests/test_mutation_audit_script.py",)),
    ("venus_evcharger/topology/", ("tests/test_topology_config.py",)),
    (
        "venus_evcharger/backend/template_meter.py",
        (
            "tests/test_venus_evcharger_backend_template_meter.py",
            "tests/test_venus_evcharger_backend_template_support.py",
        ),
    ),
    (
        "venus_evcharger/backend/template_switch.py",
        (
            "tests/test_venus_evcharger_backend_template_switch.py",
            "tests/test_venus_evcharger_backend_template_support.py",
            "tests/test_venus_evcharger_backend_tuya.py",
        ),
    ),
    (
        "venus_evcharger/backend/template_charger.py",
        (
            "tests/test_venus_evcharger_backend_template_charger.py",
            "tests/test_venus_evcharger_backend_template_support.py",
        ),
    ),
    (
        "venus_evcharger/backend/template_support.py",
        (
            "tests/test_venus_evcharger_backend_template_support.py",
            "tests/test_venus_evcharger_backend_template_meter.py",
            "tests/test_venus_evcharger_backend_template_switch.py",
            "tests/test_venus_evcharger_backend_template_charger.py",
        ),
    ),
    (
        "venus_evcharger/backend/shelly_support.py",
        (
            "tests/test_venus_evcharger_backend_shelly_support.py",
            "tests/test_venus_evcharger_backend_shelly_meter.py",
            "tests/test_venus_evcharger_backend_switch.py",
        ),
    ),
    (
        "venus_evcharger/backend/shelly_io_runtime.py",
        (
            "tests/test_venus_evcharger_shelly_io_controller.py",
            "tests/test_venus_evcharger_backend_shelly_support.py",
            "tests/test_venus_evcharger_backend_shelly_meter.py",
        ),
    ),
    (
        "venus_evcharger/backend/shelly_meter.py",
        (
            "tests/test_venus_evcharger_backend_shelly_meter.py",
            "tests/test_venus_evcharger_backend_shelly_support.py",
        ),
    ),
    (
        "venus_evcharger/backend/shelly_switch.py",
        (
            "tests/test_venus_evcharger_backend_switch.py",
            "tests/test_venus_evcharger_backend_shelly_support.py",
        ),
    ),
    (
        "venus_evcharger/auto/",
        (
            "tests/test_auto_battery_balance_contracts.py",
            "tests/test_auto_logic_types.py",
            "tests/test_venus_evcharger_auto_policy.py",
            "tests/test_venus_evcharger_auto_controller.py",
            "tests/venus_evcharger_auto_controller_cases_primary.py",
            "tests/venus_evcharger_auto_controller_cases_recovery.py",
        ),
    ),
    (
        "venus_evcharger/inventory/",
        (
            "tests/test_device_inventory_config.py",
            "tests/test_wizard_inventory_helpers.py",
        ),
    ),
    (
        "venus_evcharger/backend/",
        (
            "tests/test_backend_config_file.py",
            "tests/test_venus_evcharger_backend_factory.py",
            "tests/test_venus_evcharger_backend_probe.py",
            "tests/test_backend_factory_probe_contracts.py",
            "tests/test_topology_config.py",
        ),
    ),
    ("venus_evcharger/bootstrap/", ("tests/test_venus_evcharger_bootstrap_controller.py",)),
)


@contextlib.contextmanager
def mutmut_config_for_target(repo: Path, target_path: str) -> Iterator[None]:
    pyproject = repo / "pyproject.toml"
    original = pyproject.read_text(encoding="utf-8")
    pyproject.write_text(pyproject_with_mutmut_config(original, target_path), encoding="utf-8")
    try:
        yield
    finally:
        pyproject.write_text(original, encoding="utf-8")


def pyproject_with_mutmut_config(original: str, target_path: str) -> str:
    return f"{strip_tool_mutmut_section(original)}\n{mutmut_config_toml(target_path)}"


def strip_tool_mutmut_section(content: str) -> str:
    return re.sub(r"(?ms)^\[tool\.mutmut\]\n.*?(?=^\[|\Z)", "", content).strip() + "\n"


def mutmut_config_toml(target_path: str) -> str:
    test_selection = test_selection_for_target(target_path)
    lines = [
        "[tool.mutmut]",
        f'source_paths = ["{source_path_for_target(target_path)}"]',
        f'only_mutate = ["{target_path}"]',
        "also_copy = [",
        '    "venus_evcharger_auto_input_helper.py",',
        '    "venus_evcharger_dbus_adapter.py",',
        '    "venus_evcharger_service.py",',
        '    "CONTROL_API.md",',
        "]",
        'pytest_add_cli_args = ["-k", "not socket"]',
        "pytest_add_cli_args_test_selection = [",
    ]
    lines.extend(f'    "{path}",' for path in test_selection)
    return "\n".join([*lines, "]", ""])


def source_path_for_target(target_path: str) -> str:
    if target_path.startswith("scripts/dev/"):
        return "scripts/dev"
    top_level, _separator, _remainder = target_path.partition("/")
    return top_level


def test_selection_for_target(target_path: str) -> tuple[str, ...]:
    for prefix, selection in _FOCUSED_TEST_SELECTIONS:
        if target_path.startswith(prefix):
            return selection
    return DEFAULT_TEST_SELECTION
