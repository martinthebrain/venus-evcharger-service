# SPDX-License-Identifier: GPL-3.0-or-later
from types import SimpleNamespace
import unittest

from venus_evcharger.auto.policy import AutoPolicy
from venus_evcharger.auto.policy_settings import auto_policy_control_values
from venus_evcharger.bootstrap.path_groups import (
    connected_value,
    control_paths,
    management_paths,
    measurement_paths,
)


class BootstrapPathGroupsContracts(unittest.TestCase):
    def test_connected_value_prefers_topology_configured_over_legacy_host(self) -> None:
        self.assertEqual(connected_value(SimpleNamespace()), 1)
        self.assertEqual(connected_value(SimpleNamespace(host_configured=False)), 0)
        self.assertEqual(connected_value(SimpleNamespace(host_configured=True)), 1)
        self.assertEqual(connected_value(SimpleNamespace(topology_configured=True, host_configured=False)), 1)
        self.assertEqual(connected_value(SimpleNamespace(topology_configured=False, host_configured=True)), 0)

    def test_management_paths_are_exact_identity_contract(self) -> None:
        service = SimpleNamespace(
            connection_name="Shelly RPC",
            deviceinstance=60,
            product_name="Venus EV Charger Service",
            custom_name="Wallbox",
            firmware_version="1.2.3",
            hardware_version="Shelly 1PM Gen4",
            serial="ABC123",
            position=1,
            topology_configured=True,
            host_configured=False,
        )

        self.assertEqual(
            management_paths(service, "/data/venus_evcharger_service.py", "3.11.8"),
            {
                "/Mgmt/ProcessName": "/data/venus_evcharger_service.py",
                "/Mgmt/ProcessVersion": "Unknown version, and running on Python 3.11.8",
                "/Mgmt/Connection": "Shelly RPC",
                "/DeviceInstance": 60,
                "/ProductId": 0xFFFF,
                "/ProductName": "Venus EV Charger Service",
                "/CustomName": "Wallbox",
                "/FirmwareVersion": "1.2.3",
                "/HardwareVersion": "Shelly 1PM Gen4",
                "/Serial": "ABC123",
                "/Connected": 1,
                "/Position": 1,
                "/UpdateIndex": 0,
            },
        )
        disconnected = SimpleNamespace(
            connection_name="Shelly RPC",
            deviceinstance=60,
            product_name="Venus EV Charger Service",
            custom_name="Wallbox",
            firmware_version="1.2.3",
            hardware_version="Shelly 1PM Gen4",
            serial="ABC123",
            position=1,
            host_configured=False,
        )
        self.assertEqual(management_paths(disconnected, "/service.py", "3.11")["/Connected"], 0)

    def test_measurement_paths_use_expected_initials_and_formatters(self) -> None:
        formatters = {"w": "watts", "v": "volts", "a": "amps", "kwh": "kwh"}

        self.assertEqual(
            measurement_paths(formatters),
            {
                "/Ac/Power": (0.0, "watts"),
                "/Ac/Voltage": (0.0, "volts"),
                "/Ac/L1/Power": (0.0, "watts"),
                "/Ac/L2/Power": (0.0, "watts"),
                "/Ac/L3/Power": (0.0, "watts"),
                "/Ac/L1/Voltage": (0.0, "volts"),
                "/Ac/L2/Voltage": (0.0, "volts"),
                "/Ac/L3/Voltage": (0.0, "volts"),
                "/Ac/L1/Current": (0.0, "amps"),
                "/Ac/L2/Current": (0.0, "amps"),
                "/Ac/L3/Current": (0.0, "amps"),
                "/Ac/Energy/Forward": (0.0, "kwh"),
                "/Ac/L1/Energy/Forward": (0.0, "kwh"),
                "/Ac/L2/Energy/Forward": (0.0, "kwh"),
                "/Ac/L3/Energy/Forward": (0.0, "kwh"),
                "/Session/Energy": (0.0, None),
                "/Session/Time": (0, None),
                "/Ac/Current": (0.0, "amps"),
                "/Current": (0.0, "amps"),
            },
        )

    def test_control_paths_reflect_configured_service_values(self) -> None:
        service = _configured_control_service()
        formatters = {"a": "amps", "status": "status"}

        self.assertEqual(
            control_paths(service, formatters),
            {
                "/MinCurrent": (6.0, "amps"),
                "/MaxCurrent": (16.0, "amps"),
                "/SetCurrent": (13.0, "amps"),
                "/PhaseSelection": ("P1_P2", None),
                "/PhaseSelectionActive": ("P3", None),
                "/SupportedPhaseSelections": ("P1,P1_P2,P3", None),
                "/AutoStart": (1, None),
                "/Auto/StartSurplusWatts": (1850.0, None),
                "/Auto/StopSurplusWatts": (1350.0, None),
                "/Auto/MinSoc": (40.0, None),
                "/Auto/ResumeSoc": (50.0, None),
                "/Auto/StartDelaySeconds": (10.0, None),
                "/Auto/StopDelaySeconds": (30.0, None),
                "/Auto/ScheduledEnabledDays": ("Sat,Sun", None),
                "/Auto/ScheduledFallbackDelaySeconds": (3600.0, None),
                "/Auto/ScheduledLatestEndTime": ("07:45", None),
                "/Auto/ScheduledNightCurrent": (13.0, None),
                "/Auto/DbusBackoffBaseSeconds": (5.0, None),
                "/Auto/DbusBackoffMaxSeconds": (60.0, None),
                "/Auto/GridRecoveryStartSeconds": (14.0, None),
                "/Auto/StopSurplusDelaySeconds": (45.0, None),
                "/Auto/StopSurplusVolatilityLowWatts": (80.0, None),
                "/Auto/StopSurplusVolatilityHighWatts": (240.0, None),
                "/Auto/ReferenceChargePowerWatts": (2100.0, None),
                "/Auto/LearnChargePowerEnabled": (0, None),
                "/Auto/LearnChargePowerMinWatts": (1400.0, None),
                "/Auto/LearnChargePowerAlpha": (0.25, None),
                "/Auto/LearnChargePowerStartDelaySeconds": (12.0, None),
                "/Auto/LearnChargePowerWindowSeconds": (180.0, None),
                "/Auto/LearnChargePowerMaxAgeSeconds": (21600.0, None),
                "/Auto/PhaseSwitching": (0, None),
                "/Auto/PhasePreferLowestWhenIdle": (0, None),
                "/Auto/PhaseUpshiftDelaySeconds": (120.0, None),
                "/Auto/PhaseDownshiftDelaySeconds": (30.0, None),
                "/Auto/PhaseUpshiftHeadroomWatts": (250.0, None),
                "/Auto/PhaseDownshiftMarginWatts": (150.0, None),
                "/Auto/PhaseMismatchRetrySeconds": (300.0, None),
                "/Auto/PhaseMismatchLockoutCount": (3, None),
                "/Auto/PhaseMismatchLockoutSeconds": (1800.0, None),
                "/ChargingTime": (0, None),
                "/Mode": (2, None),
                "/StartStop": (1, None),
                "/Enable": (1, None),
                "/Status": (0, "status"),
            },
        )

    def test_control_paths_fill_non_policy_optionals_and_publish_canonical_policy_defaults(self) -> None:
        policy = AutoPolicy()
        service = SimpleNamespace(
            auto_policy=policy,
            min_current=6.0,
            max_current=16.0,
            virtual_set_current=6.0,
            virtual_autostart=0,
            virtual_mode=0,
            virtual_startstop=0,
            virtual_enable=1,
        )
        defaults = control_paths(service, {"a": "amps", "status": "status"})

        self.assertEqual(defaults["/PhaseSelection"], ("P1", None))
        self.assertEqual(defaults["/PhaseSelectionActive"], ("P1", None))
        self.assertEqual(defaults["/SupportedPhaseSelections"], ("P1", None))
        self.assertEqual(defaults["/Auto/ScheduledEnabledDays"], ("Mon,Tue,Wed,Thu,Fri", None))
        self.assertEqual(defaults["/Auto/ScheduledLatestEndTime"], ("06:30", None))
        for path, value in auto_policy_control_values(policy).items():
            with self.subTest(path=path):
                self.assertEqual(defaults[path], (value, None))
        for path in (
            "/Auto/StartDelaySeconds",
            "/Auto/StopDelaySeconds",
            "/Auto/ScheduledFallbackDelaySeconds",
            "/Auto/ScheduledNightCurrent",
            "/Auto/DbusBackoffBaseSeconds",
            "/Auto/DbusBackoffMaxSeconds",
        ):
            with self.subTest(path=path):
                self.assertEqual(defaults[path], (0.0, None))


def _configured_control_service() -> SimpleNamespace:
    policy = AutoPolicy()
    policy.normal_profile.start_surplus_watts = 1850.0
    policy.normal_profile.stop_surplus_watts = 1350.0
    policy.min_soc = 40.0
    policy.resume_soc = 50.0
    policy.grid_recovery_start_seconds = 14.0
    policy.stop_surplus_delay_seconds = 45.0
    policy.ewma.volatility_low_watts = 80.0
    policy.ewma.volatility_high_watts = 240.0
    policy.learn_charge_power.enabled = False
    policy.learn_charge_power.reference_power_watts = 2100.0
    policy.learn_charge_power.min_watts = 1400.0
    policy.learn_charge_power.alpha = 0.25
    policy.learn_charge_power.start_delay_seconds = 12.0
    policy.learn_charge_power.window_seconds = 180.0
    policy.learn_charge_power.max_age_seconds = 21600.0
    policy.phase.enabled = False
    policy.phase.prefer_lowest_phase_when_idle = False
    policy.phase.upshift_delay_seconds = 120.0
    policy.phase.downshift_delay_seconds = 30.0
    policy.phase.upshift_headroom_watts = 250.0
    policy.phase.downshift_margin_watts = 150.0
    policy.phase.mismatch_retry_seconds = 300.0
    policy.phase.mismatch_lockout_count = 3
    policy.phase.mismatch_lockout_seconds = 1800.0
    return SimpleNamespace(
        auto_policy=policy,
        min_current=6.0,
        max_current=16.0,
        virtual_set_current=13.0,
        requested_phase_selection="P1_P2",
        active_phase_selection="P3",
        supported_phase_selections=("P1", "P1_P2", "P3"),
        virtual_autostart=1,
        auto_start_delay_seconds=10.0,
        auto_stop_delay_seconds=30.0,
        auto_scheduled_enabled_days="Sat,Sun",
        auto_scheduled_night_start_delay_seconds=3600.0,
        auto_scheduled_latest_end_time="07:45",
        auto_scheduled_night_current_amps=13.0,
        auto_dbus_backoff_base_seconds=5.0,
        auto_dbus_backoff_max_seconds=60.0,
        virtual_mode=2,
        virtual_startstop=1,
        virtual_enable=1,
    )


if __name__ == "__main__":
    unittest.main()
