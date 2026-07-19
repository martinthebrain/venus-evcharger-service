# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter command inbox and rate limiter contracts."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    DbusCommandInbox,
    DbusOperationDeferred,
    DbusRateLimiter,
    GatewayAdapterContractCase,
    Path,
    tempfile,
)


class GatewayCommandRateCases(GatewayAdapterContractCase):
    """Exercise command inbox and rate limiter contracts."""

    def test_coalesced_commands_use_stable_filename_and_latest_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = DbusCommandInbox(str(Path(temp_dir) / "commands"))
            first = inbox.enqueue({"kind": "set_value", "value": 1, "created_at": 10.0, "coalesce_key": "ev:/Mode"})
            second = inbox.enqueue({"kind": "set_value", "value": 2, "created_at": 20.0, "coalesce_key": "ev:/Mode"})

            self.assertEqual(first, second)
            pending = inbox.load_pending()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0][1]["value"], 2)
            self.assertEqual(pending[0][1]["created_at"], 10.0)
            self.assertEqual(pending[0][1]["lifecycle_state"], "coalesced")
            self.assertGreater(pending[0][1]["updated_at"], 0.0)
            self.assertTrue(Path(first).name.startswith("coalesced-"))

    def test_coalesce_key_overrides_explicit_command_id_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = DbusCommandInbox(str(Path(temp_dir) / "commands"))
            first = inbox.enqueue({"id": "manual-a", "kind": "set_value", "value": 1, "coalesce_key": "ev:/Mode"})
            second = inbox.enqueue({"id": "manual-b", "kind": "set_value", "value": 2, "coalesce_key": "ev:/Mode"})

            self.assertEqual(first, second)
            self.assertTrue(Path(first).name.startswith("coalesced-"))
            self.assertFalse((Path(temp_dir) / "commands" / "manual-a.json").exists())
            self.assertFalse((Path(temp_dir) / "commands" / "manual-b.json").exists())

    def test_coalesced_physical_file_keeps_higher_priority_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = DbusCommandInbox(str(Path(temp_dir) / "commands"))
            path = inbox.enqueue(
                {"kind": "set_value", "value": "off", "priority": "safety", "coalesce_key": "relay:/StartStop"}
            )
            same_path = inbox.enqueue(
                {"kind": "set_value", "value": "on", "priority": "diagnostic", "coalesce_key": "relay:/StartStop"}
            )

            self.assertEqual(path, same_path)
            self.assertEqual(inbox.load_pending()[0][1]["value"], "off")

            inbox.enqueue(
                {"kind": "set_value", "value": "manual", "priority": "user", "coalesce_key": "relay:/StartStop"}
            )
            self.assertEqual(inbox.load_pending()[0][1]["value"], "off")

            inbox.enqueue(
                {"kind": "set_value", "value": "new-off", "priority": "safety", "coalesce_key": "relay:/StartStop"}
            )
            self.assertEqual(inbox.load_pending()[0][1]["value"], "new-off")

    def test_rate_limiter_defers_without_sleeping(self) -> None:
        default_limiter = DbusRateLimiter()
        self.assertEqual(default_limiter.intervals, {"read": 0.25, "write": 0.35, "introspection": 2.0})
        self.assertTrue(default_limiter.due("read", now=0.0))

        limiter = DbusRateLimiter(read_interval_seconds=10.0)
        self.assertEqual(limiter.intervals, {"read": 10.0, "write": 0.35, "introspection": 2.0})
        self.assertEqual(limiter.next_at, {"read": 0.0, "write": 0.0, "introspection": 0.0})
        limiter.require_due("read")
        with self.assertRaises(DbusOperationDeferred) as deferred:
            limiter.require_due("read")
        self.assertEqual(deferred.exception.args, ("read",))
        limiter.mark("read", now=1.0)
        self.assertFalse(limiter.due("read", now=2.0))
        self.assertTrue(limiter.due("read", now=11.0))
        limiter.mark("write", now=5.0)
        limiter.mark("introspection", now=5.0)
        self.assertEqual(limiter.next_at["write"], 5.35)
        self.assertEqual(limiter.next_at["introspection"], 7.0)

        clamped = DbusRateLimiter(
            read_interval_seconds=-1.0,
            write_interval_seconds=-2.0,
            introspection_interval_seconds=-3.0,
        )
        self.assertEqual(clamped.intervals, {"read": 0.0, "write": 0.0, "introspection": 0.0})
