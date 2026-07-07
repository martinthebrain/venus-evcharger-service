# SPDX-License-Identifier: GPL-3.0-or-later
"""Victron GX/Cerbo relay-backed normalized switch backend."""

from __future__ import annotations

import configparser
import time
from dataclasses import dataclass
from typing import Any, Literal

from .config_file import config_section
from .models import PhaseSelection, SwitchCapabilities, SwitchState, normalize_phase_selection_tuple
from venus_evcharger.backend.errors import BACKEND_IO_ERRORS
from venus_evcharger.core.contracts import finite_float_or_none, normalize_binary_flag
from venus_evcharger.dbus_gateway import DbusCacheStore, GatewayClient, dbus_path_key, gateway_paths

ContactMode = Literal["NO", "NC"]


@dataclass(frozen=True)
class CerboGxRelaySwitchSettings:
    """Normalized settings for one local Victron GX relay actuator."""

    relay_index: int
    contact_mode: ContactMode
    ensure_manual_function: bool
    manual_function_value: int
    verify_settle_seconds: float
    verify_retry_seconds: float
    supported_phase_selections: tuple[PhaseSelection, ...]
    requires_charge_pause_for_phase_change: bool


def _config(path: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.optionxform = str
    if not str(path).strip():
        return parser
    read_files = parser.read(path)
    if not read_files:
        raise FileNotFoundError(path)
    return parser


def _relay_index(value: object) -> int:
    try:
        index = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("Cerbo GX relay backend requires RelayIndex 0 or 1") from exc
    if index not in (0, 1):
        raise ValueError("Cerbo GX relay backend supports RelayIndex 0 or 1")
    return index


def _relay_index_or_default(value: object) -> int:
    if value is None or not str(value).strip():
        return 0
    return _relay_index(value)


def _contact_mode(value: object) -> ContactMode:
    mode = str(value).strip().upper()
    if mode in {"NO", "NORMALLY_OPEN", "NORMALLY-OPEN"}:
        return "NO"
    if mode in {"NC", "NORMALLY_CLOSED", "NORMALLY-CLOSED"}:
        return "NC"
    raise ValueError("Cerbo GX relay backend requires ContactMode NO or NC")


def _contact_mode_or_default(value: object) -> ContactMode:
    if value is None or not str(value).strip():
        return "NO"
    return _contact_mode(value)


def _binary_flag_or_default(value: object, default: bool) -> bool:
    if value is None:
        return default
    return bool(normalize_binary_flag(value))


def _manual_function_value(value: object) -> int:
    if value is None or not str(value).strip():
        return 2
    return int(value)


def _positive_seconds(value: object, default: float) -> float:
    seconds = finite_float_or_none(value)
    if seconds is None or seconds < 0.0:
        return default
    return float(seconds)


def load_cerbo_gx_relay_switch_settings(config_path: str) -> CerboGxRelaySwitchSettings:
    """Return normalized Cerbo GX relay switch settings."""
    parser = _config(config_path)
    adapter = config_section(parser, "Adapter")
    capabilities = config_section(parser, "Capabilities")
    return CerboGxRelaySwitchSettings(
        relay_index=_relay_index_or_default(adapter.get("RelayIndex")),
        contact_mode=_contact_mode_or_default(adapter.get("ContactMode")),
        ensure_manual_function=_binary_flag_or_default(adapter.get("EnsureManualFunction"), True),
        manual_function_value=_manual_function_value(adapter.get("ManualFunctionValue")),
        verify_settle_seconds=_positive_seconds(adapter.get("VerifySettleSeconds"), 0.1),
        verify_retry_seconds=_positive_seconds(adapter.get("VerifyRetrySeconds"), 0.2),
        supported_phase_selections=normalize_phase_selection_tuple(capabilities.get("SupportedPhaseSelections"), ("P1",)),
        requires_charge_pause_for_phase_change=_binary_flag_or_default(
            capabilities.get("RequiresChargePauseForPhaseChange"),
            False,
        ),
    )


class CerboGxRelaySwitchBackend:
    """Control one Victron GX relay via Venus OS D-Bus."""

    _SYSTEM_SERVICE = "com.victronenergy.system"
    _SETTINGS_SERVICE = "com.victronenergy.settings"

    def __init__(self, service: object, config_path: str = "") -> None:
        self.service = service
        self.config_path = str(config_path).strip()
        self.settings = load_cerbo_gx_relay_switch_settings(self.config_path)
        default_selection = self.settings.supported_phase_selections[0]
        requested_selection = getattr(service, "requested_phase_selection", None)
        self._selected_phase_selection: PhaseSelection = (
            requested_selection if requested_selection in self.settings.supported_phase_selections else default_selection
        )
        self._last_gateway_write_at: dict[tuple[str, str], float] = {}

    def capabilities(self) -> SwitchCapabilities:
        """Return the configured relay capabilities."""
        return SwitchCapabilities(
            switching_mode="contactor",
            supported_phase_selections=self.settings.supported_phase_selections,
            requires_charge_pause_for_phase_change=self.settings.requires_charge_pause_for_phase_change,
            max_direct_switch_power_w=None,
        )

    def read_switch_state(self) -> SwitchState:
        """Read one normalized switch state from ``/Relay/<idx>/State``."""
        relay_state = self._read_relay_state()
        enabled = self._enabled_from_relay_state(relay_state)
        return SwitchState(
            enabled=enabled,
            phase_selection=self._selected_phase_selection,
        )

    def set_enabled(self, enabled: bool) -> None:
        """Switch the configured GX relay and verify the readback."""
        if self.settings.ensure_manual_function:
            self._ensure_manual_function()
        target_state = self._relay_state_for_enabled(bool(enabled))
        self._set_relay_state(target_state)
        self._verify_relay_state(target_state)

    def set_phase_selection(self, selection: PhaseSelection) -> None:
        """Store one supported phase selection for API consistency."""
        if selection not in self.settings.supported_phase_selections:
            raise ValueError(f"Unsupported phase selection '{selection}' for Cerbo GX relay backend")
        self._selected_phase_selection = selection

    def _relay_state_for_enabled(self, enabled: bool) -> int:
        if self.settings.contact_mode == "NC":
            return 0 if enabled else 1
        return 1 if enabled else 0

    def _enabled_from_relay_state(self, relay_state: int) -> bool:
        return relay_state == self._relay_state_for_enabled(True)

    def _relay_state_path(self) -> str:
        return f"/Relay/{self.settings.relay_index}/State"

    def _manual_function_paths(self) -> tuple[str, ...]:
        path = f"/Settings/Relay/{self.settings.relay_index}/Function"
        if self.settings.relay_index == 0:
            return (path, "/Settings/Relay/Function")
        return (path,)

    def _read_relay_state(self) -> int:
        return int(self._dbus_get_value(self._SYSTEM_SERVICE, self._relay_state_path()) or 0)

    def _set_relay_state(self, state: int) -> None:
        ok = self._dbus_set_value(self._SYSTEM_SERVICE, self._relay_state_path(), int(state))
        if not ok:
            raise RuntimeError(f"DBus SetValue failed for {self._relay_state_path()} -> {state}")

    def _verify_relay_state(self, target_state: int) -> None:
        self._sleep_if_configured(self.settings.verify_settle_seconds)
        read_back = self._verified_relay_readback_or_none()
        if read_back is None or read_back == target_state:
            return
        self._retry_relay_state(target_state)
        read_back = self._verified_relay_readback_or_none()
        if read_back is not None and read_back != target_state:
            raise RuntimeError(f"Cerbo GX relay {self.settings.relay_index} stayed at {read_back}, expected {target_state}")

    def _verified_relay_readback_or_none(self) -> int | None:
        """Read relay state when the gateway cache is fresh enough to verify writes."""
        if self._cache_entry_is_stale_for_write(self._SYSTEM_SERVICE, self._relay_state_path()):
            return None
        read_back = self._dbus_get_value(self._SYSTEM_SERVICE, self._relay_state_path())
        return int(read_back) if read_back is not None else None

    def _retry_relay_state(self, target_state: int) -> None:
        """Retry one relay state write after the configured verification delay."""
        self._sleep_if_configured(self.settings.verify_retry_seconds)
        self._set_relay_state(target_state)
        self._sleep_if_configured(self.settings.verify_settle_seconds)

    def _ensure_manual_function(self) -> None:
        last_error: Exception | None = None
        for path in self._manual_function_paths():
            try:
                if self._manual_function_matches(path):
                    return
                if self._set_manual_function_path(path):
                    return
            except BACKEND_IO_ERRORS as exc:
                last_error = exc
                continue
        self._raise_manual_function_error(last_error)

    def _sleep_if_configured(self, seconds: float) -> None:
        if seconds:
            time.sleep(seconds)

    def _manual_function_matches(self, path: str) -> bool:
        value = self._dbus_get_value(self._SETTINGS_SERVICE, path)
        return value is not None and int(value) == self.settings.manual_function_value

    def _set_manual_function_path(self, path: str) -> bool:
        return self._dbus_set_value(self._SETTINGS_SERVICE, path, self.settings.manual_function_value)

    def _raise_manual_function_error(self, last_error: Exception | None) -> None:
        message = f"Unable to set Cerbo GX relay {self.settings.relay_index} to manual function"
        if last_error is not None:
            raise RuntimeError(message) from last_error
        raise RuntimeError(message)

    def _system_bus(self) -> Any:
        raise RuntimeError("Direct DBus access is disabled; use the DBus gateway adapter")

    def _busitem(self, service: str, path: str) -> Any:
        del service, path
        raise RuntimeError("Direct DBus access is disabled; use the DBus gateway adapter")

    def _with_dbus_retry(self, call: Any) -> Any:
        return call()

    def _dbus_get_value(self, service: str, path: str) -> Any:
        entry = self._dbus_value_entry(service, path)
        if entry is None:
            try:
                self._gateway_client().request_raw_value(
                    service,
                    path,
                    priority="read",
                    reason="cerbo gx relay cache miss",
                    source="cerbo-gx-relay-switch",
                )
            except OSError:
                return None
            return None
        return self._normalized_dbus_value(entry.get("value"))

    def _dbus_value_entry(self, service: str, path: str) -> dict[str, Any] | None:
        snapshot = DbusCacheStore.load_snapshot(self._gateway_cache_path())
        return DbusCacheStore.value_entry(snapshot, dbus_path_key(service, path))

    def _cache_entry_is_stale_for_write(self, service: str, path: str) -> bool:
        written_at = self._last_gateway_write_at.get((service, path), 0.0)
        if written_at <= 0.0:
            return False
        entry = self._dbus_value_entry(service, path)
        if entry is None:
            return True
        updated_at = entry.get("updated_at")
        if updated_at is None:
            return True
        try:
            return float(updated_at) <= written_at
        except (TypeError, ValueError):
            return True

    def _dbus_set_value(self, service: str, path: str, value: int) -> bool:
        try:
            self._gateway_client().enqueue_command(
                {
                    "kind": "set_value",
                    "source": "cerbo-gx-relay-switch",
                    "service": service,
                    "path": path,
                    "value": int(value),
                    "priority": "user",
                    "coalesce_key": f"{service}:{path}",
                }
            )
            self._last_gateway_write_at[(service, path)] = time.time()
            return True
        except OSError:
            return False

    def _gateway_client(self) -> GatewayClient:
        return GatewayClient(gateway_paths(self._gateway_run_dir()))

    def _gateway_cache_path(self) -> str:
        configured = str(getattr(self.service, "dbus_gateway_cache_path", None) or "")
        if configured:
            return configured
        return gateway_paths(self._gateway_run_dir()).cache_path

    def _gateway_run_dir(self) -> str:
        configured = str(getattr(self.service, "dbus_gateway_run_dir", None) or "")
        return configured or "/tmp/venus-evcharger"

    @staticmethod
    def _normalized_dbus_value(value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)):
            return value
        try:
            return int(value)
        except (TypeError, ValueError):
            try:
                return float(value)
            except (TypeError, ValueError):
                return str(value)
