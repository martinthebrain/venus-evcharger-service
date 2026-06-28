# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime-override persistence helpers for the state controller."""

from __future__ import annotations

import configparser
from io import StringIO
import logging
import time
from typing import Any, Callable

from venus_evcharger.core.common import DEFAULT_SCHEDULED_ENABLED_DAYS, normalize_hhmm_text, scheduled_enabled_days_text
from venus_evcharger.core.shared import compact_json
from venus_evcharger.core.split_mixins import ComposableControllerMixin as _ComposableControllerMixin
from venus_evcharger.controllers.state_runtime_normalize import _StateRuntimeNormalizeMixin
from venus_evcharger.controllers.state_specs import (
    RUNTIME_OVERRIDE_BY_CONFIG_KEY,
    RUNTIME_OVERRIDE_SPECS,
    RUNTIME_OVERRIDE_SECTION,
    RuntimeOverrideSpec,
    _CasePreservingConfigParser,
)


class _StateRuntimeOverridesMixin(_ComposableControllerMixin):
    @staticmethod
    def runtime_overrides_path(defaults: configparser.SectionProxy) -> str:
        device_instance = defaults.get("DeviceInstance", "60").strip() or "60"
        fallback = f"/run/dbus-venus-evcharger-overrides-{device_instance}.ini"
        return defaults.get("RuntimeOverridesPath", fallback).strip()

    @classmethod
    def _read_runtime_override_values(cls, path: str) -> dict[str, str]:
        if not str(path).strip():
            return {}
        parser = _CasePreservingConfigParser()
        try:
            read_files = parser.read(path)
        except Exception as error:  # pylint: disable=broad-except
            logging.warning("Unable to read runtime overrides from %s: %s", path, error)
            return {}
        if not read_files or not parser.has_section(RUNTIME_OVERRIDE_SECTION):
            return {}
        return cls._normalized_runtime_override_section_items(parser[RUNTIME_OVERRIDE_SECTION].items())

    @classmethod
    def _normalized_runtime_override_section_items(cls, items: Any) -> dict[str, str]:
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

    @classmethod
    def _apply_runtime_overrides_to_config(cls, svc: Any, config: configparser.ConfigParser) -> configparser.ConfigParser:
        defaults = config["DEFAULT"]
        path = cls.runtime_overrides_path(defaults)
        values = cls._read_runtime_override_values(path)
        for config_key, value in values.items():
            defaults[config_key] = str(value)
        svc.runtime_overrides_path = path
        svc._runtime_overrides_active = bool(values)
        svc._runtime_overrides_values = dict(values)
        svc._runtime_overrides_serialized = compact_json(values)
        return config

    @staticmethod
    def _override_value_as_text(spec: RuntimeOverrideSpec, value: object) -> str:
        renderers: dict[str, Callable[[object], str]] = {
            "bool": lambda raw: str(int(bool(raw))),
            "int": lambda raw: str(_StateRuntimeNormalizeMixin.coerce_runtime_int(raw)),
            "phase": lambda raw: str(_StateRuntimeNormalizeMixin._normalize_runtime_phase_selection(raw)),
            "weekday_set": lambda raw: scheduled_enabled_days_text(raw, DEFAULT_SCHEDULED_ENABLED_DAYS),
            "hhmm": lambda raw: normalize_hhmm_text(raw, "06:30"),
            "float": lambda raw: str(_StateRuntimeNormalizeMixin.coerce_runtime_float(raw)),
        }
        return renderers.get(spec.value_kind, renderers["float"])(value)

    @staticmethod
    def _runtime_override_default_value(spec: RuntimeOverrideSpec) -> object:
        if spec.value_kind in {"bool", "int"}:
            return 0
        if spec.value_kind == "phase":
            return "P1"
        if spec.value_kind == "weekday_set":
            return DEFAULT_SCHEDULED_ENABLED_DAYS
        if spec.value_kind == "hhmm":
            return "06:30"
        return 0.0

    def current_runtime_overrides(self) -> dict[str, str]:
        svc = self.service
        values: dict[str, str] = {}
        for spec in RUNTIME_OVERRIDE_SPECS:
            raw_value = getattr(svc, spec.attr_name, self._runtime_override_default_value(spec))
            values[spec.config_key] = self._override_value_as_text(spec, raw_value)
        return values

    def _serialized_runtime_overrides(self) -> str:
        return compact_json(self.current_runtime_overrides())

    @staticmethod
    def _runtime_override_write_min_interval_seconds(svc: Any) -> float:
        configured = _StateRuntimeNormalizeMixin._coerce_optional_runtime_float(
            getattr(svc, "runtime_overrides_write_min_interval_seconds", None)
        )
        return 1.0 if configured is None else max(0.0, float(configured))

    @staticmethod
    def _runtime_now(svc: Any) -> float:
        time_now = getattr(svc, "_time_now", None)
        raw_current_time: object = time_now() if callable(time_now) else time.time()
        return _StateRuntimeNormalizeMixin.coerce_runtime_float(raw_current_time, time.time())

    @staticmethod
    def _clear_pending_runtime_overrides(svc: Any) -> None:
        svc._runtime_overrides_pending_serialized = None
        svc._runtime_overrides_pending_values = None
        svc._runtime_overrides_pending_text = None
        svc._runtime_overrides_pending_due_at = None

    @staticmethod
    def _runtime_override_ini_text(payload: dict[str, str]) -> str:
        parser = _CasePreservingConfigParser()
        parser[RUNTIME_OVERRIDE_SECTION] = payload
        handle = StringIO()
        parser.write(handle)
        return handle.getvalue()

    def _stage_runtime_overrides_write(
        self,
        svc: Any,
        payload: dict[str, str],
        serialized: str,
        rendered: str,
        due_at: float,
    ) -> None:
        svc._runtime_overrides_pending_serialized = serialized
        svc._runtime_overrides_pending_values = dict(payload)
        svc._runtime_overrides_pending_text = rendered
        svc._runtime_overrides_pending_due_at = float(due_at)
        svc._runtime_overrides_active = True
        svc._runtime_overrides_values = dict(payload)

    def _write_runtime_overrides_payload(
        self,
        svc: Any,
        path: str,
        payload: dict[str, str],
        serialized: str,
        rendered: str,
        now: float,
    ) -> None:
        self._write_text_atomically(path, rendered)
        svc._runtime_overrides_serialized = serialized
        svc._runtime_overrides_last_saved_at = float(now)
        svc._runtime_overrides_active = True
        svc._runtime_overrides_values = dict(payload)
        self._clear_pending_runtime_overrides(svc)

    def flush_runtime_overrides(self, now: float | None = None) -> None:
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
        except Exception as error:  # pylint: disable=broad-except
            svc._runtime_overrides_pending_due_at = float(
                current_time + self._runtime_override_write_min_interval_seconds(svc)
            )
            logging.warning("Unable to write runtime overrides to %s: %s", path, error)

    def _runtime_override_write_due(self, svc: Any, current_time: float) -> bool:
        due_at = self._coerce_optional_runtime_float(getattr(svc, "_runtime_overrides_pending_due_at", None))
        return due_at is None or current_time >= due_at

    @staticmethod
    def _pending_runtime_overrides_payload(svc: Any, path: str) -> tuple[dict[str, str], str, str] | None:
        pending_serialized = getattr(svc, "_runtime_overrides_pending_serialized", None)
        pending_values = getattr(svc, "_runtime_overrides_pending_values", None)
        pending_text = getattr(svc, "_runtime_overrides_pending_text", None)
        if not bool(path) or not bool(pending_serialized):
            return None
        if not isinstance(pending_values, dict) or not isinstance(pending_text, str):
            return None
        return dict(pending_values), str(pending_serialized), pending_text

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

    def save_runtime_overrides(self) -> None:
        svc = self.service
        path = str(getattr(svc, "runtime_overrides_path", "")).strip()
        if not path:
            return
        payload = self.current_runtime_overrides()
        serialized = compact_json(payload)
        if serialized == getattr(svc, "_runtime_overrides_serialized", None):
            self._clear_pending_runtime_overrides(svc)
            return
        rendered = self._runtime_override_ini_text(payload)
        current_time = self._runtime_now(svc)
        last_saved_at = self._coerce_optional_runtime_float(getattr(svc, "_runtime_overrides_last_saved_at", None))
        pending_due_at = self._coerce_optional_runtime_float(getattr(svc, "_runtime_overrides_pending_due_at", None))
        min_interval = self._runtime_override_write_min_interval_seconds(svc)
        due_at = self._runtime_override_due_at(current_time, pending_due_at, last_saved_at, min_interval)
        if due_at is not None:
            self._stage_runtime_overrides_write(svc, payload, serialized, rendered, due_at)
            return
        try:
            self._write_runtime_overrides_payload(svc, path, payload, serialized, rendered, current_time)
        except Exception as error:  # pylint: disable=broad-except
            self._stage_runtime_overrides_write(svc, payload, serialized, rendered, current_time + min_interval)
            logging.warning("Unable to write runtime overrides to %s: %s", path, error)

    def _serialized_runtime_state(self) -> str:
        return compact_json(self.current_runtime_state())
