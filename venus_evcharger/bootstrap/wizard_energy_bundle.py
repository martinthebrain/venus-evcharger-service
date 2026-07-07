# SPDX-License-Identifier: GPL-3.0-or-later
"""Bundle naming helpers for external energy-source wizard recommendations."""

from __future__ import annotations


def bundle_source_id(config_snippet: str, default_source_id: str) -> str:
    for raw_line in config_snippet.splitlines():
        source_id = _bundle_source_id_from_line(raw_line)
        if source_id:
            return source_id
    return default_source_id


def _bundle_source_id_from_line(raw_line: str) -> str:
    line = raw_line.strip()
    if not line.startswith("AutoEnergySource.") or "=" not in line:
        return ""
    remainder = line[len("AutoEnergySource.") :]
    source_id, separator, _field = remainder.partition(".")
    if separator != ".":
        return ""
    return source_id.strip()


def _normalized_source_id(source_id: str) -> str:
    return source_id.strip() or "huawei"


def bundle_target_names(source_id: str) -> dict[str, str]:
    normalized_source_id = _normalized_source_id(source_id)
    if normalized_source_id == "huawei":
        return {
            "ini": "wizard-huawei-energy.ini",
            "wizard": "wizard-huawei-energy.wizard.txt",
            "summary": "wizard-huawei-energy.summary.txt",
        }
    return {
        "ini": f"wizard-energy-{normalized_source_id}.ini",
        "wizard": f"wizard-energy-{normalized_source_id}.wizard.txt",
        "summary": f"wizard-energy-{normalized_source_id}.summary.txt",
    }


def bundle_labels(source_id: str) -> tuple[str, str]:
    normalized_source_id = _normalized_source_id(source_id)
    if normalized_source_id == "huawei":
        return (
            "External energy source integration",
            "Set usable battery capacity for weighted combined SOC",
        )
    return (
        f"External energy source integration ({normalized_source_id})",
        f"Set usable battery capacity for weighted combined SOC ({normalized_source_id})",
    )


def bundle_block_label(source_id: str) -> str:
    normalized_source_id = _normalized_source_id(source_id)
    if normalized_source_id == "huawei":
        return "External energy source"
    return f"External energy source ({normalized_source_id})"
