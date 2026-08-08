# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter aggregate member and low-level read scenarios."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    DbusAdapter,
    DbusOperationDeferred,
    GatewayAdapterContractCase,
    MagicMock,
    Path,
    gateway_paths,
    install_mock,
    install_read_responder,
    read_aggregate_module,
    read_pv_module,
    run_non_write_command,
    tempfile,
    time,
)
from venus_evcharger.ipc.energy import EnergyRefreshRequest
from venus_evcharger.dbus_adapter.async_request import DbusWireRequest


class GatewayAggregateReadCases(GatewayAdapterContractCase):
    """Exercise aggregate member and low-level read scenarios."""

    def test_pv_member_contracts_cover_ac_dc_tokens_and_targets(self) -> None:
        valid_dc_spec = {
            "dc_service": " com.victronenergy.system ",
            "dc_path": " /Dc/Pv/Power ",
            "use_dc_pv": True,
        }
        self.assertEqual(read_pv_module.dc_pv_target(valid_dc_spec), ("com.victronenergy.system", "/Dc/Pv/Power"))
        self.assertEqual(
            read_pv_module.dc_pv_members(valid_dc_spec),
            [("com.victronenergy.system", "/Dc/Pv/Power")],
        )
        self.assertEqual(
            read_pv_module.pv_total_members(
                {
                    **valid_dc_spec,
                    "path": "/Ac/Power",
                },
                ["pv.b", "pv.a"],
            ),
            [
                ("pv.b", "/Ac/Power"),
                ("pv.a", "/Ac/Power"),
                ("com.victronenergy.system", "/Dc/Pv/Power"),
            ],
        )
        self.assertTrue(read_pv_module.use_dc_pv({"use_dc_pv": True}))
        for raw in ("", "1", "true", "yes", "on", 1, False, object()):
            with self.subTest(raw=raw):
                self.assertFalse(read_pv_module.use_dc_pv({"use_dc_pv": raw}))
        self.assertFalse(read_pv_module.use_dc_pv({}))
        self.assertIsNone(read_pv_module.dc_pv_target({"dc_service": "", "dc_path": "/Dc/Pv/Power"}))
        self.assertIsNone(read_pv_module.dc_pv_target({"dc_service": object(), "dc_path": "/Dc/Pv/Power"}))
        self.assertIsNone(read_pv_module.dc_pv_target({"dc_service": "svc", "dc_path": "Dc/Pv/Power"}))
        self.assertIsNone(read_pv_module.dc_pv_target({"dc_service": "svc", "dc_path": object()}))
        self.assertIsNone(read_pv_module.dc_pv_target({"dc_service": "svc"}))

    def test_pv_total_optional_member_errors_are_preserved_and_nonfatal(self) -> None:
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

            def fake_read(service: str, path: str) -> float:
                if service == "com.victronenergy.pvinverter.http_1":
                    raise RuntimeError("ac asleep")
                return 70.0

            install_read_responder(adapter, fake_read)

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
            adapter.energy_discovery.update_services(["com.victronenergy.pvinverter.http_1"], captured_at=1.0)
            install_read_responder(
                adapter,
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
                        "use_dc_pv": False,
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
            self.assertEqual(entry["status"], "unavailable")
            self.assertEqual(entry["source_state"], "unavailable")
            self.assertEqual(entry["last_error"], "sleeping")
            self.assertGreater(entry["next_probe_at"], entry["error_at"])
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
            service = "com.victronenergy.pvinverter.optional"
            adapter.cache.update_services([service])
            adapter.energy_discovery.update_services([service], captured_at=1.0)
            install_read_responder(
                adapter,
                MagicMock(side_effect=RuntimeError("sleeping")),
            )
            outcome = adapter.read_executor.poll_read_spec(
                "optional_sum",
                {
                    "aggregate": "pv-total",
                    "prefix": "com.victronenergy.pvinverter.",
                    "path": "/Ac/Power",
                    "dc_service": "",
                    "dc_path": "",
                    "use_dc_pv": False,
                },
            )

            self.assertEqual(outcome, "applied")
            self.assertEqual(
                adapter.cache.values["optional_sum"]["last_error"],
                f"{service}/Ac/Power: sleeping",
            )

    def test_optional_aggregate_member_skips_cache_for_invalid_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.rate_limiter.intervals["read"] = 0.0
            install_read_responder(
                adapter,
                MagicMock(side_effect=RuntimeError("bad path")),
            )
            completed: list[str] = []
            adapter.read_executor._poll_aggregate_step(
                read_aggregate_module.AggregateStepPlan(
                    key="optional_sum",
                    signature=("pv-total", (("svc.optional", "NotAbsolute"),)),
                    members=(("svc.optional", "NotAbsolute"),),
                    completion=completed.append,
                    ignore_member_errors=True,
                )
            )

            self.assertEqual(completed, ["applied"])
            self.assertEqual(adapter.cache.values["optional_sum"]["last_error"], "svc.optionalNotAbsolute: bad path")
            self.assertNotIn("path:svc.optionalNotAbsolute", adapter.cache.values)

    def test_optional_aggregate_member_reraises_required_source_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            install_read_responder(
                adapter,
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

            install_read_responder(
                adapter,
                MagicMock(side_effect=DbusOperationDeferred("read")),
            )
            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "required_deferred",
                    {"aggregate": "sum", "service": "svc", "paths": ["/Path"]},
                ),
                "deferred",
            )

    def test_invalid_direct_target_is_dropped_without_transport(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            submit = install_mock(adapter.read_executor, "_submit_busitem", MagicMock())
            self.assertEqual(
                adapter.read_executor.poll_read_spec("invalid", {"service": "", "path": "/Path"}),
                "dropped",
            )
            submit.assert_not_called()

    def test_slow_optional_member_extends_only_its_aggregate_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(
                str(config_path),
                paths=gateway_paths(str(Path(temp_dir) / "run")),
            )
            service = "com.victronenergy.pvinverter.optional"
            adapter.cache.update_services([service])
            adapter.energy_discovery.update_services([service], captured_at=1.0)
            optional_read = install_read_responder(
                adapter,
                MagicMock(return_value=25.0),
            )
            interval_factor = install_mock(
                adapter.circuit,
                "optional_source_interval_factor",
                MagicMock(return_value=3.0),
            )

            outcome = adapter.read_executor.poll_read_spec(
                "optional",
                {
                    "aggregate": "pv-total",
                    "prefix": "com.victronenergy.pvinverter",
                    "path": "/Power",
                    "dc_service": "",
                    "dc_path": "",
                    "use_dc_pv": False,
                },
            )

            self.assertEqual(outcome, "applied")
            self.assertTrue(adapter.read_executor.last_operation_performed)
            optional_read.assert_called_once()
            self.assertEqual(optional_read.call_args.args, (service, "/Ac/Power"))
            interval_factor.assert_called_once_with(f"{service}/Ac/Power")
            self.assertEqual(
                adapter.read_executor.consume_interval_factor("optional"),
                3.0,
            )
            self.assertEqual(
                adapter.read_executor.consume_interval_factor("optional"),
                1.0,
            )
            adapter.read_executor._interval_factors["clamped"] = 0.5
            self.assertEqual(
                adapter.read_executor.consume_interval_factor("clamped"),
                1.0,
            )

    def test_first_service_read_uses_discovered_battery_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.cache.update_services(["com.victronenergy.battery.socketcan_can1"])
            adapter.energy_discovery.update_services(["com.victronenergy.battery.socketcan_can1"], captured_at=1.0)
            read = install_read_responder(
                adapter,
                MagicMock(return_value=74.0),
            )

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
            self.assertEqual(read.call_args.args, ("com.victronenergy.battery.socketcan_can1", "/Soc"))
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
            install_read_responder(
                adapter,
                lambda service, path: values.get((service, path), 0.0),
            )

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
            adapter.energy_discovery.update_services(["pv.1"], captured_at=1.0)
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
            force_due = install_mock(adapter.read_scheduler, "force_due", MagicMock())
            grid_refresh = EnergyRefreshRequest("grid-refresh", "grid", 0.0, urgency="priority")
            self.assertEqual(
                run_non_write_command(adapter, grid_refresh.to_command(source="test")),
                "applied",
            )
            force_due.assert_called_once_with(("grid_power_w",))
            self.assertEqual(run_non_write_command(adapter, {}), "dropped")

            install_read_responder(
                adapter,
                MagicMock(side_effect=RuntimeError("read failed")),
            )
            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "broken",
                    {"service": "svc", "path": "/Broken"},
                ),
                "dropped",
            )
            self.assertEqual(adapter.cache.values["broken"]["status"], "error")
            refresh_path = adapter.commands.enqueue(grid_refresh.to_command(source="test"))
            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertFalse(Path(refresh_path).exists())
            self.assertEqual(adapter.read_executor.poll_read_spec("bad", {"service": "svc", "path": "/Bad"}), "dropped")
            self.assertEqual(adapter.cache.values["bad"]["status"], "error")
            install_read_responder(
                adapter,
                MagicMock(side_effect=DbusOperationDeferred("read")),
            )
            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "deferred",
                    {"service": "svc", "path": "/Deferred"},
                ),
                "deferred",
            )
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

    def test_read_executor_direct_dbus_busitem_uses_async_broker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            pending = object()

            def complete_get_value(
                request: DbusWireRequest,
                reply_handler: object,
                error_handler: object,
            ) -> object:
                self.assertEqual(
                    request,
                    DbusWireRequest(
                        service="svc.test",
                        path="/P",
                        interface="com.victronenergy.BusItem",
                        method_name="GetValue",
                        signature="",
                        timeout_seconds=1.0,
                    ),
                )
                assert callable(reply_handler)
                assert callable(error_handler)
                reply_handler(4.0)
                return pending

            send_async = install_mock(
                adapter.connection,
                "send_async",
                MagicMock(side_effect=complete_get_value),
            )
            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "direct",
                    {"service": "svc.test", "path": "/P"},
                ),
                "applied",
            )
            send_async.assert_called_once()
            source_health = adapter.circuit.health()["operation_sources"]
            self.assertIn("svc.test/P", source_health["read"])

            adapter.cache.update_services([])
            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "missing", {"aggregate": "services-sum", "prefix": "missing.", "path": "/P"}
                ),
                "dropped",
            )
            self.assertEqual(adapter.cache.values["missing"]["status"], "error")
            install_read_responder(adapter, MagicMock(return_value=None))
            adapter.rate_limiter.next_at["read"] = time.monotonic()
            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "explicit", {"aggregate": "services-sum", "service": "explicit", "path": "/P"}
                ),
                "applied",
            )
            self.assertEqual(adapter.cache.values["explicit"]["value"], 0.0)
