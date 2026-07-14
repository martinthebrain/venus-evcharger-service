# SPDX-License-Identifier: GPL-3.0-or-later
"""Complete command and result contracts for the local Control API."""

from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

from venus_evcharger.core import contracts_control
from venus_evcharger.core.contracts_control import (
    _normalized_items,
    _normalized_text,
    normalized_control_api_auth_scope,
    normalized_control_api_error_code,
    normalized_control_api_transport,
    normalized_control_api_version,
    normalized_control_command_fields,
    normalized_control_command_name,
    normalized_control_command_source,
    normalized_control_command_status,
    normalized_control_result_fields,
)


EMPTY_COMMAND = {
    "name": "legacy_unknown_write",
    "path": "",
    "value": None,
    "source": "http",
    "detail": "",
    "command_id": "",
    "idempotency_key": "",
}


class TestCoreContractsControlCommandContracts(unittest.TestCase):
    def test_primitive_control_normalizers(self) -> None:
        self.assertEqual(_normalized_text(None), "")
        self.assertEqual(_normalized_text(" ", "fallback"), "fallback")
        self.assertEqual(_normalized_text(" value ", "fallback"), "value")
        self.assertEqual(_normalized_items([1, 2], (3,)), (1, 2))
        self.assertEqual(_normalized_items((1, 2), (3,)), (1, 2))
        self.assertEqual(_normalized_items({2, 1}, (3,)), (1, 2))
        self.assertEqual(_normalized_items("bad", (3, 4)), (3, 4))
        self.assertEqual(normalized_control_api_version(" v1 "), "v1")
        self.assertEqual(normalized_control_api_version(None), "v1")
        self.assertEqual(normalized_control_api_transport(" HTTP "), "http")
        self.assertEqual(normalized_control_api_transport("mqtt"), "http")

    def test_version_and_transport_forward_supported_values(self) -> None:
        with patch.object(contracts_control, "CONTROL_API_VERSIONS", frozenset({"v1", "v2"})):
            self.assertEqual(normalized_control_api_version(" v2 "), "v2")
        with patch.object(contracts_control, "CONTROL_API_TRANSPORTS", frozenset({"http", "mqtt"})):
            self.assertEqual(normalized_control_api_transport(" MQTT "), "mqtt")

    def test_public_normalizer_defaults_are_part_of_the_contract(self) -> None:
        self.assertIsNone(inspect.signature(normalized_control_api_auth_scope).parameters["default"].default)
        self.assertIsNone(inspect.signature(normalized_control_command_source).parameters["default"].default)
        self.assertIsNone(inspect.signature(normalized_control_command_fields).parameters["default_source"].default)

    def test_error_and_auth_normalizers(self) -> None:
        known = {
            " Invalid-JSON ": "invalid_json",
            "rate limited": "rate_limited",
            "UNAUTHORIZED": "unauthorized",
        }
        for value, expected in known.items():
            self.assertEqual(normalized_control_api_error_code(value), expected)
        self.assertEqual(normalized_control_api_error_code(None), "bad_request")
        self.assertEqual(normalized_control_api_error_code("invalid"), "bad_request")
        for scope in ("read", "control_basic", "control_admin", "update_admin"):
            self.assertEqual(normalized_control_api_auth_scope(scope.upper()), scope)
        self.assertEqual(normalized_control_api_auth_scope("invalid"), "read")
        self.assertEqual(normalized_control_api_auth_scope("invalid", default="control_admin"), "control_admin")
        self.assertEqual(normalized_control_api_auth_scope("invalid", default="invalid"), "read")

    def test_command_name_source_and_status_normalizers(self) -> None:
        self.assertEqual(normalized_control_command_name(" set_mode "), "set_mode")
        self.assertEqual(normalized_control_command_name(None), "legacy_unknown_write")
        self.assertEqual(normalized_control_command_name("invalid"), "legacy_unknown_write")
        for source in ("dbus", "http", "internal", "mqtt"):
            self.assertEqual(normalized_control_command_source(source.upper()), source)
        self.assertEqual(normalized_control_command_source("invalid"), "http")
        self.assertEqual(normalized_control_command_source("invalid", default="dbus"), "dbus")
        self.assertEqual(normalized_control_command_source("invalid", default="invalid"), "http")
        for status in ("accepted_in_flight", "applied", "rejected"):
            self.assertEqual(normalized_control_command_status(f" {status} "), status)
        self.assertEqual(normalized_control_command_status(None), "rejected")
        self.assertEqual(normalized_control_command_status("invalid"), "rejected")

    def test_empty_and_full_command_shapes(self) -> None:
        self.assertEqual(normalized_control_command_fields(None), EMPTY_COMMAND)
        self.assertEqual(
            normalized_control_command_fields(
                {
                    "name": " set_mode ",
                    "path": " /Mode ",
                    "value": {"raw": 2},
                    "source": " MQTT ",
                    "detail": " detail ",
                    "command_id": " command-id ",
                    "idempotency_key": " key ",
                },
                default_source="dbus",
            ),
            {
                "name": "set_mode",
                "path": "/Mode",
                "value": {"raw": 2},
                "source": "mqtt",
                "detail": "detail",
                "command_id": "command-id",
                "idempotency_key": "key",
            },
        )
        self.assertEqual(normalized_control_command_fields({}, default_source="internal")["source"], "internal")

    def test_empty_result_shape(self) -> None:
        self.assertEqual(
            normalized_control_result_fields(None),
            {
                "command": EMPTY_COMMAND,
                "status": "rejected",
                "accepted": False,
                "applied": False,
                "persisted": False,
                "reversible_failure": True,
                "external_side_effect_started": False,
                "detail": "",
            },
        )

    def test_applied_result_shape(self) -> None:
        result = normalized_control_result_fields(
            {
                "command": {"name": "set_mode", "path": "/Mode", "value": 2, "source": "dbus"},
                "status": "applied",
                "persisted": 0,
                "reversible_failure": 1,
                "external_side_effect_started": 1,
                "detail": " done ",
            }
        )
        self.assertEqual(
            result,
            {
                "command": {
                    "name": "set_mode",
                    "path": "/Mode",
                    "value": 2,
                    "source": "dbus",
                    "detail": "",
                    "command_id": "",
                    "idempotency_key": "",
                },
                "status": "applied",
                "accepted": True,
                "applied": True,
                "persisted": True,
                "reversible_failure": False,
                "external_side_effect_started": True,
                "detail": "done",
            },
        )

    def test_in_flight_and_rejected_result_shapes(self) -> None:
        in_flight = normalized_control_result_fields(
            {
                "status": "accepted_in_flight",
                "persisted": 1,
                "reversible_failure": 1,
                "external_side_effect_started": 0,
            }
        )
        self.assertEqual(
            {key: in_flight[key] for key in ("status", "accepted", "applied", "persisted", "reversible_failure", "external_side_effect_started")},
            {
                "status": "accepted_in_flight",
                "accepted": True,
                "applied": False,
                "persisted": False,
                "reversible_failure": False,
                "external_side_effect_started": True,
            },
        )
        rejected = normalized_control_result_fields(
            {"status": "rejected", "reversible_failure": 0, "external_side_effect_started": 1}
        )
        self.assertEqual(
            {key: rejected[key] for key in ("status", "accepted", "applied", "persisted", "reversible_failure", "external_side_effect_started")},
            {
                "status": "rejected",
                "accepted": False,
                "applied": False,
                "persisted": False,
                "reversible_failure": False,
                "external_side_effect_started": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
