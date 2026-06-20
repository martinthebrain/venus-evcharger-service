#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check narrow architecture contracts that are easy to regress silently."""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

FORBIDDEN_SUBSTRINGS = {
    "venus_evcharger/ports/base.py": (
        "_resolve_compat_method_alias",
        "legacy ``_method`` lookups",
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


def main() -> int:
    failures = [*_check_forbidden_substrings(), *_check_forbidden_patterns()]
    if failures:
        print("Architecture contract violations found:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print("Architecture contracts passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
