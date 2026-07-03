#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check narrow architecture contracts that are easy to regress silently."""

from __future__ import annotations

import ast
import re
import sys
import tokenize
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

from architecture_gateway_read_contracts import check_gateway_read_contracts

REPO = Path(__file__).resolve().parents[2]

FORBIDDEN_SUBSTRINGS = {
    "venus_evcharger/ports/base.py": (
        "_resolve_compat_method_alias",
        "legacy ``_method`` lookups",
    ),
    "venus_evcharger/inputs/helper/sources_pv_grid.py": (
        "_read_ac_pv_total",
        "_read_dc_pv_power",
        "_grid_total_and_missing_paths",
        "_grid_path_numeric_value",
    ),
    "venus_evcharger/inputs/pv.py": (
        "_resolve_pv_service_names",
        "_read_pv_service_values",
        "_read_rescanned_pv_services",
        "_read_dc_pv_value",
        "_handle_missing_pv_power",
        "_ac_pv_total_with_optional_rescan",
        "_dc_pv_total",
    ),
    "venus_evcharger/inputs/storage.py": (
        "_read_battery_soc_value",
    ),
    "venus_evcharger/inputs/storage_support.py": (
        "_configured_grid_paths",
        "_read_grid_phase_values",
        "_read_one_grid_phase",
        "_finalize_grid_power",
    ),
}

FORBIDDEN_FILE_PATTERNS = {
    "tests": {
        "legacy private port calls": re.compile(
            r"\bport\._(?:"
            r"get_dbus_value|clear_auto_samples|queue_relay_command|publish_dbus_path|"
            r"get_worker_snapshot|update_worker_snapshot|mode_uses_auto_logic"
            r")\("
        ),
    },
    "venus_evcharger/controllers/write_support.py": {
        "write support private port calls": re.compile(
            r"\bsvc\._(?:"
            r"clear_auto_samples|queue_relay_command|publish_dbus_path|"
            r"get_worker_snapshot|update_worker_snapshot|mode_uses_auto_logic"
            r")\("
        ),
    },
    "venus_evcharger/auto/logic_samples.py": {
        "auto logic private port calls": re.compile(r"\b(?:svc|self\.service)\._clear_auto_samples\("),
    },
    "venus_evcharger/auto": {
        "auto workflow private port calls": re.compile(
            r"\b(?:svc|self\.service)\._(?:"
            r"get_available_surplus_watts|add_auto_sample|average_auto_metric|"
            r"is_within_auto_daytime_window|save_runtime_state|peek_pending_relay_command|"
            r"write_auto_audit_event"
            r")\("
        ),
    },
    "venus_evcharger/inputs/pv.py": {
        "dbus input private port calls": re.compile(
            r"\b(?:svc|self\.service)\._(?:"
            r"get_dbus_value|invalidate_auto_battery_service|resolve_auto_battery_service|"
            r"list_dbus_services|source_retry_ready|mark_recovery|mark_failure|"
            r"delay_source_retry|warning_throttled|resolve_auto_pv_services|"
            r"invalidate_auto_pv_services"
            r")\("
        ),
    },
    "venus_evcharger/inputs/storage.py": {
        "dbus input private port calls": re.compile(
            r"\b(?:svc|self\.service)\._(?:"
            r"get_dbus_value|invalidate_auto_battery_service|resolve_auto_battery_service"
            r")\("
        ),
    },
    "venus_evcharger/inputs/storage_support.py": {
        "dbus input private port calls": re.compile(
            r"\b(?:svc|self\.service)\._(?:"
            r"get_dbus_value|resolve_auto_battery_service|list_dbus_services"
            r")\("
        ),
    },
}


@dataclass(frozen=True)
class _AllowedMultipleInheritance:
    bases: tuple[str, ...]
    reason: str


ALLOWED_MULTIPLE_INHERITANCE = {
    "venus_evcharger/dbus_adapter_process_protocols.py": {
        "DbusAdapterProcessContext": _AllowedMultipleInheritance(
            bases=(
                "DbusAdapterRuntimeContext",
                "DbusAdapterSocketContext",
                "DbusAdapterIdentityContext",
                "DbusAdapterLoopContext",
                "DbusAdapterIoContext",
                "DbusAdapterHealthContext",
                "DbusAdapterIntrospectionContext",
                "DbusAdapterIntrospectionSnapshotContext",
                "Protocol",
            ),
            reason="Protocol-only composition of adapter process contracts.",
        ),
    },
    "venus_evcharger/control/http_api.py": {
        "_ThreadingLocalControlUnixHttpServer": _AllowedMultipleInheritance(
            bases=("socketserver.ThreadingMixIn", "socketserver.UnixStreamServer"),
            reason="stdlib server composition for threaded Unix-domain HTTP.",
        ),
    },
}

EXPECTED_CLASS_BASES = {
    "venus_evcharger/dbus_adapter_process.py": {
        "DbusAdapter": ("DbusAdapterLoop",),
    },
    "venus_evcharger/update/controller.py": {
        "UpdateCycleController": ("_UpdateCycleSoftwareUpdate",),
    },
    "venus_evcharger/service/factory.py": {
        "ServiceControllerFactory": (),
    },
    "venus_evcharger/service/update.py": {
        "UpdateCycle": ("ServiceControllerFactory",),
    },
    "venus_evcharger/service/auto.py": {
        "DbusAutoLogic": ("UpdateCycle",),
    },
    "venus_evcharger/service/runtime.py": {
        "RuntimeHelper": ("DbusAutoLogic",),
    },
    "venus_evcharger/service/state_publish.py": {
        "StatePublish": ("RuntimeHelper",),
    },
    "venus_evcharger/service/control.py": {
        "ControlApi": ("_ControlApiRuntime",),
    },
    "venus_evcharger_auto_input_helper.py": {
        "AutoInputHelper": ("_AutoInputHelperConfig",),
    },
}

ALLOWED_NOQA_CODES = {
    "E402",
    "F401",
    "F403",
    "N802",
    "S310",
    "S603",
}

NO_MUTATE_FILE_PREFIXES = (
    "venus_evcharger/dbus_adapter",
    "venus_evcharger/dbus_gateway",
)

GATEWAY_SURFACE_IMPORT_PATTERN = re.compile(r"\b(?:from|import)\s+venus_evcharger\.dbus_gateway_surface\b")


def _repo_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _check_forbidden_substrings() -> list[str]:
    failures: list[str] = []
    for relative_path, forbidden_values in FORBIDDEN_SUBSTRINGS.items():
        text = _repo_text(REPO / relative_path)
        failures.extend(
            f"{relative_path}: contains forbidden compatibility marker {value!r}"
            for value in forbidden_values
            if value in text
        )
    return failures


def _paths_for(relative_root: str) -> list[Path]:
    root = REPO / relative_root
    return sorted(root.rglob("*.py")) if root.is_dir() else [root]


def _pattern_failures_for(path: Path, label: str, pattern: re.Pattern[str]) -> list[str]:
    text = _repo_text(path)
    relative_path = path.relative_to(REPO)
    return [
        f"{relative_path}:{_line_number(text, match.start())}: {label}: {match.group(0)}"
        for match in pattern.finditer(text)
    ]


def _check_forbidden_patterns() -> list[str]:
    failures: list[str] = []
    for relative_root, patterns in FORBIDDEN_FILE_PATTERNS.items():
        for path in _paths_for(relative_root):
            for label, pattern in patterns.items():
                failures.extend(_pattern_failures_for(path, label, pattern))
    return failures


def _class_base_name(base: ast.expr) -> str:
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        prefix = _class_base_name(base.value)
        return f"{prefix}.{base.attr}" if prefix else base.attr
    return ast.unparse(base)


def _multiple_inheritance_classes(path: Path) -> list[tuple[int, str, tuple[str, ...]]]:
    tree = ast.parse(_repo_text(path), filename=str(path))
    return [
        (node.lineno, node.name, tuple(_class_base_name(base) for base in node.bases))
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and len(node.bases) > 1
    ]


def _top_level_class_bases(path: Path) -> dict[str, tuple[int, tuple[str, ...]]]:
    tree = ast.parse(_repo_text(path), filename=str(path))
    classes: dict[str, tuple[int, tuple[str, ...]]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            classes[node.name] = (node.lineno, tuple(_class_base_name(base) for base in node.bases))
    return classes


def _check_expected_class_bases() -> list[str]:
    failures: list[str] = []
    for relative_path, expected_classes in EXPECTED_CLASS_BASES.items():
        classes = _top_level_class_bases(REPO / relative_path)
        for class_name, expected_bases in expected_classes.items():
            if class_name not in classes:
                failures.append(f"{relative_path}: expected class {class_name} is missing")
                continue
            line_number, actual_bases = classes[class_name]
            if actual_bases != expected_bases:
                failures.append(
                    f"{relative_path}:{line_number}: {class_name} direct bases changed "
                    f"from {expected_bases!r} to {actual_bases!r}"
                )
    return failures


def _multiple_inheritance_contract_failure(
    relative_path: str,
    line_number: int,
    class_name: str,
    bases: tuple[str, ...],
) -> str | None:
    allowed = ALLOWED_MULTIPLE_INHERITANCE.get(relative_path, {}).get(class_name)
    if allowed is None:
        return (
            f"{relative_path}:{line_number}: unexpected multiple inheritance "
            f"on {class_name}: {', '.join(bases)}"
        )
    if bases != allowed.bases:
        return (
            f"{relative_path}:{line_number}: allowed multiple inheritance "
            f"on {class_name} changed from {allowed.bases!r} to {bases!r}"
        )
    return None


def _scan_multiple_inheritance_contracts() -> tuple[list[str], set[tuple[str, str]]]:
    failures: list[str] = []
    seen: set[tuple[str, str]] = set()
    for path in sorted((REPO / "venus_evcharger").rglob("*.py")):
        relative_path = str(path.relative_to(REPO))
        for line_number, class_name, bases in _multiple_inheritance_classes(path):
            failure = _multiple_inheritance_contract_failure(relative_path, line_number, class_name, bases)
            if failure:
                failures.append(failure)
            else:
                seen.add((relative_path, class_name))
    return failures, seen


def _missing_allowed_multiple_inheritance(seen: set[tuple[str, str]]) -> list[str]:
    return [
        f"{relative_path}: allowed multiple inheritance class {class_name} is missing"
        for relative_path, classes in ALLOWED_MULTIPLE_INHERITANCE.items()
        for class_name in classes
        if (relative_path, class_name) not in seen
    ]


def _check_multiple_inheritance_contract() -> list[str]:
    failures, seen = _scan_multiple_inheritance_contracts()
    failures.extend(_missing_allowed_multiple_inheritance(seen))
    return failures


def _suppression_scan_paths() -> list[Path]:
    roots = [
        REPO / "venus_evcharger",
        REPO / "tests",
        REPO / "scripts" / "dev",
    ]
    paths = [path for root in roots for path in sorted(root.rglob("*.py"))]
    paths.extend(
        REPO / name
        for name in (
            "venus_evcharger_service.py",
            "venus_evcharger_auto_input_helper.py",
            "venus_evcharger_dbus_adapter.py",
        )
        if (REPO / name).exists()
    )
    return sorted(paths)


def _line_allowed_no_cover(relative_path: str, line: str) -> bool:
    if "_protocol" in relative_path:
        return True
    return any(
        marker in line
        for marker in (
            "TYPE_CHECKING",
            "__main__",
            "Protocol",
            "...",
            "Venus DBus/GLib",
            "initialize_dbus_service",
            "foreign DBus objects",
            "from ",
        )
    )


def _noqa_codes(line: str) -> set[str]:
    _prefix, _sep, suffix = line.partition("# noqa:")
    code_text = suffix.split("-", 1)[0].strip()
    return {code.strip() for code in code_text.split(",") if code.strip()}


def _line_allowed_noqa(line: str) -> bool:
    codes = _noqa_codes(line)
    return bool(codes) and codes <= ALLOWED_NOQA_CODES


def _line_allowed_no_mutate(relative_path: str) -> bool:
    return relative_path.startswith(NO_MUTATE_FILE_PREFIXES)


def _suppression_failure(relative_path: str, line_number: int, comment: str, line: str) -> str | None:
    for checker in (
        _type_ignore_failure,
        _noqa_failure,
        _no_cover_failure,
        _no_mutate_failure,
    ):
        failure = checker(relative_path, line_number, comment, line)
        if failure is not None:
            return failure
    return None


def _type_ignore_failure(relative_path: str, line_number: int, comment: str, _line: str) -> str | None:
    if "type: ignore" in comment:
        return f"{relative_path}:{line_number}: type ignore suppressions are forbidden"
    return None


def _noqa_failure(relative_path: str, line_number: int, comment: str, line: str) -> str | None:
    if "# noqa" in comment and not _line_allowed_noqa(comment):
        return f"{relative_path}:{line_number}: unapproved noqa suppression: {line.strip()}"
    return None


def _no_cover_failure(relative_path: str, line_number: int, comment: str, line: str) -> str | None:
    if "pragma: no cover" in comment and not _line_allowed_no_cover(relative_path, line):
        return f"{relative_path}:{line_number}: unapproved coverage suppression: {line.strip()}"
    return None


def _no_mutate_failure(relative_path: str, line_number: int, comment: str, _line: str) -> str | None:
    if "pragma: no mutate" in comment and not _line_allowed_no_mutate(relative_path):
        return f"{relative_path}:{line_number}: mutation suppression outside gateway/adapter boundary"
    return None


def _check_suppression_contracts() -> list[str]:
    failures: list[str] = []
    for path in _suppression_scan_paths():
        relative_path = str(path.relative_to(REPO))
        text = _repo_text(path)
        lines = text.splitlines()
        for token in tokenize.generate_tokens(StringIO(text).readline):
            if token.type != tokenize.COMMENT:
                continue
            line_number = token.start[0]
            line = lines[line_number - 1]
            failure = _suppression_failure(relative_path, line_number, token.string, line)
            if failure is not None:
                failures.append(failure)
    return failures


def _check_gateway_surface_boundary() -> list[str]:
    failures: list[str] = []
    for path in sorted((REPO / "venus_evcharger").rglob("*.py")):
        relative_path = str(path.relative_to(REPO))
        text = _repo_text(path)
        if "dbus_venus_surface" in text:
            failures.append(f"{relative_path}: legacy dbus_venus_surface import/name is forbidden")
        if GATEWAY_SURFACE_IMPORT_PATTERN.search(text) and not relative_path.startswith("venus_evcharger/dbus_gateway"):
            failures.append(f"{relative_path}: import Venus surface contracts through venus_evcharger.dbus_gateway")
    return failures


def main() -> int:
    failures = [
        *_check_forbidden_substrings(),
        *_check_forbidden_patterns(),
        *_check_expected_class_bases(),
        *_check_multiple_inheritance_contract(),
        *_check_suppression_contracts(),
        *_check_gateway_surface_boundary(),
        *check_gateway_read_contracts(REPO),
    ]
    if failures:
        print("Architecture contract violations found:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print("Architecture contracts passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
