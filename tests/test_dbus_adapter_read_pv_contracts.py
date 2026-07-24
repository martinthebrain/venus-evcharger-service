#!/usr/bin/env python3
"""Pure contracts for automatic PV source selection."""

from __future__ import annotations

import unittest

from venus_evcharger.dbus_adapter.read.pv import (
    _stripped_text,
    ac_pv_members,
    dc_pv_members,
    dc_pv_target,
    pv_total_members,
    use_dc_pv,
)
from venus_evcharger.dbus_adapter.read.spec import read_spec_from_mapping


class DbusAdapterReadPvContractTests(unittest.TestCase):
    def test_ac_and_dc_candidates_are_combined_without_availability_policy(self) -> None:
        spec = read_spec_from_mapping(
            {
                "path": "/Ac/Power",
                "dc_service": "com.victronenergy.system",
                "dc_path": "/Dc/Pv/Power",
                "use_dc_pv": True,
            }
        )

        self.assertEqual(
            ac_pv_members(spec, ["pv.b", "pv.a"]),
            [("pv.b", "/Ac/Power"), ("pv.a", "/Ac/Power")],
        )
        self.assertEqual(
            dc_pv_members(spec),
            [("com.victronenergy.system", "/Dc/Pv/Power")],
        )
        self.assertEqual(
            pv_total_members(spec, ["pv.a"]),
            [
                ("pv.a", "/Ac/Power"),
                ("com.victronenergy.system", "/Dc/Pv/Power"),
            ],
        )

    def test_invalid_or_disabled_dc_configuration_has_no_candidate(self) -> None:
        empty = read_spec_from_mapping({})
        disabled = read_spec_from_mapping(
            {
                "dc_service": "com.victronenergy.system",
                "dc_path": "/Dc/Pv/Power",
                "use_dc_pv": False,
            }
        )
        invalid_path = read_spec_from_mapping(
            {
                "dc_service": "com.victronenergy.system",
                "dc_path": "Dc/Pv/Power",
                "use_dc_pv": True,
            }
        )

        self.assertFalse(use_dc_pv(empty))
        self.assertEqual(ac_pv_members(empty, ["pv.a"]), [])
        self.assertFalse(use_dc_pv(disabled))
        self.assertEqual(dc_pv_members(disabled), [])
        self.assertIsNone(dc_pv_target(invalid_path))
        self.assertEqual(dc_pv_members(invalid_path), [])
        self.assertEqual(_stripped_text(object()), "")


if __name__ == "__main__":
    unittest.main()
