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
    introspection_module,
    introspection_snapshot_module,
    json,
    patch,
    tempfile,
    time,
)


class GatewayIntrospectionExecutionCases(GatewayAdapterContractCase):
    """Exercise introspection execution and snapshot contracts."""

    def test_gateway_non_write_introspection_command_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            install_mock(adapter.read_executor, "refresh_requested_value", MagicMock(return_value="applied"))
            self.assertEqual(
                adapter.process_non_write_command({"type": "refresh_value", "key": "grid_power_w"}), "applied"
            )
            adapter.read_executor.refresh_requested_value.assert_called_once_with(
                {"type": "refresh_value", "key": "grid_power_w"}
            )

            list_services = install_mock(adapter, "list_services", MagicMock(return_value=["svc1", "svc2"]))
            remove_coalesced = install_mock(adapter.commands, "remove_coalesced", MagicMock())
            self.assertEqual(adapter.refresh_services_command({"kind": "refresh_services"}), "applied")
            list_services.assert_called_once_with()
            self.assertEqual(sorted(adapter.cache.services), ["svc1", "svc2"])
            remove_coalesced.assert_called_once_with("refresh:services")

            adapter.circuit.degraded_until = time.time() + 5.0
            remove_coalesced.reset_mock()
            self.assertEqual(adapter.refresh_services_command({"kind": "refresh_services"}), "deferred")
            remove_coalesced.assert_not_called()
            adapter.circuit.degraded_until = 0.0

            install_mock(adapter, "list_services", MagicMock(side_effect=DbusOperationDeferred("read")))
            remove_coalesced.reset_mock()
            self.assertEqual(adapter.refresh_services_command({"kind": "refresh_services"}), "deferred")
            remove_coalesced.assert_not_called()

            services_error = RuntimeError("dbus down")
            install_mock(adapter, "list_services", MagicMock(side_effect=services_error))
            record_error = install_mock(adapter.discovery, "record_error", MagicMock())
            remove_coalesced.reset_mock()
            with patch.object(introspection_module.time, "time", return_value=123.0):
                self.assertEqual(adapter.refresh_services_command({"kind": "refresh_services"}), "dropped")
            record_error.assert_called_once_with(services_error, now=123.0)
            remove_coalesced.assert_called_once_with("refresh:services")

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
