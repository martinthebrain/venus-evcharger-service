# SPDX-License-Identifier: GPL-3.0-or-later
"""Suggested-energy recommendation handling for the setup wizard runtime."""

from __future__ import annotations

from pathlib import Path

from venus_evcharger.bootstrap.wizard_energy import (
    build_suggested_energy_merge,
    existing_auto_energy_assignments,
    huawei_bundle_files,
    manual_review_union,
    suggested_energy_assignments,
    suggested_energy_sources_with_capacity,
    suggested_energy_sources_with_capacity_overrides,
    validate_unique_suggested_energy_sources,
)
from venus_evcharger.bootstrap.wizard_render import upsert_default_assignments


SuggestedEnergyState = tuple[
    str,
    dict[str, str],
    tuple[str, ...],
    dict[str, str],
    tuple[dict[str, object], ...],
    dict[str, object] | None,
]


def suggested_energy_state(
    config_path: Path,
    config_text: str,
    adapter_files: dict[str, str],
    manual_review: tuple[str, ...],
    recommendation_prefixes: tuple[str, ...],
    *,
    apply_suggested_energy_merge: bool,
    suggested_energy_capacity_wh: float | None,
    suggested_energy_capacity_overrides: dict[str, float] | None,
) -> SuggestedEnergyState:
    suggested_blocks: dict[str, str] = {}
    suggested_sources: tuple[dict[str, object], ...] = tuple()
    suggested_merge: dict[str, object] | None = None
    if not recommendation_prefixes:
        return config_text, adapter_files, manual_review, suggested_blocks, suggested_sources, suggested_merge
    bundle_files, manual_review, suggested_blocks, suggested_sources = merged_recommendation_bundle_state(
        recommendation_prefixes,
        manual_review,
    )
    adapter_files = {**adapter_files, **bundle_files}
    suggested_sources = suggested_energy_sources_with_requested_capacity(
        suggested_sources,
        suggested_energy_capacity_wh,
        suggested_energy_capacity_overrides,
    )
    suggested_merge, merge_files = build_suggested_energy_merge(config_path, suggested_sources)
    adapter_files = {**adapter_files, **merge_files}
    if apply_suggested_energy_merge and suggested_merge is not None:
        config_text, suggested_merge = config_text_with_suggested_energy_merge(
            config_path,
            config_text,
            suggested_sources,
            suggested_merge,
        )
    return config_text, adapter_files, manual_review, suggested_blocks, suggested_sources, suggested_merge


def merged_recommendation_bundle_state(
    recommendation_prefixes: tuple[str, ...],
    manual_review: tuple[str, ...],
) -> tuple[dict[str, str], tuple[str, ...], dict[str, str], tuple[dict[str, object], ...]]:
    bundle_files: dict[str, str] = {}
    bundle_blocks: dict[str, str] = {}
    bundle_sources: list[dict[str, object]] = []
    for recommendation_prefix in recommendation_prefixes:
        huawei_files, huawei_review_items, huawei_blocks, huawei_sources = huawei_bundle_files(recommendation_prefix)
        bundle_files.update(huawei_files)
        bundle_blocks.update(huawei_blocks)
        bundle_sources.extend(dict(source) for source in huawei_sources)
        manual_review = manual_review_union(manual_review, huawei_review_items)
    suggested_sources = validate_unique_suggested_energy_sources(tuple(bundle_sources))
    return bundle_files, manual_review, dict(bundle_blocks), suggested_sources


def suggested_energy_sources_with_requested_capacity(
    suggested_sources: tuple[dict[str, object], ...],
    suggested_energy_capacity_wh: float | None,
    suggested_energy_capacity_overrides: dict[str, float] | None,
) -> tuple[dict[str, object], ...]:
    if suggested_energy_capacity_wh is not None and len(suggested_sources) == 1:
        suggested_sources = suggested_energy_sources_with_capacity(suggested_sources, suggested_energy_capacity_wh)
    return suggested_energy_sources_with_capacity_overrides(
        suggested_sources,
        suggested_energy_capacity_overrides or {},
    )


def config_text_with_suggested_energy_merge(
    config_path: Path,
    config_text: str,
    suggested_sources: tuple[dict[str, object], ...],
    suggested_merge: dict[str, object],
) -> tuple[str, dict[str, object]]:
    config_text = upsert_default_assignments(
        config_text,
        suggested_energy_assignments(existing_auto_energy_assignments(config_path), suggested_sources),
    )
    updated_merge = dict(suggested_merge)
    updated_merge["applied_to_config"] = True
    return config_text, updated_merge
