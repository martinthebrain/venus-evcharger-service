# SPDX-License-Identifier: GPL-3.0-or-later
from types import SimpleNamespace
import unittest

from venus_evcharger.bootstrap.path_defaults import (
    active_scheduled_diagnostic_defaults,
    age_counter_diagnostic_defaults,
    backend_diagnostic_defaults,
    decision_diagnostic_defaults,
    disabled_scheduled_diagnostic_defaults,
    phase_diagnostic_defaults,
    runtime_timing_diagnostic_defaults,
    scheduled_diagnostic_defaults,
    software_update_diagnostic_defaults,
)
from venus_evcharger.backend.models import BackendRuntimeSummary


class BootstrapPathDefaultsContracts(unittest.TestCase):
    def test_scheduled_defaults_cover_disabled_and_active_snapshots(self) -> None:
        disabled = {
            "/Auto/ScheduledState": ("disabled", None),
            "/Auto/ScheduledStateCode": (0, None),
            "/Auto/ScheduledReason": ("disabled", None),
            "/Auto/ScheduledReasonCode": (0, None),
            "/Auto/ScheduledNightBoostActive": (0, None),
            "/Auto/ScheduledTargetDayEnabled": (0, None),
            "/Auto/ScheduledTargetDay": ("", None),
            "/Auto/ScheduledTargetDate": ("", None),
            "/Auto/ScheduledFallbackStart": ("", None),
            "/Auto/ScheduledBoostUntil": ("", None),
        }
        snapshot = SimpleNamespace(
            state="waiting",
            state_code=4,
            reason="outside-window",
            reason_code=8,
            night_boost_active=True,
            target_day_enabled=False,
            target_day_label="Tue",
            target_date_text="2026-07-07",
            fallback_start_text="03:00",
            boost_until_text="05:30",
        )

        self.assertEqual(disabled_scheduled_diagnostic_defaults(), disabled)
        self.assertEqual(scheduled_diagnostic_defaults(None), disabled)
        self.assertEqual(
            active_scheduled_diagnostic_defaults(snapshot),
            {
                "/Auto/ScheduledState": ("waiting", None),
                "/Auto/ScheduledStateCode": (4, None),
                "/Auto/ScheduledReason": ("outside-window", None),
                "/Auto/ScheduledReasonCode": (8, None),
                "/Auto/ScheduledNightBoostActive": (1, None),
                "/Auto/ScheduledTargetDayEnabled": (0, None),
                "/Auto/ScheduledTargetDay": ("Tue", None),
                "/Auto/ScheduledTargetDate": ("2026-07-07", None),
                "/Auto/ScheduledFallbackStart": ("03:00", None),
                "/Auto/ScheduledBoostUntil": ("05:30", None),
            },
        )
        self.assertEqual(scheduled_diagnostic_defaults(snapshot), active_scheduled_diagnostic_defaults(snapshot))

        bool_snapshot = SimpleNamespace(
            state="boosting",
            state_code=5,
            reason="night-boost",
            reason_code=9,
            night_boost_active=0,
            target_day_enabled="yes",
            target_day_label="Wed",
            target_date_text="2026-07-08",
            fallback_start_text="04:00",
            boost_until_text="06:00",
        )
        bool_defaults = active_scheduled_diagnostic_defaults(bool_snapshot)
        self.assertEqual(bool_defaults["/Auto/ScheduledNightBoostActive"], (0, None))
        self.assertEqual(bool_defaults["/Auto/ScheduledTargetDayEnabled"], (1, None))

    def test_software_update_defaults_use_service_state_and_neutral_ages(self) -> None:
        service = SimpleNamespace(
            _software_update_available=True,
            _software_update_state="available",
            _software_update_detail="new version",
            _software_update_current_version="1.0",
            _software_update_available_version="1.1",
            _software_update_no_update_active=True,
        )

        self.assertEqual(
            software_update_diagnostic_defaults(service),
            {
                "/Auto/SoftwareUpdateAvailable": (1, None),
                "/Auto/SoftwareUpdateState": ("available", None),
                "/Auto/SoftwareUpdateStateCode": (0, None),
                "/Auto/SoftwareUpdateDetail": ("new version", None),
                "/Auto/SoftwareUpdateCurrentVersion": ("1.0", None),
                "/Auto/SoftwareUpdateAvailableVersion": ("1.1", None),
                "/Auto/SoftwareUpdateNoUpdateActive": (1, None),
                "/Auto/SoftwareUpdateRun": (0, None),
                "/Auto/SoftwareUpdateLastCheckAge": (-1, None),
                "/Auto/SoftwareUpdateLastRunAge": (-1, None),
            },
        )
        self.assertEqual(
            software_update_diagnostic_defaults(SimpleNamespace()),
            {
                "/Auto/SoftwareUpdateAvailable": (0, None),
                "/Auto/SoftwareUpdateState": ("idle", None),
                "/Auto/SoftwareUpdateStateCode": (0, None),
                "/Auto/SoftwareUpdateDetail": ("", None),
                "/Auto/SoftwareUpdateCurrentVersion": ("", None),
                "/Auto/SoftwareUpdateAvailableVersion": ("", None),
                "/Auto/SoftwareUpdateNoUpdateActive": (0, None),
                "/Auto/SoftwareUpdateRun": (0, None),
                "/Auto/SoftwareUpdateLastCheckAge": (-1, None),
                "/Auto/SoftwareUpdateLastRunAge": (-1, None),
            },
        )
        converted = software_update_diagnostic_defaults(
            SimpleNamespace(
                _software_update_available=2,
                _software_update_state=12,
                _software_update_detail=34.5,
                _software_update_current_version=6,
                _software_update_available_version=None,
                _software_update_no_update_active="yes",
            )
        )
        self.assertEqual(converted["/Auto/SoftwareUpdateAvailable"], (1, None))
        self.assertEqual(converted["/Auto/SoftwareUpdateState"], ("12", None))
        self.assertEqual(converted["/Auto/SoftwareUpdateDetail"], ("34.5", None))
        self.assertEqual(converted["/Auto/SoftwareUpdateCurrentVersion"], ("6", None))
        self.assertEqual(converted["/Auto/SoftwareUpdateAvailableVersion"], ("None", None))
        self.assertEqual(converted["/Auto/SoftwareUpdateNoUpdateActive"], (1, None))
        false_flags = software_update_diagnostic_defaults(
            SimpleNamespace(_software_update_available=0, _software_update_no_update_active=0)
        )
        self.assertEqual(false_flags["/Auto/SoftwareUpdateAvailable"], (0, None))
        self.assertEqual(false_flags["/Auto/SoftwareUpdateNoUpdateActive"], (0, None))

    def test_backend_and_decision_defaults_reflect_canonical_runtime_state(self) -> None:
        service = SimpleNamespace(
            _backend_runtime_summary=BackendRuntimeSummary(
                backend_mode="split",
                meter_type="template_meter",
                meter_config_path=None,
                switch_type="template_switch",
                switch_config_path=None,
                charger_type="goe_charger",
                charger_config_path=None,
                topology_configured=True,
                primary_rpc_configured=False,
            ),
            _runtime_overrides_active=True,
            runtime_overrides_path="/run/overrides.ini",
            _last_health_reason="grid-missing",
            _last_auto_state="waiting",
            _last_auto_state_code=7,
        )

        self.assertEqual(
            backend_diagnostic_defaults(service),
            {
                "/Auto/BackendMode": ("split", None),
                "/Auto/MeterBackend": ("template_meter", None),
                "/Auto/SwitchBackend": ("template_switch", None),
                "/Auto/ChargerBackend": ("goe_charger", None),
                "/Auto/ChargerStatus": ("", None),
                "/Auto/ChargerFault": ("", None),
                "/Auto/ChargerFaultActive": (0, None),
                "/Auto/ChargerEstimateActive": (0, None),
                "/Auto/ChargerEstimateSource": ("", None),
                "/Auto/RuntimeOverridesActive": (1, None),
                "/Auto/RuntimeOverridesPath": ("/run/overrides.ini", None),
                "/Auto/ChargerTransportActive": (0, None),
                "/Auto/ChargerTransportReason": ("", None),
                "/Auto/ChargerTransportSource": ("", None),
                "/Auto/ChargerTransportDetail": ("", None),
                "/Auto/ChargerRetryActive": (0, None),
                "/Auto/ChargerRetryReason": ("", None),
                "/Auto/ChargerRetrySource": ("", None),
                "/Auto/ChargerCurrentTarget": (-1.0, None),
                "/Auto/LastChargerReadAge": (-1, None),
                "/Auto/LastChargerEstimateAge": (-1, None),
                "/Auto/LastChargerTransportAge": (-1, None),
                "/Auto/ChargerRetryRemaining": (-1, None),
            },
        )
        self.assertEqual(
            decision_diagnostic_defaults(service),
            {
                "/Auto/DecisionReason": ("grid-missing", None),
                "/Auto/DecisionState": ("waiting", None),
                "/Auto/DecisionStateCode": (7, None),
                "/Auto/DecisionRelayIntent": (-1, None),
                "/Auto/DecisionSurplusWatts": (-1.0, None),
                "/Auto/DecisionGridWatts": (-1.0, None),
                "/Auto/DecisionSocPercent": (-1.0, None),
                "/Auto/DecisionStartThresholdWatts": (-1.0, None),
                "/Auto/DecisionStopThresholdWatts": (-1.0, None),
                "/Auto/DecisionProfile": ("", None),
                "/Auto/DecisionThresholdMode": ("", None),
            },
        )
        self.assertEqual(
            backend_diagnostic_defaults(SimpleNamespace()),
            {
                "/Auto/BackendMode": ("combined", None),
                "/Auto/MeterBackend": ("shelly_meter", None),
                "/Auto/SwitchBackend": ("shelly_contactor_switch", None),
                "/Auto/ChargerBackend": ("", None),
                "/Auto/ChargerStatus": ("", None),
                "/Auto/ChargerFault": ("", None),
                "/Auto/ChargerFaultActive": (0, None),
                "/Auto/ChargerEstimateActive": (0, None),
                "/Auto/ChargerEstimateSource": ("", None),
                "/Auto/RuntimeOverridesActive": (0, None),
                "/Auto/RuntimeOverridesPath": ("", None),
                "/Auto/ChargerTransportActive": (0, None),
                "/Auto/ChargerTransportReason": ("", None),
                "/Auto/ChargerTransportSource": ("", None),
                "/Auto/ChargerTransportDetail": ("", None),
                "/Auto/ChargerRetryActive": (0, None),
                "/Auto/ChargerRetryReason": ("", None),
                "/Auto/ChargerRetrySource": ("", None),
                "/Auto/ChargerCurrentTarget": (-1.0, None),
                "/Auto/LastChargerReadAge": (-1, None),
                "/Auto/LastChargerEstimateAge": (-1, None),
                "/Auto/LastChargerTransportAge": (-1, None),
                "/Auto/ChargerRetryRemaining": (-1, None),
            },
        )
        self.assertEqual(
            backend_diagnostic_defaults(SimpleNamespace(runtime_overrides_path=123))["/Auto/RuntimeOverridesPath"],
            ("123", None),
        )
        runtime_service = SimpleNamespace(
            _backend_bundle=SimpleNamespace(
                runtime=BackendRuntimeSummary(
                    backend_mode="split",
                    meter_type="runtime_meter",
                    meter_config_path=None,
                    switch_type="runtime_switch",
                    switch_config_path=None,
                    charger_type="runtime_charger",
                    charger_config_path=None,
                    topology_configured=True,
                    primary_rpc_configured=False,
                )
            ),
            _runtime_overrides_active=False,
            runtime_overrides_path=456,
        )
        runtime_defaults = backend_diagnostic_defaults(runtime_service)
        self.assertEqual(runtime_defaults["/Auto/BackendMode"], ("split", None))
        self.assertEqual(runtime_defaults["/Auto/MeterBackend"], ("runtime_meter", None))
        self.assertEqual(runtime_defaults["/Auto/SwitchBackend"], ("runtime_switch", None))
        self.assertEqual(runtime_defaults["/Auto/ChargerBackend"], ("runtime_charger", None))
        self.assertEqual(runtime_defaults["/Auto/RuntimeOverridesActive"], (0, None))
        self.assertEqual(runtime_defaults["/Auto/RuntimeOverridesPath"], ("456", None))
        self.assertEqual(
            decision_diagnostic_defaults(
                SimpleNamespace(_last_health_reason=99, _last_auto_state=5, _last_auto_state_code="12")
            ),
            {
                "/Auto/DecisionReason": ("99", None),
                "/Auto/DecisionState": ("5", None),
                "/Auto/DecisionStateCode": (12, None),
                "/Auto/DecisionRelayIntent": (-1, None),
                "/Auto/DecisionSurplusWatts": (-1.0, None),
                "/Auto/DecisionGridWatts": (-1.0, None),
                "/Auto/DecisionSocPercent": (-1.0, None),
                "/Auto/DecisionStartThresholdWatts": (-1.0, None),
                "/Auto/DecisionStopThresholdWatts": (-1.0, None),
                "/Auto/DecisionProfile": ("", None),
                "/Auto/DecisionThresholdMode": ("", None),
            },
        )
        self.assertEqual(
            decision_diagnostic_defaults(SimpleNamespace()),
            {
                "/Auto/DecisionReason": ("init", None),
                "/Auto/DecisionState": ("idle", None),
                "/Auto/DecisionStateCode": (0, None),
                "/Auto/DecisionRelayIntent": (-1, None),
                "/Auto/DecisionSurplusWatts": (-1.0, None),
                "/Auto/DecisionGridWatts": (-1.0, None),
                "/Auto/DecisionSocPercent": (-1.0, None),
                "/Auto/DecisionStartThresholdWatts": (-1.0, None),
                "/Auto/DecisionStopThresholdWatts": (-1.0, None),
                "/Auto/DecisionProfile": ("", None),
                "/Auto/DecisionThresholdMode": ("", None),
            },
        )

    def test_phase_defaults_include_supported_selection_and_lockout_surface(self) -> None:
        service = SimpleNamespace(supported_phase_selections=("P1", "P1_P2", "P3"))

        self.assertEqual(
            phase_diagnostic_defaults(service),
            {
                "/Auto/PhaseCurrent": ("", None),
                "/Auto/PhaseObserved": ("", None),
                "/Auto/PhaseTarget": ("", None),
                "/Auto/PhaseReason": ("", None),
                "/Auto/PhaseMismatchActive": (0, None),
                "/Auto/PhaseLockoutActive": (0, None),
                "/Auto/PhaseLockoutTarget": ("", None),
                "/Auto/PhaseLockoutReason": ("", None),
                "/Auto/PhaseSupportedConfigured": ("P1,P1_P2,P3", None),
                "/Auto/PhaseSupportedEffective": ("P1,P1_P2,P3", None),
                "/Auto/PhaseDegradedActive": (0, None),
                "/Auto/SwitchFeedbackClosed": (-1, None),
                "/Auto/SwitchInterlockOk": (-1, None),
                "/Auto/SwitchFeedbackMismatch": (0, None),
                "/Auto/ContactorSuspectedOpen": (0, None),
                "/Auto/ContactorSuspectedWelded": (0, None),
                "/Auto/ContactorFaultCount": (0, None),
                "/Auto/ContactorLockoutActive": (0, None),
                "/Auto/ContactorLockoutReason": ("", None),
                "/Auto/ContactorLockoutSource": ("", None),
                "/Auto/ContactorLockoutReset": (0, None),
                "/Auto/PhaseLockoutReset": (0, None),
                "/Auto/PhaseThresholdWatts": (-1.0, None),
                "/Auto/PhaseCandidate": ("", None),
                "/Auto/PhaseCandidateAge": (-1, None),
                "/Auto/PhaseLockoutAge": (-1, None),
                "/Auto/ContactorLockoutAge": (-1, None),
                "/Auto/LastSwitchFeedbackAge": (-1, None),
            },
        )
        self.assertEqual(phase_diagnostic_defaults(SimpleNamespace())["/Auto/PhaseSupportedConfigured"], ("P1", None))
        self.assertEqual(phase_diagnostic_defaults(SimpleNamespace())["/Auto/PhaseSupportedEffective"], ("P1", None))

    def test_age_and_runtime_defaults_are_complete_neutral_surfaces(self) -> None:
        self.assertEqual(
            age_counter_diagnostic_defaults(),
            {
                "/Auto/ErrorCount": (0, None),
                "/Auto/DbusReadErrors": (0, None),
                "/Auto/ShellyReadErrors": (0, None),
                "/Auto/ShellyState": ("unknown", None),
                "/Auto/ShellyLastError": ("", None),
                "/Auto/ShellyRetryRemaining": (0, None),
                "/Auto/ShellyConsecutiveErrors": (0, None),
                "/Auto/ShellyLastOkAge": (-1, None),
                "/Auto/PendingRelayAge": (-1, None),
                "/Auto/ChargerWriteErrors": (0, None),
                "/Auto/PvReadErrors": (0, None),
                "/Auto/BatteryReadErrors": (0, None),
                "/Auto/GridReadErrors": (0, None),
                "/Auto/InputCacheHits": (0, None),
                "/Auto/LastShellyReadAge": (-1, None),
                "/Auto/LastPvReadAge": (-1, None),
                "/Auto/LastBatteryReadAge": (-1, None),
                "/Auto/LastGridReadAge": (-1, None),
                "/Auto/LastDbusReadAge": (-1, None),
                "/Auto/ChargerCurrentTargetAge": (-1, None),
                "/Auto/LastSuccessfulUpdateAge": (-1, None),
                "/Auto/Stale": (0, None),
                "/Auto/StaleSeconds": (0, None),
                "/Auto/RecoveryAttempts": (0, None),
                "/Auto/DbusIntrospectionState": ("unknown", None),
                "/Auto/DbusIntrospectionQueueDepth": (0, None),
                "/Auto/DbusIntrospectionServiceCount": (0, None),
                "/Auto/DbusIntrospectionUnusablePathCount": (0, None),
                "/Auto/DbusIntrospectionSnapshotAge": (-1, None),
            },
        )
        self.assertEqual(
            runtime_timing_diagnostic_defaults(),
            {
                "/Auto/UpdateWorkerDurationSeconds": (0.0, None),
                "/Auto/UpdateWorkerPending": (0, None),
                "/Auto/UpdateWorkerSkipped": (0, None),
                "/Auto/PublishFlushDurationSeconds": (0.0, None),
                "/Auto/PublishQueueLagSeconds": (0.0, None),
                "/Auto/PublishQueueDropped": (0, None),
                "/Auto/WriteCommandDurationSeconds": (0.0, None),
                "/Auto/WriteCommandQueueLagSeconds": (0.0, None),
                "/Auto/MainloopHeartbeatAge": (0.0, None),
            },
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
