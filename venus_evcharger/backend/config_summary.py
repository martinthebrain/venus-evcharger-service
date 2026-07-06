# SPDX-License-Identifier: GPL-3.0-or-later
"""Backend runtime-summary construction and configured-state helpers."""

from __future__ import annotations

from pathlib import Path

from .config_normalization import _configured_text
from .models import BackendMode, BackendRuntimeSummary


def _build_runtime_summary(
    *,
    backend_mode: BackendMode,
    meter_type: str | None,
    meter_config_path: Path | None,
    switch_type: str | None,
    switch_config_path: Path | None,
    charger_type: str | None,
    charger_config_path: Path | None,
    legacy_host: object = "",
    primary_rpc_configured: bool | None = None,
) -> BackendRuntimeSummary:
    """Return one normalized runtime summary with derived configured flags."""
    if primary_rpc_configured is None:
        primary_rpc_configured = _legacy_primary_rpc_configured(backend_mode, legacy_host)
    topology_configured = _topology_configured(
        backend_mode=backend_mode,
        meter_type=meter_type,
        meter_config_path=meter_config_path,
        switch_type=switch_type,
        switch_config_path=switch_config_path,
        charger_type=charger_type,
        charger_config_path=charger_config_path,
        legacy_host=legacy_host,
    )
    return BackendRuntimeSummary(
        backend_mode=backend_mode,
        meter_type=meter_type,
        meter_config_path=meter_config_path,
        switch_type=switch_type,
        switch_config_path=switch_config_path,
        charger_type=charger_type,
        charger_config_path=charger_config_path,
        topology_configured=topology_configured,
        primary_rpc_configured=bool(primary_rpc_configured),
    )


def _legacy_primary_rpc_configured(backend_mode: BackendMode, legacy_host: object) -> bool:
    """Return whether one combined-style setup still exposes a primary RPC host."""
    return backend_mode != "split" and bool(_configured_text(legacy_host))


def _topology_configured(
    *,
    backend_mode: BackendMode,
    meter_type: str | None,
    meter_config_path: Path | None,
    switch_type: str | None,
    switch_config_path: Path | None,
    charger_type: str | None,
    charger_config_path: Path | None,
    legacy_host: object,
) -> bool:
    """Return whether runtime role information represents a configured topology."""
    if backend_mode != "split":
        return bool(_configured_text(legacy_host))
    return any(
        (
            _configured_role(meter_type, meter_config_path),
            _configured_role(switch_type, switch_config_path),
            _configured_role(charger_type, charger_config_path),
        )
    )


def _configured_role(role_type: str | None, config_path: Path | None) -> bool:
    """Return whether one runtime backend role is fully configured."""
    return role_type is not None and config_path is not None


def runtime_summary_is_configured(summary: BackendRuntimeSummary, *, legacy_host: object = "") -> bool:
    """Return whether one runtime summary represents a configured load topology."""
    if summary.backend_mode != "split":
        return bool(_configured_text(legacy_host)) or summary.topology_configured
    return bool(summary.topology_configured)


def runtime_summary_uses_legacy_primary_rpc(summary: BackendRuntimeSummary, *, legacy_host: object = "") -> bool:
    """Return whether one runtime summary still uses the legacy direct Shelly RPC host."""
    return bool(summary.primary_rpc_configured) or (summary.backend_mode != "split" and bool(_configured_text(legacy_host)))
