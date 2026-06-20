# SPDX-License-Identifier: GPL-3.0-or-later
"""Static contracts for Shelly I/O mixins.

The runtime mixins are composed by ``ShellyIoController``.  These protocols keep
cross-mixin expectations explicit without placing large type-only method blocks
inside the runtime modules themselves.
"""

from __future__ import annotations

from typing import Protocol

from venus_evcharger.backend.models import ChargerState, PhaseSelection
from venus_evcharger.backend.shelly_io_types import ShellyIoHost, _ChargerStateBackendLike, _EnableBackendLike


class ShellyIoRuntimeMixinContract(Protocol):
    """Methods and state that the runtime mixin receives from sibling mixins."""

    service: ShellyIoHost

    def _runtime_now(self) -> float: ...  # pragma: no cover

    def _phase_selection_switch_backend(self) -> object | None: ...  # pragma: no cover

    def _charger_supported_phase_selections(self) -> tuple[PhaseSelection, ...]: ...  # pragma: no cover

    def _remember_phase_selection_state(
        self,
        *,
        active: object | None = None,
        requested: object | None = None,
        supported: object | None = None,
    ) -> None: ...  # pragma: no cover

    def _charger_state_backend(self) -> _ChargerStateBackendLike | None: ...  # pragma: no cover


class ShellyIoWorkerMixinContract(Protocol):
    """Methods and state that the worker mixin receives from sibling mixins."""

    service: ShellyIoHost

    def _runtime_now(self) -> float: ...  # pragma: no cover

    def _warn_if_direct_switching_under_load(self, relay_on: bool) -> None: ...  # pragma: no cover

    def _split_enable_source_key(self) -> str: ...  # pragma: no cover

    def _split_enable_source_label(self) -> str: ...  # pragma: no cover

    def _split_enable_backend(self) -> _EnableBackendLike | None: ...  # pragma: no cover

    def _charger_retry_active(self, now: float | None = None) -> bool: ...  # pragma: no cover

    def _remember_charger_transport_issue(
        self,
        reason: str,
        source: str,
        error: BaseException,
        now: float | None = None,
    ) -> None: ...  # pragma: no cover

    def _remember_charger_retry(self, reason: str, source: str, now: float | None = None) -> None: ...  # pragma: no cover

    def _clear_charger_transport_issue(self) -> None: ...  # pragma: no cover

    def _clear_charger_retry(self) -> None: ...  # pragma: no cover
