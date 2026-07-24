#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for normalized DBus read targets."""

from __future__ import annotations

import unittest

from venus_evcharger.dbus_adapter.read.targets import ReadTarget, read_target
from venus_evcharger.dbus_gateway import dbus_path_key


class DbusAdapterReadTargetContractTests(unittest.TestCase):
    def test_target_normalizes_identity_and_derives_owned_keys(self) -> None:
        target = read_target(" svc ", " /Path ")

        self.assertEqual(target, ReadTarget("svc", "/Path"))
        assert target is not None
        self.assertEqual(target.source, "svc/Path")
        self.assertEqual(target.cache_key, dbus_path_key("svc", "/Path"))

    def test_target_requires_a_service_and_absolute_path(self) -> None:
        invalid = (
            ("", "/Path"),
            ("   ", "/Path"),
            ("svc", ""),
            ("svc", "Path"),
            (None, "/Path"),
            ("svc", None),
            (0, "/Path"),
        )
        for service, path in invalid:
            with self.subTest(service=service, path=path):
                self.assertIsNone(read_target(service, path))

    def test_non_string_inputs_are_normalized_at_the_transport_boundary(self) -> None:
        self.assertEqual(read_target(7, "/8"), ReadTarget("7", "/8"))


if __name__ == "__main__":
    unittest.main()
