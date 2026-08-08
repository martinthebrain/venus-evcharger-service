# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure helper contracts for semantic write scheduling."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    GatewayAdapterContractCase,
    evcs_publication,
    patch,
    write_support_module,
)
from venus_evcharger.ipc.deadline import deadline_pair
from venus_evcharger.ipc.gateway_operations import gx_relay_refresh_command


class GatewayWriteSupportCases(GatewayAdapterContractCase):
    """Exercise transport-neutral scheduler helper contracts."""

    def test_priority_deadline_and_command_kind_normalization(self) -> None:
        expected = {
            " safety ": 0,
            "USER": 1,
            "publish": 2,
            "read": 3,
            "normal": 4,
            "optional": 5,
            "discovery": 5,
            "diagnostic": 6,
            "unknown": 6,
            None: 6,
        }
        for priority, rank in expected.items():
            self.assertEqual(write_support_module.priority_rank(priority), rank)
        self.assertEqual(deadline_pair({"deadline_s": "2.5", "created_at": "4"}), (2.5, 4.0))
        self.assertEqual(write_support_module.command_kind({"kind": "semantic", "type": "fallback"}), "semantic")
        self.assertEqual(write_support_module.command_kind({"type": "fallback"}), "fallback")
        self.assertEqual(write_support_module.command_kind({}), "")

    def test_only_semantic_field_publications_are_local_burst_commands(self) -> None:
        self.assertTrue(write_support_module.is_local_publish_command(evcs_publication()))
        self.assertTrue(
            write_support_module.is_local_publish_command(
                {"kind": "publish_companion_fields", "service_id": "grid", "fields": {"connected": 1}}
            )
        )
        self.assertFalse(write_support_module.is_local_publish_command({"kind": "register_evcs"}))

    def test_all_safety_and_user_work_reserves_durable_capacity(self) -> None:
        self.assertTrue(
            write_support_module.is_urgent_durable_command(
                {"kind": "gx_relay_set_enabled", "priority": "safety"}
            )
        )
        self.assertTrue(
            write_support_module.is_urgent_durable_command(
                {"kind": "ess_grid_setpoint", "priority": "user"}
            )
        )
        self.assertTrue(
            write_support_module.is_urgent_durable_command(
                {"kind": "publish_evcs_fields", "priority": "safety"}
            )
        )
        self.assertFalse(
            write_support_module.is_urgent_durable_command(
                {"kind": "gx_relay_refresh", "priority": "read"}
            )
        )
        self.assertFalse(write_support_module.is_urgent_durable_command({}))
        self.assertFalse(
            write_support_module.is_urgent_durable_command({"priority": None})
        )
        self.assertFalse(write_support_module.is_local_publish_command(gx_relay_refresh_command(0)))

    def test_local_burst_action_and_time_budget_helpers(self) -> None:
        self.assertEqual(write_support_module.local_publish_action_result(3, "break"), (3, True))
        self.assertEqual(write_support_module.local_publish_action_result(3, "processed"), (4, False))
        self.assertEqual(write_support_module.local_publish_action_result(3, "skip"), (3, False))
        with patch.object(vars(write_support_module)["time"], "monotonic", return_value=15.0):
            self.assertFalse(write_support_module.budget_elapsed(10.0, 5.1))
            self.assertTrue(write_support_module.budget_elapsed(10.0, 5.0))

    def test_stale_coalesced_path_selection_is_transport_neutral(self) -> None:
        self.assertEqual(
            write_support_module.stale_coalesced_paths(
                [
                    ("processed", {"coalesce_key": "semantic:evcs:live"}),
                    ("stale", {"coalesce_key": "semantic:evcs:live"}),
                    ("other", {"coalesce_key": "semantic:evcs:critical"}),
                    ("missing", {}),
                ],
                processed_path="processed",
                key="semantic:evcs:live",
            ),
            ["stale"],
        )

    def test_lifecycle_payload_preserves_semantic_command_identity(self) -> None:
        command = {
            **evcs_publication({"mode": 1}),
            "id": "cmd-1",
        }
        self.assertEqual(
            write_support_module.lifecycle_payload(command, "applied", "gui-critical-publish", 123.5),
            {
                "at": 123.5,
                "state": "applied",
                "queue_class": "gui-critical-publish",
                "kind": "publish_evcs_fields",
                "id": "cmd-1",
                "coalesce_key": "gateway-publication:evcs:live",
            },
        )
        self.assertEqual(
            write_support_module.lifecycle_payload(
                {"kind": "refresh_energy_inputs"},
                "queued",
                "read-fast",
                124.0,
            ),
            {
                "at": 124.0,
                "state": "queued",
                "queue_class": "read-fast",
                "kind": "refresh_energy_inputs",
                "id": "",
                "coalesce_key": "",
            },
        )

    def test_command_ready_honors_async_not_before_boundary(self) -> None:
        self.assertTrue(write_support_module.command_ready({"not_before": 10.0}, 10.0))
        self.assertFalse(write_support_module.command_ready({"not_before": 10.1}, 10.0))
