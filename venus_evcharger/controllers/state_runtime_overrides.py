# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime-override persistence helpers for the state controller."""

from __future__ import annotations

import configparser
import logging
from collections.abc import Iterable
from io import StringIO
from typing import Callable

from venus_evcharger.auto.policy import AutoPolicy
from venus_evcharger.controllers.errors import RUNTIME_OVERRIDE_READ_ERRORS, RUNTIME_PERSISTENCE_WRITE_ERRORS
from venus_evcharger.controllers.state_contracts import string_value_mapping
from venus_evcharger.controllers.state_runtime_normalize import RuntimeStateNormalizer, require_runtime_clock
from venus_evcharger.controllers.state_specs import (
    RUNTIME_OVERRIDE_BY_CONFIG_KEY,
    RUNTIME_OVERRIDE_SECTION,
    RUNTIME_OVERRIDE_SPECS,
    CasePreservingConfigParser,
    RuntimeOverrideSpec,
)
from venus_evcharger.core.common import DEFAULT_SCHEDULED_ENABLED_DAYS, normalize_hhmm_text, scheduled_enabled_days_text
from venus_evcharger.core.shared import compact_json, write_text_atomically

_DEVICE_INSTANCE_KEY = "DeviceInstance"
_RUNTIME_OVERRIDES_PATH_KEY = "RuntimeOverridesPath"
_DEFAULT_DEVICE_INSTANCE = "60"
_DEFAULT_SCHEDULE_END = "06:30"
_INTEGER_OVERRIDE_KINDS = frozenset(("bool", "int"))


class RuntimeOverrideStore:
    """Persist the small, user-controlled runtime override set."""

    def __init__(self, service: object, normalizer: RuntimeStateNormalizer) -> None:
        self.service = service
        self.normalizer = normalizer

    @staticmethod
    def _write_text_atomically(path: str, payload: str) -> None:
        write_text_atomically(path, payload)

    @staticmethod
    def runtime_overrides_path(defaults: configparser.SectionProxy) -> str:
        device_instance = defaults.get(_DEVICE_INSTANCE_KEY, _DEFAULT_DEVICE_INSTANCE).strip() or _DEFAULT_DEVICE_INSTANCE
        fallback = f"/run/dbus-venus-evcharger-overrides-{device_instance}.ini"
        return defaults.get(_RUNTIME_OVERRIDES_PATH_KEY, fallback).strip()

    @classmethod
    def _read_runtime_override_values(cls, path: str) -> dict[str, str]:
        if not str(path).strip():
            return {}
        parser = CasePreservingConfigParser()
        try:
            read_files = parser.read(path)
        except RUNTIME_OVERRIDE_READ_ERRORS as error:
            logging.warning("Unable to read runtime overrides from %s: %s", path, error)
            return {}
        if not read_files or not parser.has_section(RUNTIME_OVERRIDE_SECTION):
            return {}
        return cls._normalized_runtime_override_section_items(parser[RUNTIME_OVERRIDE_SECTION].items())

    @classmethod
    def _normalized_runtime_override_section_items(cls, items: Iterable[tuple[str, str]]) -> dict[str, str]:
        values: dict[str, str] = {}
        for config_key, raw_value in items:
            normalized_item = cls._normalized_runtime_override_item(config_key, raw_value)
            if normalized_item is not None:
                key, value = normalized_item
                values[key] = value
        return values

    @staticmethod
    def _normalized_runtime_override_item(config_key: object, raw_value: object) -> tuple[str, str] | None:
        spec = RUNTIME_OVERRIDE_BY_CONFIG_KEY.get(str(config_key).strip())
        if spec is None:
            return None
        return spec.config_key, str(raw_value).strip()

    def apply_to_config(self, config: configparser.ConfigParser) -> configparser.ConfigParser:
        svc = self.service
        defaults = config["DEFAULT"]
        path = self.runtime_overrides_path(defaults)
        values = self._read_runtime_override_values(path)
        for config_key, value in values.items():
            defaults[config_key] = str(value)
        setattr(svc, "runtime_overrides_path", path)
        setattr(svc, "_runtime_overrides_active", bool(values))
        setattr(svc, "_runtime_overrides_values", dict(values))
        setattr(svc, "_runtime_overrides_serialized", compact_json(values))
        return config

    def _override_value_as_text(self, spec: RuntimeOverrideSpec, value: object) -> str:
        renderers: dict[str, Callable[[object], str]] = {
            "bool": lambda raw: str(int(bool(raw))),
            "int": lambda raw: str(self.normalizer.coerce_runtime_int(raw)),
            "phase": lambda raw: str(self.normalizer.phase_selection(raw)),
            "weekday_set": scheduled_enabled_days_text,
            "hhmm": normalize_hhmm_text,
            "float": lambda raw: str(self.normalizer.coerce_runtime_float(raw)),
        }
        return renderers[spec.value_kind](value)

    @staticmethod
    def _runtime_override_default_value(spec: RuntimeOverrideSpec) -> object:
        if spec.value_kind in _INTEGER_OVERRIDE_KINDS:
            return 0
        if spec.value_kind == "phase":
            return "P1"
        if spec.value_kind == "weekday_set":
            return DEFAULT_SCHEDULED_ENABLED_DAYS
        if spec.value_kind == "hhmm":
            return _DEFAULT_SCHEDULE_END
        return 0.0

    def _current_runtime_override_value(self, svc: object, spec: RuntimeOverrideSpec) -> object:
        """Read one override from its canonical policy or runtime owner."""
        if spec.policy_setting is not None:
            policy = getattr(svc, "auto_policy", None)
            if not isinstance(policy, AutoPolicy):
                raise TypeError("state service must expose AutoPolicy as auto_policy")
            return spec.policy_setting.read(policy)
        if spec.attr_name is None:
            return self._runtime_override_default_value(spec)
        return getattr(svc, spec.attr_name, self._runtime_override_default_value(spec))

    def current(self) -> dict[str, str]:
        svc = self.service
        values: dict[str, str] = {}
        for spec in RUNTIME_OVERRIDE_SPECS:
            raw_value = self._current_runtime_override_value(svc, spec)
            values[spec.config_key] = self._override_value_as_text(spec, raw_value)
        return values

    def serialized(self) -> str:
        return compact_json(self.current())

    def _runtime_override_write_min_interval_seconds(self, svc: object) -> float:
        configured = self.normalizer.optional_float(
            getattr(svc, "runtime_overrides_write_min_interval_seconds", None)
        )
        return 1.0 if configured is None else max(0.0, float(configured))

    def _runtime_now(self, svc: object) -> float:
        return self.normalizer.load_time(require_runtime_clock(svc))

    @staticmethod
    def _clear_pending_runtime_overrides(svc: object) -> None:
        setattr(svc, "_runtime_overrides_pending_serialized", None)
        setattr(svc, "_runtime_overrides_pending_values", None)
        setattr(svc, "_runtime_overrides_pending_text", None)
        setattr(svc, "_runtime_overrides_pending_due_at", None)

    @staticmethod
    def _runtime_override_ini_text(payload: dict[str, str]) -> str:
        parser = CasePreservingConfigParser()
        parser[RUNTIME_OVERRIDE_SECTION] = payload
        handle = StringIO()
        parser.write(handle)
        return handle.getvalue()

    def _stage_runtime_overrides_write(
        self,
        svc: object,
        payload: dict[str, str],
        serialized: str,
        rendered: str,
        due_at: float,
    ) -> None:
        setattr(svc, "_runtime_overrides_pending_serialized", serialized)
        setattr(svc, "_runtime_overrides_pending_values", dict(payload))
        setattr(svc, "_runtime_overrides_pending_text", rendered)
        setattr(svc, "_runtime_overrides_pending_due_at", float(due_at))
        setattr(svc, "_runtime_overrides_active", True)
        setattr(svc, "_runtime_overrides_values", dict(payload))

    def _write_runtime_overrides_payload(
        self,
        svc: object,
        path: str,
        payload: dict[str, str],
        serialized: str,
        rendered: str,
        now: float,
    ) -> None:
        self._write_text_atomically(path, rendered)
        setattr(svc, "_runtime_overrides_serialized", serialized)
        setattr(svc, "_runtime_overrides_last_saved_at", float(now))
        setattr(svc, "_runtime_overrides_active", True)
        setattr(svc, "_runtime_overrides_values", dict(payload))
        self._clear_pending_runtime_overrides(svc)

    def flush(self, now: float | None = None) -> None:
        svc = self.service
        path = str(getattr(svc, "runtime_overrides_path", "")).strip()
        pending_payload = self._pending_runtime_overrides_payload(svc, path)
        if pending_payload is None:
            return
        current_time = self._runtime_now(svc) if now is None else float(now)
        if not self._runtime_override_write_due(svc, current_time):
            return
        try:
            self._write_runtime_overrides_payload(
                svc,
                path,
                pending_payload[0],
                pending_payload[1],
                pending_payload[2],
                current_time,
            )
        except RUNTIME_PERSISTENCE_WRITE_ERRORS as error:
            setattr(svc, "_runtime_overrides_pending_due_at", float(
                current_time + self._runtime_override_write_min_interval_seconds(svc)
            ))
            logging.warning("Unable to write runtime overrides to %s: %s", path, error)

    def _runtime_override_write_due(self, svc: object, current_time: float) -> bool:
        due_at = self.normalizer.optional_float(getattr(svc, "_runtime_overrides_pending_due_at", None))
        return due_at is None or current_time >= due_at

    @staticmethod
    def _pending_runtime_overrides_payload(svc: object, path: str) -> tuple[dict[str, str], str, str] | None:
        pending_serialized = getattr(svc, "_runtime_overrides_pending_serialized", None)
        pending_values = getattr(svc, "_runtime_overrides_pending_values", None)
        pending_text = getattr(svc, "_runtime_overrides_pending_text", None)
        if not bool(path) or not bool(pending_serialized):
            return None
        normalized_values = string_value_mapping(pending_values)
        if normalized_values is None or not isinstance(pending_text, str):
            return None
        return normalized_values, str(pending_serialized), pending_text

    @classmethod
    def _runtime_override_due_at(
        cls,
        current_time: float,
        pending_due_at: float | None,
        last_saved_at: float | None,
        min_interval: float,
    ) -> float | None:
        if pending_due_at is not None and current_time < pending_due_at:
            return pending_due_at
        if last_saved_at is not None and (current_time - last_saved_at) < min_interval:
            return last_saved_at + min_interval
        return None

    def save(self) -> None:
        svc = self.service
        path = str(getattr(svc, "runtime_overrides_path", "")).strip()
        if not path:
            return
        payload = self.current()
        serialized = compact_json(payload)
        if serialized == getattr(svc, "_runtime_overrides_serialized", None):
            self._clear_pending_runtime_overrides(svc)
            return
        rendered = self._runtime_override_ini_text(payload)
        current_time = self._runtime_now(svc)
        last_saved_at = self.normalizer.optional_float(getattr(svc, "_runtime_overrides_last_saved_at", None))
        pending_due_at = self.normalizer.optional_float(getattr(svc, "_runtime_overrides_pending_due_at", None))
        min_interval = self._runtime_override_write_min_interval_seconds(svc)
        due_at = self._runtime_override_due_at(current_time, pending_due_at, last_saved_at, min_interval)
        if due_at is not None:
            self._stage_runtime_overrides_write(svc, payload, serialized, rendered, due_at)
            return
        try:
            self._write_runtime_overrides_payload(svc, path, payload, serialized, rendered, current_time)
        except RUNTIME_PERSISTENCE_WRITE_ERRORS as error:
            self._stage_runtime_overrides_write(svc, payload, serialized, rendered, current_time + min_interval)
            logging.warning("Unable to write runtime overrides to %s: %s", path, error)
