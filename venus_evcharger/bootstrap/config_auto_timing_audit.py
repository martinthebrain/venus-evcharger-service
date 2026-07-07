# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-mode audit-log config loading."""

from __future__ import annotations

import configparser
from typing import Any

from venus_evcharger.bootstrap.config_shared import _config_value

AUTO_AUDIT_LOG_KEY = "AutoAuditLog"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
AUTO_AUDIT_LOG_PATH_KEY = "AutoAuditLogPath"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
AUTO_AUDIT_LOG_MAX_AGE_KEY = "AutoAuditLogMaxAgeHours"  # pragma: no mutate - ConfigParser option keys are case-insensitive.
AUTO_AUDIT_LOG_REPEAT_KEY = "AutoAuditLogRepeatSeconds"  # pragma: no mutate - ConfigParser option keys are case-insensitive.


def load_auto_audit_config(svc: Any, defaults: configparser.SectionProxy) -> None:
    """Load Auto decision audit-log settings."""
    svc.auto_audit_log = defaults.get(AUTO_AUDIT_LOG_KEY, "1").strip().lower() in ("1", "true", "yes", "on")
    svc.auto_audit_log_path = defaults.get(
        AUTO_AUDIT_LOG_PATH_KEY,
        "/var/volatile/log/dbus-venus-evcharger/auto-reasons.log",
    ).strip()
    svc.auto_audit_log_max_age_hours = float(_config_value(defaults, AUTO_AUDIT_LOG_MAX_AGE_KEY, 168))
    svc.auto_audit_log_repeat_seconds = float(_config_value(defaults, AUTO_AUDIT_LOG_REPEAT_KEY, 30))
