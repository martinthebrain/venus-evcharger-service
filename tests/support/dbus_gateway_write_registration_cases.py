# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter path ordering and startup registration contracts."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    DbusAdapter,
    GatewayAdapterContractCase,
    Path,
    gateway_paths,
    tempfile,
    write_publish_module,
)


class GatewayWriteRegistrationCases(GatewayAdapterContractCase):
    """Exercise path ordering and startup registration contracts."""

    def test_publish_path_priority_sort_is_ranked_then_stable_by_path_name(self) -> None:
        self.assertEqual(
            write_publish_module.UNKNOWN_PUBLISH_PATH_RANK,
            max(write_publish_module.PUBLISH_PATH_RANKS.values()) + 1,
        )
        self.assertEqual(
            write_publish_module._prioritized_publish_items(
                {
                    "/Status": "status",
                    "/Ac/Power": "power",
                    "/Mode": "mode",
                    "/ZZZ": "z",
                    42: "numeric-path",
                    "/AAA": "a",
                }
            ),
            [
                ("/Ac/Power", "power"),
                ("/Mode", "mode"),
                ("/Status", "status"),
                ("/AAA", "a"),
                ("/ZZZ", "z"),
                ("42", "numeric-path"),
            ],
        )

    def test_adapter_registers_identity_paths_before_service_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "Host=192.0.2.10\n"
                "DeviceInstance=77\n"
                "ProductName=Test EVCS\n"
                "CustomName=Garage\n"
                "Connection=Shelly RPC\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            adapter.ensure_dbus_service()

            self.assertFalse(adapter.dbus_service.registered)
            self.assertEqual(adapter.dbus_service["/DeviceInstance"], 77)
            self.assertEqual(adapter.dbus_service["/ProductName"], "Test EVCS")
            self.assertEqual(adapter.dbus_service["/CustomName"], "Garage")
            self.assertEqual(adapter.dbus_service["/Connected"], 1)
            self.assertEqual(adapter.dbus_service["/Mgmt/Connection"], "Shelly RPC")
            self.assertIn("/DeviceInstance", adapter.write_scheduler.registered_paths)

            adapter.register_dbus_service_name()
            adapter.register_dbus_service_name()

            self.assertTrue(adapter.dbus_service.registered)

    def test_startup_registration_batch_registers_paths_before_service_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nHost=192.0.2.10\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.commands.enqueue(
                {
                    "kind": "register_path",
                    "path": "/Mode",
                    "value": 0,
                    "writeable": True,
                    "coalesce_key": "register:/Mode",
                    "priority": "publish",
                }
            )
            adapter.commands.enqueue(
                {
                    "kind": "register_path",
                    "path": "/StartStop",
                    "value": 1,
                    "writeable": True,
                    "coalesce_key": "register:/StartStop",
                    "priority": "publish",
                }
            )
            adapter.commands.enqueue(
                {
                    "kind": "register_service",
                    "coalesce_key": "register-service",
                    "priority": "publish",
                }
            )

            self.assertTrue(adapter.write_scheduler.process_one())

            self.assertTrue(adapter.dbus_service.registered)
            self.assertEqual(adapter.dbus_service["/Mode"], 0)
            self.assertEqual(adapter.dbus_service["/StartStop"], 1)
            self.assertEqual(adapter.commands.load_pending(), [])

    def test_startup_registration_batch_honors_limit_before_registering_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nDbusGatewayStartupRegistrationBatchLimit=2\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            for path in ("/Mode", "/StartStop", "/Status"):
                adapter.commands.enqueue(
                    {
                        "kind": "register_path",
                        "path": path,
                        "value": 0,
                        "coalesce_key": f"register:{path}",
                        "priority": "publish",
                    }
                )
            adapter.commands.enqueue(
                {"kind": "register_service", "coalesce_key": "register-service", "priority": "publish"}
            )

            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertFalse(adapter.dbus_service.registered)
            self.assertIn("/Mode", adapter.write_scheduler.registered_paths)
            self.assertIn("/StartStop", adapter.write_scheduler.registered_paths)
            self.assertNotIn("/Status", adapter.write_scheduler.registered_paths)

            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertTrue(adapter.dbus_service.registered)
            self.assertIn("/Status", adapter.write_scheduler.registered_paths)
            self.assertEqual(adapter.commands.load_pending(), [])
