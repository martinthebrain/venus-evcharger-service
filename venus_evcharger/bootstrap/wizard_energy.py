# SPDX-License-Identifier: GPL-3.0-or-later
"""Energy-source recommendation merge helpers for the setup wizard.

The wizard can import recommended energy-source blocks from bundle manifests,
merge them with existing config, and ask focused follow-up questions for
capacity values. This module keeps that flow deterministic: source IDs are
normalized, duplicates are rejected early, optional capacity overrides are
validated against source capabilities, and rendered snippets preserve the same
assignment shape used by the runtime parser.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from venus_evcharger.energy.recommendation_schema import (
    recommendation_bundle_manifest_path,
    validate_recommendation_bundle_manifest,
)


def _source_text(source: dict[str, object], key: str) -> str:
    value = source.get(key)
    if value is None:
        return ""
    return str(value).strip()


def _source_id(source: dict[str, object]) -> str:
    return _source_text(source, "source_id")


def _source_capacity_key(source: dict[str, object]) -> str:
    return _source_text(source, "capacityConfigKey")


def _split_config_assignment(line: str) -> tuple[str, str]:
    key, separator, value = line.partition("=")
    if separator != "=":
        raise ValueError(f"Config assignment line is missing '=': {line}")
    return key, value


def _config_text_value(values: Mapping[str, object], key: str) -> str:
    value = values.get(key)
    if value is None:
        return ""
    return str(value).strip()


def _case_preserving_config(config_path: Path):
    from .wizard_render import CasePreservingConfigParser

    parser = CasePreservingConfigParser()
    parser.read(config_path, encoding="utf-8")  # pragma: no mutate
    return parser


def _read_text_utf8(path: Path) -> str:
    return path.read_text(encoding="utf-8")  # pragma: no mutate


def _config_auto_energy_sources_value(config_path: Path) -> str:
    if not config_path.exists():
        return ""
    parser = _case_preserving_config(config_path)
    return _config_text_value(parser.defaults(), "AutoEnergySources")


def existing_auto_energy_source_ids(config_path: Path) -> tuple[str, ...]:
    raw = _config_auto_energy_sources_value(config_path)
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def optional_capacity_wh(value: object) -> float | None:
    try:
        capacity = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return capacity if capacity > 0.0 else None


def _prefix_items(prefixes: str | tuple[str, ...] | list[str]) -> tuple[str, ...]:
    if isinstance(prefixes, str):
        return (prefixes,)
    return tuple(str(item) for item in prefixes)


def _normalized_prefix(value: str) -> str:
    return value.strip()


def normalized_recommendation_prefixes(
    prefixes: str | tuple[str, ...] | list[str] | None,
) -> tuple[str, ...]:
    if prefixes is None:
        return ()
    normalized = (_normalized_prefix(item) for item in _prefix_items(prefixes))
    return tuple(item for item in normalized if item)


def merged_recommendation_prefixes(
    *prefix_groups: str | tuple[str, ...] | list[str] | None,
) -> tuple[str, ...]:
    merged: list[str] = []
    for group in prefix_groups:
        for prefix in normalized_recommendation_prefixes(group):
            if prefix not in merged:
                merged.append(prefix)
    return tuple(merged)


def existing_auto_energy_assignments(config_path: Path) -> dict[str, str]:
    if not config_path.exists():
        return {}
    parser = _case_preserving_config(config_path)
    assignments: dict[str, str] = {}
    for key, value in parser.defaults().items():
        if _is_auto_energy_assignment_key(key):
            assignments[key] = str(value).strip()
    return assignments


def merge_energy_source_ids(existing_ids: tuple[str, ...], suggested_sources: tuple[dict[str, object], ...]) -> tuple[str, ...]:
    merged = list(existing_ids)
    for source in suggested_sources:
        source_id = _source_id(source)
        if source_id and source_id not in merged:
            merged.append(source_id)
    return tuple(merged)


def _energy_source_list_ids(raw_value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw_value.split(",") if item.strip())


def existing_source_ids_from_assignments(assignments: dict[str, str]) -> tuple[str, ...]:
    source_ids = list(_energy_source_list_ids(assignments.get("AutoEnergySources", "")))
    for key in assignments:
        source_id = _assignment_source_id(key)
        if source_id and source_id not in source_ids:
            source_ids.append(source_id)
    return tuple(source_ids)


def _is_auto_energy_assignment_key(key: str) -> bool:
    return key in {"AutoUseCombinedBatterySoc", "AutoEnergySources"} or key.startswith(
        "AutoEnergySource."
    )


def _assignment_source_id(key: str) -> str:
    if not key.startswith("AutoEnergySource."):
        return ""
    source_id, separator, _field = key[len("AutoEnergySource.") :].partition(".")
    if separator != ".":
        return ""
    return source_id.strip()


def suggested_energy_sources_with_capacity(
    suggested_sources: tuple[dict[str, object], ...],
    capacity_wh: float | None,
) -> tuple[dict[str, object], ...]:
    capacity = optional_capacity_wh(capacity_wh)
    if capacity is None:
        return suggested_sources
    updated_sources: list[dict[str, object]] = []
    for source in suggested_sources:
        updated = dict(source)
        if _source_supports_capacity_override(updated):
            updated["usableCapacityWh"] = capacity
        updated_sources.append(updated)
    return tuple(updated_sources)


def suggested_energy_sources_with_capacity_overrides(
    suggested_sources: tuple[dict[str, object], ...],
    capacity_overrides: dict[str, float],
) -> tuple[dict[str, object], ...]:
    if not capacity_overrides:
        return suggested_sources
    _validate_capacity_overrides(suggested_sources, capacity_overrides)
    return tuple(
        _source_with_capacity_override(dict(source), capacity_overrides)
        for source in suggested_sources
    )


def existing_auto_energy_source_ids_from_suggestions(
    suggested_sources: tuple[dict[str, object], ...],
) -> set[str]:
    return {source_id for source in suggested_sources if (source_id := _source_id(source))}


def unknown_capacity_override_source_ids(
    capacity_overrides: dict[str, float],
    known_source_ids: set[str],
) -> list[str]:
    return sorted(source_id for source_id in capacity_overrides if source_id not in known_source_ids)


def _validate_capacity_overrides(
    suggested_sources: tuple[dict[str, object], ...],
    capacity_overrides: dict[str, float],
) -> None:
    known_source_ids = existing_auto_energy_source_ids_from_suggestions(suggested_sources)
    unknown_source_ids = unknown_capacity_override_source_ids(capacity_overrides, known_source_ids)
    if not unknown_source_ids:
        return
    raise ValueError(
        "energy usable capacity overrides reference unknown source ids: "
        + ", ".join(unknown_source_ids)
    )


def _source_supports_capacity_override(source: dict[str, object]) -> bool:
    return bool(_source_id(source) and _source_capacity_key(source))


def _source_with_capacity_override(
    source: dict[str, object],
    capacity_overrides: dict[str, float],
) -> dict[str, object]:
    if not _source_supports_capacity_override(source):
        return source
    capacity = capacity_overrides.get(_source_id(source))
    if capacity is not None:
        source["usableCapacityWh"] = capacity
    return source


def validate_unique_suggested_energy_sources(
    suggested_sources: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    seen: dict[str, int] = {}
    duplicates: list[str] = []
    for source in suggested_sources:
        source_id = _source_id(source)
        if not source_id:
            continue
        seen[source_id] = seen.get(source_id, 0) + 1
        if seen[source_id] == 2:
            duplicates.append(source_id)
    if duplicates:
        raise ValueError(
            "multiple recommendation bundles resolved to the same source id: "
            + ", ".join(sorted(duplicates))
        )
    return suggested_sources


def energy_source_merge_lines(source: dict[str, object]) -> list[str]:
    source_id = _source_id(source)
    if not source_id:
        return []
    mapping = (
        ("profile", "Profile"),
        ("configPath", "ConfigPath"),
        ("host", "Host"),
        ("port", "Port"),
        ("unitId", "UnitId"),
        ("usableCapacityWh", "UsableCapacityWh"),
    )
    lines: list[str] = []
    for source_key, config_key in mapping:
        value = source.get(source_key)
        if value in (None, ""):
            continue
        if isinstance(value, float):
            rendered_value = f"{value:g}"
        else:
            rendered_value = str(value)
        lines.append(f"AutoEnergySource.{source_id}.{config_key}={rendered_value}")
    return lines


def energy_source_capacity_follow_up(source: dict[str, object]) -> dict[str, object] | None:
    source_id = _source_id(source)
    config_key = _source_capacity_key(source)
    hint = _source_text(source, "capacityHint")
    if not source_id or not config_key:
        return None
    configured_capacity = optional_capacity_wh(source.get("usableCapacityWh"))
    return {
        "source_id": source_id,
        "config_key": config_key,
        "placeholder": f"{configured_capacity:g}" if configured_capacity is not None else "<set-me>",
        "hint": hint or "Set usable battery capacity in Wh for weighted combined SOC.",
        "configured": configured_capacity is not None,
    }


def suggested_energy_assignments(
    existing_assignments: dict[str, str],
    suggested_sources: tuple[dict[str, object], ...],
) -> dict[str, str]:
    assignments = dict(existing_assignments)
    merged_ids = merge_energy_source_ids(existing_source_ids_from_assignments(assignments), suggested_sources)
    ordered: dict[str, str] = {
        "AutoUseCombinedBatterySoc": "1",
        "AutoEnergySources": ",".join(merged_ids),
    }
    for key, value in assignments.items():
        if key in ordered:
            continue
        ordered[key] = value
    for source in suggested_sources:
        for line in energy_source_merge_lines(source):
            key, value = _split_config_assignment(line)
            ordered[key] = value
    return ordered


def suggested_energy_capacity_follow_up(
    suggested_sources: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    return tuple(
        item for item in (energy_source_capacity_follow_up(source) for source in suggested_sources) if item is not None
    )


def suggested_energy_merge_lines(
    merged_ids: tuple[str, ...],
    suggested_sources: tuple[dict[str, object], ...],
    capacity_follow_up: tuple[dict[str, object], ...],
) -> list[str]:
    merge_lines = [
        "# Merge these lines into the main config when you want the suggested external energy source enabled.",
        "AutoUseCombinedBatterySoc=1",
        "AutoEnergySources=" + ",".join(merged_ids),
    ]
    for source in suggested_sources:
        merge_lines.extend(energy_source_merge_lines(source))
    if capacity_follow_up:
        merge_lines.append("# Optional but recommended for weighted combined SOC:")
        for item in capacity_follow_up:
            merge_lines.append(f"# {item['config_key']}={item['placeholder']}")
    return merge_lines


def build_suggested_energy_merge(
    config_path: Path,
    suggested_sources: tuple[dict[str, object], ...],
) -> tuple[dict[str, object] | None, dict[str, str]]:
    if not suggested_sources:
        return None, {}
    existing_assignments = existing_auto_energy_assignments(config_path)
    existing_ids = existing_source_ids_from_assignments(existing_assignments)
    merged_ids = merge_energy_source_ids(existing_ids, suggested_sources)
    capacity_follow_up = suggested_energy_capacity_follow_up(suggested_sources)
    merge_lines = suggested_energy_merge_lines(merged_ids, suggested_sources, capacity_follow_up)
    merge_block = "\n".join(merge_lines)
    merge_payload: dict[str, object] = {
        "existing_source_ids": list(existing_ids),
        "merged_source_ids": list(merged_ids),
        "auto_use_combined_battery_soc": True,
        "helper_file": "wizard-auto-energy-merge.ini",
        "merge_block": merge_block,
        "capacity_follow_up": [dict(item) for item in capacity_follow_up],
        "applied_to_config": False,
    }
    return merge_payload, {"wizard-auto-energy-merge.ini": merge_block + "\n"}


def _structured_energy_source_line(raw_line: str, prefix: str) -> str | None:
    line = raw_line.strip()
    if not line:
        return None
    if not line.startswith(prefix):
        return None
    if "=" not in line:
        return None
    return line


def _structured_energy_source_value(field_name: str, raw_value: str) -> object:
    if field_name not in {"Port", "UnitId"}:
        return raw_value.strip()
    try:
        return int(raw_value.strip())
    except ValueError:
        return raw_value.strip()


def _structured_energy_source_field(raw_line: str, prefix: str) -> tuple[str, object] | None:
    line = _structured_energy_source_line(raw_line, prefix)
    if line is None:
        return None
    key, value = _split_config_assignment(line)
    field_name = key[len(prefix) :]
    return field_name[0].lower() + field_name[1:], _structured_energy_source_value(field_name, value)


def structured_energy_source_from_block(
    source_id: str,
    config_snippet: str,
) -> dict[str, object]:
    fields: dict[str, object] = {"source_id": source_id}
    prefix = f"AutoEnergySource.{source_id}."
    for raw_line in config_snippet.splitlines():
        parsed = _structured_energy_source_field(raw_line, prefix)
        if parsed is None:
            continue
        field_name, parsed_value = parsed
        fields[field_name] = parsed_value
    return fields


def bundle_source_id(config_snippet: str, default_source_id: str) -> str:
    for raw_line in config_snippet.splitlines():
        line = raw_line.strip()
        if not line.startswith("AutoEnergySource.") or "=" not in line:
            continue
        remainder = line[len("AutoEnergySource.") :]
        source_id, separator, _field = remainder.partition(".")
        if separator != ".":
            continue
        source_id = source_id.strip()
        if source_id:
            return source_id
    return default_source_id


def bundle_target_names(source_id: str) -> dict[str, str]:
    normalized_source_id = source_id.strip() or "huawei"
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
    normalized_source_id = source_id.strip() or "huawei"
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
    normalized_source_id = source_id.strip() or "huawei"
    if normalized_source_id == "huawei":
        return "External energy source"
    return f"External energy source ({normalized_source_id})"


def manual_review_union(
    existing_items: tuple[str, ...],
    new_items: tuple[str, ...],
) -> tuple[str, ...]:
    merged = list(existing_items)
    for item in new_items:
        if item not in merged:
            merged.append(item)
    return tuple(merged)


def huawei_bundle_files(
    prefix: str,
    *,
    source_id: str = "huawei",
) -> tuple[dict[str, str], tuple[str, ...], dict[str, str], tuple[dict[str, object], ...]]:
    base = Path(prefix)
    manifest_path = recommendation_bundle_manifest_path(str(base))
    if manifest_path.exists():
        manifest = validate_recommendation_bundle_manifest(json.loads(_read_text_utf8(manifest_path)))
        manifest_files = manifest["files"]
        source_paths = {
            "wizard-huawei-energy.ini": Path(manifest_files["config_snippet"]),
            "wizard-huawei-energy.wizard.txt": Path(manifest_files["wizard_hint"]),
            "wizard-huawei-energy.summary.txt": Path(manifest_files["summary"]),
        }
        source_id = str(manifest["source_id"])
    else:
        source_paths = {
            "wizard-huawei-energy.ini": Path(str(base) + ".ini"),
            "wizard-huawei-energy.wizard.txt": Path(str(base) + ".wizard.txt"),
            "wizard-huawei-energy.summary.txt": Path(str(base) + ".summary.txt"),
        }
    missing = [str(path) for path in source_paths.values() if not path.exists()]
    if missing:
        raise ValueError("Huawei recommendation bundle is incomplete: " + ", ".join(missing))
    source_contents = {
        "ini": _read_text_utf8(source_paths["wizard-huawei-energy.ini"]),
        "wizard": _read_text_utf8(source_paths["wizard-huawei-energy.wizard.txt"]),
        "summary": _read_text_utf8(source_paths["wizard-huawei-energy.summary.txt"]),
    }
    resolved_source_id = bundle_source_id(source_contents["ini"], source_id)
    target_names = bundle_target_names(resolved_source_id)
    rendered_files = {
        target_names["ini"]: source_contents["ini"],
        target_names["wizard"]: source_contents["wizard"],
        target_names["summary"]: source_contents["summary"],
    }
    structured_source = structured_energy_source_from_block(resolved_source_id, source_contents["ini"])
    structured_source["capacityRequiredForWeightedSoc"] = True
    structured_source["capacityConfigKey"] = f"AutoEnergySource.{resolved_source_id}.UsableCapacityWh"
    structured_source["capacityHint"] = "Set usable battery capacity in Wh for weighted combined SOC."
    review_items = bundle_labels(resolved_source_id)
    block_label = bundle_block_label(resolved_source_id)
    return (
        rendered_files,
        review_items,
        {block_label: source_contents["ini"]},
        (structured_source,),
    )
