# SPDX-License-Identifier: GPL-3.0-or-later
"""Structural contracts between the composed DBus publish roles."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from venus_evcharger.core.common import ScheduledModeSnapshot
from venus_evcharger.publish.dbus_shared import PublishValue


class FieldPublisherPort(Protocol):  # pragma: no cover - static contract
    """Publish semantic EVCS fields as one throttled transaction."""

    def publish_fields(
        self,
        group_name: str,
        fields: Mapping[str, PublishValue],
        now: float | None,
        interval_seconds: float | None = None,
        force: bool = False,
    ) -> bool: ...


class ConfigLearnedPort(Protocol):  # pragma: no cover - static contract
    """Learned/readback values needed by the GUI configuration snapshot."""

    def charger_enabled_readback(self, now: float | None) -> bool | None: ...

    def display_set_current(self, now: float | None) -> float: ...


class DiagnosticsLearnedPort(Protocol):  # pragma: no cover - static contract
    """Charger readback and retry diagnostics exposed by the learned role."""

    def charger_text_observed(self, attribute_name: str) -> str: ...

    def charger_estimate_active(self) -> int: ...

    def charger_estimate_source(self) -> str: ...

    def charger_transport_active(self, now: float) -> int: ...

    def charger_transport_reason(self, now: float) -> str: ...

    def charger_transport_source(self, now: float) -> str: ...

    def charger_transport_detail(self, now: float) -> str: ...

    def charger_retry_active(self, now: float) -> int: ...

    def charger_retry_reason(self, now: float) -> str: ...

    def charger_retry_source(self, now: float) -> str: ...


class ConfigRuntimeViewPort(Protocol):  # pragma: no cover - static contract
    """Runtime view needed while publishing GUI configuration values."""

    def configured_supported_phase_selections(self, service: object) -> object: ...


class SourceRuntimeViewPort(Protocol):  # pragma: no cover - static contract
    """Runtime view needed for backend and charger-source diagnostics."""

    def backend_mode_value(self, service: object) -> str: ...

    def backend_type_value(self, service: object, attribute_name: str, default: str = "") -> str: ...

    def charger_current_target_value(self, service: object) -> float: ...


class DecisionRuntimeViewPort(Protocol):  # pragma: no cover - static contract
    """Runtime view needed for Auto-decision diagnostics."""

    def auto_metrics(self, service: object) -> dict[str, object]: ...


class PhaseRuntimeViewPort(Protocol):  # pragma: no cover - static contract
    """Runtime view needed for phase-switch and contactor diagnostics."""

    def auto_phase_metric_text(self, service: object, field_name: str) -> str: ...

    def auto_phase_metric_float(self, service: object, field_name: str) -> float: ...

    def observed_phase_value(self, service: object) -> str: ...

    def phase_switch_mismatch_active(self, service: object) -> int: ...

    def phase_switch_lockout_active(self, service: object, now: float) -> int: ...

    def phase_switch_lockout_target(self, service: object, now: float) -> str: ...

    def phase_switch_lockout_reason(self, service: object, now: float) -> str: ...

    def phase_supported_configured(self, service: object) -> str: ...

    def phase_supported_effective(self, service: object, now: float) -> str: ...

    def phase_degraded_active(self, service: object, now: float) -> int: ...

    def switch_feedback_closed(self, service: object) -> int: ...

    def switch_interlock_ok(self, service: object) -> int: ...

    def switch_feedback_mismatch(self, service: object) -> int: ...

    def contactor_suspected_open(self, service: object) -> int: ...

    def contactor_suspected_welded(self, service: object) -> int: ...

    def contactor_fault_count(self, service: object) -> int: ...

    def contactor_lockout_active(self, service: object) -> int: ...

    def contactor_lockout_reason(self, service: object) -> str: ...

    def contactor_lockout_source(self, service: object) -> str: ...


class DiagnosticSummaryRuntimeViewPort(Protocol):  # pragma: no cover - static contract
    """Runtime view used while composing the complete diagnostics snapshot."""

    def scheduled_snapshot(self, service: object, now: float) -> ScheduledModeSnapshot | None: ...

    def fault_reason(self, service: object) -> str: ...

    def recovery_active(self, service: object) -> int: ...

    def phase_switch_lockout_active(self, service: object, now: float) -> int: ...

    def contactor_lockout_active(self, service: object) -> int: ...


@dataclass(frozen=True)
class DiagnosticsRuntimeViews:
    """Explicitly compose the four diagnostic runtime-view responsibilities."""

    sources: SourceRuntimeViewPort
    decisions: DecisionRuntimeViewPort
    phases: PhaseRuntimeViewPort
    summary: DiagnosticSummaryRuntimeViewPort
