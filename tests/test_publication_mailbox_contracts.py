#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Durable field merging and tick-local mailbox snapshot contracts."""

from __future__ import annotations

import fcntl
import json
import multiprocessing
import os
import tempfile
import time
import unittest
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Protocol
from unittest.mock import patch

from tests.dbus_adapter_venus_stubs import install_venus_adapter_stubs

install_venus_adapter_stubs()

from venus_evcharger.dbus_adapter.process.adapter import DbusAdapter
from venus_evcharger.dbus_gateway import gateway_paths
from venus_evcharger.dbus_gateway_commands import DbusGatewayCommandInbox
from venus_evcharger.ipc.command_mailbox import (
    MAILBOX_LOCK_RETRY_SECONDS,
    MAILBOX_REVISION_FIELD,
    MailboxLockTimeout,
    write_command_json,
)
from venus_evcharger.ipc.gateway_publication import publish_evcs_fields_command
from venus_evcharger.ipc.pending_snapshot import TickPendingSnapshotProvider
from venus_evcharger.ipc.publication_order import (
    PUBLICATION_FIELD_ORDERS_FIELD,
    PUBLICATION_ORDER_FIELD,
)
from venus_evcharger.ipc.publication_payload import MAX_PUBLICATION_FIELDS_PER_KEY


class _ProcessEvent(Protocol):
    def wait(self, timeout: float | None = None) -> bool: ...
    def set(self) -> None: ...


def _ordered_publication(
    fields: Mapping[str, object],
    order: int,
    *,
    created_at: float,
    priority: str = "publish",
) -> dict[str, object]:
    command = publish_evcs_fields_command(fields, priority="live")
    command["priority"] = priority
    command["created_at"] = created_at
    command[PUBLICATION_ORDER_FIELD] = order
    command[PUBLICATION_FIELD_ORDERS_FIELD] = {
        field: order
        for field in fields
    }
    return command


def _enqueue_publication_worker(
    command_dir: str,
    field: str,
    order: int,
    start: _ProcessEvent,
) -> None:
    start.wait()
    DbusGatewayCommandInbox(command_dir).enqueue(
        _ordered_publication(
            {field: order},
            order,
            created_at=float(order),
        )
    )


def _hold_mailbox_lock(
    lock_path: str,
    ready: _ProcessEvent,
) -> None:
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    ready.set()


class DurablePublicationMailboxContracts(unittest.TestCase):
    def test_equal_priority_commands_merge_nonoverlapping_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = DbusGatewayCommandInbox(temp_dir)
            inbox.enqueue(
                _ordered_publication({"mode": 1}, 10, created_at=10.0)
            )
            inbox.enqueue(
                _ordered_publication({"ac_power_w": 900.0}, 11, created_at=11.0)
            )
            pending = inbox.load_pending()

        self.assertEqual(len(pending), 1)
        self.assertEqual(
            pending[0][1]["fields"],
            {"mode": 1, "ac_power_w": 900.0},
        )
        self.assertEqual(
            pending[0][1][PUBLICATION_FIELD_ORDERS_FIELD],
            {"mode": 10, "ac_power_w": 11},
        )

    def test_lower_priority_adds_new_fields_without_overriding_existing_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = DbusGatewayCommandInbox(temp_dir)
            path = inbox.enqueue(
                _ordered_publication(
                    {"mode": 1},
                    10,
                    created_at=10.0,
                    priority="safety",
                )
            )
            stale = inbox.load_pending()[0][1]
            inbox.enqueue(
                _ordered_publication(
                    {"mode": 2, "ac_power_w": 500.0},
                    11,
                    created_at=11.0,
                    priority="diagnostic",
                )
            )
            command = inbox.load_pending()[0][1]
            stale_remove = inbox.remove_if_current(path, stale)

        self.assertEqual(command["priority"], "safety")
        self.assertEqual(command["fields"], {"mode": 1, "ac_power_w": 500.0})
        self.assertFalse(stale_remove)
        self.assertEqual(
            command[PUBLICATION_FIELD_ORDERS_FIELD],
            {"mode": 10, "ac_power_w": 11},
        )

    def test_rejected_growth_keeps_the_existing_durable_payload(self) -> None:
        fields = {
            f"field_{index}": index
            for index in range(MAX_PUBLICATION_FIELDS_PER_KEY)
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = DbusGatewayCommandInbox(temp_dir)
            path = inbox.enqueue(
                _ordered_publication(fields, 10, created_at=10.0)
            )
            before = Path(path).read_bytes()
            with self.assertRaisesRegex(ValueError, "field-limit"):
                inbox.enqueue(
                    _ordered_publication(
                        {"overflow": 1},
                        11,
                        created_at=11.0,
                    )
                )
            after = Path(path).read_bytes()

        self.assertEqual(after, before)

    def test_cross_process_coalescing_keeps_every_independent_field(self) -> None:
        context = multiprocessing.get_context("fork")
        with tempfile.TemporaryDirectory() as temp_dir:
            start = context.Event()
            processes = [
                context.Process(
                    target=_enqueue_publication_worker,
                    args=(temp_dir, f"field_{index}", index + 1, start),
                )
                for index in range(8)
            ]
            for process in processes:
                process.start()
            start.set()
            for process in processes:
                process.join(5.0)
            pending = DbusGatewayCommandInbox(temp_dir).load_pending()

        self.assertTrue(all(process.exitcode == 0 for process in processes))
        self.assertEqual(len(pending), 1)
        self.assertEqual(
            pending[0][1]["fields"],
            {f"field_{index}": index + 1 for index in range(8)},
        )

    def test_conditional_remove_cannot_delete_a_newer_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = DbusGatewayCommandInbox(temp_dir)
            path = inbox.enqueue(
                _ordered_publication({"mode": 1}, 10, created_at=10.0)
            )
            stale = inbox.load_pending()[0][1]
            inbox.enqueue(
                _ordered_publication({"ac_power_w": 500.0}, 11, created_at=11.0)
            )

            self.assertFalse(inbox.remove_if_current(path, stale))
            current = inbox.load_pending()[0][1]
            self.assertTrue(Path(path).exists())
            self.assertTrue(inbox.remove_if_current(path, current))
            self.assertFalse(Path(path).exists())

    def test_conditional_replace_cannot_overwrite_a_newer_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = DbusGatewayCommandInbox(temp_dir)
            path = inbox.enqueue(
                {
                    "kind": "set_target",
                    "priority": "user",
                    "coalesce_key": "target:relay",
                    "created_at": 10.0,
                    "phase": "write",
                    "value": 0,
                }
            )
            stale = inbox.load_pending()[0][1]
            inbox.enqueue(
                {
                    "kind": "set_target",
                    "priority": "user",
                    "coalesce_key": "target:relay",
                    "created_at": 11.0,
                    "phase": "write",
                    "value": 1,
                }
            )
            current = inbox.load_pending()[0][1]

            self.assertFalse(
                inbox.replace_if_current(
                    path,
                    stale,
                    {**dict(stale), "phase": "verify"},
                )
            )
            self.assertEqual(inbox.load_pending()[0][1], current)

    def test_conditional_replace_preserves_mailbox_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = DbusGatewayCommandInbox(temp_dir)
            path = inbox.enqueue(
                {
                    "kind": "set_target",
                    "priority": "user",
                    "coalesce_key": "target:relay",
                    "created_at": 10.0,
                    "phase": "write",
                    "value": 1,
                }
            )
            current = inbox.load_pending()[0][1]
            identity_keys = (
                "schema_version",
                "id",
                "created_at",
                MAILBOX_REVISION_FIELD,
                "queue_class",
            )

            self.assertTrue(
                inbox.replace_if_current(
                    path,
                    current,
                    {
                        **dict(current),
                        "phase": "verify",
                        "schema_version": -1,
                        "id": "replacement-id",
                        "created_at": 99.0,
                        MAILBOX_REVISION_FIELD: "replacement-revision",
                        "queue_class": "replacement-queue",
                    },
                )
            )
            rewritten = inbox.load_pending()[0][1]

        self.assertEqual(rewritten["phase"], "verify")
        self.assertEqual(
            {key: rewritten[key] for key in identity_keys},
            {key: current[key] for key in identity_keys},
        )

    def test_conditional_remove_accepts_a_matching_legacy_file_without_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = DbusGatewayCommandInbox(temp_dir)
            path = str(Path(temp_dir) / "legacy.json")
            legacy = publish_evcs_fields_command({"mode": 1}, priority="live")
            write_command_json(path, legacy)

            self.assertTrue(inbox.remove_if_current(path, legacy))

        self.assertFalse(Path(path).exists())

    def test_revision_identifies_generation_independently_of_payload_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = DbusGatewayCommandInbox(temp_dir)
            path = inbox.enqueue(
                _ordered_publication({"mode": 1}, 10, created_at=10.0)
            )
            expected = inbox.load_pending()[0][1]
            current = dict(expected)
            current["fields"] = {"mode": 2}
            write_command_json(path, current)

            self.assertTrue(inbox.remove_if_current(path, expected))

    def test_invalid_revision_uses_exact_legacy_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = DbusGatewayCommandInbox(temp_dir)
            path = str(Path(temp_dir) / "invalid-revision.json")
            expected = {"kind": "legacy", MAILBOX_REVISION_FIELD: 1, "value": 1}
            current = {**expected, "value": 2}
            write_command_json(path, current)

            self.assertFalse(inbox.remove_if_current(path, expected))
            self.assertTrue(Path(path).exists())

    def test_remove_coalesced_never_matches_missing_key_fallback_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = DbusGatewayCommandInbox(temp_dir)
            path = str(Path(temp_dir) / "plain.json")
            write_command_json(path, {"kind": "plain"})

            self.assertEqual(inbox.remove_coalesced("XXXX"), 0)
            self.assertTrue(Path(path).exists())

    def test_command_json_preserves_list_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "command.json"
            write_command_json(str(path), {"values": [1, "two"]})

            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"values": [1, "two"]},
            )

    def test_zero_timeout_expires_at_the_exact_deadline_without_sleeping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = DbusGatewayCommandInbox(temp_dir, lock_timeout_seconds=0.0)
            with (
                patch(
                    "venus_evcharger.ipc.command_mailbox.fcntl.flock",
                    side_effect=BlockingIOError,
                ),
                patch(
                    "venus_evcharger.ipc.command_mailbox.time.monotonic",
                    side_effect=(10.0, 10.0),
                ),
                patch(
                    "venus_evcharger.ipc.command_mailbox.time.sleep",
                    side_effect=AssertionError("expired lock must not sleep"),
                ),
            ):
                with self.assertRaisesRegex(
                    MailboxLockTimeout,
                    "^Command mailbox lock timed out$",
                ):
                    inbox.enqueue(
                        _ordered_publication({"mode": 1}, 10, created_at=10.0)
                    )

    def test_contended_lock_retries_once_before_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = DbusGatewayCommandInbox(temp_dir, lock_timeout_seconds=1.0)
            with (
                patch(
                    "venus_evcharger.ipc.command_mailbox.fcntl.flock",
                    side_effect=(BlockingIOError, None, None),
                ) as flock,
                patch(
                    "venus_evcharger.ipc.command_mailbox.time.monotonic",
                    side_effect=(10.0, 10.5),
                ),
                patch("venus_evcharger.ipc.command_mailbox.time.sleep") as sleep,
            ):
                path = inbox.enqueue(
                    _ordered_publication({"mode": 1}, 10, created_at=10.0)
                )

            self.assertTrue(Path(path).is_file())
            self.assertEqual(flock.call_count, 3)
            sleep.assert_called_once_with(MAILBOX_LOCK_RETRY_SECONDS)

    def test_lock_wait_is_bounded_and_process_exit_releases_the_lock(self) -> None:
        context = multiprocessing.get_context("fork")
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = str(Path(temp_dir) / ".mailbox.lock")
            descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            inbox = DbusGatewayCommandInbox(
                temp_dir,
                lock_timeout_seconds=0.01,
            )
            started = time.monotonic()
            with self.assertRaisesRegex(
                MailboxLockTimeout,
                "^Command mailbox lock timed out$",
            ):
                inbox.enqueue(
                    _ordered_publication({"mode": 1}, 10, created_at=10.0)
                )
            self.assertLess(time.monotonic() - started, 0.2)
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

            ready = context.Event()
            holder = context.Process(
                target=_hold_mailbox_lock,
                args=(lock_path, ready),
            )
            holder.start()
            self.assertTrue(ready.wait(2.0))
            holder.join(2.0)
            self.assertEqual(holder.exitcode, 0)
            path = inbox.enqueue(
                _ordered_publication({"mode": 2}, 11, created_at=11.0)
            )

        self.assertTrue(Path(path).name.endswith(".json"))


class PendingSnapshotContracts(unittest.TestCase):
    def test_provider_decodes_once_per_tick_and_never_caches_across_ticks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = DbusGatewayCommandInbox(temp_dir)
            inbox.enqueue(
                _ordered_publication({"mode": 1}, 10, created_at=10.0)
            )
            provider = TickPendingSnapshotProvider(inbox)
            with (
                patch.object(inbox, "load_pending", wraps=inbox.load_pending) as load,
                patch.object(inbox, "coalesce", wraps=inbox.coalesce) as coalesce,
            ):
                first = provider.begin_tick()
                self.assertIs(provider.snapshot(), first)
                inbox.enqueue(
                    {
                        **_ordered_publication(
                            {"ac_power_w": 20.0},
                            11,
                            created_at=11.0,
                        ),
                        "coalesce_key": "second-key",
                    }
                )
                self.assertEqual(len(provider.snapshot().physical), 1)
                self.assertEqual(load.call_count, 1)
                self.assertEqual(coalesce.call_count, 1)

                provider.end_tick()
                refreshed = provider.begin_tick()
                self.assertEqual(len(refreshed.physical), 2)
                self.assertEqual(load.call_count, 2)
                self.assertEqual(coalesce.call_count, 2)
                provider.end_tick()

        self.assertIsInstance(first.physical[0][1], MappingProxyType)

    def test_provider_removal_replaces_the_active_immutable_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = DbusGatewayCommandInbox(temp_dir)
            path = inbox.enqueue(
                _ordered_publication({"mode": 1}, 10, created_at=10.0)
            )
            provider = TickPendingSnapshotProvider(inbox)
            before = provider.begin_tick()
            provider.remove(path, before.physical[0][1])
            after = provider.snapshot()
            provider.end_tick()

        self.assertEqual(len(before.physical), 1)
        self.assertEqual(after.physical, ())
        self.assertEqual(after.effective, ())
        self.assertFalse(Path(path).exists())

    def test_scheduler_and_health_share_the_active_gateway_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(
                str(config_path),
                paths=gateway_paths(str(root / "run")),
            )
            adapter.commands.enqueue(
                _ordered_publication({"mode": 1}, 10, created_at=10.0)
            )
            with (
                patch.object(
                    adapter.commands,
                    "load_pending",
                    wraps=adapter.commands.load_pending,
                ) as load,
                patch.object(
                    adapter.commands,
                    "coalesce",
                    wraps=adapter.commands.coalesce,
                ) as coalesce,
            ):
                adapter.write_scheduler.begin_tick()
                try:
                    self.assertIsNotNone(
                        adapter.write_scheduler.command_queue.next_local_publish_command()
                    )
                    adapter.health_snapshot()
                finally:
                    adapter.write_scheduler.end_tick()

        self.assertEqual(load.call_count, 1)
        self.assertEqual(coalesce.call_count, 1)


if __name__ == "__main__":
    unittest.main()
