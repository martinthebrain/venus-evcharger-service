# SPDX-License-Identifier: GPL-3.0-or-later
"""Write scheduler for DBus gateway commands."""

from __future__ import annotations

from collections import deque
from typing import Any

from venus_evcharger.core.shared import config_get_float
from venus_evcharger.dbus_adapter_write_core import DbusWriteSchedulerCoreMixin
from venus_evcharger.dbus_adapter_write_health import DbusWriteSchedulerHealthMixin
from venus_evcharger.dbus_adapter_write_publish import DbusWriteSchedulerPublishMixin


class DbusWriteScheduler(
    DbusWriteSchedulerCoreMixin,
    DbusWriteSchedulerPublishMixin,
    DbusWriteSchedulerHealthMixin,
):
    def __init__(self, adapter: Any) -> None:
        self.adapter = adapter
        self.registered_paths: set[str] = set()
        self.last_values: dict[str, Any] = {}
        defaults = adapter.config["DEFAULT"]
        self.local_publish_burst_limit = max(1, int(config_get_float(defaults, "DbusGatewayLocalPublishBurstLimit", 20.0)))
        self.local_publish_tick_budget_seconds = max(
            0.001,
            config_get_float(defaults, "DbusGatewayLocalPublishTickBudgetMs", 75.0) / 1000.0,
        )
        self.dynamic_local_publish_burst_limit = self.local_publish_burst_limit
        self.startup_registration_batch_limit = max(
            1,
            int(config_get_float(defaults, "DbusGatewayStartupRegistrationBatchLimit", 100.0)),
        )
        self.startup_registration_tick_budget_seconds = max(
            0.001,
            config_get_float(defaults, "DbusGatewayStartupRegistrationTickBudgetMs", 150.0) / 1000.0,
        )
        self.queue_class_budgets = self._queue_class_budgets(defaults)
        self.base_queue_class_budgets = dict(self.queue_class_budgets)
        self._processed_events: deque[float] = deque()
        self._budget_events: deque[tuple[float, str]] = deque()
        self._lifecycle_events: deque[tuple[float, str, str]] = deque()
        self._lifecycle_counts: dict[str, int] = {}
        self.last_processed_at = 0.0
