# SPDX-License-Identifier: GPL-3.0-or-later
"""Source selection and activation gates for Victron ESS balance-bias application."""

from __future__ import annotations

from typing import Any

from venus_evcharger.update.runtime_cycle import _UpdateCycleRuntime


class _UpdateCycleVictronEssBalanceApplySources(_UpdateCycleRuntime):
    """Resolve eligible Victron-side battery sources and activation policy."""

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
    def _normalized_text(value: object) -> str:
        return str(value).strip() if value else ""

    @staticmethod
    def _victron_ess_balance_cluster_sources(cluster: dict[str, Any]) -> list[dict[str, Any]]:
        raw_sources = cluster.get("battery_sources", [])
        return [value for value in raw_sources if isinstance(value, dict)]

    @staticmethod
    def _victron_ess_balance_configured_source_id(svc: Any) -> str:
        return _UpdateCycleVictronEssBalanceApplySources._normalized_text(
            getattr(svc, "auto_battery_discharge_balance_victron_bias_source_id", None)
        )

    @staticmethod
    def _victron_ess_balance_matching_source(
        sources: list[dict[str, Any]],
        configured_source_id: str,
    ) -> dict[str, Any] | None:
        for source in sources:
            if _UpdateCycleVictronEssBalanceApplySources._normalized_text(source.get("source_id")) == configured_source_id:
                return source
        return None

    @staticmethod
    def _victron_ess_balance_dbus_source_candidates(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            source
            for source in sources
            if _UpdateCycleVictronEssBalanceApplySources._normalized_text(
                source.get("discharge_balance_control_connector_type")
            ).lower()
            == "dbus"
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
        raw_mode = self._normalized_text(
            getattr(svc, "auto_battery_discharge_balance_victron_bias_support_mode", None)
        ).lower()
        return "supported_only" if raw_mode == "supported_only" else "allow_experimental"

    def _victron_ess_balance_source_support_allowed(self, source: dict[str, Any], svc: Any) -> bool:
        support_mode = self._victron_ess_balance_support_mode(svc)
        support = self._normalized_text(source.get("discharge_balance_control_support")).lower()
        if support_mode == "supported_only":
            return support in {"supported", ""}
        return support in {"supported", "experimental", ""}

    def _victron_ess_balance_activation_mode(self, svc: Any) -> str:
        raw_mode = self._normalized_text(
            getattr(svc, "auto_battery_discharge_balance_victron_bias_activation_mode", None)
        ).lower()
        if raw_mode in {"export_only", "above_reserve_band", "export_and_above_reserve_band"}:
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
        site_regime = self._normalized_text(learning_profile.get("site_regime"))
        reserve_phase = self._normalized_text(learning_profile.get("reserve_phase"))
        return self._victron_ess_balance_activation_site_regime_matches(
            mode,
            site_regime,
        ) and self._victron_ess_balance_activation_reserve_phase_matches(
            mode,
            reserve_phase,
        )
