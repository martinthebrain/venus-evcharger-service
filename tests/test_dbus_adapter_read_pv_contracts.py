#!/usr/bin/env python3
"""Pure contracts for automatic PV source selection."""

from __future__ import annotations

import unittest

from venus_evcharger.dbus_adapter.read.pv import pv_member_in_backoff
from venus_evcharger.dbus_gateway import dbus_path_key


class DbusAdapterReadPvContractTests(unittest.TestCase):
    def test_backoff_rejects_non_numeric_probe_timestamps(self) -> None:
        service = "com.victronenergy.pvinverter.test"
        path = "/Ac/Power"
        key = dbus_path_key(service, path)

        for next_probe_at in (False, "invalid", object()):
            with self.subTest(next_probe_at=next_probe_at):
                self.assertFalse(
                    pv_member_in_backoff(
                        {
                            key: {
                                "source_state": "unavailable",
                                "next_probe_at": next_probe_at,
                            }
                        },
                        service,
                        path,
                        now=0.5,
                    )
                )


if __name__ == "__main__":
    unittest.main()
