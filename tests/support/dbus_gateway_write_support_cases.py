# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter write support helper contracts."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    GatewayAdapterContractCase,
    patch,
    write_support_module,
)


class GatewayWriteSupportCases(GatewayAdapterContractCase):
    """Exercise write support helper contracts."""

    def test_write_scheduler_support_helper_contracts(self) -> None:
        self.assertEqual(write_support_module.priority_rank(" safety "), 0)
        self.assertEqual(write_support_module.priority_rank("USER"), 1)
        self.assertEqual(write_support_module.priority_rank("publish"), 2)
        self.assertEqual(write_support_module.priority_rank("read"), 3)
        self.assertEqual(write_support_module.priority_rank("normal"), 4)
        self.assertEqual(write_support_module.priority_rank("optional"), 5)
        self.assertEqual(write_support_module.priority_rank("discovery"), 5)
        self.assertEqual(write_support_module.priority_rank("diagnostic"), 6)
        self.assertEqual(write_support_module.priority_rank("unknown"), 6)
        self.assertEqual(write_support_module.priority_rank(None), 6)

        self.assertEqual(write_support_module.deadline_pair({"deadline_s": "2.5", "created_at": "4"}), (2.5, 4.0))
        self.assertTrue(
            write_support_module.has_startup_registration(
                commands=[
                    ("path", {"kind": "register_path"}),
                    ("publish", {"kind": "publish_value"}),
                ]
            )
        )
        self.assertTrue(
            write_support_module.has_startup_registration(commands=[("service", {"kind": "register_service"})])
        )
        self.assertFalse(
            write_support_module.has_startup_registration(commands=[("publish", {"kind": "publish_value"})])
        )
        self.assertTrue(write_support_module.is_local_publish_command({"kind": "publish_value"}))
        self.assertTrue(write_support_module.is_local_publish_command({"type": "publish_desired"}))
        self.assertTrue(write_support_module.is_local_publish_command({"kind": "publish_fields"}))
        self.assertFalse(write_support_module.is_local_publish_command({"kind": "set_value"}))
        self.assertTrue(write_support_module.should_follow_with_local_burst({"kind": "publish_value"}, "applied"))
        self.assertTrue(write_support_module.should_follow_with_local_burst({"kind": "publish_desired"}, "dropped"))
        self.assertTrue(write_support_module.should_follow_with_local_burst({"kind": "publish_fields"}, "applied"))
        self.assertFalse(write_support_module.should_follow_with_local_burst({"kind": "publish_value"}, "deferred"))
        self.assertFalse(write_support_module.should_follow_with_local_burst({"kind": "set_value"}, "applied"))

        self.assertEqual(write_support_module.local_publish_action_result(3, "break"), (3, True))
        self.assertEqual(write_support_module.local_publish_action_result(3, "processed"), (4, False))
        self.assertEqual(write_support_module.local_publish_action_result(3, "skip"), (3, False))
        with patch.object(write_support_module.time, "monotonic", return_value=15.0):
            self.assertFalse(write_support_module.budget_elapsed(10.0, 5.1))
            self.assertTrue(write_support_module.budget_elapsed(10.0, 5.0))
        self.assertEqual(
            write_support_module.command_kind({"kind": "publish_value", "type": "set_value"}), "publish_value"
        )
        self.assertEqual(write_support_module.command_kind({"type": "set_value"}), "set_value")
        self.assertEqual(write_support_module.command_kind({}), "")
        self.assertEqual(
            write_support_module.register_service_command(
                [
                    ("first", {"kind": "register_service"}),
                    ("path", {"kind": "register_path"}),
                    ("second", {"kind": "register_service"}),
                    ("third", {"kind": "register_service"}),
                ]
            ),
            ("third", {"kind": "register_service"}),
        )
        self.assertIsNone(write_support_module.register_service_command([("path", {"kind": "register_path"})]))
        self.assertEqual(
            write_support_module.stale_coalesced_paths(
                [
                    ("processed", {"coalesce_key": "same"}),
                    ("stale", {"coalesce_key": "same"}),
                    ("other", {"coalesce_key": "other"}),
                    ("missing", {}),
                ],
                processed_path="processed",
                key="same",
            ),
            ["stale"],
        )
        self.assertEqual(
            write_support_module.lifecycle_payload(
                {"kind": "publish_value", "id": "cmd-1", "coalesce_key": "path:/Mode"},
                "applied",
                "gui-critical-publish",
                123.5,
            ),
            {
                "at": 123.5,
                "state": "applied",
                "queue_class": "gui-critical-publish",
                "kind": "publish_value",
                "id": "cmd-1",
                "coalesce_key": "path:/Mode",
            },
        )
        self.assertEqual(
            write_support_module.lifecycle_payload({"type": "refresh_services"}, "", "", 0.0),
            {"at": 0.0, "state": "", "queue_class": "", "kind": "refresh_services", "id": "", "coalesce_key": ""},
        )
