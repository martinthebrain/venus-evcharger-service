#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for immediate and in-flight gateway command execution."""

from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from venus_evcharger.dbus_adapter.contracts import CommandExecution


class CommandExecutionContractTests(unittest.TestCase):
    """Keep durable command ownership explicit across asynchronous callbacks."""

    def test_immediate_execution_is_complete_and_preserves_outcome(self) -> None:
        execution = CommandExecution.immediate("dropped")

        self.assertEqual(execution.outcome, "dropped")
        self.assertIs(execution.in_flight, False)
        self.assertFalse(hasattr(execution, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            setattr(execution, "outcome", "applied")

    def test_pending_execution_retains_durable_command_ownership(self) -> None:
        execution = CommandExecution.pending()

        self.assertEqual(execution.outcome, "deferred")
        self.assertIs(execution.in_flight, True)


if __name__ == "__main__":
    unittest.main()
