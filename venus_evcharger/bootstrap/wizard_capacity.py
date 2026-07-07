# SPDX-License-Identifier: GPL-3.0-or-later
"""Capacity prompt and parsing helpers for the setup wizard."""

from __future__ import annotations

import argparse
from typing import Callable

from venus_evcharger.bootstrap.wizard_cli import prompt_yes_no
from venus_evcharger.bootstrap.wizard_energy import optional_capacity_wh


def resolved_energy_capacity_wh(
    namespace: argparse.Namespace,
    recommendation_prefixes: tuple[str, ...],
    *,
    prompt_yes_no_fn: Callable[[str, bool], bool] = prompt_yes_no,
    input_fn: Callable[[str], str] = input,
) -> float | None:
    """Return the chosen usable capacity for one suggested energy source."""
    direct = _direct_energy_capacity_wh(namespace)
    if direct is not None or _non_interactive_enabled(namespace):
        return direct
    if len(recommendation_prefixes) != 1:
        return None
    if not prompt_yes_no_fn("Set usable battery capacity for the suggested energy source now?", False):
        return None
    return optional_capacity_wh(input_fn("Usable battery capacity in Wh [skip]: ").strip())


def resolved_energy_capacity_overrides(namespace: argparse.Namespace) -> dict[str, float]:
    """Return per-source usable capacity overrides."""
    raw_values = getattr(namespace, "energy_usable_capacity_wh", None)
    if not raw_values:
        return {}
    overrides: dict[str, float] = {}
    for raw_value in raw_values:
        normalized_source_id, capacity = _parsed_energy_capacity_override(raw_value)
        overrides[normalized_source_id] = capacity
    return overrides


def _direct_energy_capacity_wh(namespace: argparse.Namespace) -> float | None:
    direct = optional_capacity_wh(getattr(namespace, "energy_default_usable_capacity_wh", None))
    if direct is not None:
        return direct
    return optional_capacity_wh(getattr(namespace, "huawei_usable_capacity_wh", None))


def _non_interactive_enabled(namespace: argparse.Namespace) -> bool:
    try:
        raw_value = namespace.non_interactive
    except AttributeError:
        return False
    return bool(raw_value)


def _parsed_energy_capacity_override(raw_value: object) -> tuple[str, float]:
    item = str(raw_value).strip()
    if "=" not in item:
        raise ValueError("energy usable capacity overrides must use source_id=Wh, for example huawei_a=15360")
    source_id, capacity_text = item.split("=", 1)
    normalized_source_id = source_id.strip()
    capacity = optional_capacity_wh(capacity_text)
    if not normalized_source_id or capacity is None:
        raise ValueError("energy usable capacity overrides must use source_id=Wh with a positive Wh value")
    return normalized_source_id, capacity
