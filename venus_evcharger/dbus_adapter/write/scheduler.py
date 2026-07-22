# SPDX-License-Identifier: GPL-3.0-or-later
"""Write scheduler assembly for DBus gateway commands."""

from __future__ import annotations

from collections import deque

from venus_evcharger.core.shared import config_get_float
from venus_evcharger.dbus_adapter.contracts import CommandOutcome
from venus_evcharger.dbus_adapter.write.health import DbusWriteSchedulerHealth
from venus_evcharger.dbus_adapter.write.protocols import DbusWriteSchedulerAdapter


class DbusWriteScheduler(DbusWriteSchedulerHealth):
    def __init__(self, adapter: DbusWriteSchedulerAdapter) -> None:
        self.adapter = adapter
        defaults = adapter.config["DEFAULT"]
        self.local_publish_burst_limit = max(1, int(config_get_float(defaults, "DbusGatewayLocalPublishBurstLimit", 20.0)))
        self.local_publish_tick_budget_seconds = max(
            0.001,
            config_get_float(defaults, "DbusGatewayLocalPublishTickBudgetMs", 75.0) / 1000.0,
        )
        self.dynamic_local_publish_burst_limit = self.local_publish_burst_limit
        self.queue_class_budgets = self._queue_class_budgets(defaults)
        self.base_queue_class_budgets = dict(self.queue_class_budgets)
        self._processed_events: deque[float] = deque()
        self._budget_events: deque[tuple[float, str]] = deque()
        self._lifecycle_events: deque[tuple[float, str, str]] = deque()
        self._lifecycle_counts: dict[str, int] = {}
        self.last_processed_at = 0.0
        self.last_scheduled_outcome: CommandOutcome | None = None
