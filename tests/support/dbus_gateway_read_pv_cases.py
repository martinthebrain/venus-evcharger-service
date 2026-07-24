# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter automatic AC and DC PV read scenarios."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    DbusAdapter,
    GatewayAdapterContractCase,
    MagicMock,
    Path,
    gateway_paths,
    install_mock,
    read_pv_module,
    tempfile,
)
from venus_evcharger.dbus_adapter.health.freshness import cache_freshness


class GatewayPvReadCases(GatewayAdapterContractCase):
    """Exercise automatic AC and DC PV read scenarios."""

    def test_fast_pv_poll_uses_cached_services_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nAutoPvServicePrefix=com.victronenergy.pvinverter\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            services = ["com.victronenergy.pvinverter.http_1"]
            adapter.cache.update_services(services)
            adapter.energy_discovery.update_services(services, captured_at=1.0)
            calls: list[tuple[str, str]] = []

            def fake_read(service: str, path: str) -> float:
                calls.append((service, path))
                return 123.0

            setattr(adapter.read_executor, "read_busitem_now", fake_read)

            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "pv",
                    {"aggregate": "services-sum", "prefix": "com.victronenergy.pvinverter", "path": "/Ac/Power"},
                ),
                "applied",
            )
            self.assertEqual(calls, [("com.victronenergy.pvinverter.http_1", "/Ac/Power")])
            self.assertEqual(adapter.cache.values["pv"]["value"], 123.0)

    def test_optional_pv_read_falls_back_to_zero_without_health_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nAutoPvServicePrefix=com.victronenergy.pvinverter\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.rate_limiter.intervals["read"] = 0.0
            services = [
                "com.victronenergy.pvinverter.http_1",
                "com.victronenergy.system",
            ]
            adapter.cache.update_services(services)
            adapter.energy_discovery.update_services(services, captured_at=1.0)
            install_mock(
                adapter.read_executor,
                "read_busitem_now",
                MagicMock(side_effect=RuntimeError("offline")),
            )

            first = adapter.read_executor.poll_read_spec("pv_power_w", adapter.read_scheduler.specs["pv_power_w"])
            outcome = adapter.read_executor.poll_read_spec("pv_power_w", adapter.read_scheduler.specs["pv_power_w"])

            self.assertEqual(first, "deferred")
            self.assertEqual(outcome, "applied")
            entry = adapter.cache.values["pv_power_w"]
            self.assertEqual(entry["value"], 0.0)
            self.assertEqual(entry["status"], "fresh")
            self.assertEqual(entry["confidence"], 0.2)
            self.assertIn("offline", entry["last_error"])
            self.assertEqual(adapter.read_executor.read_busitem_now.call_count, 2)

    def test_optional_pv_member_failure_does_not_trip_circuit_breaker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.rate_limiter.intervals["read"] = 0.0
            services = ["com.victronenergy.pvinverter.http_1"]
            adapter.cache.update_services(services)
            adapter.energy_discovery.update_services(services, captured_at=1.0)
            install_mock(
                adapter.read_executor,
                "read_busitem_now",
                MagicMock(side_effect=RuntimeError("night pv asleep")),
            )
            install_mock(adapter.circuit, "record_error", MagicMock())

            outcome = adapter.read_executor.poll_read_spec(
                "pv_power_w",
                {
                    "aggregate": "pv-total",
                    "prefix": "com.victronenergy.pvinverter",
                    "path": "/Ac/Power",
                    "dc_service": "",
                    "dc_path": "",
                    "use_dc_pv": False,
                },
            )

            self.assertEqual(outcome, "applied")
            adapter.circuit.record_error.assert_not_called()
            member = adapter.cache.values["path:com.victronenergy.pvinverter.http_1/Ac/Power"]
            self.assertEqual(member["status"], "unavailable")
            self.assertEqual(member["source_state"], "unavailable")
            self.assertEqual(member["last_error"], "night pv asleep")
            self.assertEqual(adapter.cache.values["pv_power_w"]["value"], 0.0)
            health = cache_freshness(adapter.cache, 1.0 + float(adapter.cache.values["pv_power_w"]["confirmed_at"]))
            self.assertEqual(health["pv_power_w_status"], "fresh")
            self.assertEqual(health["status_counts"], {"fresh": 1})
            self.assertEqual(health["optional_source_error_count"], 0)
            self.assertEqual(health["optional_source_unavailable_count"], 1)

    def test_optional_direct_read_falls_back_to_fresh_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            install_mock(adapter.read_executor, "read_busitem", MagicMock(return_value=1.0))
            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "optional_value",
                    {"aggregate": "sum", "service": "svc.optional", "paths": ["/L1", "/L2"]},
                ),
                "deferred",
            )
            self.assertTrue(adapter.read_executor.has_pending_aggregate())
            install_mock(
                adapter.read_executor,
                "read_busitem",
                MagicMock(side_effect=RuntimeError("optional offline")),
            )

            outcome = adapter.read_executor.poll_read_spec(
                "optional_value",
                {
                    "service": "svc.optional",
                    "path": "/Maybe",
                    "optional_zero_on_error": "yes",
                    "optional_confidence": 0.45,
                },
            )

            self.assertEqual(outcome, "applied")
            entry = adapter.cache.values["optional_value"]
            self.assertEqual(entry["value"], 0.0)
            self.assertEqual(entry["status"], "fresh")
            self.assertEqual(entry["source"], "svc.optional")
            self.assertEqual(entry["confidence"], 0.45)
            self.assertEqual(entry["last_error"], "optional offline")
            self.assertFalse(adapter.read_executor.has_pending_aggregate())

    def test_optional_direct_read_uses_prefix_source_and_tolerates_missing_aggregate_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            outcome = adapter.read_executor.poll_read_spec(
                "prefix_optional",
                {
                    "aggregate": "services-sum",
                    "prefix": "svc.prefix",
                    "path": "/Maybe",
                    "optional_zero_on_error": "yes",
                },
            )

            self.assertEqual(outcome, "applied")
            entry = adapter.cache.values["prefix_optional"]
            self.assertEqual(entry["source"], "svc.prefix")
            self.assertEqual(entry["confidence"], 0.2)
            self.assertIn("No cached services", entry["last_error"])

    def test_optional_zero_on_error_accepts_only_explicit_truthy_values(self) -> None:
        truthy = ("1", "true", "TRUE", " yes ", "on")
        falsey = ("", "0", "false", "no", "off", None)

        for value in truthy:
            with self.subTest(value=value):
                with tempfile.TemporaryDirectory() as temp_dir:
                    config_path = Path(temp_dir) / "config.ini"
                    config_path.write_text("[DEFAULT]\n", encoding="utf-8")
                    adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
                    outcome = adapter.read_executor.poll_read_spec(
                        "optional",
                        {
                            "aggregate": "services-sum",
                            "prefix": "missing.",
                            "path": "/P",
                            "optional_zero_on_error": value,
                        },
                    )
                    self.assertEqual(outcome, "applied")
                    self.assertEqual(adapter.cache.values["optional"]["value"], 0.0)

        for value in falsey:
            with self.subTest(value=value):
                with tempfile.TemporaryDirectory() as temp_dir:
                    config_path = Path(temp_dir) / "config.ini"
                    config_path.write_text("[DEFAULT]\n", encoding="utf-8")
                    adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
                    outcome = adapter.read_executor.poll_read_spec(
                        "required",
                        {
                            "aggregate": "services-sum",
                            "prefix": "missing.",
                            "path": "/P",
                            "optional_zero_on_error": value,
                        },
                    )
                    self.assertEqual(outcome, "dropped")
                    self.assertEqual(adapter.cache.values["required"]["status"], "error")

    def test_pv_total_automatically_combines_ac_services_and_dc_pv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.rate_limiter.intervals["read"] = 0.0
            services = [
                "com.victronenergy.pvinverter.http_1",
                "com.victronenergy.system",
            ]
            adapter.cache.update_services(services)
            adapter.energy_discovery.update_services(services, captured_at=1.0)
            values = {
                ("com.victronenergy.pvinverter.http_1", "/Ac/Power"): 120.0,
                ("com.victronenergy.system", "/Dc/Pv/Power"): 30.0,
            }
            install_mock(
                adapter.read_executor,
                "read_busitem_now",
                MagicMock(side_effect=lambda service, path: values[(service, path)]),
            )

            first = adapter.read_executor.poll_read_spec("pv_power_w", adapter.read_scheduler.specs["pv_power_w"])
            second = adapter.read_executor.poll_read_spec("pv_power_w", adapter.read_scheduler.specs["pv_power_w"])

            self.assertEqual(first, "deferred")
            self.assertEqual(second, "applied")
            self.assertEqual(adapter.cache.values["pv_power_w"]["value"], 150.0)
            adapter.read_executor.read_busitem_now.assert_any_call("com.victronenergy.pvinverter.http_1", "/Ac/Power")
            adapter.read_executor.read_busitem_now.assert_any_call("com.victronenergy.system", "/Dc/Pv/Power")

    def test_pv_total_uses_configured_empty_confidence_when_all_sources_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.rate_limiter.intervals["read"] = 0.0
            services = [
                "com.victronenergy.pvinverter.http_2",
                "com.victronenergy.system",
            ]
            adapter.cache.update_services(services)
            adapter.energy_discovery.update_services(services, captured_at=1.0)
            install_mock(
                adapter.read_executor,
                "read_optional_busitem",
                MagicMock(side_effect=RuntimeError("all pv asleep")),
            )
            spec = {
                "aggregate": "pv-total",
                "prefix": "com.victronenergy.pvinverter",
                "path": "/Ac/Power",
                "dc_service": "com.victronenergy.system",
                "dc_path": "/Dc/Pv/Power",
                "use_dc_pv": True,
                "optional_confidence": 0.6,
            }

            self.assertEqual(adapter.read_executor.poll_read_spec("pv_power_w", spec), "deferred")
            self.assertEqual(adapter.read_executor.poll_read_spec("pv_power_w", spec), "applied")
            entry = adapter.cache.values["pv_power_w"]
            self.assertEqual(entry["value"], 0.0)
            self.assertEqual(entry["confidence"], 0.6)
            self.assertIn("all pv asleep", entry["last_error"])

    def test_pv_total_uses_default_empty_confidence_when_all_sources_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.rate_limiter.intervals["read"] = 0.0
            adapter.cache.update_services(["com.victronenergy.pvinverter.http_2"])
            adapter.energy_discovery.update_services(["com.victronenergy.pvinverter.http_2"], captured_at=1.0)
            install_mock(
                adapter.read_executor,
                "read_optional_busitem",
                MagicMock(side_effect=RuntimeError("pv asleep")),
            )
            spec = {
                "aggregate": "pv-total",
                "prefix": "com.victronenergy.pvinverter",
                "path": "/Ac/Power",
                "dc_service": "",
                "dc_path": "",
                "use_dc_pv": False,
            }

            self.assertEqual(adapter.read_executor.poll_read_spec("pv_power_w", spec), "applied")
            entry = adapter.cache.values["pv_power_w"]
            self.assertEqual(entry["value"], 0.0)
            self.assertEqual(entry["confidence"], 0.2)
            self.assertIn("pv asleep", entry["last_error"])

    def test_pv_total_requires_at_least_one_autodetected_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            spec = {
                "aggregate": "pv-total",
                "prefix": "com.victronenergy.pvinverter",
                "path": "/Ac/Power",
                "dc_service": "",
                "dc_path": "/Dc/Pv/Power",
                "use_dc_pv": False,
            }

            self.assertEqual(adapter.read_executor.poll_read_spec("pv_power_w", spec), "dropped")
            self.assertEqual(
                adapter.cache.values["pv_power_w"]["last_error"],
                "No available AC or DC PV source candidates",
            )

    def test_pv_total_member_discovery_keeps_ac_and_dc_sources_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.cache.update_services(
                [
                    "com.victronenergy.pvinverter.http_9",
                    "com.victronenergy.pvinverter.http_1",
                    "com.victronenergy.battery.socketcan_can1",
                ]
            )
            adapter.energy_discovery.update_services(sorted(adapter.cache.services), captured_at=1.0)

            def members(spec: dict[str, object]) -> list[tuple[str, str]]:
                prefix = str(spec.get("prefix") or "")
                return read_pv_module.pv_total_members(
                    spec,
                    sorted(name for name in adapter.cache.services if name.startswith(prefix)),
                )

            self.assertEqual(
                members(
                    {
                        "prefix": "com.victronenergy.pvinverter",
                        "path": "/Ac/Power",
                        "dc_service": "com.victronenergy.system",
                        "dc_path": "/Dc/Pv/Power",
                        "use_dc_pv": True,
                    }
                ),
                [
                    ("com.victronenergy.pvinverter.http_1", "/Ac/Power"),
                    ("com.victronenergy.pvinverter.http_9", "/Ac/Power"),
                    ("com.victronenergy.system", "/Dc/Pv/Power"),
                ],
            )
            self.assertEqual(
                members(
                    {
                        "prefix": "com.victronenergy.pvinverter",
                        "path": "/Ac/Power",
                        "dc_service": "com.victronenergy.system",
                        "dc_path": "/Dc/Pv/Power",
                        "use_dc_pv": False,
                    }
                ),
                [
                    ("com.victronenergy.pvinverter.http_1", "/Ac/Power"),
                    ("com.victronenergy.pvinverter.http_9", "/Ac/Power"),
                ],
            )

            self.assertEqual(
                members(
                    {
                        "prefix": "com.victronenergy.pvinverter",
                        "path": "/Ac/Power",
                        "dc_service": "",
                        "dc_path": "",
                        "use_dc_pv": False,
                    }
                ),
                [
                    ("com.victronenergy.pvinverter.http_1", "/Ac/Power"),
                    ("com.victronenergy.pvinverter.http_9", "/Ac/Power"),
                ],
            )
            self.assertEqual(
                members(
                    {
                        "prefix": "com.victronenergy.pvinverter",
                        "path": "",
                        "dc_service": "com.victronenergy.system",
                        "dc_path": "/Dc/Pv/Power",
                        "use_dc_pv": True,
                    }
                ),
                [("com.victronenergy.system", "/Dc/Pv/Power")],
            )
            self.assertEqual(
                members(
                    {
                        "prefix": "com.victronenergy.pvinverter",
                        "path": "/Ac/Power",
                        "dc_service": "",
                        "dc_path": "/Dc/Pv/Power",
                        "use_dc_pv": True,
                    }
                ),
                [
                    ("com.victronenergy.pvinverter.http_1", "/Ac/Power"),
                    ("com.victronenergy.pvinverter.http_9", "/Ac/Power"),
                ],
            )
            self.assertEqual(
                members(
                    {
                        "prefix": "com.victronenergy.pvinverter",
                        "path": "",
                        "dc_service": "com.victronenergy.system",
                        "dc_path": "/Dc/Pv/Power",
                        "use_dc_pv": True,
                    }
                ),
                [("com.victronenergy.system", "/Dc/Pv/Power")],
            )
            self.assertEqual(
                members(
                    {
                        "prefix": "com.victronenergy.pvinverter",
                        "path": "/Ac/Power",
                        "dc_service": "com.victronenergy.system",
                        "dc_path": "",
                        "use_dc_pv": True,
                    }
                ),
                [
                    ("com.victronenergy.pvinverter.http_1", "/Ac/Power"),
                    ("com.victronenergy.pvinverter.http_9", "/Ac/Power"),
                ],
            )
