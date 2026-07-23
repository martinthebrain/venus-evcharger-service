# SPDX-License-Identifier: GPL-3.0-or-later
"""Edge contracts for transport-neutral gateway boundaries."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, patch

import venus_evcharger.dbus_gateway_client as gateway_client_module
import venus_evcharger.dbus_gateway_surface as gateway_surface_module
from venus_evcharger.dbus_gateway_client import GatewayClient, GatewayOperationsClient
from venus_evcharger.dbus_gateway_core import normalized_object_mapping
from venus_evcharger.dbus_gateway_surface import (
    evcs_path_freshness_kind,
    venus_control_route,
    venus_path_for_control_target,
)
from venus_evcharger.ipc.core_commands import core_control_command_payload
from venus_evcharger.ipc.gateway_operations import (
    ess_grid_setpoint_command,
    gx_relay_set_command,
    parse_ess_grid_setpoint,
    parse_gx_relay_refresh,
    parse_gx_relay_set,
)
from venus_evcharger.ipc.gateway_pressure import read_gateway_pressure_snapshot
from venus_evcharger.ports.gateway_operations import (
    GatewayOperationsPort,
    GxRelaySetRequest,
    UnavailableGatewayOperations,
    require_gateway_operations,
)
from venus_evcharger.ports.gateway_publication import (
    GatewayPublicationPort,
    PublicationReceipt,
    require_gateway_publication,
)


class _PublicationPort:
    def register_evcs(self, identity: object, initial_fields: object) -> PublicationReceipt:
        del identity, initial_fields
        return PublicationReceipt(True)

    def publish_evcs_fields(self, fields: object, *, priority: object) -> PublicationReceipt:
        del fields, priority
        return PublicationReceipt(True)

    def register_companion(self, identity: object, initial_fields: object) -> PublicationReceipt:
        del identity, initial_fields
        return PublicationReceipt(True)

    def publish_companion_fields(
        self,
        service_id: str,
        fields: object,
        *,
        priority: object,
    ) -> PublicationReceipt:
        del service_id, fields, priority
        return PublicationReceipt(True)


class GatewayBoundaryEdgeContractTests(unittest.TestCase):
    def test_gateway_core_normalizes_only_mappings(self) -> None:
        self.assertIsNone(normalized_object_mapping([("key", "value")]))
        self.assertEqual(normalized_object_mapping({1: "value"}), {"1": "value"})

    def test_surface_contract_exposes_routes_paths_and_freshness_classes(self) -> None:
        route = venus_control_route("/Mode")
        self.assertIsNotNone(route)
        assert route is not None
        self.assertEqual((route.name, route.target), ("set_mode", "mode"))
        self.assertIsNone(venus_control_route("/Unknown"))
        self.assertEqual(venus_path_for_control_target("mode"), "/Mode")
        self.assertEqual(venus_path_for_control_target("unknown"), "")
        self.assertEqual(evcs_path_freshness_kind("/ProductName"), "static")
        self.assertEqual(evcs_path_freshness_kind("/Auto/Health"), "diagnostic")
        self.assertEqual(evcs_path_freshness_kind("/Ac/Power"), "local_owned")

    def test_surface_contract_rejects_domain_and_gateway_target_drift(self) -> None:
        gateway_surface_module._validate_control_target_coverage({"mode": object()}, {"mode": object()})
        with self.assertRaisesRegex(ValueError, "out of sync"):
            gateway_surface_module._validate_control_target_coverage(
                {"mode": object()},
                {"mode": object(), "enable": object()},
            )

    def test_core_command_builder_rejects_unsupported_semantic_routes(self) -> None:
        valid_auto = core_control_command_payload(
            "set_auto_runtime_setting",
            "auto_min_soc",
            50,
            source="control-surface",
            origin="test",
        )
        valid_current = core_control_command_payload(
            "set_current_setting",
            "set_current",
            10,
            source="control-surface",
            origin="test",
        )
        self.assertEqual(valid_auto["target"], "auto_min_soc")
        self.assertEqual(valid_current["target"], "set_current")
        with self.assertRaisesRegex(ValueError, "Unsupported core control route"):
            core_control_command_payload(
                "set_mode",
                "set_current",
                1,
                source="control-surface",
                origin="test",
            )

    def test_gateway_operation_parsers_ignore_commands_owned_by_other_handlers(self) -> None:
        unrelated = {"kind": "other"}
        self.assertIsNone(parse_gx_relay_refresh(unrelated))
        self.assertIsNone(parse_gx_relay_set(unrelated))
        self.assertIsNone(parse_ess_grid_setpoint(unrelated))

    def test_gateway_operation_builders_reject_invalid_typed_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "ESS setpoint intent"):
            ess_grid_setpoint_command(1.0, intent=cast(object, "invalid"))
        with self.assertRaisesRegex(TypeError, "watts must be numeric"):
            ess_grid_setpoint_command(cast(float, True), intent="tracking")
        with self.assertRaisesRegex(TypeError, "ensure_manual must be bool"):
            gx_relay_set_command(
                0,
                "NO",
                True,
                ensure_manual=cast(bool, 1),
                verify_settle_seconds=0.0,
                verify_retry_seconds=0.0,
            )

    def test_pressure_reader_normalizes_all_resource_states_and_foreign_timestamps(self) -> None:
        cases = (
            ({"captured_at": object(), "resources": {}}, ("unknown", "unknown")),
            ({"resources": {"state": "constrained"}}, ("slow", "resources")),
            ({"resources": {"state": "ok"}}, ("ok", "resources")),
            ({"resources": {"state": "idle"}}, ("unknown", "unknown")),
        )
        with patch("venus_evcharger.ipc.gateway_pressure._read_json") as read_json:
            for payload, expected in cases:
                with self.subTest(payload=payload):
                    read_json.return_value = payload
                    snapshot = read_gateway_pressure_snapshot(
                        "/run/gateway-health.json",
                        now=100.0,
                        max_age_seconds=10.0,
                    )
                    self.assertEqual((snapshot.state, snapshot.source), expected)

    def test_operations_client_normalizes_boolean_relay_state(self) -> None:
        client = MagicMock(spec=GatewayClient)
        operations = GatewayOperationsClient(client)
        with patch.object(gateway_client_module, "gateway_value", return_value=True):
            self.assertEqual(operations.read_gx_relay_state(0, max_age_seconds=2.0), 1)
        with patch.object(gateway_client_module, "gateway_value", return_value=0):
            self.assertEqual(operations.read_gx_relay_state(0, max_age_seconds=2.0), 0)
        client.enqueue_command.assert_not_called()

    def test_unavailable_operations_are_explicit_and_boundary_checked(self) -> None:
        unavailable = UnavailableGatewayOperations()
        request = GxRelaySetRequest(0, "NO", True, True, 0.1, 0.2)
        self.assertIsNone(unavailable.read_gx_relay_state(0, max_age_seconds=2.0))
        self.assertFalse(unavailable.set_gx_relay_enabled(request).accepted)
        self.assertFalse(unavailable.set_ess_grid_setpoint(1.0, intent="tracking").accepted)
        self.assertIs(require_gateway_operations(SimpleNamespace(gateway_operations=unavailable)), unavailable)
        self.assertIsInstance(unavailable, GatewayOperationsPort)
        with self.assertRaisesRegex(RuntimeError, "operations are not configured"):
            require_gateway_operations(SimpleNamespace())

    def test_publication_port_boundary_accepts_only_complete_semantic_ports(self) -> None:
        publication = _PublicationPort()
        self.assertIsInstance(publication, GatewayPublicationPort)
        self.assertIs(
            require_gateway_publication(SimpleNamespace(gateway_publication=publication)),
            publication,
        )
        with self.assertRaisesRegex(RuntimeError, "publication is not configured"):
            require_gateway_publication(SimpleNamespace(gateway_publication=object()))


if __name__ == "__main__":
    unittest.main()
