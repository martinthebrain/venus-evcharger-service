# SPDX-License-Identifier: GPL-3.0-or-later
"""Config-text helpers for wizard rendering."""

from __future__ import annotations

import configparser
from datetime import datetime
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


class CasePreservingConfigParser(configparser.ConfigParser):
    """Config parser that keeps option names exactly as written."""

    def optionxform(self, optionstr: str) -> str:
        return optionstr


def default_template_path() -> Path:
    return repo_root() / "deploy" / "venus" / "config.venus_evcharger.ini"


def default_config_path() -> Path:
    return default_template_path()


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def mode_value(policy_mode: str) -> str:
    return {"manual": "0", "auto": "1", "scheduled": "2"}[policy_mode]


def replace_assignment(text: str, key: str, value: str) -> str:
    replaced = False
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith(f"{key}="):
            lines.append(f"{key}={value}")
            replaced = True
            continue
        lines.append(line)
    if not replaced:
        raise ValueError(f"Config template is missing required key '{key}'")
    return "\n".join(lines) + "\n"


def replace_optional_assignment(text: str, key: str, value: object) -> str:
    if value is None:
        return text
    if isinstance(value, float):
        rendered = f"{value:g}"
    else:
        rendered = str(value)
    return replace_assignment(text, key, rendered)


def upsert_default_assignments(text: str, assignments: dict[str, str]) -> str:
    if not assignments:
        return text
    lines = text.splitlines()
    remaining = dict(assignments)
    rendered: list[str] = []
    inserted = False
    in_default_section = False

    for line in lines:
        if line == "[DEFAULT]":
            in_default_section = True
            rendered.append(line)
            continue
        rendered, inserted = _maybe_insert_default_assignments(
            line,
            rendered,
            remaining,
            inserted=inserted,
            in_default_section=in_default_section,
        )
        matched_key = _matching_default_assignment_key(line, remaining)
        if matched_key is None:
            rendered.append(line)
            continue
        rendered.append(_render_default_assignment_line(matched_key, remaining))
    rendered.extend(_remaining_default_assignment_lines(rendered, remaining))
    return "\n".join(rendered) + "\n"


def remove_section(text: str, section_name: str) -> str:
    result: list[str] = []
    in_section = False
    for line in text.splitlines():
        if _is_section_header(line):
            in_section = line == f"[{section_name}]"
            if in_section:
                continue
        if in_section:
            continue
        result.append(line)
    return "\n".join(result).rstrip() + "\n"


def _maybe_insert_default_assignments(
    line: str,
    rendered: list[str],
    remaining: dict[str, str],
    *,
    inserted: bool,
    in_default_section: bool,
) -> tuple[list[str], bool]:
    _validate_default_insert_flags(inserted, in_default_section)
    if not _should_insert_default_assignments(line, inserted=inserted, in_default_section=in_default_section):
        return rendered, inserted
    rendered.extend(_render_remaining_default_assignments(remaining))
    remaining.clear()
    return rendered, True


def _validate_default_insert_flags(inserted: bool, in_default_section: bool) -> None:
    if not isinstance(inserted, bool) or not isinstance(in_default_section, bool):
        raise TypeError("inserted and in_default_section must be bool")


def _should_insert_default_assignments(
    line: str,
    *,
    inserted: bool,
    in_default_section: bool,
) -> bool:
    if inserted:
        return False
    if not in_default_section:
        return False
    return _is_section_header(line)


def _matching_default_assignment_key(line: str, remaining: dict[str, str]) -> str | None:
    return next((key for key in remaining if line.startswith(f"{key}=")), None)


def _render_default_assignment_line(key: str, remaining: dict[str, str]) -> str:
    return f"{key}={remaining.pop(key)}"


def _remaining_default_assignment_lines(rendered: list[str], remaining: dict[str, str]) -> list[str]:
    if not remaining:
        return []
    lines: list[str] = []
    if rendered and rendered[-1].strip():
        lines.append("")
    lines.extend(_render_remaining_default_assignments(remaining))
    return lines


def _render_remaining_default_assignments(remaining: dict[str, str]) -> list[str]:
    return [f"{key}={value}" for key, value in remaining.items()]


def _is_section_header(line: str) -> bool:
    return line.startswith("[") and line.endswith("]")


def append_backends(text: str, lines: list[str]) -> str:
    if not lines:
        return remove_section(text, "Backends")
    result = remove_section(text, "Backends").rstrip()
    return f"{result}\n\n[Backends]\n" + "\n".join(lines) + "\n"
