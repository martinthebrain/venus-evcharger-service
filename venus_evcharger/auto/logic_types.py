# SPDX-License-Identifier: GPL-3.0-or-later
"""Small explicit types shared across Auto-mode workflow modules."""

from __future__ import annotations

from dataclasses import dataclass


class RelayDecisionTypeError(TypeError):
    """Raised when an Auto decision helper returns an invalid value."""


@dataclass(frozen=True)
class RelayDecisionState:
    """Represent one intermediate Auto decision without relying on raw sentinels."""

    relay_on: bool | None

    @classmethod
    def pending(cls) -> RelayDecisionState:
        """Return the explicit "continue evaluating" state."""
        return cls(None)

    @classmethod
    def resolved(cls, relay_on: bool) -> RelayDecisionState:
        """Return one settled relay decision."""
        return cls(bool(relay_on))

    def resolved_value(self) -> bool:
        """Return the settled relay decision value."""
        if self.relay_on is None:
            raise RelayDecisionTypeError("pending relay decision has no resolved value")
        return self.relay_on

    @property
    def is_pending(self) -> bool:
        """Return True when later workflow stages must keep evaluating."""
        return self.relay_on is None

NO_RELAY_DECISION = RelayDecisionState.pending()
RelayDecision = bool | RelayDecisionState


def require_relay_bool(value: object, label: str = "relay decision") -> bool:
    """Return a boolean relay decision or raise for malformed helper output."""
    if isinstance(value, bool):
        return value
    raise RelayDecisionTypeError(f"{label} must be bool, got {type(value).__name__}")


def require_relay_decision(value: object, label: str = "relay decision") -> RelayDecision:
    """Return a boolean or pending relay decision, rejecting malformed values."""
    if isinstance(value, bool) or isinstance(value, RelayDecisionState):
        return value
    raise RelayDecisionTypeError(f"{label} must be bool or RelayDecisionState, got {type(value).__name__}")
