# SPDX-License-Identifier: GPL-3.0-or-later
"""Transport-neutral command validation and dispatch for Control API v1.

External adapters resolve their own routes to stable target names before this
module validates values and dispatches canonical commands.
"""

from __future__ import annotations

from typing import Any, Callable, TypeGuard

from venus_evcharger.control.models import (
    ControlCommand,
    ControlCommandName,
    ControlCommandSource,
)
from venus_evcharger.core.contracts_control_surface import (
    CONTROL_AUTO_RUNTIME_VALUE_KIND_BY_TARGET,
    CONTROL_BINARY_AUTO_RUNTIME_TARGETS,
    CONTROL_BINARY_COMMANDS,
    CONTROL_COMMAND_DEFAULT_TARGETS,
    CONTROL_COMMAND_NAMES,
    CONTROL_FLOAT_AUTO_RUNTIME_TARGETS,
    CONTROL_INTEGER_AUTO_RUNTIME_TARGETS,
    CONTROL_PHASE_SELECTIONS,
    CONTROL_STRING_AUTO_RUNTIME_TARGETS,
)

class ControlApiV1Service:
    """Map transport-level writes to canonical commands and dispatch them."""

    _COMMAND_NAMES = CONTROL_COMMAND_NAMES
    _KNOWN_MODE_VALUES = frozenset({0, 1, 2})
    _KNOWN_PHASE_SELECTIONS = CONTROL_PHASE_SELECTIONS
    _HANDLER_SPECS = {
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
    _COMMAND_DEFAULT_TARGETS = CONTROL_COMMAND_DEFAULT_TARGETS
    _TRACKING_KEYS = frozenset({"command_id", "detail", "idempotency_key"})
    _NAMED_PAYLOAD_KEYS = frozenset({"name", "target", "value", *tuple(_TRACKING_KEYS)})
    _BINARY_COMMANDS = CONTROL_BINARY_COMMANDS
    _FLOAT_AUTO_RUNTIME_TARGETS = CONTROL_FLOAT_AUTO_RUNTIME_TARGETS
    _STRING_AUTO_RUNTIME_TARGETS = CONTROL_STRING_AUTO_RUNTIME_TARGETS
    _BINARY_AUTO_RUNTIME_TARGETS = CONTROL_BINARY_AUTO_RUNTIME_TARGETS
    _INTEGER_AUTO_RUNTIME_TARGETS = CONTROL_INTEGER_AUTO_RUNTIME_TARGETS

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
        current_setting_targets: set[str] | frozenset[str],
        auto_runtime_setting_targets: set[str] | frozenset[str],
    ) -> None:
        self._current_setting_targets = frozenset(current_setting_targets)
        self._auto_runtime_setting_targets = frozenset(auto_runtime_setting_targets)

    def command_for_target(
        self,
        name: ControlCommandName,
        target: str,
        value: Any,
        *,
        source: ControlCommandSource = "internal",
    ) -> ControlCommand:
        """Build one canonical command from a semantic target."""
        normalized_target = str(target).strip()
        self._validate_command_target(name, normalized_target)
        self._validate_command_value(name, normalized_target, value)
        return ControlCommand(name=name, target=normalized_target, value=value, source=source)

    def command_from_payload(
        self,
        payload: dict[str, Any],
        *,
        source: ControlCommandSource = "http",
    ) -> ControlCommand:
        """Translate one structured API payload into one canonical command."""
        if "name" in payload:
            return self._command_from_named_payload(payload, source=source)
        raise ValueError("Control command payload must include 'name'.")

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
        target = self._resolved_command_target(command_name, payload)
        detail = str(payload.get("detail", "")).strip()
        self._validate_command_value(command_name, target, payload.get("value"))
        return ControlCommand(
            name=command_name,
            target=target,
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

    def _resolved_command_target(self, command_name: ControlCommandName, payload: dict[str, Any]) -> str:
        """Return the semantic target required for one canonical command."""
        explicit_target = str(payload.get("target", "")).strip()
        if explicit_target:
            self._validate_command_target(command_name, explicit_target)
            return explicit_target
        default_target = self._COMMAND_DEFAULT_TARGETS.get(command_name)
        if default_target is not None:
            return default_target
        raise ValueError(f"Control command '{command_name}' requires an explicit 'target'.")

    def _validate_command_target(self, command_name: ControlCommandName, target: str) -> None:
        error = self._command_target_error(command_name, target)
        if error:
            raise ValueError(error)

    def _command_target_error(self, command_name: ControlCommandName, target: str) -> str:
        default_target_error = self._default_command_target_error(command_name, target)
        if default_target_error:
            return default_target_error
        return self._specialized_command_target_error(command_name, target)

    def _default_command_target_error(self, command_name: ControlCommandName, target: str) -> str:
        default_target = self._COMMAND_DEFAULT_TARGETS.get(command_name)
        if default_target is None or default_target == target:
            return ""
        return f"Control command '{command_name}' does not support target '{target}'."

    def _specialized_command_target_error(self, command_name: ControlCommandName, target: str) -> str:
        if command_name == "set_auto_runtime_setting":
            return self._target_membership_error(command_name, target, self._auto_runtime_setting_targets)
        if command_name == "set_current_setting":
            return self._target_membership_error(command_name, target, self._current_setting_targets)
        return ""

    @staticmethod
    def _target_membership_error(command_name: ControlCommandName, target: str, allowed_targets: frozenset[str]) -> str:
        if target in allowed_targets:
            return ""
        return f"Control command '{command_name}' requires one of: {', '.join(sorted(allowed_targets))}."

    def _validate_command_value(self, command_name: ControlCommandName, target: str, value: Any) -> None:
        validator = self._command_value_validator(command_name, target)
        if not validator(value):
            raise ValueError(self._command_value_error(command_name, target))

    def _command_value_validator(
        self,
        command_name: ControlCommandName,
        target: str,
    ) -> Callable[[Any], bool]:
        if command_name in self._BINARY_COMMANDS:
            return self._is_bool_or_binary_int
        validators: dict[ControlCommandName, Callable[[Any], bool]] = {
            "set_mode": self._is_known_mode_value,
            "set_phase_selection": self._is_known_phase_selection,
            "set_current_setting": self._is_non_negative_numeric,
        }
        if command_name == "set_auto_runtime_setting":
            return self._auto_runtime_value_validator(target)
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

    def _auto_runtime_value_validator(self, target: str) -> Callable[[Any], bool]:
        kind = self._auto_runtime_value_kind(target)
        if kind == "float":
            def float_validator(value: Any) -> bool:
                return self._is_non_negative_numeric(value) and self._within_auto_runtime_bounds(target, float(value))

            return float_validator
        if kind == "string":
            def string_validator(value: Any) -> bool:
                return self._is_non_empty_text(value) and self._valid_auto_runtime_text(target, str(value))

            return string_validator
        if kind == "binary":
            return self._is_bool_or_binary_int
        if kind == "integer":
            def integer_validator(value: Any) -> bool:
                return self._is_non_bool_integer(value) and int(value) >= 0

            return integer_validator
        return self._always_valid_value

    def _auto_runtime_value_kind(self, target: str) -> str:
        return CONTROL_AUTO_RUNTIME_VALUE_KIND_BY_TARGET.get(target, "any")

    @classmethod
    def _within_auto_runtime_bounds(cls, target: str, value: float) -> bool:
        if target in {"auto_min_soc", "auto_resume_soc"}:
            return value <= 100.0
        if target == "auto_learn_charge_power_alpha":
            return 0.0 < value <= 1.0
        return True

    @staticmethod
    def _valid_auto_runtime_text(target: str, value: str) -> bool:
        if target != "auto_scheduled_latest_end_time":
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
    def _command_value_error(cls, command_name: ControlCommandName, target: str) -> str:
        simple_errors: dict[ControlCommandName, str] = {
            "set_mode": "Control command 'set_mode' requires one of: 0, 1, 2.",
            "set_phase_selection": "Control command 'set_phase_selection' requires one of: P1, P1_P2, P1_P2_P3.",
        }
        if command_name in cls._BINARY_COMMANDS:
            return f"Control command '{command_name}' requires a boolean or binary integer value (0 or 1)."
        if command_name == "set_auto_runtime_setting":
            return cls._auto_runtime_value_error(target)
        if command_name == "set_current_setting":
            return f"Control command '{command_name}' requires a non-negative numeric value for target '{target}'."
        return simple_errors[command_name]

    @classmethod
    def _auto_runtime_value_error(cls, target: str) -> str:
        kind = cls._auto_runtime_error_kind(target)
        if kind == "numeric":
            return cls._auto_runtime_numeric_error(target)
        if kind == "string":
            return cls._auto_runtime_string_error(target)
        if kind == "binary":
            return (
                "Control command 'set_auto_runtime_setting' requires a boolean or binary integer "
                f"value (0 or 1) for target '{target}'."
            )
        if kind == "integer":
            return f"Control command 'set_auto_runtime_setting' requires a non-negative integer value for target '{target}'."
        return f"Control command 'set_auto_runtime_setting' received an invalid value for target '{target}'."

    @staticmethod
    def _auto_runtime_numeric_error(target: str) -> str:
        if target in {"auto_min_soc", "auto_resume_soc"}:
            return f"Control command 'set_auto_runtime_setting' requires a numeric value between 0 and 100 for target '{target}'."
        if target == "auto_learn_charge_power_alpha":
            return (
                "Control command 'set_auto_runtime_setting' requires a numeric value in the interval "
                f"(0, 1] for target '{target}'."
            )
        return f"Control command 'set_auto_runtime_setting' requires a non-negative numeric value for target '{target}'."

    @staticmethod
    def _auto_runtime_string_error(target: str) -> str:
        if target == "auto_scheduled_latest_end_time":
            return f"Control command 'set_auto_runtime_setting' requires a HH:MM time string for target '{target}'."
        return f"Control command 'set_auto_runtime_setting' requires a non-empty string value for target '{target}'."

    @classmethod
    def _auto_runtime_error_kind(cls, target: str) -> str:
        if target in cls._FLOAT_AUTO_RUNTIME_TARGETS:
            return "numeric"
        if target in cls._STRING_AUTO_RUNTIME_TARGETS:
            return "string"
        if target in cls._BINARY_AUTO_RUNTIME_TARGETS:
            return "binary"
        if target in cls._INTEGER_AUTO_RUNTIME_TARGETS:
            return "integer"
        return "generic"

    def execute(self, controller: Any, command: ControlCommand) -> None:
        """Dispatch one canonical command onto the existing write controller."""
        command_name = self._validated_command_name(str(command.name))
        self._validate_command_target(command_name, command.target)
        handler_name, include_target = self._HANDLER_SPECS[command_name]
        handler = getattr(controller, handler_name)
        if include_target:
            handler(command.target, command.value)
            return
        handler(command.value)
