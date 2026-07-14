# SPDX-License-Identifier: GPL-3.0-or-later
"""Transport-agnostic command mapping and dispatch for Control API v1.

The Control API accepts writes from DBus, HTTP, and CLI entry points, then maps
them onto one canonical command contract. This module owns command names,
path/value validation, idempotent command construction, and dispatch into the
write controller. Keeping that policy here prevents each transport from growing
its own subtly different interpretation of Mode, StartStop, Auto settings, and
phase-selection writes.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable
from typing import TypeGuard

from venus_evcharger.control.models import ControlCommand, ControlCommandName, ControlCommandSource


class ControlApiV1Service:
    """Map transport-level writes to canonical commands and dispatch them."""

    _COMMAND_NAMES: frozenset[ControlCommandName] = frozenset(
        {
            "legacy_unknown_write",
            "reset_contactor_lockout",
            "reset_phase_lockout",
            "set_auto_runtime_setting",
            "set_auto_start",
            "set_current_setting",
            "set_enable",
            "set_mode",
            "set_phase_selection",
            "set_start_stop",
            "trigger_software_update",
        }
    )
    _KNOWN_MODE_VALUES = frozenset({0, 1, 2})
    _KNOWN_PHASE_SELECTIONS = frozenset({"P1", "P1_P2", "P1_P2_P3"})
    _DIRECT_PATH_COMMANDS: dict[str, ControlCommandName] = {
        "/Mode": "set_mode",
        "/AutoStart": "set_auto_start",
        "/StartStop": "set_start_stop",
        "/Enable": "set_enable",
        "/PhaseSelection": "set_phase_selection",
        "/Auto/PhaseLockoutReset": "reset_phase_lockout",
        "/Auto/ContactorLockoutReset": "reset_contactor_lockout",
        "/Auto/SoftwareUpdateRun": "trigger_software_update",
    }
    _HANDLER_SPECS = {
        "legacy_unknown_write": ("_handle_unknown_write", True),
        "reset_contactor_lockout": ("_handle_contactor_lockout_reset_write", False),
        "reset_phase_lockout": ("_handle_phase_lockout_reset_write", False),
        "set_auto_runtime_setting": ("_handle_auto_runtime_setting_write", True),
        "set_auto_start": ("_handle_autostart_write", False),
        "set_current_setting": ("_handle_current_setting_write", True),
        "set_enable": ("_handle_enable_value_write", False),
        "set_mode": ("_handle_mode_value_write", False),
        "set_phase_selection": ("_handle_phase_selection_write", False),
        "set_start_stop": ("_handle_startstop_value_write", False),
        "trigger_software_update": ("_handle_software_update_run_write", False),
    }
    _COMMAND_DEFAULT_PATHS: dict[ControlCommandName, str] = {
        command_name: path
        for path, command_name in _DIRECT_PATH_COMMANDS.items()
    }
    _TRACKING_KEYS = frozenset({"command_id", "detail", "idempotency_key"})
    _PATH_ONLY_PAYLOAD_KEYS = frozenset({"path", "value", *tuple(_TRACKING_KEYS)})
    _NAMED_PAYLOAD_KEYS = frozenset({"name", "path", "value", *tuple(_TRACKING_KEYS)})
    _BINARY_COMMANDS: frozenset[ControlCommandName] = frozenset(
        {
            "reset_contactor_lockout",
            "reset_phase_lockout",
            "set_auto_start",
            "set_enable",
            "set_start_stop",
            "trigger_software_update",
        }
    )
    _FLOAT_AUTO_RUNTIME_PATHS = frozenset(
        {
            "/Auto/StartSurplusWatts",
            "/Auto/StopSurplusWatts",
            "/Auto/MinSoc",
            "/Auto/ResumeSoc",
            "/Auto/StartDelaySeconds",
            "/Auto/StopDelaySeconds",
            "/Auto/ScheduledFallbackDelaySeconds",
            "/Auto/ScheduledNightCurrent",
            "/Auto/DbusBackoffBaseSeconds",
            "/Auto/DbusBackoffMaxSeconds",
            "/Auto/GridRecoveryStartSeconds",
            "/Auto/StopSurplusDelaySeconds",
            "/Auto/StopSurplusVolatilityLowWatts",
            "/Auto/StopSurplusVolatilityHighWatts",
            "/Auto/ReferenceChargePowerWatts",
            "/Auto/LearnChargePowerMinWatts",
            "/Auto/LearnChargePowerAlpha",
            "/Auto/LearnChargePowerStartDelaySeconds",
            "/Auto/LearnChargePowerWindowSeconds",
            "/Auto/LearnChargePowerMaxAgeSeconds",
            "/Auto/PhaseUpshiftDelaySeconds",
            "/Auto/PhaseDownshiftDelaySeconds",
            "/Auto/PhaseUpshiftHeadroomWatts",
            "/Auto/PhaseDownshiftMarginWatts",
            "/Auto/PhaseMismatchRetrySeconds",
            "/Auto/PhaseMismatchLockoutSeconds",
        }
    )
    _STRING_AUTO_RUNTIME_PATHS = frozenset(
        {
            "/Auto/ScheduledEnabledDays",
            "/Auto/ScheduledLatestEndTime",
        }
    )
    _BINARY_AUTO_RUNTIME_PATHS = frozenset(
        {
            "/Auto/LearnChargePowerEnabled",
            "/Auto/PhaseSwitching",
            "/Auto/PhasePreferLowestWhenIdle",
        }
    )
    _INTEGER_AUTO_RUNTIME_PATHS = frozenset({"/Auto/PhaseMismatchLockoutCount"})

    @staticmethod
    def _always_valid_value(_value: Any) -> bool:
        return True

    @classmethod
    def _is_command_name(cls, value: str) -> TypeGuard[ControlCommandName]:
        return value in cls._COMMAND_NAMES

    @staticmethod
    def _is_bool_or_binary_int(value: Any) -> bool:
        if isinstance(value, bool):
            return True
        return isinstance(value, int) and value in {0, 1}

    @staticmethod
    def _is_numeric(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    def __init__(
        self,
        *,
        current_setting_paths: tuple[str, ...],
        auto_runtime_setting_paths: set[str],
    ) -> None:
        self._current_setting_paths = frozenset(current_setting_paths)
        self._auto_runtime_setting_paths = frozenset(auto_runtime_setting_paths)

    def command_for_write(
        self,
        path: str,
        value: Any,
        *,
        source: ControlCommandSource = "dbus",
    ) -> ControlCommand:
        """Translate one transport-level write into one canonical Control API command."""
        command_name = self._DIRECT_PATH_COMMANDS.get(path)
        if command_name is not None:
            return ControlCommand(name=command_name, path=path, value=value, source=source)
        if path in self._current_setting_paths:
            return ControlCommand(name="set_current_setting", path=path, value=value, source=source)
        if path in self._auto_runtime_setting_paths:
            return ControlCommand(name="set_auto_runtime_setting", path=path, value=value, source=source)
        return ControlCommand(
            name="legacy_unknown_write",
            path=path,
            value=value,
            source=source,
            detail="No canonical Control API command is registered for this write path.",
        )

    def command_for_dbus_write(self, path: str, value: Any) -> ControlCommand:
        """Translate one DBus write into one canonical Control API command."""
        return self.command_for_write(path, value)

    def command_from_payload(
        self,
        payload: dict[str, Any],
        *,
        source: ControlCommandSource = "http",
    ) -> ControlCommand:
        """Translate one structured API payload into one canonical command."""
        if "name" in payload:
            return self._command_from_named_payload(payload, source=source)
        if "path" in payload:
            return self._command_from_path_payload(payload, source=source)
        raise ValueError("Control command payload must include either 'name' or 'path'.")

    def _command_from_path_payload(
        self,
        payload: dict[str, Any],
        *,
        source: ControlCommandSource,
    ) -> ControlCommand:
        self._reject_extra_keys(payload, self._PATH_ONLY_PAYLOAD_KEYS)
        path = self._validated_explicit_path(payload)
        command = self.command_for_write(path, payload.get("value"), source=source)
        if command.name == "legacy_unknown_write":
            raise ValueError(f"Unsupported control path '{path}'.")
        self._validate_command_value(command.name, path, payload.get("value"))
        return replace(
            command,
            detail=str(payload.get("detail", "")).strip(),
            command_id=str(payload.get("command_id", "")).strip(),
            idempotency_key=str(payload.get("idempotency_key", "")).strip(),
        )

    def _command_from_named_payload(
        self,
        payload: dict[str, Any],
        *,
        source: ControlCommandSource,
    ) -> ControlCommand:
        """Translate one canonical command payload into one concrete command."""
        self._reject_extra_keys(payload, self._NAMED_PAYLOAD_KEYS)
        raw_name = str(payload["name"]).strip()
        command_name = self._validated_command_name(raw_name)
        path = self._resolved_command_path(command_name, payload)
        detail = str(payload.get("detail", "")).strip()
        self._validate_command_value(command_name, path, payload.get("value"))
        return ControlCommand(
            name=command_name,
            path=path,
            value=payload.get("value"),
            source=source,
            detail=detail,
            command_id=str(payload.get("command_id", "")).strip(),
            idempotency_key=str(payload.get("idempotency_key", "")).strip(),
        )

    def _validated_command_name(self, raw_name: str) -> ControlCommandName:
        if not self._is_command_name(raw_name):
            raise ValueError(f"Unsupported control command '{raw_name}'.")
        return raw_name

    @classmethod
    def _reject_extra_keys(cls, payload: dict[str, Any], allowed_keys: frozenset[str]) -> None:
        extra_keys = sorted(set(payload) - allowed_keys)
        if extra_keys:
            raise ValueError(f"Unsupported payload field(s): {', '.join(extra_keys)}.")

    @staticmethod
    def _validated_explicit_path(payload: dict[str, Any]) -> str:
        path = str(payload.get("path", "")).strip()
        if not path:
            raise ValueError("Control command payload field 'path' must be a non-empty string.")
        return path

    def _resolved_command_path(self, command_name: ControlCommandName, payload: dict[str, Any]) -> str:
        """Return the concrete write path required for one canonical command."""
        explicit_path = self._validated_explicit_path(payload) if "path" in payload else ""
        if explicit_path:
            self._validate_command_path(command_name, explicit_path)
            return explicit_path
        default_path = self._COMMAND_DEFAULT_PATHS.get(command_name)
        if default_path is not None:
            return default_path
        raise ValueError(f"Control command '{command_name}' requires an explicit 'path'.")

    def _validate_command_path(self, command_name: ControlCommandName, path: str) -> None:
        error = self._command_path_error(command_name, path)
        if error:
            raise ValueError(error)

    def _command_path_error(self, command_name: ControlCommandName, path: str) -> str:
        default_path_error = self._default_command_path_error(command_name, path)
        if default_path_error:
            return default_path_error
        return self._specialized_command_path_error(command_name, path)

    def _default_command_path_error(self, command_name: ControlCommandName, path: str) -> str:
        default_path = self._COMMAND_DEFAULT_PATHS.get(command_name)
        if default_path is None or default_path == path:
            return ""
        return f"Control command '{command_name}' does not support path '{path}'."

    def _specialized_command_path_error(self, command_name: ControlCommandName, path: str) -> str:
        if command_name == "set_auto_runtime_setting":
            return self._path_membership_error(command_name, path, self._auto_runtime_setting_paths)
        if command_name == "set_current_setting":
            return self._path_membership_error(command_name, path, self._current_setting_paths)
        return ""

    @staticmethod
    def _path_membership_error(command_name: ControlCommandName, path: str, allowed_paths: frozenset[str]) -> str:
        if path in allowed_paths:
            return ""
        return f"Control command '{command_name}' requires one of: {', '.join(sorted(allowed_paths))}."

    def _validate_command_value(self, command_name: ControlCommandName, path: str, value: Any) -> None:
        validator = self._command_value_validator(command_name, path)
        if not validator(value):
            raise ValueError(self._command_value_error(command_name, path))

    def _command_value_validator(
        self,
        command_name: ControlCommandName,
        path: str,
    ) -> Callable[[Any], bool]:
        if command_name in self._BINARY_COMMANDS:
            return self._is_bool_or_binary_int
        validators: dict[ControlCommandName, Callable[[Any], bool]] = {
            "set_mode": self._is_known_mode_value,
            "set_phase_selection": self._is_known_phase_selection,
            "set_current_setting": self._is_non_negative_numeric,
        }
        if command_name == "set_auto_runtime_setting":
            return self._auto_runtime_value_validator(path)
        return validators.get(command_name, self._always_valid_value)

    @staticmethod
    def _is_non_bool_integer(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool)

    @staticmethod
    def _is_non_empty_text(value: Any) -> bool:
        return isinstance(value, str) and bool(value.strip())

    @classmethod
    def _is_known_mode_value(cls, value: Any) -> bool:
        return cls._is_non_bool_integer(value) and int(value) in cls._KNOWN_MODE_VALUES

    @classmethod
    def _is_known_phase_selection(cls, value: Any) -> bool:
        return isinstance(value, str) and value.strip() in cls._KNOWN_PHASE_SELECTIONS

    @classmethod
    def _is_non_negative_numeric(cls, value: Any) -> bool:
        return cls._is_numeric(value) and float(value) >= 0.0

    def _auto_runtime_value_validator(self, path: str) -> Callable[[Any], bool]:
        kind = self._auto_runtime_value_kind(path)
        if kind == "float":
            def float_validator(value: Any) -> bool:
                return self._is_non_negative_numeric(value) and self._within_auto_runtime_bounds(path, float(value))

            return float_validator
        if kind == "string":
            def string_validator(value: Any) -> bool:
                return self._is_non_empty_text(value) and self._valid_auto_runtime_text(path, str(value))

            return string_validator
        if kind == "binary":
            return self._is_bool_or_binary_int
        if kind == "integer":
            def integer_validator(value: Any) -> bool:
                return self._is_non_bool_integer(value) and int(value) >= 0

            return integer_validator
        return self._always_valid_value

    def _auto_runtime_value_kind(self, path: str) -> str:
        kind_by_group = (
            (self._FLOAT_AUTO_RUNTIME_PATHS, "float"),
            (self._STRING_AUTO_RUNTIME_PATHS, "string"),
            (self._BINARY_AUTO_RUNTIME_PATHS, "binary"),
            (self._INTEGER_AUTO_RUNTIME_PATHS, "integer"),
        )
        for allowed_paths, kind in kind_by_group:
            if path in allowed_paths:
                return kind
        return "any"

    @classmethod
    def _within_auto_runtime_bounds(cls, path: str, value: float) -> bool:
        if path in {"/Auto/MinSoc", "/Auto/ResumeSoc"}:
            return value <= 100.0
        if path == "/Auto/LearnChargePowerAlpha":
            return 0.0 < value <= 1.0
        return True

    @staticmethod
    def _valid_auto_runtime_text(path: str, value: str) -> bool:
        if path != "/Auto/ScheduledLatestEndTime":
            return True
        return ControlApiV1Service._is_valid_hour_minute(value)

    @staticmethod
    def _is_valid_hour_minute(value: str) -> bool:
        normalized = value.strip()
        if normalized.count(":") != 1:
            return False
        hour_text, minute_text = normalized.split(":")
        if not hour_text.isdigit() or not minute_text.isdigit():
            return False
        return ControlApiV1Service._hour_in_range(hour_text) and ControlApiV1Service._minute_in_range(minute_text)

    @staticmethod
    def _hour_in_range(value: str) -> bool:
        return 0 <= int(value) <= 23

    @staticmethod
    def _minute_in_range(value: str) -> bool:
        return 0 <= int(value) <= 59

    @classmethod
    def _command_value_error(cls, command_name: ControlCommandName, path: str) -> str:
        simple_errors: dict[ControlCommandName, str] = {
            "set_mode": "Control command 'set_mode' requires one of: 0, 1, 2.",
            "set_phase_selection": "Control command 'set_phase_selection' requires one of: P1, P1_P2, P1_P2_P3.",
        }
        if command_name in cls._BINARY_COMMANDS:
            return f"Control command '{command_name}' requires a boolean or binary integer value (0 or 1)."
        if command_name in simple_errors:
            return simple_errors[command_name]
        if command_name == "set_current_setting":
            return f"Control command '{command_name}' requires a non-negative numeric value for path '{path}'."
        if command_name == "set_auto_runtime_setting":
            return cls._auto_runtime_value_error(path)
        return f"Control command '{command_name}' received an invalid value for path '{path}'."

    @classmethod
    def _auto_runtime_value_error(cls, path: str) -> str:
        kind = cls._auto_runtime_error_kind(path)
        if kind == "numeric":
            return cls._auto_runtime_numeric_error(path)
        if kind == "string":
            return cls._auto_runtime_string_error(path)
        if kind == "binary":
            return (
                "Control command 'set_auto_runtime_setting' requires a boolean or binary integer "
                f"value (0 or 1) for path '{path}'."
            )
        if kind == "integer":
            return f"Control command 'set_auto_runtime_setting' requires a non-negative integer value for path '{path}'."
        return f"Control command 'set_auto_runtime_setting' received an invalid value for path '{path}'."

    @staticmethod
    def _auto_runtime_numeric_error(path: str) -> str:
        if path in {"/Auto/MinSoc", "/Auto/ResumeSoc"}:
            return f"Control command 'set_auto_runtime_setting' requires a numeric value between 0 and 100 for path '{path}'."
        if path == "/Auto/LearnChargePowerAlpha":
            return (
                "Control command 'set_auto_runtime_setting' requires a numeric value in the interval "
                f"(0, 1] for path '{path}'."
            )
        return f"Control command 'set_auto_runtime_setting' requires a non-negative numeric value for path '{path}'."

    @staticmethod
    def _auto_runtime_string_error(path: str) -> str:
        if path == "/Auto/ScheduledLatestEndTime":
            return f"Control command 'set_auto_runtime_setting' requires a HH:MM time string for path '{path}'."
        return f"Control command 'set_auto_runtime_setting' requires a non-empty string value for path '{path}'."

    @classmethod
    def _auto_runtime_error_kind(cls, path: str) -> str:
        if path in cls._FLOAT_AUTO_RUNTIME_PATHS:
            return "numeric"
        if path in cls._STRING_AUTO_RUNTIME_PATHS:
            return "string"
        if path in cls._BINARY_AUTO_RUNTIME_PATHS:
            return "binary"
        if path in cls._INTEGER_AUTO_RUNTIME_PATHS:
            return "integer"
        return "generic"

    def execute(self, controller: Any, command: ControlCommand) -> None:
        """Dispatch one canonical command onto the existing write controller."""
        handler_name, include_path = self._HANDLER_SPECS[command.name]
        handler = getattr(controller, handler_name)
        if include_path:
            handler(command.path, command.value)
            return
        handler(command.value)
