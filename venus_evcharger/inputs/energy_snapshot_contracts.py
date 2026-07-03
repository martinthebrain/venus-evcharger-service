# SPDX-License-Identifier: GPL-3.0-or-later
"""Small validation helpers for energy snapshot payload boundaries."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TypeGuard

from venus_evcharger.energy import EnergyLearningProfile, EnergySourceDefinition


def energy_source_definitions(value: object) -> tuple[EnergySourceDefinition, ...]:
    if isinstance(value, EnergySourceDefinition):
        return (value,)
    if not is_source_definition_iterable(value):
        return ()
    return tuple(item for item in value if isinstance(item, EnergySourceDefinition))


def is_source_definition_iterable(value: object) -> TypeGuard[Iterable[object]]:
    if isinstance(value, (str, bytes, Mapping)):
        return False
    return isinstance(value, Iterable)


def object_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def nested_object_mappings(value: object) -> dict[str, dict[str, object]]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): object_mapping(item)
        for key, item in value.items()
        if isinstance(item, Mapping)
    }


def learning_profiles(value: object) -> dict[str, EnergyLearningProfile | Mapping[str, object]]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(source_id): profile
        for source_id, profile in value.items()
        if isinstance(profile, (EnergyLearningProfile, Mapping))
    }


def learning_profile_payloads(value: object) -> dict[str, dict[str, object]]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(source_id): object_mapping(profile.as_dict())
        for source_id, profile in value.items()
        if isinstance(profile, EnergyLearningProfile)
    }
