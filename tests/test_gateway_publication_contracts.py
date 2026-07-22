# SPDX-License-Identifier: GPL-3.0-or-later
"""Contract tests for semantic gateway publication envelopes."""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from dataclasses import replace
from typing import cast

from venus_evcharger.ipc.gateway_publication import (
    parse_publish_companion_fields,
    parse_publish_evcs_fields,
    parse_register_companion,
    parse_register_evcs,
    publish_companion_fields_command,
    publish_evcs_fields_command,
    register_companion_command,
    register_evcs_command,
)
from venus_evcharger.ports.gateway_publication import (
    CompanionServiceIdentity,
    EvcsServiceIdentity,
    PublicationPriority,
)


class GatewayPublicationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evcs_identity = EvcsServiceIdentity(
            product_name="EVCS",
            custom_name="Garage",
            firmware_version="1.2.3",
            hardware_version="relay",
            serial="evcs-60",
            connection_name="Local controller",
            process_name="venus_evcharger_service.py",
            process_version="Python",
        )
        self.companion_identity = CompanionServiceIdentity(
            service_id="aggregate-battery",
            kind="battery",
            product_name="External Energy Battery",
            custom_name="Garage Battery",
            firmware_version="1.2.3",
            hardware_version="virtual",
            serial="battery-100",
            connection_name="External energy companion",
            process_name="venus_evcharger_service.py",
            process_version="Python",
        )

    def test_evcs_registration_round_trip(self) -> None:
        payload = register_evcs_command(self.evcs_identity, {"mode": 0, "connected": 1})

        parsed = parse_register_evcs(payload)

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.identity, self.evcs_identity)
        self.assertEqual(parsed.initial_fields, {"mode": 0, "connected": 1})
        self.assertEqual(payload["coalesce_key"], "gateway-publication:evcs:registration")

    def test_evcs_publication_round_trip_preserves_priority(self) -> None:
        payload = publish_evcs_fields_command({"ac_power_w": 1234.0}, priority="critical")

        parsed = parse_publish_evcs_fields(payload)

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.fields, {"ac_power_w": 1234.0})
        self.assertEqual(parsed.priority, "critical")
        self.assertEqual(payload["priority"], "safety")

    def test_companion_registration_round_trip_has_opaque_identity(self) -> None:
        payload = register_companion_command(self.companion_identity, {"connected": 0, "soc_percent": None})

        parsed = parse_register_companion(payload)

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.identity, self.companion_identity)
        self.assertNotIn("service", payload)
        self.assertNotIn("path", payload)

    def test_companion_publication_round_trip_is_scoped_by_service_id(self) -> None:
        payload = publish_companion_fields_command(
            "source:pv-roof",
            {"ac_power_w": 750.0},
            priority="live",
        )

        parsed = parse_publish_companion_fields(payload)

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.service_id, "source:pv-roof")
        self.assertEqual(parsed.fields, {"ac_power_w": 750.0})
        self.assertEqual(payload["priority"], "publish")

    def test_builders_reject_empty_or_malformed_semantics(self) -> None:
        invalid_identity = replace(self.companion_identity, service_id="has whitespace")
        with self.assertRaises(ValueError):
            register_companion_command(invalid_identity, {"connected": 1})
        with self.assertRaises(ValueError):
            publish_companion_fields_command("", {"connected": 1}, priority="live")
        with self.assertRaises(ValueError):
            publish_evcs_fields_command({}, priority="live")
        with self.assertRaises(ValueError):
            publish_evcs_fields_command({" mode": 1}, priority="live")
        with self.assertRaises(ValueError):
            publish_evcs_fields_command({"mode": 1}, priority=cast(PublicationPriority, "urgent"))

    def test_parsers_reject_wrong_kind_or_malformed_payloads(self) -> None:
        self.assertIsNone(parse_register_evcs({"kind": "other"}))
        self.assertIsNone(parse_publish_evcs_fields({"kind": "other"}))
        self.assertIsNone(parse_register_companion({"kind": "other"}))
        self.assertIsNone(parse_publish_companion_fields({"kind": "other"}))

        evcs = register_evcs_command(self.evcs_identity, {"mode": 0})
        raw_evcs_identity = evcs["identity"]
        assert isinstance(raw_evcs_identity, Mapping)
        invalid_evcs_identity = {str(key): value for key, value in raw_evcs_identity.items()}
        invalid_evcs_identity["unexpected"] = -1
        evcs["identity"] = invalid_evcs_identity
        self.assertIsNone(parse_register_evcs(evcs))

        del invalid_evcs_identity["unexpected"]
        invalid_evcs_identity["product_name"] = 42
        self.assertIsNone(parse_register_evcs(evcs))
        evcs["identity"] = {1: "not-string-key"}
        self.assertIsNone(parse_register_evcs(evcs))

        publish = publish_evcs_fields_command({"mode": 0}, priority="diagnostic")
        publish["publication_priority"] = "unexpected"
        self.assertIsNone(parse_publish_evcs_fields(publish))

        companion = register_companion_command(self.companion_identity, {"connected": 0})
        raw_identity = companion["identity"]
        self.assertIsInstance(raw_identity, Mapping)
        assert isinstance(raw_identity, Mapping)
        identity = {str(key): value for key, value in raw_identity.items()}
        identity["kind"] = "charger"
        companion["identity"] = identity
        self.assertIsNone(parse_register_companion(companion))
        companion["identity"] = None
        self.assertIsNone(parse_register_companion(companion))

        companion_publish = publish_companion_fields_command(
            "aggregate-battery",
            {"connected": 1},
            priority="live",
        )
        companion_publish["service_id"] = "bad id"
        self.assertIsNone(parse_publish_companion_fields(companion_publish))
        companion_publish["service_id"] = "aggregate-battery"
        companion_publish["fields"] = None
        self.assertIsNone(parse_publish_companion_fields(companion_publish))
        companion_publish["fields"] = {1: "invalid-key"}
        self.assertIsNone(parse_publish_companion_fields(companion_publish))

        publish["publication_priority"] = "live"
        publish["fields"] = {}
        self.assertIsNone(parse_publish_evcs_fields(publish))


if __name__ == "__main__":
    unittest.main()
