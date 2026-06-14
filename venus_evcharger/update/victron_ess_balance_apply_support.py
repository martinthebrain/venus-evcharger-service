# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="attr-defined,no-any-return"
# pyright: reportAttributeAccessIssue=false, reportReturnType=false
"""Support helpers for Victron ESS balance-bias application."""

from __future__ import annotations

import logging
import sys
from typing import Any

from venus_evcharger.dbus_gateway import GatewayClient, gateway_paths


class _UpdateCycleVictronEssBalanceApplySupportMixin:
    """Shared source, PID, and write helpers for Victron ESS balance-bias application."""

    @staticmethod
    def _merge_victron_ess_balance_metrics(svc: Any, metrics: dict[str, Any]) -> None:
        last_metrics = getattr(svc, "_last_auto_metrics", None)
        if not isinstance(last_metrics, dict):
            svc._last_auto_metrics = dict(metrics)
            return
        last_metrics.update(metrics)

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if not isinstance(value, (int, float)):
            return None
        return float(value)

    @staticmethod
    def _normalized_mapping(raw_value: object) -> dict[str, Any]:
        return raw_value if isinstance(raw_value, dict) else {}

    @staticmethod
    def _victron_ess_balance_cluster_sources(cluster: dict[str, Any]) -> list[dict[str, Any]]:
        raw_sources = cluster.get("battery_sources", [])
        return [value for value in raw_sources if isinstance(value, dict)]

    @staticmethod
    def _victron_ess_balance_configured_source_id(svc: Any) -> str:
        return str(getattr(svc, "auto_battery_discharge_balance_victron_bias_source_id", "") or "").strip()

    @staticmethod
    def _victron_ess_balance_matching_source(
        sources: list[dict[str, Any]],
        configured_source_id: str,
    ) -> dict[str, Any] | None:
        for source in sources:
            if str(source.get("source_id", "")).strip() == configured_source_id:
                return source
        return None

    @staticmethod
    def _victron_ess_balance_dbus_source_candidates(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            source
            for source in sources
            if str(source.get("discharge_balance_control_connector_type", "")).strip().lower() == "dbus"
        ]

    def _victron_ess_balance_source(self, cluster: dict[str, Any], svc: Any) -> tuple[dict[str, Any] | None, str]:
        sources = self._victron_ess_balance_cluster_sources(cluster)
        configured_source_id = self._victron_ess_balance_configured_source_id(svc)
        if configured_source_id:
            source = self._victron_ess_balance_matching_source(sources, configured_source_id)
            if source is not None:
                return source, "configured-source"
            return None, "victron-source-not-found"
        candidates = self._victron_ess_balance_dbus_source_candidates(sources)
        if len(candidates) == 1:
            return candidates[0], "auto-detected-dbus-source"
        if not candidates:
            return None, "victron-source-not-detected"
        return None, "victron-source-ambiguous"

    def _victron_ess_balance_support_mode(self, svc: Any) -> str:
        raw_mode = str(
            getattr(svc, "auto_battery_discharge_balance_victron_bias_support_mode", "allow_experimental") or ""
        ).strip().lower()
        if raw_mode in {"supported_only", "allow_experimental"}:
            return raw_mode
        return "allow_experimental"

    def _victron_ess_balance_source_support_allowed(self, source: dict[str, Any], svc: Any) -> bool:
        support_mode = self._victron_ess_balance_support_mode(svc)
        support = str(source.get("discharge_balance_control_support", "")).strip().lower()
        if support_mode == "supported_only":
            return support in {"supported", ""}
        return support in {"supported", "experimental", ""}

    def _victron_ess_balance_activation_mode(self, svc: Any) -> str:
        raw_mode = str(
            getattr(svc, "auto_battery_discharge_balance_victron_bias_activation_mode", "always") or ""
        ).strip().lower()
        if raw_mode in {"always", "export_only", "above_reserve_band", "export_and_above_reserve_band"}:
            return raw_mode
        return "always"

    @staticmethod
    def _victron_ess_balance_activation_site_regime_matches(mode: str, site_regime: str) -> bool:
        if mode in {"export_only", "export_and_above_reserve_band"}:
            return site_regime == "export"
        return True

    @staticmethod
    def _victron_ess_balance_activation_reserve_phase_matches(mode: str, reserve_phase: str) -> bool:
        if mode in {"above_reserve_band", "export_and_above_reserve_band"}:
            return reserve_phase == "above_reserve_band"
        return True

    def _victron_ess_balance_activation_allowed(self, learning_profile: dict[str, str], svc: Any) -> bool:
        mode = self._victron_ess_balance_activation_mode(svc)
        if mode == "always":
            return True
        site_regime = str(learning_profile.get("site_regime", "") or "")
        reserve_phase = str(learning_profile.get("reserve_phase", "") or "")
        return self._victron_ess_balance_activation_site_regime_matches(
            mode,
            site_regime,
        ) and self._victron_ess_balance_activation_reserve_phase_matches(
            mode,
            reserve_phase,
        )

    @staticmethod
    def _victron_ess_balance_pid_gain_config(svc: Any) -> dict[str, float]:
        return {
            "kp": float(getattr(svc, "auto_battery_discharge_balance_victron_bias_kp", 0.0) or 0.0),
            "ki": float(getattr(svc, "auto_battery_discharge_balance_victron_bias_ki", 0.0) or 0.0),
            "kd": float(getattr(svc, "auto_battery_discharge_balance_victron_bias_kd", 0.0) or 0.0),
        }

    @staticmethod
    def _victron_ess_balance_pid_limit_config(svc: Any) -> dict[str, float]:
        return {
            "deadband_w": max(
                0.0,
                float(getattr(svc, "auto_battery_discharge_balance_victron_bias_deadband_watts", 0.0) or 0.0),
            ),
            "integral_limit_w": max(
                0.0,
                float(getattr(svc, "auto_battery_discharge_balance_victron_bias_integral_limit_watts", 0.0) or 0.0),
            ),
            "max_abs_w": max(
                0.0,
                float(getattr(svc, "auto_battery_discharge_balance_victron_bias_max_abs_watts", 0.0) or 0.0),
            ),
            "ramp_rate_w_per_second": max(
                0.0,
                float(
                    getattr(svc, "auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second", 0.0) or 0.0
                ),
            ),
        }

    @staticmethod
    def _victron_ess_balance_pid_config(svc: Any) -> dict[str, float]:
        return {
            **_UpdateCycleVictronEssBalanceApplySupportMixin._victron_ess_balance_pid_gain_config(svc),
            **_UpdateCycleVictronEssBalanceApplySupportMixin._victron_ess_balance_pid_limit_config(svc),
        }

    @staticmethod
    def _victron_ess_balance_effective_error(raw_error_w: float, deadband_w: float) -> float:
        return 0.0 if abs(raw_error_w) < deadband_w else raw_error_w

    def _victron_ess_balance_pid_timing(self, svc: Any, now: float) -> tuple[float, float]:
        last_at = self._optional_float(getattr(svc, "_victron_ess_balance_pid_last_at", None))
        dt = 0.0 if last_at is None else max(0.0, float(now) - float(last_at))
        last_error_w = float(getattr(svc, "_victron_ess_balance_pid_last_error_w", 0.0) or 0.0)
        return dt, last_error_w

    @staticmethod
    def _victron_ess_balance_pid_integral_output(
        current_integral_output_w: float,
        effective_error_w: float,
        dt: float,
        ki: float,
        integral_limit_w: float,
    ) -> float:
        integral_output_w = float(current_integral_output_w)
        if dt > 0.0 and ki > 0.0:
            integral_output_w += ki * effective_error_w * dt
        if integral_limit_w > 0.0:
            integral_output_w = max(-integral_limit_w, min(integral_output_w, integral_limit_w))
        return integral_output_w

    @staticmethod
    def _victron_ess_balance_pid_derivative_w(effective_error_w: float, last_error_w: float, dt: float) -> float:
        return 0.0 if dt <= 0.0 else (effective_error_w - last_error_w) / dt

    @staticmethod
    def _victron_ess_balance_pid_target_output_w(
        effective_error_w: float,
        kp: float,
        integral_output_w: float,
        kd: float,
        derivative_w: float,
    ) -> float:
        return (kp * effective_error_w) + integral_output_w + (kd * derivative_w)

    @staticmethod
    def _victron_ess_balance_pid_clamped_output_w(target_output_w: float, max_abs_w: float) -> float:
        if max_abs_w <= 0.0:
            return float(target_output_w)
        return max(-max_abs_w, min(target_output_w, max_abs_w))

    @staticmethod
    def _victron_ess_balance_pid_ramped_output_w(
        last_output_w: float,
        target_output_w: float,
        dt: float,
        ramp_rate_w_per_second: float,
    ) -> float:
        if ramp_rate_w_per_second <= 0.0 or dt <= 0.0:
            return float(target_output_w)
        max_step_w = ramp_rate_w_per_second * dt
        delta_w = max(-max_step_w, min(target_output_w - last_output_w, max_step_w))
        return float(last_output_w + delta_w)

    @staticmethod
    def _victron_ess_balance_pid_store_state(
        svc: Any,
        effective_error_w: float,
        now: float,
        integral_output_w: float,
    ) -> None:
        svc._victron_ess_balance_pid_last_error_w = float(effective_error_w)
        svc._victron_ess_balance_pid_last_at = float(now)
        svc._victron_ess_balance_pid_integral_output_w = float(integral_output_w)

    def _victron_ess_balance_pid_output(self, svc: Any, error_w: float, now: float) -> float:
        config = self._victron_ess_balance_pid_config(svc)
        raw_error_w = float(error_w)
        effective_error_w = self._victron_ess_balance_effective_error(raw_error_w, config["deadband_w"])
        dt, last_error_w = self._victron_ess_balance_pid_timing(svc, now)
        integral_output_w = float(getattr(svc, "_victron_ess_balance_pid_integral_output_w", 0.0) or 0.0)
        if effective_error_w == 0.0:
            integral_output_w = 0.0
            target_output_w = 0.0
        else:
            integral_output_w = self._victron_ess_balance_pid_integral_output(
                integral_output_w,
                effective_error_w,
                dt,
                config["ki"],
                config["integral_limit_w"],
            )
            derivative_w = self._victron_ess_balance_pid_derivative_w(effective_error_w, last_error_w, dt)
            target_output_w = self._victron_ess_balance_pid_target_output_w(
                effective_error_w,
                config["kp"],
                integral_output_w,
                config["kd"],
                derivative_w,
            )
        target_output_w = self._victron_ess_balance_pid_clamped_output_w(target_output_w, config["max_abs_w"])
        last_output_w = float(getattr(svc, "_victron_ess_balance_pid_last_output_w", 0.0) or 0.0)
        output_w = self._victron_ess_balance_pid_ramped_output_w(
            last_output_w,
            target_output_w,
            dt,
            config["ramp_rate_w_per_second"],
        )
        self._victron_ess_balance_pid_store_state(svc, effective_error_w, now, integral_output_w)
        return float(output_w)

    def _victron_ess_balance_should_write(self, svc: Any, now: float, setpoint_w: float) -> bool:
        min_update_seconds = max(
            0.0,
            float(getattr(svc, "auto_battery_discharge_balance_victron_bias_min_update_seconds", 0.0) or 0.0),
        )
        last_write_at = self._optional_float(getattr(svc, "_victron_ess_balance_last_write_at", None))
        if last_write_at is not None and (float(now) - float(last_write_at)) < min_update_seconds:
            return False
        last_setpoint_w = self._victron_ess_balance_last_setpoint(svc)
        if last_setpoint_w is None:
            return True
        return abs(float(setpoint_w) - float(last_setpoint_w)) >= 1.0

    @staticmethod
    def _victron_ess_balance_last_setpoint(svc: Any) -> float | None:
        return _UpdateCycleVictronEssBalanceApplySupportMixin._optional_float(
            getattr(svc, "_victron_ess_balance_last_setpoint_w", None)
        )

    @staticmethod
    def _victron_ess_balance_write_target(service_name: object, path: object) -> tuple[str, str]:
        return str(service_name or "").strip(), str(path or "").strip()

    @staticmethod
    def _victron_ess_balance_write_payload(dbus_module: Any, value: float) -> Any:
        del dbus_module
        return float(value)

    def _victron_ess_balance_try_write_setpoint(
        self,
        svc: Any,
        normalized_service: str,
        normalized_path: str,
        value: float,
    ) -> None:
        GatewayClient(gateway_paths(str(getattr(svc, "dbus_gateway_run_dir", "") or "") or None)).enqueue_command(
            {
                "kind": "set_value",
                "source": "victron-ess-balance",
                "service": normalized_service,
                "path": normalized_path,
                "value": self._victron_ess_balance_write_payload(None, value),
                "priority": "user",
                "coalesce_key": f"{normalized_service}:{normalized_path}",
            }
        )

    @staticmethod
    def _victron_ess_balance_log_write_retry(
        normalized_service: str,
        normalized_path: str,
        error: Exception,
    ) -> None:
        _UpdateCycleVictronEssBalanceApplySupportMixin._victron_ess_balance_logging_module().debug(
            "Victron ESS balance-bias write retry for %s %s after error: %s",
            normalized_service,
            normalized_path,
            error,
        )

    def _victron_ess_balance_write_setpoint(
        self,
        svc: Any,
        service_name: object,
        path: object,
        value: float,
    ) -> bool:
        normalized_service, normalized_path = self._victron_ess_balance_write_target(service_name, path)
        if not normalized_service or not normalized_path:
            return False
        last_error = self._victron_ess_balance_write_error(
            svc,
            normalized_service,
            normalized_path,
            value,
        )
        if last_error is None:
            return True
        svc._warning_throttled(
            "victron-ess-balance-write-failed",
            self._victron_ess_balance_write_warning_interval_seconds(svc),
            "Victron ESS balance-bias write to %s %s failed: %s",
            normalized_service,
            normalized_path,
            last_error,
        )
        return False

    def _victron_ess_balance_write_error(
        self,
        svc: Any,
        normalized_service: str,
        normalized_path: str,
        value: float,
    ) -> Exception | None:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                self._victron_ess_balance_try_write_setpoint(
                    svc,
                    normalized_service,
                    normalized_path,
                    value,
                )
                return None
            except Exception as error:  # pylint: disable=broad-except
                last_error = error
                if attempt == 0:
                    self._victron_ess_balance_log_write_retry(normalized_service, normalized_path, error)
        return last_error

    @staticmethod
    def _victron_ess_balance_write_warning_interval_seconds(svc: Any) -> float:
        return max(
            5.0,
            float(getattr(svc, "auto_battery_discharge_balance_victron_bias_min_update_seconds", 2.0) or 2.0),
        )
    @staticmethod
    def _victron_ess_balance_apply_module() -> Any:
        return sys.modules.get("venus_evcharger.update.victron_ess_balance_apply")

    @classmethod
    def _victron_ess_balance_dbus_module(cls) -> Any:
        del cls
        raise RuntimeError("Direct DBus access is disabled; use the DBus gateway adapter")

    @classmethod
    def _victron_ess_balance_logging_module(cls) -> Any:
        module = cls._victron_ess_balance_apply_module()
        return getattr(module, "logging", logging)
