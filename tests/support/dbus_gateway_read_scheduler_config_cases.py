# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter read scheduler, discovery, and config contracts."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    DbusDiscoveryManager,
    DbusReadScheduler,
    GatewayAdapterContractCase,
    configparser,
    process_config_module,
    read_spec_from_mapping,
)


class GatewayReadSchedulerConfigCases(GatewayAdapterContractCase):
    """Exercise read scheduler, discovery, and config contracts."""

    def test_read_scheduler_tracks_due_reads_and_degraded_intervals(self) -> None:
        scheduler = DbusReadScheduler({"grid": {"interval": 2.0, "priority": "read"}})

        due = scheduler.next_due(
            monotonic_at=10.0,
            circuit_state="degraded",
            priority_allowed=lambda _priority: True,
        )
        self.assertIsNotNone(due)
        assert due is not None
        key, _spec, interval = due
        self.assertEqual(key, "grid")
        self.assertEqual(interval, 6.0)

        scheduler.record_success(key, monotonic_at=10.0, interval=interval)
        self.assertIsNone(
            scheduler.next_due(
                monotonic_at=15.0,
                circuit_state="ok",
                priority_allowed=lambda _priority: True,
            )
        )
        self.assertIsNotNone(
            scheduler.next_due(
                monotonic_at=16.0,
                circuit_state="ok",
                priority_allowed=lambda _priority: True,
            )
        )
        scheduler.record_error("grid", monotonic_at=20.0, interval=2.0)
        self.assertIsNone(
            scheduler.next_due(
                monotonic_at=21.0,
                circuit_state="ok",
                priority_allowed=lambda _priority: True,
            )
        )
        self.assertEqual(DbusReadScheduler.effective_interval({"interval": 2.0}, "protective"), 10.0)
        self.assertEqual(DbusReadScheduler.effective_interval({"interval": True}, "ok"), 2.0)
        self.assertEqual(DbusReadScheduler.effective_interval({"interval": "bad"}, "ok"), 2.0)
        self.assertEqual(DbusReadScheduler.effective_interval({"interval": object()}, "ok"), 2.0)
        blocked = DbusReadScheduler({"grid": {"interval": 1.0, "priority": "discovery"}})
        self.assertIsNone(
            blocked.next_due(
                monotonic_at=1.0,
                circuit_state="ok",
                priority_allowed=lambda _priority: False,
            )
        )

        fair = DbusReadScheduler(
            {
                "grid": {"interval": 2.0, "priority": "read"},
                "pv": {"interval": 2.0, "priority": "read"},
                "battery": {"interval": 2.0, "priority": "read"},
            }
        )
        fair.next_read_at = {"grid": 100.0, "pv": 90.0, "battery": 0.0}
        due = fair.next_due(
            monotonic_at=100.0,
            circuit_state="ok",
            priority_allowed=lambda _priority: True,
        )
        self.assertIsNotNone(due)
        assert due is not None
        self.assertEqual(due[0], "battery")

    def test_read_spec_from_mapping_validates_known_fields(self) -> None:
        spec = read_spec_from_mapping(
            {
                "aggregate": "pv-total",
                "dc_path": "/Dc/Pv/Power",
                "dc_service": "com.victronenergy.system",
                "path": "/Ac/Power",
                "prefix": "com.victronenergy.pvinverter",
                "priority": "read",
                "service": "svc",
                "paths": ["/A", "/B"],
                "interval": 2,
                "stale_after_seconds": 6,
                "use_dc_pv": True,
                "optional_zero_on_error": False,
                "optional_confidence": 0.2,
            }
        )
        self.assertEqual(spec["aggregate"], "pv-total")
        self.assertEqual(spec["dc_path"], "/Dc/Pv/Power")
        self.assertEqual(spec["dc_service"], "com.victronenergy.system")
        self.assertEqual(spec["path"], "/Ac/Power")
        self.assertEqual(spec["prefix"], "com.victronenergy.pvinverter")
        self.assertEqual(spec["priority"], "read")
        self.assertEqual(spec["service"], "svc")
        self.assertEqual(spec["paths"], ["/A", "/B"])
        self.assertEqual(spec["interval"], 2.0)
        self.assertEqual(spec["stale_after_seconds"], 6.0)
        self.assertEqual(spec["optional_confidence"], 0.2)
        self.assertFalse(spec["optional_zero_on_error"])
        self.assertTrue(spec["use_dc_pv"])

        with self.assertRaisesRegex(KeyError, "unknown read spec field"):
            read_spec_from_mapping({"unexpected": "value"})
        with self.assertRaisesRegex(TypeError, "service must be str"):
            read_spec_from_mapping({"service": object()})
        with self.assertRaisesRegex(TypeError, "interval must be float"):
            read_spec_from_mapping({"interval": True})
        with self.assertRaisesRegex(TypeError, "paths must be list\\[str\\]"):
            read_spec_from_mapping({"paths": ["/ok", 1]})
        with self.assertRaisesRegex(TypeError, "use_dc_pv must be bool"):
            read_spec_from_mapping({"use_dc_pv": 1})
        with self.assertRaisesRegex(TypeError, "optional_confidence must be float"):
            read_spec_from_mapping({"optional_confidence": True})
        for bad_field, bad_value, expected_type in (
            ("service", object(), "object"),
            ("interval", True, "bool"),
            ("use_dc_pv", 1, "int"),
            ("paths", ("/ok",), "tuple"),
        ):
            with self.subTest(field=bad_field):
                with self.assertRaises(TypeError) as caught:
                    read_spec_from_mapping({bad_field: bad_value})
                self.assertIn(f"got {expected_type}", str(caught.exception))

        true_spec = read_spec_from_mapping({"optional_zero_on_error": True})
        self.assertTrue(true_spec["optional_zero_on_error"])

    def test_discovery_manager_tracks_success_and_error_backoff(self) -> None:
        discovery = DbusDiscoveryManager(
            interval_seconds=900.0,
            missing_pv_interval_seconds=60.0,
        )

        self.assertTrue(
            discovery.due(
                monotonic_at=100.0,
                priority_allowed=lambda _priority: True,
            )
        )
        discovery.record_success(
            monotonic_at=100.0,
            captured_at=1000.0,
            needs_early_rescan=False,
        )
        self.assertEqual(discovery.last_success_at, 1000.0)
        self.assertEqual(discovery.next_scan_monotonic, 1000.0)
        self.assertEqual(discovery.next_scan_at, 1900.0)
        self.assertFalse(
            discovery.due(
                monotonic_at=999.0,
                priority_allowed=lambda _priority: True,
            )
        )

        discovery.record_error(
            RuntimeError("dbus down"),
            monotonic_at=200.0,
            captured_at=3000.0,
        )
        self.assertEqual(discovery.last_error, "dbus down")
        self.assertEqual(discovery.next_scan_monotonic, 260.0)
        self.assertEqual(discovery.next_scan_at, 3060.0)
        self.assertFalse(
            discovery.due(
                monotonic_at=260.0,
                priority_allowed=lambda _priority: False,
            )
        )

    def test_read_scheduler_errors_back_off_and_success_resets_failure_count(self) -> None:
        scheduler = DbusReadScheduler({"pv": {"interval": 2.0, "priority": "read"}})
        scheduler.record_error("pv", monotonic_at=100.0, interval=2.0)
        self.assertEqual(scheduler.failure_counts["pv"], 1)
        self.assertEqual(scheduler.next_read_at["pv"], 130.0)
        scheduler.record_error("pv", monotonic_at=130.0, interval=2.0)
        self.assertEqual(scheduler.failure_counts["pv"], 2)
        self.assertEqual(scheduler.next_read_at["pv"], 190.0)
        scheduler.record_success("pv", monotonic_at=200.0, interval=2.0)
        self.assertEqual(scheduler.failure_counts["pv"], 0)
        self.assertEqual(scheduler.next_read_at["pv"], 202.0)
        scheduler.force_due(["missing"])
        self.assertNotIn("missing", scheduler.next_read_at)

    def test_adapter_static_config_helpers_cover_defaults_and_invalid_instance(self) -> None:
        parser = configparser.ConfigParser()
        parser["DEFAULT"] = {
            "ServiceName": "com.example.ev",
            "DeviceInstance": "bad",
            "AutoGridL2Path": "",
            "AutoPvService": "pv.fixed",
            "AutoBatteryService": "com.victronenergy.battery.example",
            "AutoBatteryServicePrefix": "com.victronenergy.battery",
            "AutoBatterySocPath": "/Soc",
        }
        self.assertEqual(process_config_module.evcharger_service_name(parser["DEFAULT"]), "com.example.ev.http_60")
        specs = process_config_module.configured_read_specs(parser["DEFAULT"])
        self.assertEqual(specs["grid_power_w"]["paths"], ["/Ac/Grid/L1/Power", "/Ac/Grid/L3/Power"])
        self.assertEqual(specs["pv_power_w"]["service"], "pv.fixed")
        self.assertEqual(specs["pv_power_w"]["aggregate"], "pv-total")
        self.assertEqual(specs["pv_power_w"]["dc_service"], "com.victronenergy.system")
        self.assertEqual(specs["pv_power_w"]["dc_path"], "/Dc/Pv/Power")
        self.assertTrue(specs["pv_power_w"]["use_dc_pv"])
        self.assertEqual(specs["battery_soc"]["service"], "")
        self.assertEqual(specs["battery_soc"]["prefix"], "com.victronenergy.battery")
        self.assertEqual(specs["battery_soc"]["aggregate"], "first-service")
        self.assertEqual(specs["battery_soc"]["path"], "/Soc")
        self.assertEqual(process_config_module.configured_device_instance(parser["DEFAULT"]), 60)
        with self.assertRaises(ValueError):
            process_config_module.load_adapter_config("/tmp/does-not-exist-venus-evcharger.ini")
