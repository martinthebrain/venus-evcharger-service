# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.controllers.state import ServiceStateController
from tests.venus_evcharger_state_controller_support import (
    STATE_RUNTIME_WRITE,
    RuntimeOverrideServiceFixture,
    ServiceStateControllerTestBase,
)


class TestServiceStateControllerSecondary(ServiceStateControllerTestBase):
    def test_load_config_applies_runtime_overrides_and_records_override_state(self) -> None:
        service = SimpleNamespace(runtime_state_path="/tmp/does-not-exist.json", deviceinstance=60)
        controller = ServiceStateController(service, self._normalize_mode)

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.ini")
            overrides_path = os.path.join(temp_dir, "runtime-overrides.ini")
            with open(config_path, "w", encoding="utf-8") as handle:
                handle.write(
                    "[DEFAULT]\n"
                    "Host=192.0.2.20\n"
                    f"RuntimeOverridesPath={overrides_path}\n"
                    "Mode=0\n"
                    "AutoStart=1\n"
                    "AutoStartSurplusWatts=1700\n"
                    "PhaseSelection=P1\n"
                    "AutoScheduledEnabledDays=Mon,Tue,Wed,Thu,Fri\n"
                )
            with open(overrides_path, "w", encoding="utf-8") as handle:
                handle.write(
                    "[RuntimeOverrides]\n"
                    "Mode=1\n"
                    "AutoStart=0\n"
                    "AutoStartSurplusWatts=1850\n"
                    "PhaseSelection=P1_P2\n"
                    "AutoPhaseMismatchLockoutCount=4\n"
                    "AutoLearnChargePower=0\n"
                    "AutoReferenceChargePowerWatts=2050\n"
                    "AutoScheduledEnabledDays=Sat,Sun\n"
                    "AutoScheduledLatestEndTime=07:15\n"
                )

            with patch.object(ServiceStateController, "config_path", return_value=config_path):
                config = controller.load_config()

        self.assertEqual(config["DEFAULT"]["Mode"], "1")
        self.assertEqual(config["DEFAULT"]["AutoStart"], "0")
        self.assertEqual(config["DEFAULT"]["AutoStartSurplusWatts"], "1850")
        self.assertEqual(config["DEFAULT"]["PhaseSelection"], "P1_P2")
        self.assertEqual(config["DEFAULT"]["AutoPhaseMismatchLockoutCount"], "4")
        self.assertEqual(config["DEFAULT"]["AutoLearnChargePower"], "0")
        self.assertEqual(config["DEFAULT"]["AutoReferenceChargePowerWatts"], "2050")
        self.assertEqual(config["DEFAULT"]["AutoScheduledEnabledDays"], "Sat,Sun")
        self.assertEqual(config["DEFAULT"]["AutoScheduledLatestEndTime"], "07:15")
        self.assertTrue(service._runtime_overrides_active)
        self.assertEqual(service.runtime_overrides_path, overrides_path)
        self.assertEqual(
            service._runtime_overrides_values,
            {
                "Mode": "1",
                "AutoStart": "0",
                "AutoStartSurplusWatts": "1850",
                "PhaseSelection": "P1_P2",
                "AutoPhaseMismatchLockoutCount": "4",
                "AutoLearnChargePower": "0",
                "AutoReferenceChargePowerWatts": "2050",
                "AutoScheduledEnabledDays": "Sat,Sun",
                "AutoScheduledLatestEndTime": "07:15",
            },
        )

    def test_save_runtime_overrides_writes_small_ini_and_skips_unchanged_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            overrides_path = os.path.join(temp_dir, "runtime-overrides.ini")
            service = RuntimeOverrideServiceFixture(
                overrides_path,
                requested_phase_selection="P1_P2",
            )
            controller = ServiceStateController(service, self._normalize_mode)

            controller.save_runtime_overrides()

            with open(overrides_path, "r", encoding="utf-8") as handle:
                payload = handle.read()

            self.assertIn("[RuntimeOverrides]", payload)
            self.assertIn("Mode = 1", payload)
            self.assertIn("PhaseSelection = P1_P2", payload)
            self.assertIn("AutoStartSurplusWatts = 1850.0", payload)
            self.assertIn("AutoScheduledEnabledDays = Mon,Tue,Wed,Thu,Fri", payload)
            self.assertIn("AutoScheduledNightStartDelaySeconds = 3600.0", payload)
            self.assertIn("AutoScheduledLatestEndTime = 06:30", payload)
            self.assertIn("AutoScheduledNightCurrentAmps = 13.0", payload)
            self.assertIn("AutoDbusBackoffBaseSeconds = 5.0", payload)
            self.assertIn("AutoLearnChargePower = 0", payload)
            self.assertIn("AutoReferenceChargePowerWatts = 2100.0", payload)
            self.assertIn("AutoPhasePreferLowestWhenIdle = 0", payload)
            self.assertIn("AutoPhaseMismatchLockoutCount = 3", payload)
            self.assertTrue(service._runtime_overrides_active)
            self.assertEqual(service._runtime_overrides_values["AutoPhaseSwitching"], "1")

            with patch(STATE_RUNTIME_WRITE) as write_mock:
                controller.save_runtime_overrides()

        write_mock.assert_not_called()

    def test_save_runtime_overrides_debounces_and_flushes_latest_payload(self) -> None:
        current_time = 10.0
        with tempfile.TemporaryDirectory() as temp_dir:
            overrides_path = os.path.join(temp_dir, "runtime-overrides.ini")
            service = RuntimeOverrideServiceFixture(
                overrides_path,
                runtime_overrides_write_min_interval_seconds=1.0,
                time_now=lambda: current_time,
            )
            controller = ServiceStateController(service, self._normalize_mode)

            with patch(STATE_RUNTIME_WRITE) as write_mock:
                controller.save_runtime_overrides()
                self.assertEqual(write_mock.call_count, 1)
                self.assertIn("Mode = 1", write_mock.call_args.args[1])

                current_time = 10.2
                service.virtual_mode = 2
                controller.save_runtime_overrides()
                self.assertEqual(write_mock.call_count, 1)
                self.assertIsNotNone(service._runtime_overrides_pending_serialized)

                current_time = 10.4
                service.virtual_mode = 3
                controller.save_runtime_overrides()
                self.assertEqual(write_mock.call_count, 1)

                controller.flush_runtime_overrides(10.8)
                self.assertEqual(write_mock.call_count, 1)

                controller.flush_runtime_overrides(11.1)
                self.assertEqual(write_mock.call_count, 2)
                self.assertIn("Mode = 3", write_mock.call_args.args[1])
                self.assertIsNone(service._runtime_overrides_pending_serialized)

    def test_save_runtime_overrides_clears_stale_pending_snapshot_after_revert(self) -> None:
        current_time = 10.0
        with tempfile.TemporaryDirectory() as temp_dir:
            overrides_path = os.path.join(temp_dir, "runtime-overrides.ini")
            service = RuntimeOverrideServiceFixture(
                overrides_path,
                runtime_overrides_write_min_interval_seconds=1.0,
                time_now=lambda: current_time,
            )
            controller = ServiceStateController(service, self._normalize_mode)

            with patch(STATE_RUNTIME_WRITE) as write_mock:
                controller.save_runtime_overrides()
                self.assertEqual(write_mock.call_count, 1)

                current_time = 10.2
                service.virtual_mode = 2
                controller.save_runtime_overrides()
                self.assertIsNotNone(service._runtime_overrides_pending_serialized)

                current_time = 10.4
                service.virtual_mode = 1
                controller.save_runtime_overrides()
                self.assertIsNone(service._runtime_overrides_pending_serialized)

                controller.flush_runtime_overrides(11.1)
                self.assertEqual(write_mock.call_count, 1)

    def test_save_runtime_overrides_respects_pending_due_time_after_write_failure(self) -> None:
        current_time = 10.0
        with tempfile.TemporaryDirectory() as temp_dir:
            overrides_path = os.path.join(temp_dir, "runtime-overrides.ini")
            service = RuntimeOverrideServiceFixture(
                overrides_path,
                runtime_overrides_write_min_interval_seconds=1.0,
                time_now=lambda: current_time,
            )
            controller = ServiceStateController(service, self._normalize_mode)

            with patch(
                STATE_RUNTIME_WRITE,
                side_effect=RuntimeError("boom"),
            ) as write_mock:
                controller.save_runtime_overrides()
                self.assertEqual(write_mock.call_count, 1)
                self.assertEqual(service._runtime_overrides_pending_due_at, 11.0)

                current_time = 10.2
                service.virtual_mode = 2
                controller.save_runtime_overrides()
                self.assertEqual(write_mock.call_count, 1)
                self.assertEqual(service._runtime_overrides_pending_due_at, 11.0)

                controller.flush_runtime_overrides(10.9)
                self.assertEqual(write_mock.call_count, 1)
