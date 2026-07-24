#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Write-scheduler contracts for field-wise fast/durable arbitration."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.dbus_adapter_venus_stubs import install_venus_adapter_stubs

install_venus_adapter_stubs()

from venus_evcharger.dbus_adapter.process.adapter import DbusAdapter
from venus_evcharger.dbus_gateway import gateway_paths
from venus_evcharger.ipc.command_types import CommandFileList
from venus_evcharger.ipc.gateway_publication import publish_evcs_fields_command
from venus_evcharger.ipc.publication_order import (
    PUBLICATION_FIELD_ORDERS_FIELD,
    PUBLICATION_ORDER_FIELD,
)


def _ordered(fields: dict[str, object], order: int) -> dict[str, object]:
    return {
        **publish_evcs_fields_command(fields, priority="live"),
        "created_at": 100.0,
        "deadline_s": 30.0,
        PUBLICATION_ORDER_FIELD: order,
        PUBLICATION_FIELD_ORDERS_FIELD: {
            field: order
            for field in fields
        },
    }


class FastPublicationSchedulerContracts(unittest.TestCase):
    def test_scheduler_remove_facade_preserves_expected_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(
                str(config_path),
                paths=gateway_paths(str(root / "run")),
            )
            command = _ordered({"mode": 1}, 10)
            with patch.object(
                adapter.write_scheduler.command_queue,
                "remove_pending",
                return_value=True,
            ) as remove:
                removed = adapter.write_scheduler.remove_pending(
                    "command.json",
                    command,
                )

        self.assertTrue(removed)
        remove.assert_called_once_with("command.json", command)

    def test_scheduler_applies_only_durable_fields_not_superseded_by_fast(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(
                str(config_path),
                paths=gateway_paths(str(root / "run")),
            )
            with patch(
                "venus_evcharger.ipc.fast_publication.time.time",
                return_value=100.0,
            ):
                self.assertTrue(
                    adapter.fast_publications.enqueue(
                        _ordered({"ac_power_w": 900.0}, 11)
                    ).accepted
                )
            durable = _ordered(
                {"ac_power_w": 100.0, "charged_energy_kwh": 4.2},
                10,
            )
            path = adapter.commands.enqueue(durable)
            pending = adapter.commands.load_pending()
            queue = adapter.write_scheduler.command_queue

            with (
                patch.object(queue, "command_expired", return_value=False),
                patch.object(queue, "command_outcome", return_value="applied") as outcome,
            ):
                result = queue.process_loaded_command(
                    path,
                    pending[0][1],
                    pending_commands=pending,
                )

            applied = outcome.call_args.args[1]
            fast = adapter.fast_publications.pop_next()

        self.assertEqual(result, "applied")
        self.assertEqual(applied["fields"], {"charged_energy_kwh": 4.2})
        self.assertIsNotNone(fast)
        assert fast is not None
        self.assertEqual(fast.command["fields"], {"ac_power_w": 900.0})
        self.assertFalse(Path(path).exists())

    def test_expired_durable_command_is_dropped_before_order_arbitration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(
                str(config_path),
                paths=gateway_paths(str(root / "run")),
            )
            command = _ordered({"mode": 1}, 10)
            command.pop("created_at")
            path = str(root / "missing-anchor.json")
            pending: CommandFileList = [(path, command)]
            queue = adapter.write_scheduler.command_queue

            with patch.object(
                adapter.fast_publications,
                "prepare_durable",
                wraps=adapter.fast_publications.prepare_durable,
            ) as prepare:
                with patch(
                    "venus_evcharger.dbus_adapter.write.core.time.time",
                    return_value=100.0,
                ):
                    result = queue.process_loaded_command(
                        path,
                        pending[0][1],
                        pending_commands=pending,
                    )

        self.assertEqual(result, "dropped")
        prepare.assert_not_called()
        self.assertFalse(Path(path).exists())


if __name__ == "__main__":
    unittest.main()
