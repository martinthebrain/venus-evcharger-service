# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto helper polling and snapshot path config loading."""

from __future__ import annotations

import configparser
from typing import Any

from venus_evcharger.bootstrap.config_shared import _config_value


AUTO_INPUT_POLL_INTERVAL_KEY = "AutoInputPollIntervalMs"  # pragma: no mutate - ConfigParser keys are case-insensitive.
LEGACY_POLL_INTERVAL_KEY = "PollIntervalMs"  # pragma: no mutate - ConfigParser keys are case-insensitive.
AUTO_PV_POLL_INTERVAL_KEY = "AutoPvPollIntervalMs"  # pragma: no mutate - ConfigParser keys are case-insensitive.
AUTO_GRID_POLL_INTERVAL_KEY = "AutoGridPollIntervalMs"  # pragma: no mutate - ConfigParser keys are case-insensitive.
AUTO_BATTERY_POLL_INTERVAL_KEY = "AutoBatteryPollIntervalMs"  # pragma: no mutate - ConfigParser keys are case-insensitive.
AUTO_INPUT_VALIDATION_POLL_KEY = "AutoInputValidationPollSeconds"  # pragma: no mutate - ConfigParser keys are case-insensitive.
AUTO_INPUT_SNAPSHOT_PATH_KEY = "AutoInputSnapshotPath"  # pragma: no mutate - ConfigParser keys are case-insensitive.


def _milliseconds_to_seconds_with_minimum(value: object, minimum: float) -> float:
    return max(minimum, float(value) / 1000.0)


def load_helper_polling_config(svc: Any, defaults: configparser.SectionProxy) -> None:
    auto_input_poll_interval_ms = float(
        _config_value(
            defaults,
            AUTO_INPUT_POLL_INTERVAL_KEY,
            _config_value(defaults, LEGACY_POLL_INTERVAL_KEY, 1000),
        )
    )
    svc.auto_pv_poll_interval_seconds = _milliseconds_to_seconds_with_minimum(
        _config_value(defaults, AUTO_PV_POLL_INTERVAL_KEY, auto_input_poll_interval_ms),
        0.2,
    )
    svc.auto_grid_poll_interval_seconds = _milliseconds_to_seconds_with_minimum(
        _config_value(defaults, AUTO_GRID_POLL_INTERVAL_KEY, auto_input_poll_interval_ms),
        0.2,
    )
    svc.auto_battery_poll_interval_seconds = _milliseconds_to_seconds_with_minimum(
        _config_value(defaults, AUTO_BATTERY_POLL_INTERVAL_KEY, auto_input_poll_interval_ms),
        0.2,
    )
    svc.auto_input_validation_poll_seconds = max(
        5.0,
        float(_config_value(defaults, AUTO_INPUT_VALIDATION_POLL_KEY, 30)),
    )
    svc.auto_input_snapshot_path = defaults.get(
        AUTO_INPUT_SNAPSHOT_PATH_KEY,
        f"/run/dbus-venus-evcharger-auto-{svc.deviceinstance}.json",
    ).strip()
