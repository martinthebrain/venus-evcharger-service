# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter background introspection scheduling contracts."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    DbusAdapter,
    GatewayAdapterContractCase,
    MagicMock,
    Path,
    gateway_paths,
    install_mock,
    introspection_module,
    patch,
    tempfile,
)


def _discover(adapter: DbusAdapter, services: list[str], *, now: float = 1.0) -> None:
    adapter.cache.update_services(services, now=now)
    adapter.energy_discovery.update_services(services, captured_at=now)


def _target_tuple(target: object) -> tuple[object, ...]:
    return (
        getattr(target, "service"),
        getattr(target, "path"),
        getattr(target, "priority"),
        getattr(target, "source"),
        getattr(target, "reason"),
    )


class GatewayIntrospectionBackgroundCases(GatewayAdapterContractCase):
    """Exercise adapter-private targets and semantic discovery snapshots."""

    def test_background_introspection_uses_discovery_targets_and_opaque_topology(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusIntrospectionFullScanIntervalSeconds=1\n"
                "AutoGridService= com.victronenergy.system \n"
                "AutoGridL1Path= /Ac/Grid/L1/Power \n"
                "AutoGridL2Path=\n"
                "AutoGridL3Path=/Ac/Grid/L3/Power\n"
                "AutoBatteryServicePrefix=com.victronenergy.battery\n"
                "AutoBatterySocPath=/Soc\n"
                "AutoPvServicePrefix=com.victronenergy.pvinverter\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            services = [
                "com.victronenergy.battery.tty2",
                "com.victronenergy.battery.tty1",
                *(f"com.victronenergy.pvinverter.http_{index:02d}" for index in range(12)),
                "com.victronenergy.system",
            ]
            _discover(adapter, services)
            pv_services = sorted(
                service
                for service in services
                if service.startswith("com.victronenergy.pvinverter.")
            )[:10]
            for service in pv_services:
                adapter.energy_discovery.record_pv_value(
                    service,
                    "/Ac/Power",
                    0.0,
                )
            adapter.energy_discovery.record_pv_value(
                "com.victronenergy.system",
                "/Dc/Pv/Power",
                0.0,
            )

            self.assertFalse(adapter.introspection_role.background_introspection_due(59.9))
            self.assertTrue(adapter.introspection_role.background_introspection_due(60.0))
            adapter._last_introspection_full_scan_at = 30.0
            self.assertFalse(adapter.introspection_role.background_introspection_due(40.0))
            adapter._last_introspection_full_scan_at = 0.0
            adapter.dbus_introspection_enabled = False
            self.assertFalse(adapter.introspection_role.background_introspection_due(61.0))
            adapter.dbus_introspection_enabled = True
            allows_priority = install_mock(adapter.circuit, "allows_priority", MagicMock(return_value=False))
            self.assertFalse(adapter.introspection_role.background_introspection_due(61.0))
            allows_priority.assert_called_with("discovery")
            allows_priority.return_value = True

            targets = adapter.energy_discovery.introspection_targets()
            target_values = [_target_tuple(target) for target in targets]
            self.assertEqual(
                [value for value in target_values if value[3] == "grid"],
                [
                    (
                        "com.victronenergy.system",
                        "/Ac/Grid/L1/Power",
                        80,
                        "grid",
                        "configured-grid-field",
                    ),
                    (
                        "com.victronenergy.system",
                        "/Ac/Grid/L3/Power",
                        80,
                        "grid",
                        "configured-grid-field",
                    ),
                ],
            )
            self.assertEqual(
                [value for value in target_values if value[3] == "battery"],
                [
                    (
                        "com.victronenergy.battery.tty1",
                        "/Soc",
                        70,
                        "battery",
                        "discovered-battery-field",
                    ),
                    (
                        "com.victronenergy.battery.tty2",
                        "/Soc",
                        70,
                        "battery",
                        "discovered-battery-field",
                    ),
                    (
                        "com.victronenergy.system",
                        "/Dc/Battery/Power",
                        70,
                        "battery",
                        "configured-battery-power-field",
                    ),
                ],
            )
            pv_targets = [value for value in target_values if value[3] == "pv"]
            self.assertEqual(len(pv_targets), 11)
            self.assertEqual(
                pv_targets[0],
                (
                    "com.victronenergy.pvinverter.http_00",
                    "/Ac/Power",
                    30,
                    "pv",
                    "discovered-ac-pv-field",
                ),
            )
            self.assertEqual(
                pv_targets[-1],
                (
                    "com.victronenergy.system",
                    "/Dc/Pv/Power",
                    30,
                    "pv",
                    "configured-dc-pv-field",
                ),
            )

            topology = adapter.energy_discovery.topology_snapshot(captured_at=100.0)
            self.assertEqual(topology.generation, 12)
            self.assertEqual(
                [source.kind for source in topology.sources].count("pv_ac"),
                10,
            )
            self.assertEqual([source.kind for source in topology.sources].count("pv_dc"), 1)
            self.assertNotIn("com.victronenergy", str(topology.to_payload()))

            queued = install_mock(adapter.commands, "enqueue", MagicMock())
            adapter.introspection_role.enqueue_introspection_command(
                "svc.discovery",
                "/Discovery",
                priority=89,
                source="test",
                reason="discovery-priority",
            )
            adapter.introspection_role.enqueue_introspection_command(
                "svc.optional",
                "/Optional",
                priority=90,
                source="test",
                reason="optional-priority",
            )
            self.assertEqual(
                [call.args[0]["priority"] for call in queued.call_args_list],
                ["discovery", "optional"],
            )

            enqueue = install_mock(adapter.introspection_role, "enqueue_introspection_command", MagicMock())
            with patch.object(introspection_module.time, "time", return_value=100.0):
                adapter.introspection_role.enqueue_background_introspection_if_due()
            self.assertEqual(enqueue.call_count, len(targets))
            enqueue.assert_any_call(
                "com.victronenergy.system",
                "/Dc/Pv/Power",
                priority=30,
                source="pv",
                reason="configured-dc-pv-field",
            )
            enqueue.reset_mock()
            with patch.object(introspection_module.time, "time", return_value=100.0):
                adapter.introspection_role.enqueue_background_introspection_if_due()
            enqueue.assert_not_called()

    def test_introspection_discovery_honors_custom_and_explicit_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            custom_config = root / "custom.ini"
            custom_config.write_text(
                "[DEFAULT]\n"
                "AutoGridService=custom.grid\n"
                "AutoGridL1Path=/Custom/L1\n"
                "AutoGridL2Path=/Custom/L2\n"
                "AutoGridL3Path=/Custom/L3\n"
                "AutoBatteryServicePrefix=custom.battery\n"
                "AutoBatterySocPath=/Custom/Soc\n"
                "AutoPvServicePrefix=custom.pv\n"
                "AutoPvPath=/Custom/Pv\n"
                "AutoUseDcPv=0\n",
                encoding="utf-8",
            )
            custom = DbusAdapter(str(custom_config), paths=gateway_paths(str(root / "run-custom")))
            _discover(custom, ["custom.battery.1", "custom.pv.1"])
            self.assertEqual(
                [_target_tuple(target) for target in custom.energy_discovery.introspection_targets()],
                [
                    ("custom.grid", "/Custom/L1", 80, "grid", "configured-grid-field"),
                    ("custom.grid", "/Custom/L2", 80, "grid", "configured-grid-field"),
                    ("custom.grid", "/Custom/L3", 80, "grid", "configured-grid-field"),
                    ("custom.battery.1", "/Custom/Soc", 70, "battery", "discovered-battery-field"),
                    (
                        "com.victronenergy.system",
                        "/Dc/Battery/Power",
                        70,
                        "battery",
                        "configured-battery-power-field",
                    ),
                    ("custom.pv.1", "/Custom/Pv", 30, "pv", "discovered-ac-pv-field"),
                ],
            )

            explicit_config = root / "explicit.ini"
            explicit_config.write_text(
                "[DEFAULT]\n"
                "AutoBatteryService=custom.battery.explicit\n"
                "AutoBatteryServicePrefix=custom.battery\n"
                "AutoPvService=custom.pv.explicit\n"
                "AutoPvServicePrefix=custom.pv\n"
                "AutoUseDcPv=0\n",
                encoding="utf-8",
            )
            explicit = DbusAdapter(str(explicit_config), paths=gateway_paths(str(root / "run-explicit")))
            _discover(
                explicit,
                [
                    "custom.battery.explicit",
                    "custom.battery.other",
                    "custom.pv.explicit",
                    "custom.pv.other",
                ],
            )
            target_values = [_target_tuple(target) for target in explicit.energy_discovery.introspection_targets()]
            self.assertIn(
                ("custom.battery.explicit", "/Dc/Battery/Soc", 70, "battery", "discovered-battery-field"),
                target_values,
            )
            self.assertIn(
                ("custom.pv.explicit", "/Ac/Power", 30, "pv", "discovered-ac-pv-field"),
                target_values,
            )
            self.assertNotIn(
                ("custom.battery.other", "/Dc/Battery/Soc", 70, "battery", "discovered-battery-field"),
                target_values,
            )
