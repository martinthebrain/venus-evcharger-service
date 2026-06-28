# SPDX-License-Identifier: GPL-3.0-or-later
"""Type-only contracts for split relay charger mixins."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


class _RelayChargerHealthContractsMixin:
    """Declare readback and current helpers used by charger health logic."""

    if TYPE_CHECKING:  # pragma: no cover
        CHARGER_STATUS_CHARGING_HINT_TOKENS: frozenset[str]
        CHARGER_STATUS_READY_HINT_TOKENS: frozenset[str]
        CHARGER_STATUS_WAITING_HINT_TOKENS: frozenset[str]
        CHARGER_STATUS_FINISHED_HINT_TOKENS: frozenset[str]

        @staticmethod
        def _contactor_heuristic_delay_seconds(svc: Any) -> float: ...

        @staticmethod
        def _contactor_lockout_threshold(svc: Any) -> int: ...

        @staticmethod
        def _contactor_lockout_persistence_seconds(svc: Any) -> float: ...

        @staticmethod
        def _contactor_power_threshold_w(svc: Any) -> float: ...

        @staticmethod
        def _contactor_current_threshold_a(svc: Any) -> float: ...

        @staticmethod
        def _charger_enable_backend(svc: Any) -> Any | None: ...

        @classmethod
        def _charger_readback_now(cls, svc: Any, now: float | None = None) -> float: ...

        @classmethod
        def _fresh_charger_power_readback(cls, svc: Any, now: float | None = None) -> float | None: ...

        @classmethod
        def _fresh_charger_actual_current_readback(cls, svc: Any, now: float | None = None) -> float | None: ...

        @classmethod
        def _fresh_charger_text_readback(
            cls,
            svc: Any,
            attribute_name: str,
            now: float | None = None,
        ) -> str | None: ...

        @classmethod
        def _charger_text_tokens(cls, value: str | None) -> set[str]: ...

        @classmethod
        def _charger_text_indicates_fault(cls, value: str | None) -> bool: ...

        @classmethod
        def _fresh_switch_interlock_ok(cls, svc: Any, now: float | None = None) -> bool | None: ...

        @classmethod
        def _fresh_switch_feedback_closed(cls, svc: Any, now: float | None = None) -> bool | None: ...

        @classmethod
        def _fresh_charger_enabled_readback(cls, svc: Any, now: float | None = None) -> bool | None: ...
