# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus path groups registered by the EV charger bootstrap layer."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from venus_evcharger.bootstrap.path_defaults import PathMap


def connected_value(svc: Any) -> int:
    """Return one static initial Connected flag for DBus registration."""
    configured = getattr(svc, "topology_configured", getattr(svc, "host_configured", True))
    return 1 if bool(configured) else 0


def management_paths(svc: Any, script_path: str, python_version: str) -> dict[str, Any]:
    """Return immutable management and identity DBus path initial values."""
    return {
        "/Mgmt/ProcessName": script_path,
        "/Mgmt/ProcessVersion": "Unknown version, and running on Python " + python_version,
        "/Mgmt/Connection": svc.connection_name,
        "/DeviceInstance": svc.deviceinstance,
        "/ProductId": 0xFFFF,
        "/ProductName": svc.product_name,
        "/CustomName": svc.custom_name,
        "/FirmwareVersion": svc.firmware_version,
        "/HardwareVersion": svc.hardware_version,
        "/Serial": svc.serial,
        "/Connected": connected_value(svc),
        "/Position": svc.position,
        "/UpdateIndex": 0,
    }


def measurement_paths(formatters: Mapping[str, Any]) -> PathMap:
    """Return measurement and energy paths shown on the EV charger tile."""
    return {
        "/Ac/Power": (0.0, formatters["w"]),
        "/Ac/Voltage": (0.0, formatters["v"]),
        "/Ac/L1/Power": (0.0, formatters["w"]),
        "/Ac/L2/Power": (0.0, formatters["w"]),
        "/Ac/L3/Power": (0.0, formatters["w"]),
        "/Ac/L1/Voltage": (0.0, formatters["v"]),
        "/Ac/L2/Voltage": (0.0, formatters["v"]),
        "/Ac/L3/Voltage": (0.0, formatters["v"]),
        "/Ac/L1/Current": (0.0, formatters["a"]),
        "/Ac/L2/Current": (0.0, formatters["a"]),
        "/Ac/L3/Current": (0.0, formatters["a"]),
        "/Ac/Energy/Forward": (0.0, formatters["kwh"]),
        "/Ac/L1/Energy/Forward": (0.0, formatters["kwh"]),
        "/Ac/L2/Energy/Forward": (0.0, formatters["kwh"]),
        "/Ac/L3/Energy/Forward": (0.0, formatters["kwh"]),
        "/Session/Energy": (0.0, None),
        "/Session/Time": (0, None),
        "/Ac/Current": (0.0, formatters["a"]),
        "/Current": (0.0, formatters["a"]),
    }


def control_paths(svc: Any, formatters: Mapping[str, Any]) -> PathMap:
    """Return writable and status-like EV charger control paths."""
    return {
        "/MinCurrent": (svc.min_current, formatters["a"]),
        "/MaxCurrent": (svc.max_current, formatters["a"]),
        "/SetCurrent": (svc.virtual_set_current, formatters["a"]),
        "/PhaseSelection": (getattr(svc, "requested_phase_selection", "P1"), None),
        "/PhaseSelectionActive": (getattr(svc, "active_phase_selection", "P1"), None),
        "/SupportedPhaseSelections": (",".join(getattr(svc, "supported_phase_selections", ("P1",))), None),
        "/AutoStart": (svc.virtual_autostart, None),
        "/Auto/StartSurplusWatts": (getattr(svc, "auto_start_surplus_watts", 0.0), None),
        "/Auto/StopSurplusWatts": (getattr(svc, "auto_stop_surplus_watts", 0.0), None),
        "/Auto/MinSoc": (getattr(svc, "auto_min_soc", 0.0), None),
        "/Auto/ResumeSoc": (getattr(svc, "auto_resume_soc", 0.0), None),
        "/Auto/StartDelaySeconds": (getattr(svc, "auto_start_delay_seconds", 0.0), None),
        "/Auto/StopDelaySeconds": (getattr(svc, "auto_stop_delay_seconds", 0.0), None),
        "/Auto/ScheduledEnabledDays": (str(getattr(svc, "auto_scheduled_enabled_days", "Mon,Tue,Wed,Thu,Fri")), None),
        "/Auto/ScheduledFallbackDelaySeconds": (getattr(svc, "auto_scheduled_night_start_delay_seconds", 0.0), None),
        "/Auto/ScheduledLatestEndTime": (str(getattr(svc, "auto_scheduled_latest_end_time", "06:30")), None),
        "/Auto/ScheduledNightCurrent": (getattr(svc, "auto_scheduled_night_current_amps", 0.0), None),
        "/Auto/DbusBackoffBaseSeconds": (getattr(svc, "auto_dbus_backoff_base_seconds", 0.0), None),
        "/Auto/DbusBackoffMaxSeconds": (getattr(svc, "auto_dbus_backoff_max_seconds", 0.0), None),
        "/Auto/GridRecoveryStartSeconds": (getattr(svc, "auto_grid_recovery_start_seconds", 0.0), None),
        "/Auto/StopSurplusDelaySeconds": (getattr(svc, "auto_stop_surplus_delay_seconds", 0.0), None),
        "/Auto/StopSurplusVolatilityLowWatts": (getattr(svc, "auto_stop_surplus_volatility_low_watts", 0.0), None),
        "/Auto/StopSurplusVolatilityHighWatts": (getattr(svc, "auto_stop_surplus_volatility_high_watts", 0.0), None),
        "/Auto/ReferenceChargePowerWatts": (getattr(svc, "auto_reference_charge_power_watts", 0.0), None),
        "/Auto/LearnChargePowerEnabled": (int(bool(getattr(svc, "auto_learn_charge_power_enabled", True))), None),
        "/Auto/LearnChargePowerMinWatts": (getattr(svc, "auto_learn_charge_power_min_watts", 0.0), None),
        "/Auto/LearnChargePowerAlpha": (getattr(svc, "auto_learn_charge_power_alpha", 0.0), None),
        "/Auto/LearnChargePowerStartDelaySeconds": (getattr(svc, "auto_learn_charge_power_start_delay_seconds", 0.0), None),
        "/Auto/LearnChargePowerWindowSeconds": (getattr(svc, "auto_learn_charge_power_window_seconds", 0.0), None),
        "/Auto/LearnChargePowerMaxAgeSeconds": (getattr(svc, "auto_learn_charge_power_max_age_seconds", 0.0), None),
        "/Auto/PhaseSwitching": (int(bool(getattr(svc, "auto_phase_switching_enabled", True))), None),
        "/Auto/PhasePreferLowestWhenIdle": (int(bool(getattr(svc, "auto_phase_prefer_lowest_when_idle", True))), None),
        "/Auto/PhaseUpshiftDelaySeconds": (getattr(svc, "auto_phase_upshift_delay_seconds", 0.0), None),
        "/Auto/PhaseDownshiftDelaySeconds": (getattr(svc, "auto_phase_downshift_delay_seconds", 0.0), None),
        "/Auto/PhaseUpshiftHeadroomWatts": (getattr(svc, "auto_phase_upshift_headroom_watts", 0.0), None),
        "/Auto/PhaseDownshiftMarginWatts": (getattr(svc, "auto_phase_downshift_margin_watts", 0.0), None),
        "/Auto/PhaseMismatchRetrySeconds": (getattr(svc, "auto_phase_mismatch_retry_seconds", 0.0), None),
        "/Auto/PhaseMismatchLockoutCount": (getattr(svc, "auto_phase_mismatch_lockout_count", 0), None),
        "/Auto/PhaseMismatchLockoutSeconds": (getattr(svc, "auto_phase_mismatch_lockout_seconds", 0.0), None),
        "/ChargingTime": (0, None),
        "/Mode": (svc.virtual_mode, None),
        "/StartStop": (svc.virtual_startstop, None),
        "/Enable": (svc.virtual_enable, None),
        "/Status": (0, formatters["status"]),
    }
