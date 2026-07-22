# SPDX-License-Identifier: GPL-3.0-or-later
"""Semantic EVCS publication with local enqueue throttling."""

from __future__ import annotations

import time
from collections.abc import Mapping

from venus_evcharger.core.contracts import finite_float_or_none
from venus_evcharger.ipc.gateway_pressure import service_gateway_pressure_policy
from venus_evcharger.ports.gateway_publication import PublicationPriority, require_gateway_publication
from venus_evcharger.publish.dbus_shared import DbusPublishContext, PublishServicePort, PublishStateEntry, PublishValue

PUBLICATION_ERRORS = (OSError, RuntimeError, TypeError, ValueError)

_CRITICAL_FIELDS = frozenset(
    (
        "connected",
        "status",
        "mode",
        "auto_start",
        "start_stop",
        "enable",
        "set_current",
        "phase_selection",
        "phase_selection_active",
        "supported_phase_selections",
        "auto_health",
        "auto_state",
        "auto_status_source",
    )
)


class DbusPublishCore:
    """Publish only semantic EVCS fields through the gateway port."""

    def __init__(self, context: DbusPublishContext) -> None:
        self.service: PublishServicePort = context.service
        self._accepted: dict[str, PublishStateEntry] = {}

    def ensure_state(self) -> None:
        """Ensure configurable publication intervals exist on partial test hosts."""
        if not hasattr(self.service, "_dbus_live_publish_interval_seconds"):
            self.service._dbus_live_publish_interval_seconds = 1.0
        if not hasattr(self.service, "_dbus_slow_publish_interval_seconds"):
            self.service._dbus_slow_publish_interval_seconds = 5.0

    def publish_field(
        self,
        field: str,
        value: PublishValue,
        now: float | None = None,
        interval_seconds: float | None = None,
        force: bool = False,
    ) -> bool:
        """Publish one semantic EVCS field."""
        return self.publish_fields(
            "single-field",
            {field: value},
            now,
            interval_seconds=interval_seconds,
            force=force,
        )

    def publish_fields(
        self,
        group_name: str,
        fields: Mapping[str, PublishValue],
        now: float | None,
        interval_seconds: float | None = None,
        force: bool = False,
    ) -> bool:
        """Enqueue changed semantic fields and remember only mailbox acceptance."""
        self.ensure_state()
        current = time.time() if now is None else float(now)
        effective_interval = self._effective_publish_interval(interval_seconds, group_name=group_name, force=force)
        staged = self._staged_fields(fields, current, effective_interval, force)
        if not staged:
            return False
        if not self._enqueue_fields(group_name, staged):
            return False
        self._remember_accepted(staged, current)
        return True

    def _staged_fields(
        self,
        fields: Mapping[str, PublishValue],
        current: float,
        interval_seconds: float | None,
        force: bool,
    ) -> dict[str, PublishValue]:
        return {
            str(field): value
            for field, value in fields.items()
            if self._should_publish(str(field), value, current, interval_seconds, force)
        }

    def _enqueue_fields(self, group_name: str, staged: Mapping[str, PublishValue]) -> bool:
        try:
            receipt = require_gateway_publication(self.service).publish_evcs_fields(
                staged,
                priority=_publication_priority(group_name, staged),
            )
        except PUBLICATION_ERRORS as error:
            self._publication_failure(group_name, error)
            return False
        return receipt.accepted

    def _remember_accepted(self, staged: Mapping[str, PublishValue], current: float) -> None:
        for field, value in staged.items():
            self._accepted[field] = {"value": value, "updated_at": current}

    def last_accepted_field(self, field: str) -> object:
        """Return the last value accepted by IPC, never an applied DBus readback."""
        entry = self._accepted.get(str(field))
        return None if entry is None else entry.get("value")

    def _effective_publish_interval(
        self,
        interval_seconds: float | None,
        *,
        group_name: str,
        force: bool,
    ) -> float | None:
        if force or interval_seconds is None:
            return interval_seconds
        return float(
            service_gateway_pressure_policy(self.service).publish_interval_seconds(
                float(interval_seconds),
                group=group_name,
            )
        )

    def _should_publish(
        self,
        field: str,
        value: PublishValue,
        current: float,
        interval_seconds: float | None,
        force: bool,
    ) -> bool:
        entry = self._accepted.get(field)
        if force or entry is None:
            return True
        if interval_seconds is None:
            return bool(value != entry.get("value"))
        updated_at = finite_float_or_none(entry.get("updated_at"))
        return updated_at is None or current - updated_at >= float(interval_seconds)

    def _publication_failure(self, group_name: str, error: BaseException) -> None:
        self.service.runtime.mark_failure("dbus")
        self.service.runtime.warning_throttled(
            f"gateway-publication-{group_name}-failed",
            1.0,
            "Gateway publication group %s was not accepted: %s",
            group_name,
            error,
        )


def _publication_priority(group_name: str, fields: Mapping[str, object]) -> PublicationPriority:
    if str(group_name).startswith("diagnostic"):
        return "diagnostic"
    if any(field in _CRITICAL_FIELDS or field.startswith("auto_software_update") for field in fields):
        return "critical"
    return "live"


__all__ = ["DbusPublishCore"]
