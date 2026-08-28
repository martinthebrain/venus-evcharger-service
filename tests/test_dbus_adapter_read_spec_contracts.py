#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Boundary contracts for typed gateway read specifications."""

from __future__ import annotations

import unittest

from venus_evcharger.dbus_adapter.read.spec import (
    ReadSpec,
    read_spec_from_mapping,
    read_spec_optional_confidence,
    read_spec_optional_zero_on_error,
    read_spec_source,
    read_spec_stale_after_seconds,
    read_spec_text,
)


class DbusAdapterReadSpecContractTests(unittest.TestCase):
    def test_mapping_parser_preserves_every_supported_field_and_copies_paths(self) -> None:
        paths = ["/A", "/B"]
        aggregate_paths = ["/Ac/PvOnGrid/Total/Power"]
        spec = read_spec_from_mapping(
            {
                "aggregate": "pv-total",
                "aggregate_paths": aggregate_paths,
                "aggregate_service": "com.victronenergy.system",
                "dc_path": "/Dc/Pv/Power",
                "dc_service": "com.victronenergy.system",
                "interval": 2,
                "optional_confidence": 0.35,
                "optional_zero_on_error": False,
                "path": "/Ac/Power",
                "paths": paths,
                "prefix": "com.victronenergy.pvinverter",
                "priority": "read",
                "service": "svc",
                "stale_after_seconds": 6,
                "use_dc_pv": True,
            }
        )
        paths.append("/C")
        aggregate_paths.append("/Ac/PvOnOutput/Total/Power")

        self.assertEqual(
            spec,
            {
                "aggregate": "pv-total",
                "aggregate_paths": ["/Ac/PvOnGrid/Total/Power"],
                "aggregate_service": "com.victronenergy.system",
                "dc_path": "/Dc/Pv/Power",
                "dc_service": "com.victronenergy.system",
                "interval": 2.0,
                "optional_confidence": 0.35,
                "optional_zero_on_error": False,
                "path": "/Ac/Power",
                "paths": ["/A", "/B"],
                "prefix": "com.victronenergy.pvinverter",
                "priority": "read",
                "service": "svc",
                "stale_after_seconds": 6.0,
                "use_dc_pv": True,
            },
        )

    def test_mapping_parser_rejects_unknown_or_mistyped_fields_exactly(self) -> None:
        invalid = (
            ({"unexpected": "value"}, KeyError, "unknown read spec field: unexpected"),
            ({"service": object()}, TypeError, "read spec field service must be str, got object"),
            ({"interval": True}, TypeError, "read spec field interval must be float, got bool"),
            ({"paths": ["/ok", 1]}, TypeError, "read spec field paths must be list[str], got list"),
            ({"paths": ("/ok",)}, TypeError, "read spec field paths must be list[str], got tuple"),
            ({"use_dc_pv": 1}, TypeError, "read spec field use_dc_pv must be bool, got int"),
            (
                {"optional_confidence": True},
                TypeError,
                "read spec field optional_confidence must be float, got bool",
            ),
        )
        for mapping, exception_type, message in invalid:
            with self.subTest(mapping=mapping):
                with self.assertRaises(exception_type) as caught:
                    read_spec_from_mapping(mapping)
                self.assertEqual(str(caught.exception).strip("'"), message)

    def test_read_helpers_apply_explicit_defaults_precedence_and_bounds(self) -> None:
        self.assertEqual(read_spec_text(ReadSpec(service="  svc  "), "service"), "svc")
        self.assertEqual(read_spec_text(ReadSpec(), "service"), "")

        self.assertEqual(read_spec_stale_after_seconds(ReadSpec(stale_after_seconds=-2.0)), 0.0)
        self.assertEqual(read_spec_stale_after_seconds(ReadSpec(interval=2.0)), 6.0)
        self.assertEqual(read_spec_stale_after_seconds(ReadSpec(interval=0.1)), 1.0)
        self.assertIsNone(read_spec_stale_after_seconds(ReadSpec()))

        self.assertFalse(read_spec_optional_zero_on_error(ReadSpec()))
        self.assertFalse(read_spec_optional_zero_on_error(ReadSpec(optional_zero_on_error=False)))
        self.assertTrue(read_spec_optional_zero_on_error(ReadSpec(optional_zero_on_error=True)))
        for value in ("1", "true", "yes", "on"):
            with self.subTest(optional_zero_alias=value):
                self.assertTrue(
                    read_spec_optional_zero_on_error(
                        {"optional_zero_on_error": value},
                    )
                )

        self.assertEqual(read_spec_optional_confidence(ReadSpec()), 0.2)
        self.assertEqual(read_spec_optional_confidence(ReadSpec(optional_confidence=0.0)), 0.2)
        self.assertEqual(read_spec_optional_confidence(ReadSpec(optional_confidence=0.75)), 0.75)
        self.assertEqual(read_spec_optional_confidence({"optional_confidence": "0.5"}), 0.5)
        with self.assertRaisesRegex(
            TypeError,
            "^read spec field optional_confidence must be numeric, got object$",
        ):
            read_spec_optional_confidence({"optional_confidence": object()})

        self.assertEqual(
            read_spec_source(ReadSpec(service=" service ", prefix="prefix"), fallback="fallback"),
            "service",
        )
        self.assertEqual(read_spec_source(ReadSpec(prefix=" prefix "), fallback="fallback"), "prefix")
        self.assertEqual(read_spec_source(ReadSpec(), fallback="fallback"), "fallback")
        self.assertEqual(read_spec_source(ReadSpec()), "")


if __name__ == "__main__":
    unittest.main()
