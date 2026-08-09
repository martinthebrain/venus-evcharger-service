# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter introspection execution and snapshot contracts."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    DbusAdapter,
    DbusOperationDeferred,
    GatewayAdapterContractCase,
    MagicMock,
    Path,
    gateway_paths,
    dbus_wire_call,
    install_dbus_call_responder,
    install_mock,
    introspection_snapshot_module,
    json,
    patch,
    process_io_module,
    run_non_write_command,
    tempfile,
)
from venus_evcharger.ipc.energy import EnergyRefreshRequest


class GatewayIntrospectionExecutionCases(GatewayAdapterContractCase):
    """Exercise introspection execution and snapshot contracts."""

    def test_gateway_non_write_introspection_command_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            self.assertEqual(run_non_write_command(adapter, {"kind": "introspect"}), "dropped")

            force_reads = install_mock(adapter.read_scheduler, "force_due", MagicMock())
            force_discovery = install_mock(adapter.discovery, "force_due", MagicMock())
            refresh = EnergyRefreshRequest(
                request_id="all-inputs",
                scope="all",
                max_age_seconds=0.0,
                urgency="priority",
                reason="test-refresh",
            )
            self.assertEqual(
                run_non_write_command(adapter, refresh.to_command(source="test")),
                "applied",
            )
            force_reads.assert_called_once_with(
                ("grid_power_w", "pv_power_w", "battery_soc", "battery_net_power_w")
            )
            force_discovery.assert_called_once_with()
            force_reads.reset_mock()
            battery_refresh = EnergyRefreshRequest("battery", "battery", 0.0)
            self.assertEqual(
                run_non_write_command(adapter, battery_refresh.to_command(source="test")),
                "applied",
            )
            force_reads.assert_called_once_with(("battery_soc", "battery_net_power_w"))
            self.assertEqual(
                run_non_write_command(
                    adapter, {"type": "refresh_value", "key": "grid_power_w", "service": "svc", "path": "/P"}
                ),
                "dropped",
            )

            service_responder = MagicMock(return_value=["svc1", "svc2"])
            send_async = install_dbus_call_responder(
                adapter.connection,
                service_responder,
            )
            expected_call = (
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "ListNames",
                "",
                (),
            )
            discovery_due = install_mock(adapter.discovery, "due", MagicMock(return_value=True))
            record_success = install_mock(adapter.discovery, "record_success", MagicMock())
            needs_early_rescan = install_mock(
                adapter.energy_discovery,
                "needs_early_pv_rescan",
                MagicMock(return_value=False),
            )
            with (
                patch.object(process_io_module.time, "time", return_value=100.0),
                patch.object(
                    process_io_module.time,
                    "monotonic",
                    return_value=10.0,
                ),
            ):
                self.assertTrue(adapter.io_role.refresh_services_if_due_once())
            self.assertEqual(dbus_wire_call(send_async.call_args.args[0]), expected_call)
            self.assertEqual(send_async.call_args.args[0].timeout_seconds, 1.0)
            self.assertEqual(sorted(adapter.cache.services), ["svc1", "svc2"])
            self.assertEqual(adapter.energy_discovery.topology_snapshot(captured_at=100.0).generation, 1)
            needs_early_rescan.assert_called_once_with()
            record_success.assert_called_once_with(
                monotonic_at=10.0,
                captured_at=100.0,
                needs_early_rescan=False,
            )
            discovery_due.assert_called_once_with(
                monotonic_at=10.0,
                priority_allowed=adapter.circuit.allows_priority,
            )

            record_success.reset_mock()
            discovery_due.return_value = False
            self.assertFalse(adapter.io_role.refresh_services_if_due_once())
            self.assertEqual(send_async.call_count, 1)
            record_success.assert_not_called()

            discovery_due.return_value = True
            with patch.object(
                adapter.operation_broker,
                "submit",
                side_effect=DbusOperationDeferred("read"),
            ):
                self.assertFalse(adapter.io_role.refresh_services_if_due_once())

            services_error = RuntimeError("dbus down")
            adapter.rate_limiter.next_at["read"] = 0.0
            service_responder.side_effect = services_error
            record_error = install_mock(adapter.discovery, "record_error", MagicMock())
            with (
                patch.object(process_io_module.time, "time", return_value=123.0),
                patch.object(
                    process_io_module.time,
                    "monotonic",
                    return_value=12.0,
                ),
            ):
                self.assertTrue(adapter.io_role.refresh_services_if_due_once())
            record_error.assert_called_once_with(
                services_error,
                monotonic_at=12.0,
                captured_at=123.0,
            )
            self.assertEqual(
                [dbus_wire_call(mock_call.args[0]) for mock_call in send_async.call_args_list],
                [expected_call, expected_call],
            )
            self.assertEqual(
                [mock_call.args[0].timeout_seconds for mock_call in send_async.call_args_list],
                [1.0, 1.0],
            )

            adapter._introspection_queue_depth = 2
            adapter.introspection_role.record_introspection_xml("svc", "/Recorded", "<node/>")
            recorded = adapter.cache.values["introspection:svc:/Recorded"]
            self.assertEqual(recorded["confidence"], 0.5)
            self.assertEqual(recorded["freshness_kind"], "diagnostic")
            self.assertEqual(adapter._introspection_queue_depth, 1)

            error = RuntimeError("offline")
            self.assertEqual(adapter.introspection_role.drop_failed_introspection("svc", "/Failed", error), "dropped")
            failed = adapter.cache.values["introspection:svc:/Failed"]
            self.assertEqual(failed["freshness_kind"], "diagnostic")
            self.assertEqual(adapter._introspection_queue_depth, 0)

    def test_gateway_writes_legacy_introspection_snapshot_from_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "map.json"
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                f"[DEFAULT]\nDbusIntrospectionSnapshotPath={snapshot_path}\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            xml = "<node><interface name='com.victronenergy.BusItem'/><node name='Child'/></node>"
            adapter.cache.update_value(
                "introspection:com.victronenergy.system:/Ac/Grid",
                xml,
                source="com.victronenergy.system/Ac/Grid",
                confidence=0.7,
                now=123.0,
            )

            adapter.introspection_snapshot_role.write_introspection_snapshot()

            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
            finding = payload["services"]["com.victronenergy.system"]["paths"]["/Ac/Grid"]
            self.assertEqual(payload["worker_state"], "gateway")
            self.assertEqual(finding["status"], "fresh")
            self.assertEqual(finding["interfaces"], ["com.victronenergy.BusItem"])
            self.assertEqual(finding["children"], ["Child"])
            adapter.cache.mark_error("introspection:bad-key", source="bad", error="bad", now=124.0)
            adapter.cache.mark_error("introspection::/NoService", source="bad", error="bad", now=124.5)
            adapter.cache.mark_error("introspection:svc:/Broken", source="svc/Broken", error="offline", now=125.0)
            snapshot = adapter.introspection_snapshot_role.introspection_services_snapshot(200.0)
            self.assertNotIn("", snapshot)
            self.assertEqual(snapshot["svc"]["paths"]["/Broken"]["status"], "unresponsive-backoff")
            self.assertEqual(adapter.introspection_snapshot_role.parse_introspection_xml("<bad"), ([], []))

            services = {"svc": {"paths": "broken", "last_updated_at": "bad"}}
            adapter.introspection_snapshot_role.add_introspection_service_entry(
                services,
                "svc",
                "/Recovered",
                {"status": "error", "source": "svc/Recovered", "updated_at": "bad"},
                210.0,
            )
            self.assertIsInstance(services["svc"]["paths"], dict)
            self.assertEqual(services["svc"]["paths"]["/Recovered"]["status"], "unresponsive-backoff")
            self.assertEqual(services["svc"]["last_updated_at"], 210.0)

            adapter.dbus_introspection_enabled = False
            adapter.introspection_snapshot_role.write_introspection_snapshot()

    def test_gateway_introspection_snapshot_logs_write_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "map.json"
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                f"[DEFAULT]\nDbusIntrospectionSnapshotPath={snapshot_path}\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.dbus_introspection_enabled = True

            with (
                patch.object(
                    introspection_snapshot_module,
                    "write_text_atomically",
                    MagicMock(side_effect=OSError("readonly")),
                ),
                patch.object(introspection_snapshot_module.logging, "debug") as debug_log,
            ):
                adapter.introspection_snapshot_role.write_introspection_snapshot()

        debug_log.assert_called_once()
