# SPDX-License-Identifier: GPL-3.0-or-later
"""Policy-default helpers for the setup wizard."""

from __future__ import annotations

from typing import Callable

from venus_evcharger.bootstrap.wizard_import import ImportedWizardDefaults
from venus_evcharger.bootstrap.wizard_models import WizardPolicyMode

AUTO_START_DEFAULT = 1850.0
AUTO_STOP_DEFAULT = 1350.0
AUTO_MIN_SOC_DEFAULT = 30.0
AUTO_RESUME_SOC_DEFAULT = 33.0
SCHEDULED_DAYS_DEFAULT = "Mon,Tue,Wed,Thu,Fri"
SCHEDULED_END_DEFAULT = "06:30"
SCHEDULED_NIGHT_CURRENT_DEFAULT = 0.0

PromptText = Callable[[str, str], str]

_POLICY_DEFAULT_KEYS: tuple[tuple[str, str, float], ...] = (
    ("auto_start_surplus_watts", "auto_start_surplus_watts", AUTO_START_DEFAULT),
    ("auto_stop_surplus_watts", "auto_stop_surplus_watts", AUTO_STOP_DEFAULT),
    ("auto_min_soc", "auto_min_soc", AUTO_MIN_SOC_DEFAULT),
    ("auto_resume_soc", "auto_resume_soc", AUTO_RESUME_SOC_DEFAULT),
)
_POLICY_PROMPTS: tuple[tuple[str, str], ...] = (
    ("auto_start_surplus_watts", "Auto start surplus watts"),
    ("auto_stop_surplus_watts", "Auto stop surplus watts"),
    ("auto_min_soc", "Battery minimum SOC for Auto"),
    ("auto_resume_soc", "Battery resume SOC for Auto"),
)


def policy_defaults(
    policy_mode: WizardPolicyMode,
    imported: ImportedWizardDefaults,
    namespace: object,
) -> tuple[float | None, float | None, float | None, float | None, str | None, str | None, float | None]:
    auto_values = _policy_numeric_defaults(policy_mode, imported, namespace)
    scheduled = _scheduled_defaults(policy_mode, imported, namespace)
    return (
        auto_values["auto_start_surplus_watts"],
        auto_values["auto_stop_surplus_watts"],
        auto_values["auto_min_soc"],
        auto_values["auto_resume_soc"],
        scheduled[0],
        scheduled[1],
        scheduled[2],
    )


def _policy_numeric_defaults(policy_mode: WizardPolicyMode, imported: ImportedWizardDefaults, namespace: object) -> dict[str, float | None]:
    values: dict[str, float | None] = {key: _optional_float_attr(namespace, key) for key, _, _ in _POLICY_DEFAULT_KEYS}
    if policy_mode not in {"auto", "scheduled"}:
        return values
    for key, imported_key, default in _POLICY_DEFAULT_KEYS:
        values[key] = _float_default(values[key], getattr(imported, imported_key), default)
    return values


def _float_default(current_value: float | None, imported_value: float | None, default: float) -> float:
    return float(current_value if current_value is not None else imported_value or default)


def _scheduled_defaults(
    policy_mode: WizardPolicyMode,
    imported: ImportedWizardDefaults,
    namespace: object,
) -> tuple[str | None, str | None, float | None]:
    if policy_mode != "scheduled":
        return _non_scheduled_defaults(namespace)
    return (
        _scheduled_days_default(namespace, imported),
        _scheduled_end_default(namespace, imported),
        float(_scheduled_night_current(namespace, imported)),
    )


def _non_scheduled_defaults(namespace: object) -> tuple[str | None, str | None, float | None]:
    return (
        _optional_str_attr(namespace, "scheduled_enabled_days"),
        _optional_str_attr(namespace, "scheduled_latest_end_time"),
        _optional_float_attr(namespace, "scheduled_night_current_amps"),
    )


def _scheduled_night_current(namespace: object, imported: ImportedWizardDefaults) -> float:
    night_current = _optional_float_attr(namespace, "scheduled_night_current_amps")
    return float(night_current if night_current is not None else imported.scheduled_night_current_amps or SCHEDULED_NIGHT_CURRENT_DEFAULT)


def _scheduled_days_default(namespace: object, imported: ImportedWizardDefaults) -> str:
    return str(_optional_str_attr(namespace, "scheduled_enabled_days") or imported.scheduled_enabled_days or SCHEDULED_DAYS_DEFAULT)


def _scheduled_end_default(namespace: object, imported: ImportedWizardDefaults) -> str:
    return str(_optional_str_attr(namespace, "scheduled_latest_end_time") or imported.scheduled_latest_end_time or SCHEDULED_END_DEFAULT)


def _optional_float_attr(namespace: object, name: str) -> float | None:
    raw_value = getattr(namespace, name)
    return None if raw_value is None else float(raw_value)


def _optional_str_attr(namespace: object, name: str) -> str | None:
    raw_value = getattr(namespace, name)
    return None if raw_value is None else str(raw_value)


def prompt_policy_defaults(
    policy_mode: WizardPolicyMode,
    imported: ImportedWizardDefaults,
    namespace: object,
    *,
    prompt_text: PromptText,
) -> tuple[float | None, float | None, float | None, float | None, str | None, str | None, float | None]:
    defaults = policy_defaults(policy_mode, imported, namespace)
    auto_values = _prompted_policy_values(policy_mode, namespace, prompt_text, defaults[:4])
    scheduled_days, scheduled_latest_end, scheduled_night_current = _prompted_scheduled_values(
        policy_mode,
        namespace,
        prompt_text,
        defaults[4],
        defaults[5],
        defaults[6],
    )
    return (
        auto_values["auto_start_surplus_watts"],
        auto_values["auto_stop_surplus_watts"],
        auto_values["auto_min_soc"],
        auto_values["auto_resume_soc"],
        scheduled_days,
        scheduled_latest_end,
        scheduled_night_current,
    )


def _prompted_policy_values(
    policy_mode: WizardPolicyMode,
    namespace: object,
    prompt_text: PromptText,
    defaults: tuple[float | None, float | None, float | None, float | None],
) -> dict[str, float | None]:
    values = dict(zip((item[0] for item in _POLICY_DEFAULT_KEYS), defaults))
    if policy_mode not in {"auto", "scheduled"}:
        return values
    for key, label in _POLICY_PROMPTS:
        if getattr(namespace, key) is None:
            values[key] = float(prompt_text(label, _format_prompt_default(values[key])))
    return values


def _format_prompt_default(value: float | None) -> str:
    if value is None:
        raise ValueError("Policy prompt default must be resolved before prompting")
    return f"{value:g}"


def _prompted_scheduled_values(
    policy_mode: WizardPolicyMode,
    namespace: object,
    prompt_text: PromptText,
    scheduled_days: str | None,
    scheduled_latest_end: str | None,
    scheduled_night_current: float | None,
) -> tuple[str | None, str | None, float | None]:
    if policy_mode != "scheduled":
        return scheduled_days, scheduled_latest_end, scheduled_night_current
    scheduled_days = _prompted_scheduled_days(namespace, prompt_text, scheduled_days)
    scheduled_latest_end = _prompted_scheduled_end(namespace, prompt_text, scheduled_latest_end)
    scheduled_night_current = _prompted_scheduled_current(namespace, prompt_text, scheduled_night_current)
    return scheduled_days, scheduled_latest_end, scheduled_night_current


def _prompted_scheduled_days(namespace: object, prompt_text: PromptText, scheduled_days: str | None) -> str | None:
    if getattr(namespace, "scheduled_enabled_days") is None:
        return prompt_text("Scheduled weekdays", scheduled_days or SCHEDULED_DAYS_DEFAULT)
    return scheduled_days


def _prompted_scheduled_end(namespace: object, prompt_text: PromptText, scheduled_latest_end: str | None) -> str | None:
    if getattr(namespace, "scheduled_latest_end_time") is None:
        return prompt_text("Scheduled latest end time (HH:MM)", scheduled_latest_end or SCHEDULED_END_DEFAULT)
    return scheduled_latest_end


def _prompted_scheduled_current(namespace: object, prompt_text: PromptText, scheduled_night_current: float | None) -> float | None:
    if getattr(namespace, "scheduled_night_current_amps") is None:
        return float(prompt_text("Scheduled fallback night current amps", f"{scheduled_night_current or 0.0:g}"))
    return scheduled_night_current
