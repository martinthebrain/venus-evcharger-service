# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter aggregate member and low-level read scenarios."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    BusItemInterfaceStub,
    DbusAdapter,
    DbusBusStub,
    DbusOperationDeferred,
    GatewayAdapterContractCase,
    MagicMock,
    Path,
    gateway_paths,
    install_mock,
    patch,
    read_module,
    read_pv_module,
    tempfile,
    time,
)


class GatewayAggregateReadCases(GatewayAdapterContractCase):
    """Exercise aggregate member and low-level read scenarios."""

    def test_pv_member_contracts_cover_dc_tokens_targets_and_backoff_edges(self) -> None:
        valid_dc_spec = {
            "dc_service": " com.victronenergy.system ",
            "dc_path": " /Dc/Pv/Power ",
            "use_dc_pv": " yes ",
        }
        self.assertEqual(read_pv_module.dc_pv_target(valid_dc_spec), ("com.victronenergy.system", "/Dc/Pv/Power"))
        self.assertEqual(
            read_pv_module.dc_pv_members(valid_dc_spec, {}, now=100.0), [("com.victronenergy.system", "/Dc/Pv/Power")]
        )
        for raw in ("1", "true", "yes", "on", " ON "):
            with self.subTest(raw=raw):
                self.assertTrue(read_pv_module.use_dc_pv({"use_dc_pv": raw}))
        for raw in ("", "0", "false", "no", "off", object()):
            with self.subTest(raw=raw):
                self.assertFalse(read_pv_module.use_dc_pv({"use_dc_pv": raw}))
        self.assertFalse(read_pv_module.use_dc_pv({}))
        self.assertIsNone(read_pv_module.dc_pv_target({"dc_service": "", "dc_path": "/Dc/Pv/Power"}))
        self.assertIsNone(read_pv_module.dc_pv_target({"dc_service": object(), "dc_path": "/Dc/Pv/Power"}))
        self.assertIsNone(read_pv_module.dc_pv_target({"dc_service": "svc", "dc_path": "Dc/Pv/Power"}))
        self.assertIsNone(read_pv_module.dc_pv_target({"dc_service": "svc", "dc_path": object()}))
        self.assertIsNone(read_pv_module.dc_pv_target({"dc_service": "svc"}))

        failed_values = {
            "path:svc/Path": {"status": "error", "error_at": "100.0"},
            "path:svc/Other": {"status": "fresh", "error_at": "100.0"},
            "path:svc/MissingErrorAt": {"status": "error"},
            "path:svc/OneSecond": {"status": "error", "error_at": 1.0},
        }
        self.assertTrue(
            read_pv_module.pv_member_recently_failed(
                failed_values,
                "svc",
                "/Path",
                now=399.9,
                backoff_seconds=300.0,
            )
        )
        self.assertFalse(
            read_pv_module.pv_member_recently_failed(
                failed_values,
                "svc",
                "/Path",
                now=400.0,
                backoff_seconds=300.0,
            )
        )
        self.assertFalse(read_pv_module.pv_member_recently_failed(failed_values, "svc", "/Other", now=101.0))
        self.assertFalse(read_pv_module.pv_member_recently_failed(failed_values, "svc", "/MissingErrorAt", now=1.0))
        self.assertTrue(read_pv_module.pv_member_recently_failed(failed_values, "svc", "/OneSecond", now=2.0))
        self.assertFalse(
            read_pv_module.pv_member_recently_failed(
                {"path:svc/Path": {"status": "error", "error_at": True}},
                "svc",
                "/Path",
                now=101.0,
            )
        )
        self.assertFalse(
            read_pv_module.pv_member_recently_failed(
                {"path:svc/Path": {"status": "error", "error_at": "bad"}},
                "svc",
                "/Path",
                now=101.0,
            )
        )

    def test_pv_total_optional_member_errors_are_preserved_and_nonfatal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.rate_limiter.intervals["read"] = 0.0
            adapter.cache.update_services(["com.victronenergy.pvinverter.http_1"])

            def fake_read(service: str, path: str) -> float:
                if service == "com.victronenergy.pvinverter.http_1":
                    raise RuntimeError("ac asleep")
                return 70.0

            install_mock(adapter.read_executor, "read_busitem_now", MagicMock(side_effect=fake_read))

            first = adapter.read_executor.poll_read_spec("pv_power_w", adapter.read_scheduler.specs["pv_power_w"])
            second = adapter.read_executor.poll_read_spec("pv_power_w", adapter.read_scheduler.specs["pv_power_w"])

            self.assertEqual(first, "deferred")
            self.assertEqual(second, "applied")
            entry = adapter.cache.values["pv_power_w"]
            self.assertEqual(entry["value"], 70.0)
            self.assertEqual(entry["confidence"], 1.0)
            self.assertEqual(entry["source"], "com.victronenergy.system/Dc/Pv/Power")
            self.assertIn("com.victronenergy.pvinverter.http_1/Ac/Power: ac asleep", entry["last_error"])

    def test_optional_aggregate_member_logs_and_appends_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.rate_limiter.intervals["read"] = 0.0
            adapter.cache.update_services(["com.victronenergy.pvinverter.http_1"])
            install_mock(
                adapter.read_executor,
                "read_optional_busitem",
                MagicMock(side_effect=RuntimeError("sleeping")),
            )
            with self.assertLogs(level="DEBUG") as logs:
                outcome = adapter.read_executor.poll_read_spec(
                    "pv_power_w",
                    {
                        "aggregate": "pv-total",
                        "prefix": "com.victronenergy.pvinverter",
                        "path": "/Ac/Power",
                        "dc_service": "",
                        "dc_path": "",
                        "use_dc_pv": "false",
                        "optional_confidence": 0.7,
                    },
                )

            self.assertEqual(outcome, "applied")
            self.assertEqual(adapter.cache.values["pv_power_w"]["confidence"], 0.7)
            self.assertEqual(
                adapter.cache.values["pv_power_w"]["last_error"],
                "com.victronenergy.pvinverter.http_1/Ac/Power: sleeping",
            )
            entry = adapter.cache.values["path:com.victronenergy.pvinverter.http_1/Ac/Power"]
            self.assertEqual(entry["status"], "error")
            self.assertEqual(entry["last_error"], "sleeping")
            self.assertTrue(
                any(
                    "DBus adapter optional aggregate member failed com.victronenergy.pvinverter.http_1/Ac/Power"
                    in message
                    and "sleeping" in message
                    for message in logs.output
                )
            )

    def test_optional_aggregate_member_initializes_missing_error_list(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.rate_limiter.intervals["read"] = 0.0
            adapter.cache.update_services(["svc.optional"])
            install_mock(
                adapter.read_executor,
                "read_optional_busitem",
                MagicMock(side_effect=RuntimeError("sleeping")),
            )
            outcome = adapter.read_executor.poll_read_spec(
                "optional_sum",
                {
                    "aggregate": "pv-total",
                    "prefix": "svc.",
                    "path": "/Power",
                    "dc_service": "",
                    "dc_path": "",
                    "use_dc_pv": "false",
                },
            )

            self.assertEqual(outcome, "applied")
            self.assertEqual(adapter.cache.values["optional_sum"]["last_error"], "svc.optional/Power: sleeping")

    def test_optional_aggregate_member_skips_cache_for_invalid_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.rate_limiter.intervals["read"] = 0.0
            adapter.cache.update_services(["svc.optional"])
            install_mock(
                adapter.read_executor,
                "read_optional_busitem",
                MagicMock(side_effect=RuntimeError("bad path")),
            )
            outcome = adapter.read_executor.poll_read_spec(
                "optional_sum",
                {
                    "aggregate": "pv-total",
                    "prefix": "svc.",
                    "path": "NotAbsolute",
                    "dc_service": "",
                    "dc_path": "",
                    "use_dc_pv": "false",
                },
            )

            self.assertEqual(outcome, "applied")
            self.assertEqual(adapter.cache.values["optional_sum"]["last_error"], "svc.optionalNotAbsolute: bad path")
            self.assertNotIn("path:svc.optionalNotAbsolute", adapter.cache.values)

    def test_optional_aggregate_member_reraises_required_source_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            install_mock(
                adapter.read_executor,
                "read_busitem",
                MagicMock(side_effect=RuntimeError("required offline")),
            )

            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "required_sum",
                    {"aggregate": "sum", "service": "svc", "paths": ["/Path"]},
                ),
                "dropped",
            )
            self.assertEqual(adapter.cache.values["required_sum"]["last_error"], "required offline")

            install_mock(
                adapter.read_executor,
                "read_busitem",
                MagicMock(side_effect=DbusOperationDeferred("read")),
            )
            install_mock(
                adapter.read_executor,
                "read_optional_busitem",
                MagicMock(side_effect=DbusOperationDeferred("read")),
            )
            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "required_deferred",
                    {"aggregate": "sum", "service": "svc", "paths": ["/Path"]},
                ),
                "deferred",
            )

    def test_optional_busitem_returns_none_for_incomplete_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            self.assertIsNone(adapter.read_executor.read_optional_busitem("", "/Path"))
            self.assertIsNone(adapter.read_executor.read_optional_busitem("svc", ""))

    def test_first_service_read_uses_discovered_battery_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.cache.update_services(["com.victronenergy.battery.socketcan_can1"])
            install_mock(adapter.read_executor, "read_busitem", MagicMock(return_value=74.0))

            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "battery_soc",
                    {
                        "aggregate": "first-service",
                        "prefix": "com.victronenergy.battery",
                        "path": "/Soc",
                        "interval": 2.0,
                    },
                ),
                "applied",
            )
            adapter.read_executor.read_busitem.assert_called_once_with(
                "com.victronenergy.battery.socketcan_can1", "/Soc"
            )
            self.assertEqual(adapter.cache.values["battery_soc"]["value"], 74.0)
            member_key = "path:com.victronenergy.battery.socketcan_can1/Soc"
            self.assertEqual(adapter.cache.values[member_key]["value"], 74.0)
            self.assertEqual(adapter.cache.values[member_key]["status"], "fresh")
            self.assertEqual(adapter.cache.values[member_key]["freshness_kind"], "external_read")
            self.assertEqual(adapter.cache.values["battery_soc"]["stale_after_s"], 6.0)

    def test_read_executor_covers_refresh_sum_error_and_direct_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.rate_limiter.intervals["read"] = 0.0
            values = {
                ("svc", "/L1"): 1.5,
                ("svc", "/L2"): None,
                ("svc", "/Path"): 7,
            }
            setattr(adapter.read_executor, "read_busitem_now", lambda service, path: values.get((service, path), 0.0))
            setattr(adapter.read_executor, "read_busitem", lambda service, path: values.get((service, path), 0.0))

            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "sum", {"aggregate": "sum", "service": "svc", "paths": ["/L1", "/L2"]}
                ),
                "deferred",
            )
            adapter.rate_limiter.next_at["read"] = time.monotonic()
            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "sum", {"aggregate": "sum", "service": "svc", "paths": ["/L1", "/L2"]}
                ),
                "applied",
            )
            self.assertEqual(adapter.cache.values["sum"]["value"], 1.5)
            self.assertEqual(adapter.cache.values["path:svc/L1"]["value"], 1.5)
            self.assertEqual(adapter.cache.values["path:svc/L2"]["value"], None)
            adapter.cache.update_services(["pv.1"])
            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "pv", {"aggregate": "services-sum", "prefix": "pv.", "path": "/P"}
                ),
                "applied",
            )
            self.assertEqual(
                adapter.read_executor.poll_read_spec("direct", {"service": "svc", "path": "/Path"}), "applied"
            )
            self.assertEqual(adapter.cache.values["direct"]["value"], 7)
            self.assertEqual(adapter.cache.values["path:svc/Path"]["value"], 7)
            self.assertEqual(
                adapter.read_executor.poll_read_spec("invalid", {"service": "svc", "path": "Path"}), "dropped"
            )
            self.assertNotIn("path:svcPath", adapter.cache.values)
            self.assertEqual(
                adapter.read_executor.refresh_requested_value({"service": "svc", "path": "/Path"}), "applied"
            )
            self.assertEqual(adapter.read_executor.refresh_requested_value({"key": "grid_power_w"}), "deferred")
            self.assertEqual(adapter.read_executor.refresh_requested_value({}), "dropped")

            install_mock(
                adapter.read_executor,
                "read_busitem",
                MagicMock(side_effect=RuntimeError("read failed")),
            )
            self.assertEqual(
                adapter.read_executor.refresh_requested_value({"service": "svc", "path": "/Broken"}), "dropped"
            )
            self.assertEqual(adapter.cache.values["path:svc/Broken"]["status"], "error")
            refresh_path = adapter.commands.enqueue(
                {
                    "kind": "refresh_value",
                    "service": "svc",
                    "path": "/Broken",
                    "priority": "read",
                    "coalesce_key": "refresh:svc:/Broken",
                }
            )
            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertFalse(Path(refresh_path).exists())
            self.assertEqual(adapter.read_executor.poll_read_spec("bad", {"service": "svc", "path": "/Bad"}), "dropped")
            self.assertEqual(adapter.cache.values["bad"]["status"], "error")
            install_mock(
                adapter.read_executor,
                "read_busitem",
                MagicMock(side_effect=DbusOperationDeferred("read")),
            )
            self.assertEqual(
                adapter.read_executor.refresh_requested_value({"service": "svc", "path": "/Deferred"}), "deferred"
            )
            deferred_refresh_path = adapter.commands.enqueue(
                {
                    "kind": "refresh_value",
                    "service": "svc",
                    "path": "/Deferred",
                    "priority": "read",
                    "coalesce_key": "refresh:svc:/Deferred",
                }
            )
            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertTrue(Path(deferred_refresh_path).exists())
            self.assertEqual(
                adapter.read_executor.poll_read_spec("later", {"service": "svc", "path": "/Later"}), "deferred"
            )
            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "empty",
                    {"aggregate": "sum", "service": "svc", "paths": [], "interval": 2.0},
                ),
                "applied",
            )
            self.assertEqual(adapter.cache.values["empty"]["stale_after_s"], 6.0)
            self.assertEqual(adapter.cache.values["empty"]["freshness_kind"], "external_read")
            self.assertEqual(adapter.cache.values["empty"]["value"], 0.0)
            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "battery_missing",
                    {"aggregate": "first-service", "prefix": "missing.", "path": "/Soc"},
                ),
                "dropped",
            )

    def test_read_executor_direct_dbus_busitem_uses_timed_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            fake_iface = BusItemInterfaceStub(4.0)
            fake_bus = DbusBusStub()
            install_mock(adapter.connection, "bus", MagicMock(return_value=fake_bus))
            with patch.object(read_module.dbus, "Interface", return_value=fake_iface):
                self.assertEqual(adapter.read_executor.read_busitem("svc", "/P"), 4.0)
            self.assertEqual(fake_bus.get_object_calls, [("svc", "/P", False)])
            self.assertEqual(fake_iface.get_calls, [1.0])
            self.assertIsNone(adapter.read_executor.read_busitem("", "/P"))
            self.assertIsNone(adapter.read_executor.read_busitem("svc", ""))

            adapter.cache.update_services([])
            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "missing", {"aggregate": "services-sum", "prefix": "missing.", "path": "/P"}
                ),
                "dropped",
            )
            self.assertEqual(adapter.cache.values["missing"]["status"], "error")
            install_mock(adapter.read_executor, "read_busitem_now", MagicMock(return_value=None))
            adapter.rate_limiter.next_at["read"] = time.monotonic()
            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "explicit", {"aggregate": "services-sum", "service": "explicit", "path": "/P"}
                ),
                "applied",
            )
            self.assertEqual(adapter.cache.values["explicit"]["value"], 0.0)
