#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Scenarios for bounded transient gateway publication IPC."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.dbus_adapter_venus_stubs import install_venus_adapter_stubs

install_venus_adapter_stubs()

from venus_evcharger.dbus_adapter.process.adapter import DbusAdapter
from venus_evcharger.dbus_adapter.contracts import CommandExecution
import venus_evcharger.dbus_adapter.write.core as write_core_module
from venus_evcharger.dbus_gateway import gateway_paths
from venus_evcharger.dbus_gateway_client import GatewayClient
from venus_evcharger.dbus_gateway_commands import DbusGatewayCommandQueuePolicy
from venus_evcharger.ipc.command_mailbox import MailboxLockTimeout, normalized_mapping
from venus_evcharger.ipc.deadline import TRANSIENT_PUBLICATION_DEADLINE_SECONDS
from venus_evcharger.ipc.enqueue_result import GatewayEnqueueResult
from venus_evcharger.ipc.gateway_publication import (
    PublishEvcsFields,
    publish_companion_fields_command,
    publish_evcs_fields_command,
    register_evcs_command,
)
from venus_evcharger.ipc.gateway_operations import gx_relay_refresh_command
from venus_evcharger.ipc.publication_order import PUBLICATION_ORDER_FIELD
from venus_evcharger.ports.gateway_publication import EvcsServiceIdentity


def _identity() -> EvcsServiceIdentity:
    return EvcsServiceIdentity(
        product_name="EVCS",
        custom_name="Wallbox",
        firmware_version="1",
        hardware_version="relay",
        serial="evcs-60",
        connection_name="Gateway",
        process_name="service",
        process_version="Python",
    )


class FastPublicationTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        config_path = root / "config.ini"
        config_path.write_text("[DEFAULT]\n", encoding="utf-8")
        self.paths = gateway_paths(str(root / "run"))
        self.adapter = DbusAdapter(str(config_path), paths=self.paths)

    def test_socket_accepts_only_transient_publish_contracts(self) -> None:
        live = publish_evcs_fields_command({"ac_power_w": 1200.0}, priority="live")
        critical = publish_evcs_fields_command({"connected": 0}, priority="critical")

        accepted = self.adapter.socket_role.dispatch_socket_payload(live)
        rejected = self.adapter.socket_role.dispatch_socket_payload(critical)

        self.assertTrue(accepted["ok"])
        self.assertTrue(accepted["accepted"])
        self.assertEqual(len(self.adapter.fast_publications), 1)
        self.assertEqual(rejected["reason"], "durable-command-required")

    def test_client_uses_socket_then_falls_back_to_durable_mailbox(self) -> None:
        client = GatewayClient(self.paths)
        live = publish_evcs_fields_command({"ac_power_w": 1200.0}, priority="live")
        with (
            patch.object(client, "backpressure_state", return_value="ok"),
            patch.object(
                client,
                "_send_fast_publication",
                return_value={"ok": True, "accepted": True, "command_id": "fast-one"},
            ) as send,
            patch.object(client.commands, "enqueue", return_value="durable.json") as durable,
        ):
            self.assertEqual(
                client.enqueue_command(live),
                GatewayEnqueueResult(True, "fast-one", "socket"),
            )
        sent = send.call_args.args[0]
        self.assertEqual(sent["fields"], live["fields"])
        self.assertIsInstance(sent[PUBLICATION_ORDER_FIELD], int)
        durable.assert_not_called()

    def test_live_socket_publication_bypasses_pressure_without_durable_fallback(self) -> None:
        client = GatewayClient(self.paths)
        live = publish_evcs_fields_command({"ac_power_w": 0.0}, priority="live")
        with (
            patch.object(client, "backpressure_state") as backpressure,
            patch.object(
                client,
                "_send_fast_publication",
                return_value={"ok": True, "accepted": True, "command_id": "fast-live"},
            ) as send,
            patch.object(client.commands, "enqueue") as durable,
        ):
            result = client.enqueue_command(live)

        self.assertEqual(result, GatewayEnqueueResult(True, "fast-live", "socket"))
        send.assert_called_once()
        backpressure.assert_not_called()
        durable.assert_not_called()

        with (
            patch.object(client, "backpressure_state", return_value="ok"),
            patch.object(
                client,
                "_send_fast_publication",
                return_value={"ok": False, "accepted": False, "reason": "queue-full"},
            ),
            patch.object(client.commands, "enqueue", return_value="/run/durable.json") as durable,
        ):
            self.assertEqual(
                client.enqueue_command(live),
                GatewayEnqueueResult(
                    True,
                    "durable",
                    "mailbox",
                    command_path="/run/durable.json",
                ),
            )
        fallback = durable.call_args.args[0]
        self.assertEqual(fallback["fields"], live["fields"])
        self.assertIsInstance(fallback[PUBLICATION_ORDER_FIELD], int)

    def test_client_rejects_incomplete_or_invalid_socket_acceptance(self) -> None:
        live = publish_evcs_fields_command({"ac_power_w": 1200.0}, priority="live")
        rejected_responses = (
            {"ok": False, "accepted": True, "command_id": "fast"},
            {"ok": True, "accepted": False, "command_id": "fast"},
            {"ok": True, "accepted": True, "command_id": 7},
            {"ok": True, "accepted": True},
        )
        for response in rejected_responses:
            with self.subTest(response=response):
                client = GatewayClient(self.paths)
                with (
                    patch.object(client, "backpressure_state", return_value="ok"),
                    patch.object(client, "_send_fast_publication", return_value=response),
                    patch.object(
                        client.commands,
                        "enqueue",
                        return_value="/run/durable.json",
                    ),
                ):
                    result = client.enqueue_command(live)
                self.assertEqual(
                    result,
                    GatewayEnqueueResult(
                        True,
                        "durable",
                        "mailbox",
                        command_path="/run/durable.json",
                    ),
                )

    def test_mailbox_lock_timeout_is_a_bounded_client_rejection(self) -> None:
        client = GatewayClient(self.paths)
        critical = publish_evcs_fields_command({"connected": 0}, priority="critical")
        with (
            patch.object(client, "backpressure_state", return_value="ok"),
            patch.object(
                client.commands,
                "enqueue",
                side_effect=MailboxLockTimeout("busy"),
            ),
            self.assertLogs(level="ERROR") as captured,
        ):
            result = client.enqueue_command(critical)
            repeated = client.enqueue_command(critical)
        self.assertEqual(result, GatewayEnqueueResult(False, reason="mailbox-lock-timeout"))
        self.assertEqual(repeated, result)
        self.assertEqual(
            captured.output,
            [
                "ERROR:root:Durable gateway command was not accepted: mailbox-lock-timeout",
                "ERROR:root:Durable gateway command was not accepted: mailbox-lock-timeout",
            ],
        )

    def test_invalid_durable_command_is_rejected_with_exact_health_reason(self) -> None:
        client = GatewayClient(self.paths)
        critical = publish_evcs_fields_command({"connected": 0}, priority="critical")
        with (
            patch.object(client, "backpressure_state", return_value="ok"),
            patch.object(client.commands, "enqueue", side_effect=ValueError("invalid")),
        ):
            result = client.enqueue_command(critical)

        self.assertEqual(result, GatewayEnqueueResult(False, reason="invalid-command"))

    def test_transient_durable_fallback_expires_but_critical_publication_does_not(self) -> None:
        client = GatewayClient(self.paths)
        live = publish_evcs_fields_command({"ac_power_w": 1200.0}, priority="live")
        critical = publish_evcs_fields_command({"connected": 0}, priority="critical")
        with (
            patch.object(client, "backpressure_state", return_value="ok"),
            patch.object(client, "_send_fast_publication", return_value={"ok": False}),
        ):
            live_path = client.enqueue_command(live).command_path
            critical_path = client.enqueue_command(critical).command_path

        pending = {Path(path).name: command for path, command in client.commands.load_pending()}
        self.assertEqual(
            pending[Path(live_path).name]["deadline_s"],
            TRANSIENT_PUBLICATION_DEADLINE_SECONDS,
        )
        self.assertNotIn("deadline_s", pending[Path(critical_path).name])

    def test_transient_fallback_normalizes_invalid_deadlines_and_preserves_shorter_one(self) -> None:
        command = publish_evcs_fields_command({"ac_power_w": 1200.0}, priority="live")
        for invalid in (None, 0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(deadline=invalid):
                normalized = DbusGatewayCommandQueuePolicy.normalize(
                    {**command, "deadline_s": invalid}
                )
                self.assertEqual(
                    normalized["deadline_s"],
                    TRANSIENT_PUBLICATION_DEADLINE_SECONDS,
                )
        self.assertEqual(
            DbusGatewayCommandQueuePolicy.normalize(
                {**command, "deadline_s": 5.0}
            )["deadline_s"],
            5.0,
        )
        self.assertEqual(
            DbusGatewayCommandQueuePolicy.normalize(
                {
                    **command,
                    "deadline_s": TRANSIENT_PUBLICATION_DEADLINE_SECONDS + 1.0,
                }
            )["deadline_s"],
            TRANSIENT_PUBLICATION_DEADLINE_SECONDS,
        )

    def test_gateway_restart_preserves_durable_mailbox_only(self) -> None:
        live = publish_evcs_fields_command({"ac_power_w": 1200.0}, priority="live")
        critical = publish_evcs_fields_command({"connected": 0}, priority="critical")
        self.adapter.fast_publications.enqueue(live)
        durable_path = self.adapter.commands.enqueue(critical)

        replacement = DbusAdapter(self.adapter.config_path, paths=self.paths)

        self.assertEqual(len(replacement.fast_publications), 0)
        self.assertTrue(Path(durable_path).exists())
        pending = replacement.commands.load_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0][1]["publication_priority"], "critical")

    def test_durable_classes_never_depend_on_socket_and_backpressure_is_preserved(self) -> None:
        client = GatewayClient(self.paths)
        registration = register_evcs_command(_identity(), {"mode": 0})
        critical = publish_evcs_fields_command({"connected": 0}, priority="critical")
        diagnostic = publish_evcs_fields_command({"diagnostic_text": "idle"}, priority="diagnostic")
        with (
            patch.object(client, "backpressure_state", return_value="ok"),
            patch.object(client, "_send_fast_publication") as send,
            patch.object(client.commands, "enqueue", side_effect=("register.json", "critical.json")) as durable,
        ):
            self.assertEqual(client.enqueue_command(registration).command_id, "register")
            self.assertEqual(client.enqueue_command(critical).command_id, "critical")
        send.assert_not_called()
        self.assertEqual(durable.call_count, 2)

        with (
            patch.object(client, "backpressure_state", return_value="protective"),
            patch.object(client, "_send_fast_publication") as send,
            patch.object(client.commands, "enqueue") as durable,
        ):
            self.assertEqual(client.enqueue_command(diagnostic).reason, "backpressure")
        send.assert_not_called()
        durable.assert_not_called()

    def test_scheduler_drains_coalesced_memory_before_file_mailbox(self) -> None:
        scheduler = self.adapter.write_scheduler
        registry = self.adapter.publication_registry
        publish = MagicMock(return_value="applied")
        with patch.object(registry, "publish_evcs", publish):
            self.adapter.fast_publications.enqueue(
                publish_evcs_fields_command({"mode": 1}, priority="live")
            )
            self.adapter.fast_publications.enqueue(
                publish_evcs_fields_command({"ac_power_w": 800.0}, priority="live")
            )
            self.assertEqual(scheduler.process_local_publish_burst(limit=2), 1)

        publication = publish.call_args.args[0]
        self.assertEqual(publication.fields, {"mode": 1, "ac_power_w": 800.0})
        self.assertEqual(len(self.adapter.fast_publications), 0)
        health = scheduler.health()
        fast_health = normalized_mapping(health["fast_publication_queue"])
        self.assertIsNotNone(fast_health)
        assert fast_health is not None
        counts = normalized_mapping(fast_health.get("counts"))
        self.assertIsNotNone(counts)
        assert counts is not None
        self.assertEqual(counts["applied"], 1)

    def test_deferred_memory_publish_is_retained_and_fully_diagnosed(self) -> None:
        scheduler = self.adapter.write_scheduler
        command = publish_evcs_fields_command({"mode": 1}, priority="live")
        self.adapter.fast_publications.enqueue(command)
        with patch.object(self.adapter.publication_registry, "publish_evcs", return_value="deferred"):
            self.assertEqual(scheduler.process_local_publish_burst(limit=2), 1)

        self.assertEqual(len(self.adapter.fast_publications), 1)
        self.assertEqual(scheduler.health_tracker.lifecycle_counts_60s(), {"deferred": 1})
        fast_health = normalized_mapping(scheduler.health()["fast_publication_queue"])
        self.assertIsNotNone(fast_health)
        assert fast_health is not None
        counts = normalized_mapping(fast_health.get("counts"))
        self.assertIsNotNone(counts)
        assert counts is not None
        self.assertEqual(counts["deferred"], 1)

    def test_deferred_high_priority_fast_work_does_not_starve_lower_class(self) -> None:
        scheduler = self.adapter.write_scheduler
        high = publish_evcs_fields_command({"mode": 1}, priority="live")
        lower = publish_companion_fields_command(
            "grid",
            {"ac_power_w": 100.0},
            priority="diagnostic",
        )
        self.adapter.fast_publications.enqueue(high)
        self.adapter.fast_publications.enqueue(lower)

        with (
            patch.object(
                self.adapter.publication_registry,
                "publish_evcs",
                return_value="deferred",
            ) as publish_high,
            patch.object(
                self.adapter.publication_registry,
                "publish_companion",
                return_value="applied",
            ) as publish_lower,
        ):
            self.assertEqual(scheduler.process_local_publish_burst(limit=2), 2)

        publish_high.assert_called_once()
        publish_lower.assert_called_once()
        self.assertEqual(len(self.adapter.fast_publications), 1)

    def test_fast_queue_obeys_count_time_and_queue_class_budgets(self) -> None:
        scheduler = self.adapter.write_scheduler
        evcs = publish_evcs_fields_command({"mode": 1}, priority="live")
        companion = publish_companion_fields_command(
            "grid",
            {"ac_power_w": 100.0},
            priority="live",
        )
        self.adapter.fast_publications.enqueue(evcs)
        self.adapter.fast_publications.enqueue(companion)
        with (
            patch.object(self.adapter.publication_registry, "publish_evcs", return_value="applied"),
            patch.object(self.adapter.publication_registry, "publish_companion", return_value="applied"),
        ):
            self.assertEqual(scheduler.process_local_publish_burst(limit=1), 1)
        self.assertEqual(len(self.adapter.fast_publications), 1)

        with patch.object(write_core_module, "budget_elapsed", return_value=True):
            self.assertEqual(scheduler.process_local_publish_burst(limit=2), 0)
        self.assertEqual(len(self.adapter.fast_publications), 1)

        with patch.object(scheduler.health_tracker, "budget_available", return_value=False):
            self.assertEqual(scheduler.process_local_publish_burst(limit=2), 0)
        self.assertEqual(len(self.adapter.fast_publications), 1)

    def test_dropped_fast_publish_is_not_sampled_away(self) -> None:
        scheduler = self.adapter.write_scheduler
        self.adapter.fast_publications.enqueue(
            publish_evcs_fields_command({"mode": 1}, priority="live")
        )
        with patch.object(self.adapter.publication_registry, "publish_evcs", return_value="dropped"):
            self.assertEqual(scheduler.process_local_publish_burst(limit=1), 1)

        self.assertEqual(scheduler.health_tracker.lifecycle_counts_60s(), {"dropped": 1})
        self.assertEqual(len(self.adapter.fast_publications), 0)

    def test_applied_fast_successes_are_sampled_without_losing_processed_count(self) -> None:
        scheduler = self.adapter.write_scheduler
        self.assertEqual(
            scheduler.publication_executor.process(register_evcs_command(_identity(), {"mode": 0})),
            "applied",
        )
        self.adapter.fast_publications.enqueue(
            publish_evcs_fields_command({"mode": 1}, priority="live")
        )
        self.adapter.fast_publications.enqueue(
            publish_evcs_fields_command({"ac_current_a": 4.0}, priority="diagnostic")
        )

        self.assertEqual(scheduler.process_local_publish_burst(limit=2), 2)

        self.assertEqual(scheduler.health_tracker.lifecycle_counts_60s(), {"applied": 1})
        self.assertEqual(scheduler.health()["processed_commands_60s"], 2)

    def test_lost_ack_fallback_cannot_revive_an_older_file_value(self) -> None:
        client = GatewayClient(self.paths)
        old = publish_evcs_fields_command({"ac_power_w": 100.0}, priority="live")
        new = publish_evcs_fields_command({"ac_power_w": 900.0}, priority="live")

        def accept_without_ack(command: dict[str, object]) -> dict[str, object]:
            self.adapter.fast_publications.enqueue(command)
            return {"ok": False, "error": "ack-lost"}

        def accept_with_ack(command: dict[str, object]) -> dict[str, object]:
            return self.adapter.fast_publications.enqueue(command).to_payload()

        with (
            patch.object(client, "backpressure_state", return_value="ok"),
            patch.object(client, "_send_fast_publication", side_effect=accept_without_ack),
        ):
            fallback_path = client.enqueue_command(old).command_path
        self.assertTrue(Path(fallback_path).exists())

        with (
            patch.object(client, "backpressure_state", return_value="ok"),
            patch.object(client, "_send_fast_publication", side_effect=accept_with_ack),
        ):
            self.assertTrue(client.enqueue_command(new).command_id.startswith("fast-"))

        published: list[float] = []

        def record(publication: PublishEvcsFields) -> str:
            value = publication.fields["ac_power_w"]
            self.assertIsInstance(value, (int, float))
            assert isinstance(value, (int, float))
            published.append(float(value))
            return "applied"

        with patch.object(self.adapter.publication_registry, "publish_evcs", side_effect=record):
            self.assertEqual(self.adapter.write_scheduler.process_local_publish_burst(limit=1), 1)
            self.assertTrue(self.adapter.write_scheduler.process_one())

        self.assertEqual(published, [900.0])
        self.assertFalse(Path(fallback_path).exists())
        lifecycle = normalized_mapping(
            self.adapter.write_scheduler.health().get("lifecycle_counts")
        )
        self.assertIsNotNone(lifecycle)
        assert lifecycle is not None
        self.assertEqual(lifecycle["coalesced"], 1)

    def test_continuous_fast_flood_reserves_a_durable_safety_slot(self) -> None:
        for index in range(20):
            self.adapter.fast_publications.enqueue(
                publish_companion_fields_command(
                    f"grid-{index}",
                    {"ac_power_w": float(index)},
                    priority="live",
                )
            )
        safety_path = self.adapter.commands.enqueue(
            {
                **gx_relay_refresh_command(0),
                "priority": "safety",
            }
        )
        scheduler = self.adapter.write_scheduler

        self.assertEqual(scheduler.process_local_publish_burst(limit=20), 0)
        self.assertEqual(len(self.adapter.fast_publications), 20)
        with patch.object(
            scheduler.semantic_executor,
            "schedule_semantic_operation",
            return_value=CommandExecution.immediate("applied"),
        ) as execute:
            self.assertTrue(scheduler.process_one(include_local_publish=False))

        execute.assert_called_once()
        self.assertFalse(Path(safety_path).exists())
        self.assertEqual(len(self.adapter.fast_publications), 20)

    def test_continuous_fast_flood_reserves_a_critical_publication_slot(self) -> None:
        for index in range(20):
            self.adapter.fast_publications.enqueue(
                publish_companion_fields_command(
                    f"grid-{index}",
                    {"ac_power_w": float(index)},
                    priority="live",
                )
            )
        critical_path = self.adapter.commands.enqueue(
            publish_evcs_fields_command({"connected": 0}, priority="critical")
        )
        scheduler = self.adapter.write_scheduler

        self.assertEqual(scheduler.process_local_publish_burst(limit=20), 0)
        self.assertEqual(len(self.adapter.fast_publications), 20)
        with patch.object(
            self.adapter.publication_registry,
            "publish_evcs",
            return_value="applied",
        ) as publish:
            self.assertTrue(scheduler.process_one())

        publish.assert_called_once()
        self.assertFalse(Path(critical_path).exists())
        self.assertEqual(len(self.adapter.fast_publications), 20)

    def test_delayed_safety_file_does_not_freeze_fast_publications(self) -> None:
        self.adapter.fast_publications.enqueue(
            publish_evcs_fields_command({"mode": 1}, priority="live")
        )
        self.adapter.commands.enqueue(
            {
                **gx_relay_refresh_command(0),
                "priority": "safety",
                "not_before": 10_000.0,
            }
        )
        scheduler = self.adapter.write_scheduler
        with (
            patch.object(write_core_module.time, "time", return_value=100.0),
            patch.object(self.adapter.publication_registry, "publish_evcs", return_value="applied"),
        ):
            self.assertEqual(scheduler.process_local_publish_burst(limit=1), 1)

if __name__ == "__main__":
    unittest.main()
