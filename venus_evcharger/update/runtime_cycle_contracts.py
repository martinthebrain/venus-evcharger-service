# SPDX-License-Identifier: GPL-3.0-or-later
"""Type-only contracts required by the runtime update-cycle role."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from venus_evcharger.update.state import _UpdateCycleState


class _UpdateCycleRuntimeContracts(_UpdateCycleState):
    """Expose composed controller methods used by the runtime-cycle role."""

    if TYPE_CHECKING:  # pragma: no cover

        def resolve_pm_status_for_update(
            self,
            svc: Any,
            worker_snapshot: dict[str, Any],
            now: float,
        ) -> dict[str, Any] | None: ...

        def publish_offline_update(self, now: float) -> bool: ...

        def resolve_auto_inputs(
            self,
            worker_snapshot: dict[str, Any],
            now: float,
            auto_mode_active: bool,
        ) -> tuple[Any, Any, Any]: ...

        def apply_victron_ess_balance_bias(self, svc: Any, now: float, auto_mode_active: bool) -> None: ...

        def extract_pm_measurements(self, svc: Any, pm_status: dict[str, Any]) -> tuple[bool, float, float, float, float]: ...
