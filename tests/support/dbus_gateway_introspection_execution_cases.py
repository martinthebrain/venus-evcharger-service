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
    install_mock,
    introspection_snapshot_module,
    json,
    patch,
    process_io_module,
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

            timed_introspection = install_mock(
                adapter,
                "timed_introspection_result",
                MagicMock(return_value=("applied", "<unexpected/>")),
            )
            self.assertEqual(adapter.introspect_command({}), "dropped")
            timed_introspection.assert_not_called()

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
                adapter.process_non_write_command(refresh.to_command(source="test")),
                "applied",
            )
            force_reads.assert_called_once_with(("grid_power_w", "pv_power_w", "battery_soc"))
            force_discovery.assert_called_once_with()
            force_reads.reset_mock()
            battery_refresh = EnergyRefreshRequest("battery", "battery", 0.0)
            self.assertEqual(
                adapter.process_non_write_command(battery_refresh.to_command(source="test")),
                "applied",
            )
            force_reads.assert_called_once_with(("battery_soc",))
            self.assertEqual(
                adapter.process_non_write_command(
                    {"type": "refresh_value", "key": "grid_power_w", "service": "svc", "path": "/P"}
                ),
                "dropped",
            )

            list_services = install_mock(adapter, "list_services", MagicMock(return_value=["svc1", "svc2"]))
            discovery_due = install_mock(adapter.discovery, "due", MagicMock(return_value=True))
            record_success = install_mock(adapter.discovery, "record_success", MagicMock())
            with patch.object(process_io_module.time, "time", return_value=100.0):
                self.assertTrue(adapter.refresh_services_if_due_once())
            list_services.assert_called_once_with()
            self.assertEqual(sorted(adapter.cache.services), ["svc1", "svc2"])
            self.assertEqual(adapter.energy_discovery.topology_snapshot(now=100.0).generation, 1)
            record_success.assert_called_once_with(now=100.0)
            discovery_due.assert_called_once()

            list_services.reset_mock()
            record_success.reset_mock()
            discovery_due.return_value = False
            self.assertFalse(adapter.refresh_services_if_due_once())
            list_services.assert_not_called()
            record_success.assert_not_called()

            discovery_due.return_value = True
            install_mock(adapter, "list_services", MagicMock(side_effect=DbusOperationDeferred("read")))
            self.assertFalse(adapter.refresh_services_if_due_once())

            services_error = RuntimeError("dbus down")
            install_mock(adapter, "list_services", MagicMock(side_effect=services_error))
            record_error = install_mock(adapter.discovery, "record_error", MagicMock())
            with patch.object(process_io_module.time, "time", return_value=123.0):
                self.assertTrue(adapter.refresh_services_if_due_once())
            record_error.assert_called_once_with(services_error, now=123.0)

            adapter._introspection_queue_depth = 2
            adapter.record_introspection_xml("svc", "/Recorded", "<node/>")
            recorded = adapter.cache.values["introspection:svc:/Recorded"]
            self.assertEqual(recorded["confidence"], 0.5)
            self.assertEqual(recorded["freshness_kind"], "diagnostic")
            self.assertEqual(adapter._introspection_queue_depth, 1)

            error = RuntimeError("offline")
            self.assertEqual(adapter.drop_failed_introspection("svc", "/Failed", error), "dropped")
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

            adapter.write_introspection_snapshot()

            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
            finding = payload["services"]["com.victronenergy.system"]["paths"]["/Ac/Grid"]
            self.assertEqual(payload["worker_state"], "gateway")
            self.assertEqual(finding["status"], "fresh")
            self.assertEqual(finding["interfaces"], ["com.victronenergy.BusItem"])
            self.assertEqual(finding["children"], ["Child"])
            adapter.cache.mark_error("introspection:bad-key", source="bad", error="bad", now=124.0)
            adapter.cache.mark_error("introspection::/NoService", source="bad", error="bad", now=124.5)
            adapter.cache.mark_error("introspection:svc:/Broken", source="svc/Broken", error="offline", now=125.0)
            snapshot = adapter.introspection_services_snapshot(200.0)
            self.assertNotIn("", snapshot)
            self.assertEqual(snapshot["svc"]["paths"]["/Broken"]["status"], "unresponsive-backoff")
            self.assertEqual(adapter.parse_introspection_xml("<bad"), ([], []))

            services = {"svc": {"paths": "broken", "last_updated_at": "bad"}}
            adapter.add_introspection_service_entry(
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
            adapter.write_introspection_snapshot()

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
                adapter.write_introspection_snapshot()

        debug_log.assert_called_once()
