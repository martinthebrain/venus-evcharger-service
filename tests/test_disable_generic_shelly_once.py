"""Behavior tests for the semantic generic Shelly one-shot helper."""

from __future__ import annotations

import configparser
import logging
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

from venus_evcharger.ops import disable_generic_shelly_once as helper
from venus_evcharger.ports.generic_shelly_configuration import (
    DisableMatchingGenericShellyOnceRequest,
    GenericShellyConfigurationReceipt,
)


class _StringValue:
    def __init__(self, value: str) -> None:
        self.value = value

    def __str__(self) -> str:
        return self.value


class _RecordingConfigurationPort:
    def __init__(self, receipt: GenericShellyConfigurationReceipt) -> None:
        self.receipt = receipt
        self.requests: list[DisableMatchingGenericShellyOnceRequest] = []

    def disable_matching_device_channel_once(
        self,
        request: DisableMatchingGenericShellyOnceRequest,
    ) -> GenericShellyConfigurationReceipt:
        self.requests.append(request)
        return self.receipt


def _settings(
    *,
    enabled: bool = True,
    allow_persistent_disable: bool = True,
    target_ip: str = "192.0.2.7",
    target_mac: str = "",
    channel: int = 1,
    delay_seconds: float = 0.0,
) -> helper.DisableShellySettings:
    return {
        "enabled": enabled,
        "allow_persistent_disable": allow_persistent_disable,
        "target_ip": target_ip,
        "target_mac": target_mac,
        "channel": channel,
        "delay_seconds": delay_seconds,
    }


class DisableGenericShellyOnceTests(unittest.TestCase):
    def test_scalar_normalization_is_fail_safe_and_bounded(self) -> None:
        self.assertTrue(helper._as_bool(None, True))
        self.assertFalse(helper._as_bool(None, False))
        self.assertTrue(helper._as_bool(" YES "))
        self.assertFalse(helper._as_bool("invalid"))
        self.assertEqual(helper._as_int("2", 7), 2)
        self.assertEqual(helper._as_int(_StringValue("3"), 7), 3)
        self.assertEqual(helper._as_int("bad", 7), 7)
        self.assertEqual(helper._as_float("2.5", 7.5), 2.5)
        self.assertEqual(helper._as_float(_StringValue("3.5"), 7.5), 3.5)
        self.assertEqual(helper._as_float("bad", 7.5), 7.5)
        self.assertEqual(helper._normalize_mac(" aa:bb-cc dd:ee:ff "), "AABBCCDDEEFF")
        self.assertEqual(helper._normalize_mac(None), "")
        self.assertEqual(helper._normalized_channel(0), 1)
        self.assertEqual(helper._normalized_channel(2), 2)
        self.assertEqual(helper._normalized_channel("bad"), 1)
        self.assertEqual(helper._normalized_delay_seconds(-1), 0.0)
        self.assertEqual(helper._normalized_delay_seconds(0.5), 0.5)
        self.assertEqual(helper._normalized_delay_seconds("bad"), 180.0)

    def test_load_settings_returns_only_semantic_admin_settings(self) -> None:
        with tempfile.NamedTemporaryFile("w+", suffix=".ini") as handle:
            handle.write(
                "[DEFAULT]\n"
                "Host=192.0.2.1\n"
                "DisableGenericShellyDevice=no\n"
                "GenericShellyAllowPersistentDisable=yes\n"
                "GenericShellyService=com.example.ignored\n"
                "GenericShellyDisableIp=192.0.2.2\n"
                "GenericShellyDisableMac=aa:bb:cc:dd:ee:ff\n"
                "GenericShellyDisableChannel=2\n"
                "GenericShellyDisableDelaySeconds=3.5\n"
                "DbusGatewayCachePath=/tmp/ignored.json\n"
            )
            handle.flush()
            settings = helper.load_settings(handle.name)
        self.assertEqual(
            settings,
            {
                "enabled": False,
                "allow_persistent_disable": True,
                "target_ip": "192.0.2.2",
                "target_mac": "AABBCCDDEEFF",
                "channel": 2,
                "delay_seconds": 3.5,
            },
        )
        self.assertNotIn("service", settings)
        self.assertNotIn("gateway_cache_path", settings)

    def test_load_settings_defaults_and_errors_are_explicit(self) -> None:
        with tempfile.NamedTemporaryFile("w+", suffix=".ini") as handle:
            handle.write("[DEFAULT]\nHost=192.0.2.1\n")
            handle.flush()
            self.assertEqual(helper.load_settings(handle.name), _settings(target_ip="192.0.2.1", delay_seconds=180.0))

        missing = "/tmp/definitely-missing-generic-shelly.ini"
        with self.assertRaisesRegex(ValueError, f"^Unable to read config file: {missing}$"):
            helper.load_settings(missing)

        parser = configparser.ConfigParser()
        parser.read_dict({"DEFAULT": {"Host": "  "}})
        with self.assertRaisesRegex(ValueError, "^DEFAULT Host is required in the config$"):
            helper._required_host(parser["DEFAULT"])

    def test_reference_matcher_preserves_ip_precedence_and_mac_fallback(self) -> None:
        self.assertTrue(helper.matches_device("AABBCCDDEEFF", " 192.0.2.1 ", "other", "192.0.2.1", "AABBCCDDEEFF"))
        self.assertFalse(helper.matches_device("AABBCCDDEEFF", "192.0.2.2", "AABBCCDDEEFF", "192.0.2.1", "AABBCCDDEEFF"))
        self.assertTrue(helper.matches_device("aa-bb-cc-dd-ee-ff", None, None, "", "AABBCCDDEEFF"))
        self.assertTrue(helper.matches_device("serial", None, "aa:bb:cc:dd:ee:ff", "", "AABBCCDDEEFF"))
        self.assertFalse(helper.matches_device("not-a-mac", None, None, "", "AABBCCDDEEFF"))
        self.assertFalse(helper.matches_device("serial", None, None, "", ""))

    def test_preconditions_are_terminal_before_delay_or_port_access(self) -> None:
        cases = (
            (_settings(enabled=False), "disabled-by-config"),
            (_settings(allow_persistent_disable=False), "persistent-disable-blocked"),
            (_settings(target_ip="", target_mac=""), "no-target"),
        )
        for settings, expected in cases:
            port = _RecordingConfigurationPort(GenericShellyConfigurationReceipt(True, "unused"))
            with (
                patch.object(helper, "load_settings", return_value=settings),
                patch("venus_evcharger.ops.disable_generic_shelly_once.time.sleep") as sleep,
            ):
                self.assertEqual(helper.run_once("config", configuration_port=port), expected)
            sleep.assert_not_called()
            self.assertEqual(port.requests, [])

    def test_run_once_submits_ip_request_after_delay_and_reports_acceptance(self) -> None:
        port = _RecordingConfigurationPort(GenericShellyConfigurationReceipt(True, "command-17"))
        with (
            patch.object(helper, "load_settings", return_value=_settings(delay_seconds=2.0, channel=3)),
            patch("venus_evcharger.ops.disable_generic_shelly_once.time.sleep") as sleep,
            patch("venus_evcharger.ops.disable_generic_shelly_once.logging.info") as info,
        ):
            self.assertEqual(helper.run_once("config", configuration_port=port), "accepted")
        sleep.assert_called_once_with(2.0)
        self.assertEqual(
            info.call_args_list,
            [
                call("Waiting %.0f seconds before generic Shelly one-shot request", 2.0),
                call("Gateway accepted generic Shelly disable request %s", "command-17"),
            ],
        )
        self.assertEqual(len(port.requests), 1)
        request = port.requests[0]
        self.assertEqual((request.selector.kind, request.selector.value, request.channel), ("ip", "192.0.2.7", 3))

    def test_run_once_submits_mac_request_without_delay_and_reports_rejection(self) -> None:
        port = _RecordingConfigurationPort(GenericShellyConfigurationReceipt(False, reason="backpressure"))
        settings = _settings(target_ip="", target_mac="AABBCCDDEEFF")
        with (
            patch.object(helper, "load_settings", return_value=settings),
            patch("venus_evcharger.ops.disable_generic_shelly_once.time.sleep") as sleep,
            patch("venus_evcharger.ops.disable_generic_shelly_once.logging.warning") as warning,
        ):
            self.assertEqual(helper.run_once(configuration_port=port), "rejected")
        sleep.assert_not_called()
        warning.assert_called_once_with("Gateway rejected generic Shelly disable request: %s", "backpressure")
        self.assertEqual(port.requests[0].selector.kind, "mac")

    def test_main_reports_acceptance_rejection_missing_composition_and_failure(self) -> None:
        accepted_port = _RecordingConfigurationPort(GenericShellyConfigurationReceipt(True, "accepted-id"))
        with (
            patch.object(helper, "run_once", return_value="accepted") as run,
            patch("venus_evcharger.ops.disable_generic_shelly_once.logging.basicConfig") as basic,
            patch("venus_evcharger.ops.disable_generic_shelly_once.logging.info") as info,
        ):
            self.assertEqual(helper.main(["config"], configuration_port=accepted_port), 0)
        run.assert_called_once_with("config", configuration_port=accepted_port)
        basic.assert_called_once_with(level=logging.INFO, format="%(levelname)s %(message)s")
        info.assert_called_once_with("Generic Shelly one-shot helper finished: %s", "accepted")

        with patch.object(helper, "run_once", return_value="rejected"):
            self.assertEqual(helper.main(["config"], configuration_port=accepted_port), 1)

        with patch("venus_evcharger.ops.disable_generic_shelly_once.logging.error") as error:
            self.assertEqual(helper.main(["config"]), 1)
        error.assert_called_once_with("Generic Shelly configuration port is not configured")

        failure = RuntimeError("failed")
        with (
            patch.object(sys, "argv", ["helper", "from-sys-argv"]),
            patch.object(helper, "run_once", side_effect=failure) as failed_run,
            patch("venus_evcharger.ops.disable_generic_shelly_once.logging.exception") as exception,
        ):
            self.assertEqual(helper.main(configuration_port=accepted_port), 1)
        failed_run.assert_called_once_with("from-sys-argv", configuration_port=accepted_port)
        exception.assert_called_once_with("Generic Shelly one-shot helper failed: %s", failure)

if __name__ == "__main__":
    unittest.main()
