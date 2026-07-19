# SPDX-License-Identifier: GPL-3.0-or-later
"""Relay queue and worker-loop helpers for Shelly I/O support."""

from __future__ import annotations

from collections.abc import Callable

from venus_evcharger.backend.errors import BACKEND_IO_ERRORS
from venus_evcharger.backend.modbus_transport import modbus_transport_issue_reason
from venus_evcharger.backend.shelly_io_capabilities import ShellyCapabilities
from venus_evcharger.backend.shelly_io_ports import ShellyWorkerHost
from venus_evcharger.backend.shelly_io_requests import ShellyRequestClient
from venus_evcharger.backend.shelly_io_runtime import ShellyChargerRuntime
from venus_evcharger.backend.shelly_io_worker_status import local_pm_status_payload, normalized_energy_payload
from venus_evcharger.backend.shelly_io_worker_transport import ShellyWorkerTransport
from venus_evcharger.backend.shelly_io_types import (
    JsonObject,
    PendingRelayCommand,
    ShellyEnergyData,
    ShellyPmStatus,
    optional_json_object,
)


class ShellyWorker:
    """Handle optimistic PM publishing, queued relay writes, and the worker loop."""

    def __init__(
        self,
        service: ShellyWorkerHost,
        requests: ShellyRequestClient,
        capabilities: ShellyCapabilities,
        runtime: ShellyChargerRuntime,
        transport: ShellyWorkerTransport,
        clock: Callable[[], float],
        fetch_pm_status: Callable[[], JsonObject],
    ) -> None:
        self.service = service
        self.requests = requests
        self.capabilities = capabilities
        self.runtime = runtime
        self.transport = transport
        self._clock = clock
        self._fetch_pm_status = fetch_pm_status

    @staticmethod
    def _normalized_energy_payload(value: object) -> ShellyEnergyData:
        return normalized_energy_payload(value)

    def build_local_pm_status(self, relay_on: bool) -> ShellyPmStatus:
        svc = self.service
        source = getattr(svc, "_last_pm_status", None)
        raw_status = optional_json_object(source) or {}
        pm_status = local_pm_status_payload(raw_status)
        last_voltage = getattr(svc, "_last_voltage", None)
        voltage = (
            float(last_voltage)
            if isinstance(last_voltage, (int, float)) and not isinstance(last_voltage, bool)
            else 230.0
        )
        pm_status["output"] = bool(relay_on)
        status_voltage = pm_status.get("voltage")
        pm_status["voltage"] = float(status_voltage or voltage)
        pm_status["aenergy"] = self._normalized_energy_payload(pm_status.get("aenergy"))
        pm_status["apower"] = 0.0
        pm_status["current"] = 0.0
        return pm_status

    def publish_local_pm_status(self, relay_on: bool, now: float | None = None) -> ShellyPmStatus:
        svc = self.service
        current = svc.time_now() if now is None else float(now)
        pm_status = self.build_local_pm_status(relay_on)
        pm_status["_pm_confirmed"] = False
        svc._last_pm_status = dict(pm_status)
        svc._last_pm_status_at = current
        svc._last_pm_status_confirmed = False
        svc.runtime.update_worker_snapshot(
            captured_at=current,
            pm_captured_at=current,
            pm_status=pm_status,
            pm_confirmed=False,
        )
        return pm_status

    def queue_relay_command(self, relay_on: bool, now: float | None = None) -> None:
        svc = self.service
        svc.runtime.ensure_worker_state()
        current = svc.time_now() if now is None else float(now)
        self.capabilities.warn_if_direct_switching_under_load(bool(relay_on))
        with svc._relay_command_lock:
            svc._pending_relay_state = bool(relay_on)
            svc._pending_relay_requested_at = current
            svc._relay_sync_expected_state = bool(relay_on)
            svc._relay_sync_requested_at = current
            svc._relay_sync_deadline_at = current + float(getattr(svc, "relay_sync_timeout_seconds", 2.0))
            svc._relay_sync_failure_reported = False

    def peek_pending_relay_command(self) -> PendingRelayCommand:
        svc = self.service
        svc.runtime.ensure_worker_state()
        with svc._relay_command_lock:
            return svc._pending_relay_state, svc._pending_relay_requested_at

    def clear_pending_relay_command(self, relay_on: bool) -> None:
        svc = self.service
        svc.runtime.ensure_worker_state()
        with svc._relay_command_lock:
            if svc._pending_relay_state == bool(relay_on):
                svc._pending_relay_state = None
                svc._pending_relay_requested_at = None

    def worker_apply_pending_relay_command(self) -> None:
        command_context = self._pending_relay_command_context()
        if command_context is None:
            return
        svc, target_on, source_key, source_label, current = command_context
        try:
            self._apply_pending_relay_target(svc, bool(target_on))
        except BACKEND_IO_ERRORS as error:
            self._handle_pending_relay_command_error(svc, source_key, source_label, current, error)
            return
        self._finalize_pending_relay_command(svc, bool(target_on), source_key, source_label)

    def _pending_relay_command_context(self) -> tuple[ShellyWorkerHost, bool, str, str, float] | None:
        svc = self.service
        target_on, _requested_at = self.peek_pending_relay_command()
        if target_on is None:
            return None
        source_key = self.capabilities.split_enable_source_key()
        current = self._clock()
        if self._source_retry_blocks_pending_relay(source_key, current):
            return None
        return svc, bool(target_on), source_key, self.capabilities.split_enable_source_label(), current

    def _source_retry_blocks_pending_relay(self, source_key: str, current: float) -> bool:
        """Return whether source backoff should defer the pending relay command."""
        if source_key == "charger":
            return bool(self.runtime.charger_retry_active(current))
        if source_key == "shelly":
            return bool(self.transport.retry_active(current))
        return False

    def _apply_pending_relay_target(self, svc: ShellyWorkerHost, target_on: bool) -> None:
        backend = self.capabilities.split_enable_backend()
        if backend is not None:
            backend.set_enabled(bool(target_on))
            return
        self.requests.rpc_call_with_session(
            svc._worker_session,
            "Switch.Set",
            id=self.requests.service.pm_id,
            on=bool(target_on),
        )

    def _handle_pending_relay_command_error(
        self,
        svc: ShellyWorkerHost,
        source_key: str,
        source_label: str,
        current: float,
        error: BaseException,
    ) -> None:
        reason = self._remember_pending_relay_command_error(source_key, current, error)
        self._warn_pending_relay_command_error(svc, source_key, source_label, current, reason, error)

    def _remember_pending_relay_command_error(
        self,
        source_key: str,
        current: float,
        error: BaseException,
    ) -> str:
        if source_key == "charger":
            transport_reason = modbus_transport_issue_reason(error)
            if transport_reason is not None:
                self.runtime.remember_charger_transport_issue(transport_reason, "enable", error, current)
                self.runtime.remember_charger_retry(transport_reason, "enable", current)
            return "error"
        if source_key == "shelly":
            shelly_reason = self.transport.classify_error(error)
            self.transport.remember_failure(shelly_reason, "relay", error, current)
            return shelly_reason
        return "error"

    def _warn_pending_relay_command_error(
        self,
        svc: ShellyWorkerHost,
        source_key: str,
        source_label: str,
        current: float,
        reason: str,
        error: BaseException,
    ) -> None:
        svc.runtime.mark_failure(source_key)
        svc.runtime.warning_throttled(
            f"worker-{source_key}-switch-failed-{reason}",
            svc.auto_shelly_soft_fail_seconds,
            "%s switch failed (%s, consecutive=%s, retry=%ss): %s",
            source_label,
            reason,
            self._pending_relay_shelly_error_count(source_key),
            self._pending_relay_shelly_retry_remaining(source_key, current),
            error,
            exc_info=self._pending_relay_error_exc_info(source_key, error),
        )

    def _pending_relay_shelly_error_count(self, source_key: str) -> int:
        """Return Shelly consecutive error count only for Shelly-backed commands."""
        if source_key != "shelly":
            return 0
        return self.transport.consecutive_errors()

    def _pending_relay_shelly_retry_remaining(self, source_key: str, current: float) -> float:
        """Return Shelly retry time remaining when the helper exists."""
        if source_key != "shelly":
            return 0.0
        return self.transport.retry_remaining(current)

    def _pending_relay_error_exc_info(self, source_key: str, error: BaseException) -> BaseException | None:
        """Suppress noisy tracebacks for common Shelly network failures."""
        if source_key == "shelly" and self.transport.is_common_network_error(error):
            return None
        return error

    def _finalize_pending_relay_command(
        self,
        svc: ShellyWorkerHost,
        target_on: bool,
        source_key: str,
        source_label: str,
    ) -> None:
        completed_at = svc.time_now()
        self.clear_pending_relay_command(bool(target_on))
        svc.auto.mark_relay_changed(bool(target_on), completed_at)
        if source_key == "shelly":
            self.transport.remember_success(svc.time_now(), "Shelly relay writes recovered")
        else:
            self.runtime.clear_charger_transport_issue()
            self.runtime.clear_charger_retry()
            svc.runtime.mark_recovery(source_key, "%s writes recovered", source_label)
        self.publish_local_pm_status(bool(target_on), completed_at)

    def io_worker_once(self) -> None:
        svc = self.service
        svc.runtime.ensure_worker_state()
        now = svc.time_now()
        auto_mode_active = self._worker_auto_mode_active(svc)
        self._update_worker_tick_snapshot(svc, now, auto_mode_active)
        self.worker_apply_pending_relay_command()

        if self.transport.retry_active(now):
            self._update_worker_unconfirmed_snapshot(svc, now, auto_mode_active)
            return

        try:
            pm_status = self._fetch_pm_status()
            read_at = svc.time_now()
            self.transport.remember_success(read_at, "Shelly status reads recovered")
            self._update_worker_confirmed_snapshot(svc, read_at, auto_mode_active, pm_status)
        except BACKEND_IO_ERRORS as error:
            reason = self.transport.classify_error(error)
            self.transport.remember_failure(reason, "read", error, now)
            svc.runtime.mark_failure("shelly")
            exc_info = None if self.transport.is_common_network_error(error) else error
            svc.runtime.warning_throttled(
                f"worker-shelly-read-failed-{reason}",
                svc.auto_shelly_soft_fail_seconds,
                "Shelly status read failed (%s, consecutive=%s, retry=%ss): %s",
                reason,
                self._pending_relay_shelly_error_count("shelly"),
                self._pending_relay_shelly_retry_remaining("shelly", now),
                error,
                exc_info=exc_info,
            )
            self._update_worker_unconfirmed_snapshot(svc, now, auto_mode_active)

    @staticmethod
    def _worker_auto_mode_active(svc: ShellyWorkerHost) -> bool:
        return svc.auto.mode_uses_auto_logic(getattr(svc, "virtual_mode", 0))

    @staticmethod
    def _update_worker_tick_snapshot(svc: ShellyWorkerHost, captured_at: float, auto_mode_active: bool) -> None:
        svc.runtime.update_worker_snapshot(
            captured_at=captured_at,
            auto_mode_active=auto_mode_active,
        )

    @staticmethod
    def _update_worker_unconfirmed_snapshot(svc: ShellyWorkerHost, captured_at: float, auto_mode_active: bool) -> None:
        svc.runtime.update_worker_snapshot(
            captured_at=captured_at,
            auto_mode_active=auto_mode_active,
            pm_status=None,
            pm_captured_at=None,
            pm_confirmed=False,
        )

    @staticmethod
    def _update_worker_confirmed_snapshot(
        svc: ShellyWorkerHost,
        captured_at: float,
        auto_mode_active: bool,
        pm_status: JsonObject,
    ) -> None:
        svc.runtime.update_worker_snapshot(
            captured_at=captured_at,
            pm_captured_at=captured_at,
            auto_mode_active=auto_mode_active,
            pm_status=pm_status,
            pm_confirmed=True,
        )

    def io_worker_loop(self) -> None:
        svc = self.service
        svc.runtime.ensure_worker_state()
        stop_event = svc._worker_stop_event
        while not stop_event.is_set():
            cycle_started = svc.time_now()
            try:
                self.io_worker_once()
            except Exception as error:
                svc.runtime.warning_throttled(
                    "io-worker-cycle-failed",
                    max(1.0, svc._worker_poll_interval_seconds),
                    "Background I/O worker cycle failed: %s",
                    error,
                    exc_info=error,
                )

            wait_seconds = self._worker_loop_wait_seconds(svc, cycle_started)
            if stop_event.wait(wait_seconds):
                return

    @staticmethod
    def _worker_loop_wait_seconds(svc: ShellyWorkerHost, cycle_started: float) -> float:
        elapsed = svc.time_now() - cycle_started
        return max(0.05, svc._worker_poll_interval_seconds - elapsed)


__all__ = ["ShellyWorker"]
