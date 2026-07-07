# SPDX-License-Identifier: GPL-3.0-or-later
"""Helper resilience, startup, and request timeout config loading."""

from __future__ import annotations

import configparser
from typing import Any

from venus_evcharger.bootstrap.config_shared import _config_value


AUTO_INPUT_HELPER_RESTART_KEY = "AutoInputHelperRestartSeconds"
AUTO_INPUT_HELPER_STALE_KEY = "AutoInputHelperStaleSeconds"
AUTO_SHELLY_SOFT_FAIL_KEY = "AutoShellySoftFailSeconds"
AUTO_CONTACTOR_FAULT_LATCH_COUNT_KEY = "AutoContactorFaultLatchCount"
AUTO_CONTACTOR_FAULT_LATCH_SECONDS_KEY = "AutoContactorFaultLatchSeconds"
AUTO_WATCHDOG_STALE_KEY = "AutoWatchdogStaleSeconds"
AUTO_WATCHDOG_RECOVERY_KEY = "AutoWatchdogRecoverySeconds"
AUTO_WATCHDOG_RESTART_ATTEMPTS_KEY = "AutoWatchdogRestartAttempts"
AUTO_STARTUP_WARMUP_KEY = "AutoStartupWarmupSeconds"
AUTO_MANUAL_OVERRIDE_KEY = "AutoManualOverrideSeconds"
STARTUP_DEVICE_INFO_RETRIES_KEY = "StartupDeviceInfoRetries"
STARTUP_DEVICE_INFO_RETRY_SECONDS_KEY = "StartupDeviceInfoRetrySeconds"
SHELLY_REQUEST_TIMEOUT_KEY = "ShellyRequestTimeoutSeconds"
DBUS_METHOD_TIMEOUT_KEY = "DbusMethodTimeoutSeconds"


def load_helper_resilience_config(svc: Any, defaults: configparser.SectionProxy) -> None:
    svc.auto_input_helper_restart_seconds = float(_config_value(defaults, AUTO_INPUT_HELPER_RESTART_KEY, 5))
    svc.auto_input_helper_stale_seconds = float(_config_value(defaults, AUTO_INPUT_HELPER_STALE_KEY, 15))
    svc.auto_shelly_soft_fail_seconds = float(_config_value(defaults, AUTO_SHELLY_SOFT_FAIL_KEY, 10))
    svc.auto_contactor_fault_latch_count = int(_config_value(defaults, AUTO_CONTACTOR_FAULT_LATCH_COUNT_KEY, 3))
    svc.auto_contactor_fault_latch_seconds = float(_config_value(defaults, AUTO_CONTACTOR_FAULT_LATCH_SECONDS_KEY, 60))
    svc.auto_watchdog_stale_seconds = float(_config_value(defaults, AUTO_WATCHDOG_STALE_KEY, 180))
    svc.auto_watchdog_recovery_seconds = float(_config_value(defaults, AUTO_WATCHDOG_RECOVERY_KEY, 60))
    svc.auto_watchdog_restart_attempts = int(_config_value(defaults, AUTO_WATCHDOG_RESTART_ATTEMPTS_KEY, 5))
    svc.auto_startup_warmup_seconds = float(_config_value(defaults, AUTO_STARTUP_WARMUP_KEY, 15))
    svc.auto_manual_override_seconds = float(_config_value(defaults, AUTO_MANUAL_OVERRIDE_KEY, 300))
    svc.startup_device_info_retries = int(_config_value(defaults, STARTUP_DEVICE_INFO_RETRIES_KEY, 3))
    svc.startup_device_info_retry_seconds = float(_config_value(defaults, STARTUP_DEVICE_INFO_RETRY_SECONDS_KEY, 2))
    svc.shelly_request_timeout_seconds = float(_config_value(defaults, SHELLY_REQUEST_TIMEOUT_KEY, 2.0))
    svc.dbus_method_timeout_seconds = float(_config_value(defaults, DBUS_METHOD_TIMEOUT_KEY, 1.0))
