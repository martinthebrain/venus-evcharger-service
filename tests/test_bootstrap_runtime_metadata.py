# SPDX-License-Identifier: GPL-3.0-or-later
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from venus_evcharger.bootstrap.runtime_metadata import (
    apply_adapter_topology_device_metadata,
    apply_device_metadata,
    apply_rpc_device_metadata,
    apply_unconfigured_device_metadata,
    device_info_payload,
    fetch_device_info_with_fallback,
    primary_rpc_configured,
    topology_configured,
)


class BootstrapRuntimeMetadataContracts(unittest.TestCase):
    def test_runtime_topology_and_rpc_flags_fall_back_to_legacy_host_flag(self) -> None:
        self.assertTrue(topology_configured(SimpleNamespace(topology_configured=True, host_configured=False)))
        self.assertTrue(topology_configured(SimpleNamespace(host_configured=True)))
        self.assertFalse(topology_configured(SimpleNamespace(host_configured=False)))
        self.assertFalse(topology_configured(SimpleNamespace()))
        self.assertTrue(primary_rpc_configured(SimpleNamespace(primary_rpc_configured=True, host_configured=False)))
        self.assertTrue(primary_rpc_configured(SimpleNamespace(host_configured=True)))
        self.assertFalse(primary_rpc_configured(SimpleNamespace(primary_rpc_configured=False, host_configured=True)))
        self.assertFalse(primary_rpc_configured(SimpleNamespace()))

    def test_device_info_payload_accepts_only_mappings(self) -> None:
        self.assertEqual(device_info_payload({"name": "EVCS"}), {"name": "EVCS"})
        self.assertEqual(device_info_payload([("name", "EVCS")]), {})
        self.assertEqual(device_info_payload(None), {})

    def test_fetch_device_info_retries_sleeps_and_normalizes_payload(self) -> None:
        failure = RuntimeError("offline")
        service = SimpleNamespace(
            startup_device_info_retries=1,
            startup_device_info_retry_seconds=2.5,
            runtime=SimpleNamespace(rpc_call=MagicMock(side_effect=[failure, {"mac": "ABC"}])),
        )
        sleep = MagicMock()

        with patch("venus_evcharger.bootstrap.runtime_metadata.logging.warning") as warning_mock:
            result = fetch_device_info_with_fallback(service, sleep_func=sleep)

        self.assertEqual(result, {"mac": "ABC"})
        service.runtime.rpc_call.assert_called_with("Shelly.GetDeviceInfo")
        self.assertEqual(service.runtime.rpc_call.call_count, 2)
        sleep.assert_called_once_with(2.5)
        warning_mock.assert_called_once_with(
            "Shelly.GetDeviceInfo failed during startup (attempt %s/%s): %s",
            1,
            2,
            failure,
        )

    def test_fetch_device_info_returns_empty_after_exhausting_attempts(self) -> None:
        failure = RuntimeError("offline")
        service = SimpleNamespace(
            startup_device_info_retries=2,
            startup_device_info_retry_seconds=0,
            runtime=SimpleNamespace(rpc_call=MagicMock(side_effect=failure)),
        )

        with patch("venus_evcharger.bootstrap.runtime_metadata.logging.warning") as warning_mock:
            result = fetch_device_info_with_fallback(service, sleep_func=MagicMock())

        self.assertEqual(result, {})
        self.assertEqual(service.runtime.rpc_call.call_count, 3)
        warning_mock.assert_called_once_with(
            "Shelly.GetDeviceInfo unavailable during startup, continuing with generic metadata: %s",
            failure,
        )

    def test_fetch_device_info_does_not_sleep_after_final_failed_attempt(self) -> None:
        service = SimpleNamespace(
            startup_device_info_retries=0,
            startup_device_info_retry_seconds=1.0,
            runtime=SimpleNamespace(rpc_call=MagicMock(side_effect=RuntimeError("offline"))),
        )
        sleep = MagicMock()

        fetch_device_info_with_fallback(service, sleep_func=sleep)

        sleep.assert_not_called()

    def test_fetch_device_info_retries_with_short_positive_delay(self) -> None:
        service = SimpleNamespace(
            startup_device_info_retries=1,
            startup_device_info_retry_seconds=0.5,
            runtime=SimpleNamespace(
                rpc_call=MagicMock(side_effect=[RuntimeError("offline"), {"mac": "ABC"}])
            ),
        )
        sleep = MagicMock()

        self.assertEqual(fetch_device_info_with_fallback(service, sleep_func=sleep), {"mac": "ABC"})

        sleep.assert_called_once_with(0.5)

    def test_fetch_device_info_ignores_non_mapping_success_payload(self) -> None:
        service = SimpleNamespace(
            startup_device_info_retries=0,
            startup_device_info_retry_seconds=0,
            runtime=SimpleNamespace(rpc_call=MagicMock(return_value=["not", "mapping"])),
        )

        self.assertEqual(fetch_device_info_with_fallback(service, sleep_func=MagicMock()), {})

    def test_fetch_device_info_handles_zero_attempt_budget_without_rpc(self) -> None:
        service = SimpleNamespace(
            startup_device_info_retries=-1,
            startup_device_info_retry_seconds=1.0,
            runtime=SimpleNamespace(rpc_call=MagicMock()),
        )

        with patch("venus_evcharger.bootstrap.runtime_metadata.logging.warning") as warning_mock:
            result = fetch_device_info_with_fallback(service, sleep_func=MagicMock())

        self.assertEqual(result, {})
        service.runtime.rpc_call.assert_not_called()
        warning_mock.assert_called_once_with(
            "Shelly.GetDeviceInfo unavailable during startup, continuing with generic metadata: %s",
            None,
        )

    def test_apply_unconfigured_and_adapter_metadata_use_generic_identity(self) -> None:
        unconfigured = SimpleNamespace(custom_name_override="", deviceinstance=60)
        adapter = SimpleNamespace(custom_name_override="Named", deviceinstance=61)
        read_version = MagicMock(side_effect=lambda name: f"version:{name}")

        with patch("venus_evcharger.bootstrap.runtime_metadata.logging.info") as info_mock:
            apply_unconfigured_device_metadata(unconfigured, read_version=read_version)
            apply_adapter_topology_device_metadata(adapter, read_version=read_version)

        self.assertEqual(unconfigured.custom_name, "Venus EV Charger Service")
        self.assertEqual(unconfigured.serial, "unconfigured-60")
        self.assertEqual(unconfigured.firmware_version, "version:version.txt")
        self.assertEqual(unconfigured.hardware_version, "Not configured")
        self.assertEqual(adapter.custom_name, "Named")
        self.assertEqual(adapter.serial, "topology-61")
        self.assertEqual(adapter.firmware_version, "version:version.txt")
        self.assertEqual(adapter.hardware_version, "External adapter topology")
        self.assertEqual(read_version.call_args_list, [unittest.mock.call("version.txt"), unittest.mock.call("version.txt")])
        self.assertEqual(
            info_mock.call_args_list,
            [
                unittest.mock.call("No load topology is configured yet; starting without device metadata"),
                unittest.mock.call("No direct legacy RPC endpoint is configured; starting with generic device metadata"),
            ],
        )

        adapter_default_name = SimpleNamespace(custom_name_override="", deviceinstance=62)
        apply_adapter_topology_device_metadata(adapter_default_name, read_version=read_version)
        self.assertEqual(adapter_default_name.custom_name, "Venus EV Charger Service")

    def test_apply_rpc_metadata_prefers_override_then_device_info_then_defaults(self) -> None:
        read_version = MagicMock(side_effect=lambda name: f"version:{name}")
        with_info = SimpleNamespace(custom_name_override="", host="192.168.1.20")
        with_override = SimpleNamespace(custom_name_override="Override", host="192.168.1.21")
        without_info = SimpleNamespace(custom_name_override="", host="192.168.1.22")

        apply_rpc_device_metadata(
            with_info,
            read_version=read_version,
            fetch_device_info=lambda: {"name": "Shelly", "mac": "ABC", "fw_id": "fw", "model": "Plus"},
        )
        apply_rpc_device_metadata(
            with_override,
            read_version=read_version,
            fetch_device_info=lambda: {"name": "Ignored", "mac": "DEF", "fw_id": "fw2", "model": "Gen4"},
        )
        apply_rpc_device_metadata(without_info, read_version=read_version, fetch_device_info=lambda: {})

        self.assertEqual(with_info.custom_name, "Shelly")
        self.assertEqual(with_info.serial, "ABC")
        self.assertEqual(with_info.firmware_version, "fw")
        self.assertEqual(with_info.hardware_version, "Plus")
        self.assertEqual(with_override.custom_name, "Override")
        self.assertEqual(with_override.serial, "DEF")
        self.assertEqual(with_override.firmware_version, "fw2")
        self.assertEqual(with_override.hardware_version, "Gen4")
        self.assertEqual(without_info.custom_name, "Venus EV Charger Service")
        self.assertEqual(without_info.serial, "192168122")
        self.assertEqual(without_info.firmware_version, "version:version.txt")
        self.assertEqual(without_info.hardware_version, "Shelly 1PM Gen4")

    def test_apply_device_metadata_routes_by_topology_state(self) -> None:
        read_version = MagicMock(return_value="1.0")
        fetch_device_info = MagicMock(return_value={"name": "Shelly", "mac": "ABC"})
        cases = [
            (
                SimpleNamespace(
                    config={"DEFAULT": {"ProductName": "  Configured  "}},
                    custom_name_override="",
                    topology_configured=False,
                    host_configured=False,
                    deviceinstance=60,
                ),
                "unconfigured-60",
                0,
            ),
            (
                SimpleNamespace(
                    config={"DEFAULT": {}},
                    custom_name_override="",
                    topology_configured=True,
                    primary_rpc_configured=False,
                    deviceinstance=61,
                ),
                "topology-61",
                0,
            ),
            (
                SimpleNamespace(
                    config={"DEFAULT": {}},
                    custom_name_override="",
                    topology_configured=True,
                    primary_rpc_configured=True,
                    host="192.168.1.23",
                    deviceinstance=62,
                ),
                "ABC",
                1,
            ),
        ]

        for service, expected_serial, expected_fetch_count in cases:
            fetch_device_info.reset_mock()
            apply_device_metadata(service, read_version=read_version, fetch_device_info=fetch_device_info)
            self.assertEqual(service.serial, expected_serial)
            self.assertEqual(fetch_device_info.call_count, expected_fetch_count)

        self.assertEqual(cases[0][0].product_name, "Configured")
        self.assertEqual(cases[1][0].product_name, "Venus EV Charger Service")


if __name__ == "__main__":
    unittest.main()
