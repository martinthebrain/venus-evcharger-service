# SPDX-License-Identifier: GPL-3.0-or-later
"""Victron ESS payload helpers for runtime-state snapshots."""

from __future__ import annotations

from collections.abc import Mapping

from venus_evcharger.core.contracts import finite_float_or_none


def _victron_ess_balance_energy_ids(svc: object) -> list[str]:
    energy_ids: list[str] = []
    for definition in tuple(getattr(svc, "auto_energy_sources", None) or ()):
        normalized_id = str(getattr(definition, "source_id", None) or "").strip()
        if normalized_id:
            energy_ids.append(normalized_id)
    return energy_ids


def _victron_ess_balance_runtime_string(svc: object, attr_name: str) -> str:
    return str(getattr(svc, attr_name, None) or "").strip()


def _victron_ess_balance_runtime_non_negative_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return max(0, int(value))
    return 0


def _victron_ess_balance_runtime_attr_text(
    svc: object,
    attr_name: str,
    *,
    fallback: str = "",
    normalize_lower: bool = False,
) -> str:
    value = str(getattr(svc, attr_name, None) or fallback).strip()
    return value.lower() if normalize_lower else value


def _victron_ess_balance_runtime_profile_text(
    profile: Mapping[str, object],
    key: str,
    *,
    fallback: str = "",
) -> str:
    return str(profile.get(key) or fallback)


def _victron_ess_balance_runtime_profile_sample_count(profile: Mapping[str, object]) -> int:
    explicit_sample_count = profile.get("sample_count")
    if explicit_sample_count is not None:
        return _victron_ess_balance_runtime_non_negative_int(explicit_sample_count)
    return max(
        _victron_ess_balance_runtime_non_negative_int(profile.get("delay_samples")),
        _victron_ess_balance_runtime_non_negative_int(profile.get("gain_samples")),
        _victron_ess_balance_runtime_non_negative_int(profile.get("settled_count"))
        + _victron_ess_balance_runtime_non_negative_int(profile.get("overshoot_count")),
    )


def _victron_ess_balance_runtime_profile_metric(
    profile: Mapping[str, object],
    key: str,
    fallback_key: str | None = None,
) -> float | None:
    if fallback_key is None:
        return finite_float_or_none(profile.get(key))
    return finite_float_or_none(profile.get(key, profile.get(fallback_key)))


def _victron_ess_balance_runtime_profile_identity(
    profile_key: str,
    profile: Mapping[str, object],
) -> dict[str, object]:
    return {
        "key": _victron_ess_balance_runtime_profile_text(profile, "key", fallback=profile_key),
        "action_direction": _victron_ess_balance_runtime_profile_text(profile, "action_direction"),
        "site_regime": _victron_ess_balance_runtime_profile_text(profile, "site_regime"),
        "direction": _victron_ess_balance_runtime_profile_text(profile, "direction"),
        "day_phase": _victron_ess_balance_runtime_profile_text(profile, "day_phase"),
        "reserve_phase": _victron_ess_balance_runtime_profile_text(profile, "reserve_phase"),
        "ev_phase": _victron_ess_balance_runtime_profile_text(profile, "ev_phase"),
        "pv_phase": _victron_ess_balance_runtime_profile_text(profile, "pv_phase"),
        "battery_limit_phase": _victron_ess_balance_runtime_profile_text(profile, "battery_limit_phase"),
    }


def _victron_ess_balance_runtime_profile_counts(profile: Mapping[str, object]) -> dict[str, object]:
    return {
        "sample_count": _victron_ess_balance_runtime_profile_sample_count(profile),
        "delay_samples": _victron_ess_balance_runtime_non_negative_int(profile.get("delay_samples")),
        "gain_samples": _victron_ess_balance_runtime_non_negative_int(profile.get("gain_samples")),
        "overshoot_count": _victron_ess_balance_runtime_non_negative_int(profile.get("overshoot_count")),
        "settled_count": _victron_ess_balance_runtime_non_negative_int(profile.get("settled_count")),
    }


def _victron_ess_balance_runtime_profile_response_metrics(profile: Mapping[str, object]) -> dict[str, object]:
    return {
        "response_delay_seconds": _victron_ess_balance_runtime_profile_metric(profile, "response_delay_seconds"),
        "estimated_gain": _victron_ess_balance_runtime_profile_metric(profile, "estimated_gain"),
        "response_delay_mad_seconds": _victron_ess_balance_runtime_profile_metric(
            profile,
            "response_delay_mad_seconds",
        ),
        "gain_mad": _victron_ess_balance_runtime_profile_metric(profile, "gain_mad"),
        "stability_score": _victron_ess_balance_runtime_profile_metric(profile, "stability_score"),
    }


def _victron_ess_balance_runtime_profile_learning_metrics(profile: Mapping[str, object]) -> dict[str, object]:
    return {
        "typical_response_delay_seconds": _victron_ess_balance_runtime_profile_metric(
            profile,
            "typical_response_delay_seconds",
            "response_delay_seconds",
        ),
        "effective_gain": _victron_ess_balance_runtime_profile_metric(
            profile,
            "effective_gain",
            "estimated_gain",
        ),
        "regime_consistency_score": _victron_ess_balance_runtime_profile_metric(
            profile,
            "regime_consistency_score",
        ),
        "response_variance_score": _victron_ess_balance_runtime_profile_metric(
            profile,
            "response_variance_score",
        ),
        "reproducibility_score": _victron_ess_balance_runtime_profile_metric(
            profile,
            "reproducibility_score",
        ),
    }


def _victron_ess_balance_runtime_profile_limit_metrics(profile: Mapping[str, object]) -> dict[str, object]:
    return {
        "safe_ramp_rate_watts_per_second": _victron_ess_balance_runtime_profile_metric(
            profile,
            "safe_ramp_rate_watts_per_second",
        ),
        "preferred_bias_limit_watts": _victron_ess_balance_runtime_profile_metric(
            profile,
            "preferred_bias_limit_watts",
        ),
    }
