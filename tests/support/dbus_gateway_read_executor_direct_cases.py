# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter direct and optional read executor contracts."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    BusItemInterfaceStub,
    DbusAdapter,
    DbusOperationDeferred,
    GatewayAdapterContractCase,
    MagicMock,
    Path,
    gateway_paths,
    install_mock,
    introspection_module,
    patch,
    read_module,
    tempfile,
    unittest,
)
from venus_evcharger.ipc.energy import EnergyRefreshRequest


class GatewayReadExecutorDirectCases(GatewayAdapterContractCase):
    """Exercise direct and optional read executor contracts."""

    def test_read_executor_drops_invalid_first_service_path_without_dbus_read(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.cache.update_services(["svc.first"])
            adapter.energy_discovery.update_services(["svc.first"], captured_at=1.0)
            read_busitem = install_mock(adapter.read_executor, "read_busitem", MagicMock(return_value=12.0))

            outcome = adapter.read_executor.poll_read_spec(
                "first_value",
                {"aggregate": "first-service", "prefix": "svc.", "path": "NotAbsolute"},
            )

            self.assertEqual(outcome, "dropped")
            read_busitem.assert_not_called()
            self.assertNotIn("first_value", adapter.cache.values)

    def test_read_executor_handles_invalid_aggregate_member_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            install_mock(adapter.read_executor, "read_busitem", MagicMock(return_value=5.0))

            outcome = adapter.read_executor.poll_read_spec(
                "bad_sum",
                {"aggregate": "sum", "service": "svc.aggregate", "paths": ["NotAbsolute"]},
            )

            self.assertEqual(outcome, "applied")
            self.assertEqual(adapter.cache.values["bad_sum"]["value"], 5.0)
            self.assertNotIn("path:svc.aggregateNotAbsolute", adapter.cache.values)

    def test_read_executor_records_optional_invalid_aggregate_errors_only_on_semantic_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            install_mock(
                adapter.read_executor,
                "read_busitem",
                MagicMock(side_effect=RuntimeError("no reply")),
            )

            outcome = adapter.read_executor.poll_read_spec(
                "pv_power_w",
                {
                    "aggregate": "pv-total",
                    "use_dc_pv": "yes",
                    "dc_service": "com.victronenergy.system",
                    "dc_path": "NotAbsolute",
                    "optional_zero_on_error": "yes",
                    "optional_confidence": 0.25,
                },
            )

            self.assertEqual(outcome, "applied")
            self.assertEqual(adapter.cache.values["pv_power_w"]["value"], 0.0)
            self.assertEqual(adapter.cache.values["pv_power_w"]["confidence"], 0.25)
            self.assertIn(
                "No available AC or DC PV source candidates", adapter.cache.values["pv_power_w"]["last_error"]
            )
            self.assertNotIn("path:com.victronenergy.systemNotAbsolute", adapter.cache.values)

    def test_read_executor_direct_path_key_updates_only_one_cache_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            install_mock(adapter.read_executor, "read_busitem", MagicMock(return_value=11.0))

            outcome = adapter.read_executor.poll_read_spec(
                "path:svc.direct/Value",
                {"service": "svc.direct", "path": "/Value"},
            )

            self.assertEqual(outcome, "applied")
            self.assertEqual(adapter.cache.values["path:svc.direct/Value"]["value"], 11.0)
            self.assertEqual(list(adapter.cache.values), ["path:svc.direct/Value"])

    def test_read_executor_direct_and_semantic_refresh_contracts_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            self.assertIs(adapter.read_executor.adapter, adapter)
            self.assertFalse(adapter.read_executor.has_pending_aggregate())
            self.assertIs(adapter.read_executor.last_operation_performed, False)

            install_mock(adapter.read_executor, "read_busitem", MagicMock(return_value=12.5))
            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "direct_value",
                    {"service": "svc.direct", "path": "/Value"},
                ),
                "applied",
            )
            adapter.read_executor.read_busitem.assert_called_once_with("svc.direct", "/Value")
            entry = adapter.cache.values["path:svc.direct/Value"]
            self.assertEqual(entry["value"], 12.5)
            self.assertEqual(entry["source"], "svc.direct/Value")
            self.assertEqual(entry["status"], "fresh")
            self.assertEqual(entry["freshness_kind"], "external_read")

            force_due = install_mock(adapter.read_scheduler, "force_due", MagicMock())
            refresh = EnergyRefreshRequest(
                request_id="grid-refresh",
                scope="grid",
                max_age_seconds=0.0,
                urgency="priority",
                reason="test-stale-grid",
            )
            self.assertEqual(adapter.process_non_write_command(refresh.to_command(source="test")), "applied")
            force_due.assert_called_once_with(("grid_power_w",))
            adapter.read_executor.read_busitem.assert_called_once_with("svc.direct", "/Value")
            self.assertEqual(
                adapter.process_non_write_command(
                    {
                        "kind": "refresh_value",
                        "key": "grid_power_w",
                        "service": "svc.direct",
                        "path": "/Value",
                    }
                ),
                "dropped",
            )

            adapter.read_executor.last_operation_performed = True
            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "direct_value",
                    {"service": "svc.direct", "path": "/Direct", "interval": 2.0},
                ),
                "applied",
            )
            self.assertIs(adapter.read_executor.last_operation_performed, False)
            self.assertEqual(adapter.cache.values["direct_value"]["stale_after_s"], 6.0)
            self.assertEqual(adapter.cache.values["direct_value"]["freshness_kind"], "external_read")

            error = RuntimeError("offline")
            install_mock(adapter.read_executor, "read_busitem", MagicMock(side_effect=error))
            with patch.object(read_module.logging, "debug") as log_debug:
                self.assertEqual(
                    adapter.read_executor.poll_read_spec(
                        "broken_value",
                        {"service": "svc.direct", "path": "/Broken"},
                    ),
                    "dropped",
                )
            failed = adapter.cache.values["broken_value"]
            self.assertEqual(failed["source"], "svc.direct")
            self.assertEqual(failed["status"], "error")
            self.assertEqual(failed["last_error"], "offline")
            self.assertEqual(failed["confidence"], 0.0)
            log_debug.assert_called_once_with(
                "DBus adapter read failed key=%s: %s",
                "broken_value",
                error,
            )

            install_mock(
                adapter.read_executor,
                "read_busitem",
                MagicMock(side_effect=DbusOperationDeferred("wait")),
            )
            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "deferred_value",
                    {"service": "svc.direct", "path": "/Deferred"},
                ),
                "deferred",
            )
            self.assertNotIn("deferred_value", adapter.cache.values)

    def test_semantic_refresh_uses_cache_age_without_an_extra_dbus_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.cache.update_external_read("grid_power_w", 123.0, source="system", now=20.0)
            force_due = install_mock(adapter.read_scheduler, "force_due", MagicMock())
            read = install_mock(adapter.read_executor, "poll_read_spec", MagicMock())
            fresh_request = EnergyRefreshRequest("fresh-grid", "grid", 10.0)
            stale_request = EnergyRefreshRequest("stale-grid", "grid", 1.0)

            with patch.object(introspection_module.time, "time", return_value=25.0):
                self.assertEqual(
                    adapter.process_non_write_command(fresh_request.to_command(source="test")),
                    "applied",
                )
                self.assertEqual(
                    adapter.process_non_write_command(stale_request.to_command(source="test")),
                    "applied",
                )

            self.assertEqual(force_due.call_args_list, [unittest.mock.call(()), unittest.mock.call(("grid_power_w",))])
            read.assert_not_called()

    def test_semantic_source_refresh_rejects_unknown_source_and_never_accepts_raw_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.energy_discovery.update_services(["com.victronenergy.battery.test"], captured_at=10.0)
            source_id = adapter.energy_discovery.source_ids("battery")[0]
            force_due = install_mock(adapter.read_scheduler, "force_due", MagicMock())
            request = EnergyRefreshRequest("battery-source", "energy_source", 0.0, source_id=source_id)

            self.assertEqual(adapter.process_non_write_command(request.to_command(source="test")), "applied")
            force_due.assert_called_once_with(("battery_soc",))
            unknown = EnergyRefreshRequest("missing-source", "energy_source", 0.0, source_id="unknown")
            self.assertEqual(adapter.process_non_write_command(unknown.to_command(source="test")), "dropped")
            self.assertEqual(
                adapter.process_non_write_command(
                    {
                        **request.to_command(source="test"),
                        "service": "svc.direct",
                        "path": "/Value",
                    }
                ),
                "dropped",
            )

    def test_read_executor_optional_and_low_level_dbus_contracts_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            install_mock(adapter.rate_limiter, "require_due", MagicMock())
            install_mock(adapter.circuit, "record_success", MagicMock())
            install_mock(adapter.read_executor, "read_busitem_now", MagicMock(return_value="8.5"))
            with patch.object(read_module.time, "monotonic", side_effect=[10.0, 10.25]):
                self.assertEqual(adapter.read_executor.read_optional_busitem("svc.optional", "/Power"), "8.5")
            adapter.rate_limiter.require_due.assert_called_once_with("read")
            adapter.read_executor.read_busitem_now.assert_called_once_with("svc.optional", "/Power")
            adapter.circuit.record_success.assert_called_once_with(250.0, kind="optional_read")

            low_level_adapter = DbusAdapter(
                str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-low-level"))
            )
            fake_iface = BusItemInterfaceStub("4.25")
            fake_obj = object()
            get_object = install_mock(low_level_adapter.connection, "get_object", MagicMock(return_value=fake_obj))
            with patch.object(read_module.dbus, "Interface", return_value=fake_iface) as interface:
                self.assertEqual(low_level_adapter.read_executor.read_busitem_now("svc.low", "/P"), 4.25)
            get_object.assert_called_once_with("svc.low", "/P", introspect=False)
            interface.assert_called_once_with(fake_obj, "com.victronenergy.BusItem")
            self.assertEqual(fake_iface.get_calls, [1.0])

    def test_read_executor_aggregate_contracts_preserve_members_and_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            self.assertEqual(
                adapter.read_executor._services_for_sum({"service": "svc.explicit", "prefix": "ignored."}),
                ["svc.explicit"],
            )
            adapter.cache.update_services(["svc.2", "other.1", "svc.1"])
            adapter.energy_discovery.update_services(["svc.2", "other.1", "svc.1"], captured_at=1.0)
            self.assertEqual(adapter.read_executor._services_for_sum({"prefix": "svc."}), ["svc.1", "svc.2"])

            install_mock(adapter.read_executor, "read_busitem", MagicMock(side_effect=[2.0, 3.5]))
            spec = {"aggregate": "sum", "service": "svc.sum", "paths": ["/L1", "/L2"], "optional_confidence": 0.75}

            self.assertEqual(adapter.read_executor.poll_read_spec("sum_power", spec), "deferred")
            self.assertTrue(adapter.read_executor.last_operation_performed)
            self.assertTrue(adapter.read_executor.has_pending_aggregate())
            self.assertEqual(adapter.cache.values["path:svc.sum/L1"]["value"], 2.0)
            self.assertEqual(adapter.cache.values["path:svc.sum/L1"]["source"], "svc.sum/L1")

            self.assertEqual(adapter.read_executor.poll_read_spec("sum_power", spec), "applied")
            self.assertFalse(adapter.read_executor.has_pending_aggregate())
            adapter.read_executor.read_busitem.assert_has_calls(
                [unittest.mock.call("svc.sum", "/L1"), unittest.mock.call("svc.sum", "/L2")]
            )
            payload = adapter.cache.values["sum_power"]
            self.assertEqual(payload["value"], 5.5)
            self.assertEqual(payload["source"], "svc.sum/L1,svc.sum/L2")
            self.assertEqual(payload["confidence"], 1.0)
            self.assertEqual(payload["last_error"], "")

    def test_read_executor_error_contracts_keep_cache_source_logs_and_pending_state_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.read_executor._aggregates.state_for(
                "required_value",
                ("sum", (("svc.old", "/L1"),)),
                0.4,
            )
            error = RuntimeError("required offline")

            with (
                patch.object(adapter.cache, "mark_error") as mark_error,
                patch.object(read_module.logging, "debug") as log_debug,
            ):
                adapter.read_executor._mark_read_error(
                    "required_value",
                    {"service": "svc.required", "path": "/Power"},
                    error,
                )

            self.assertFalse(adapter.read_executor.has_pending_aggregate())
            mark_error.assert_called_once_with(
                "required_value",
                source="svc.required",
                error=error,
            )
            log_debug.assert_called_once_with(
                "DBus adapter read failed key=%s: %s",
                "required_value",
                error,
            )

    def test_read_executor_optional_zero_contract_keeps_fallback_confidence_and_diagnostics_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.read_executor._aggregates.state_for(
                "optional_value",
                ("services-sum", "/Power", ("svc.old",)),
                0.4,
            )
            error = RuntimeError("optional offline")

            with (
                patch.object(adapter.cache, "update_value") as update_value,
                patch.object(read_module.logging, "debug") as log_debug,
            ):
                adapter.read_executor._mark_optional_zero(
                    "optional_value",
                    {"optional_confidence": 0.55},
                    error,
                )

            self.assertFalse(adapter.read_executor.has_pending_aggregate())
            update_value.assert_called_once_with(
                "optional_value",
                0.0,
                source="optional_value",
                confidence=0.55,
                last_error="optional offline",
                freshness_kind="external_read",
                stale_after_seconds=None,
            )
            log_debug.assert_called_once_with(
                "DBus adapter optional read fell back to zero key=%s: %s",
                "optional_value",
                error,
            )

    def test_read_executor_optional_helpers_have_explicit_defaults_and_sources(self) -> None:
        self.assertEqual(read_module._spec_text({}, "service"), "")
        self.assertEqual(read_module._spec_text({"service": None}, "service"), "")
        self.assertEqual(read_module._spec_text({"service": 42}, "service"), "")
        self.assertEqual(read_module._spec_text({"service": ""}, "service"), "")
        self.assertEqual(read_module._spec_text({"service": "svc"}, "service"), "svc")
        self.assertTrue(read_module.DbusReadExecutor._optional_zero_on_error({"optional_zero_on_error": "TRUE"}))
        self.assertTrue(read_module.DbusReadExecutor._optional_zero_on_error({"optional_zero_on_error": " yes "}))
        self.assertTrue(read_module.DbusReadExecutor._optional_zero_on_error({"optional_zero_on_error": "on"}))
        self.assertFalse(read_module.DbusReadExecutor._optional_zero_on_error({}))
        self.assertFalse(
            read_module.DbusReadExecutor._optional_zero_on_error({"optional_zero_on_error": "TRUE " + "x"})
        )
        self.assertEqual(read_module.DbusReadExecutor._optional_confidence({}), 0.2)
        self.assertEqual(read_module.DbusReadExecutor._optional_confidence({"optional_confidence": None}), 0.2)
        self.assertEqual(read_module.DbusReadExecutor._optional_confidence({"optional_confidence": 0.0}), 0.2)
        self.assertEqual(read_module.DbusReadExecutor._optional_confidence({"optional_confidence": 0.45}), 0.45)
        self.assertIsNone(read_module._spec_stale_after_seconds({}))
        self.assertEqual(read_module._spec_stale_after_seconds({"interval": 2.0}), 6.0)
        self.assertEqual(read_module._spec_stale_after_seconds({"interval": 0.25}), 1.0)
        self.assertEqual(read_module._spec_stale_after_seconds({"stale_after_seconds": -1.0}), 0.0)
        self.assertEqual(
            read_module.DbusReadExecutor._spec_source({"service": "svc", "prefix": "ignored"}, fallback="fb"), "svc"
        )
        self.assertEqual(
            read_module.DbusReadExecutor._spec_source({"service": "", "prefix": "pv."}, fallback="fb"), "pv."
        )
        self.assertEqual(read_module.DbusReadExecutor._spec_source({}, fallback="fb"), "fb")
        self.assertEqual(read_module.DbusReadExecutor._spec_source({}), "")

    def test_read_executor_ttl_lifecycle_is_keyed_and_preserved_while_deferred(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            spec = {"service": "svc", "path": "/Power", "interval": 2.0}

            with patch.object(
                adapter.read_executor,
                "_poll_read_spec_unchecked",
                side_effect=["deferred", "applied"],
            ):
                self.assertEqual(adapter.read_executor.poll_read_spec("power", spec), "deferred")
                self.assertEqual(adapter.read_executor._stale_after_by_key, {"power": 6.0})
                self.assertEqual(adapter.read_executor.poll_read_spec("power", spec), "applied")
                self.assertEqual(adapter.read_executor._stale_after_by_key, {})

            adapter.read_executor._stale_after_by_key = {"power": 6.0, "other": 9.0}
            with patch.object(adapter.cache, "mark_error") as mark_error:
                adapter.read_executor._mark_read_error("power", spec, RuntimeError("offline"))
            self.assertEqual(adapter.read_executor._stale_after_by_key, {"other": 9.0})
            mark_error.assert_called_once()

            adapter.read_executor._stale_after_by_key = {"power": 6.0, "other": 9.0}
            with patch.object(adapter.cache, "update_value") as update_value:
                adapter.read_executor._mark_optional_zero("power", spec, RuntimeError("offline"))
            self.assertEqual(adapter.read_executor._stale_after_by_key, {"other": 9.0})
            update_value.assert_called_once_with(
                "power",
                0.0,
                source="svc",
                confidence=0.2,
                last_error="offline",
                freshness_kind="external_read",
                stale_after_seconds=6.0,
            )

    def test_read_executor_aggregate_dispatch_contracts_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            aggregate = install_mock(adapter.read_executor, "_poll_aggregate_step", MagicMock(return_value="deferred"))
            self.assertEqual(
                adapter.read_executor._poll_sum_step(
                    "sum_power",
                    {"aggregate": "sum", "service": "svc.sum", "paths": ["/L1", "/L2"]},
                ),
                "deferred",
            )
            aggregate.assert_called_once_with(
                "sum_power",
                ("sum", (("svc.sum", "/L1"), ("svc.sum", "/L2"))),
                [("svc.sum", "/L1"), ("svc.sum", "/L2")],
            )

            with patch.object(adapter.cache, "update_value") as update_value:
                self.assertEqual(
                    adapter.read_executor._poll_sum_step("empty_sum", {"aggregate": "sum", "service": "svc.empty"}),
                    "applied",
                )
            update_value.assert_called_once_with(
                "empty_sum",
                0.0,
                source="svc.empty",
                freshness_kind="external_read",
                stale_after_seconds=None,
            )

            aggregate.reset_mock()
            with patch.object(adapter.cache, "update_value") as update_value:
                self.assertEqual(
                    adapter.read_executor._poll_sum_step(
                        "empty_path_sum",
                        {"aggregate": "sum", "service": "svc.empty", "paths": [""]},
                    ),
                    "applied",
                )
                aggregate.assert_not_called()
                update_value.assert_called_once_with(
                    "empty_path_sum",
                    0.0,
                    source="svc.empty",
                    freshness_kind="external_read",
                    stale_after_seconds=None,
                )

            adapter.cache.update_services(["pv.2", "other.1", "pv.1"])
            adapter.energy_discovery.update_services(["pv.2", "other.1", "pv.1"], captured_at=1.0)
            aggregate.reset_mock()
            self.assertEqual(
                adapter.read_executor._poll_services_sum_step(
                    "pv_sum",
                    {"aggregate": "services-sum", "prefix": "pv.", "path": "/Ac/Power"},
                ),
                "deferred",
            )
            aggregate.assert_called_once_with(
                "pv_sum",
                ("services-sum", "/Ac/Power", ("pv.1", "pv.2")),
                [("pv.1", "/Ac/Power"), ("pv.2", "/Ac/Power")],
            )

            with self.assertRaisesRegex(RuntimeError, "No cached services for prefix 'missing\\.'"):
                adapter.read_executor._poll_services_sum_step(
                    "missing_sum",
                    {"aggregate": "services-sum", "prefix": "missing.", "path": "/Ac/Power"},
                )
            with self.assertRaisesRegex(RuntimeError, "No cached services for prefix ''"):
                empty_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-empty")))
                empty_adapter.read_executor._poll_services_sum_step(
                    "missing_default_sum",
                    {"aggregate": "services-sum", "path": "/Ac/Power"},
                )
