"""Port and wire-contract tests for generic Shelly configuration."""

from __future__ import annotations

import ast
import inspect
import math
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import venus_evcharger_generic_shelly_configuration as entrypoint
from venus_evcharger.dbus_gateway_client import GatewayClient, GatewayGenericShellyConfigurationClient
from venus_evcharger.ops import disable_generic_shelly_once as helper
from venus_evcharger.ipc.generic_shelly_configuration import (
    DISABLE_MATCHING_GENERIC_SHELLY_ONCE_KIND,
    GENERIC_SHELLY_CONFIGURATION_QUEUE_CLASS,
    disable_matching_generic_shelly_once_command,
    parse_disable_matching_generic_shelly_once,
)
from venus_evcharger.ports.generic_shelly_configuration import (
    DisableMatchingGenericShellyOnceRequest,
    GenericShellyConfigurationPort,
    GenericShellyConfigurationReceipt,
    GenericShellyDeviceSelector,
    GenericShellySelectorKind,
    generic_shelly_device_selector,
    normalize_mac_address,
)


class _ConfigurationPort:
    def disable_matching_device_channel_once(
        self,
        request: DisableMatchingGenericShellyOnceRequest,
    ) -> GenericShellyConfigurationReceipt:
        del request
        return GenericShellyConfigurationReceipt(True, "command")


def _request(*, kind: GenericShellySelectorKind = "ip", value: str = "192.0.2.9", channel: int = 2) -> DisableMatchingGenericShellyOnceRequest:
    return DisableMatchingGenericShellyOnceRequest(GenericShellyDeviceSelector(kind, value), channel)


class GenericShellyConfigurationPortTests(unittest.TestCase):
    def test_ops_module_cannot_regain_gateway_or_dbus_knowledge(self) -> None:
        source = inspect.getsource(helper)
        tree = ast.parse(source)
        forbidden_symbols = {
            "DbusCacheStore",
            "GatewayClient",
            "dbus_path_key",
            "gateway_paths",
            "get_dbus_child_nodes",
            "get_dbus_value",
            "set_dbus_value",
        }
        forbidden_literals = {"set_value", "introspect", "/Devices"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                self.assertNotIn(node.id, forbidden_symbols)
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, forbidden_symbols)
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                self.assertNotIn(node.value, forbidden_literals)
                self.assertFalse(node.value.startswith("com.victronenergy."))
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn("dbus_gateway", node.module or "")
                self.assertNotIn("dbus_adapter", node.module or "")
        for legacy_name in forbidden_symbols:
            self.assertFalse(hasattr(helper, legacy_name))

    def test_selectors_are_canonical_and_ip_has_precedence(self) -> None:
        self.assertEqual(GenericShellyDeviceSelector("ip", " 2001:0db8::1 ").value, "2001:db8::1")
        self.assertEqual(GenericShellyDeviceSelector("mac", "aa:bb-cc.dd ee ff").value, "AABBCCDDEEFF")
        self.assertEqual(normalize_mac_address("aa bb cc dd ee ff"), "AABBCCDDEEFF")
        selected = generic_shelly_device_selector(target_ip="192.0.2.1", target_mac="AABBCCDDEEFF")
        self.assertEqual((selected.kind, selected.value), ("ip", "192.0.2.1"))
        selected = generic_shelly_device_selector(target_ip="", target_mac="aa-bb-cc-dd-ee-ff")
        self.assertEqual((selected.kind, selected.value), ("mac", "AABBCCDDEEFF"))

    def test_invalid_selectors_and_channels_fail_at_the_port_boundary(self) -> None:
        invalid_selectors = (
            ("ip", "hostname"),
            ("mac", "AABB"),
            ("mac", "GG:BB:CC:DD:EE:FF"),
        )
        for kind, value in invalid_selectors:
            with self.assertRaises(ValueError):
                GenericShellyDeviceSelector(cast(GenericShellySelectorKind, kind), value)
        with self.assertRaises(ValueError):
            GenericShellyDeviceSelector(cast(GenericShellySelectorKind, "serial"), "value")
        with self.assertRaises(TypeError):
            GenericShellyDeviceSelector("ip", cast(str, 7))
        with self.assertRaises(TypeError):
            normalize_mac_address(cast(str, 7))
        with self.assertRaises(ValueError):
            generic_shelly_device_selector(target_ip="", target_mac="")
        for channel in (0, -1, True):
            with self.assertRaises(ValueError):
                DisableMatchingGenericShellyOnceRequest(GenericShellyDeviceSelector("ip", "192.0.2.1"), channel)
        with self.assertRaises(TypeError):
            DisableMatchingGenericShellyOnceRequest(cast(GenericShellyDeviceSelector, "selector"), 1)

    def test_receipts_cannot_misrepresent_acceptance(self) -> None:
        self.assertEqual(GenericShellyConfigurationReceipt(True, " command ").command_id, "command")
        self.assertEqual(GenericShellyConfigurationReceipt(False, reason=" blocked ").reason, "blocked")
        invalid = (
            (True, "", ""),
            (True, "id", "reason"),
            (False, "id", "reason"),
            (False, "", ""),
        )
        for accepted, command_id, reason in invalid:
            with self.assertRaises(ValueError):
                GenericShellyConfigurationReceipt(accepted, command_id, reason)
        with self.assertRaises(TypeError):
            GenericShellyConfigurationReceipt(cast(bool, 1), "id")
        with self.assertRaises(TypeError):
            GenericShellyConfigurationReceipt(True, cast(str, 7))
        with self.assertRaises(TypeError):
            GenericShellyConfigurationReceipt(False, reason=cast(str, 7))
        self.assertIsInstance(_ConfigurationPort(), GenericShellyConfigurationPort)


class GenericShellyConfigurationIpcTests(unittest.TestCase):
    def test_builder_and_parser_define_one_exact_semantic_command(self) -> None:
        request = _request()
        payload = disable_matching_generic_shelly_once_command(request)
        self.assertEqual(
            payload,
            {
                "kind": DISABLE_MATCHING_GENERIC_SHELLY_ONCE_KIND,
                "source": "generic-shelly-configuration",
                "selector": {"kind": "ip", "value": "192.0.2.9"},
                "channel": 2,
                "execution": "once",
                "persistence": "persistent",
                "priority": "user",
                "coalesce_key": "generic-shelly-configuration:disable-once:ip:192.0.2.9:channel:2",
            },
        )
        parsed = parse_disable_matching_generic_shelly_once(payload)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.request, request)

    def test_parser_accepts_only_a_complete_valid_transport_envelope(self) -> None:
        payload = disable_matching_generic_shelly_once_command(_request(kind="mac", value="AABBCCDDEEFF"))
        envelope = {
            **payload,
            "schema_version": 1,
            "id": "command-1",
            "created_at": 10.0,
            "queue_class": GENERIC_SHELLY_CONFIGURATION_QUEUE_CLASS,
            "lifecycle_state": "queued",
            "updated_at": 11.0,
        }
        parsed = parse_disable_matching_generic_shelly_once(envelope)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.request.selector.value, "AABBCCDDEEFF")

        allowed_states = ("queued", "coalesced", "deferred", "applied", "dropped", "expired")
        for state in allowed_states:
            self.assertIsNotNone(parse_disable_matching_generic_shelly_once({**envelope, "lifecycle_state": state}))

    def test_parser_rejects_malformed_field_sets_selectors_and_channels(self) -> None:
        valid = disable_matching_generic_shelly_once_command(_request())
        malformed = [
            {key: value for key, value in valid.items() if key != "channel"},
            {**valid, "unknown": True},
            {**valid, "schema_version": 1},
            {**valid, "selector": None},
            {**valid, "selector": {"kind": "ip"}},
            {**valid, "selector": {1: "ip", "value": "192.0.2.9"}},
            {**valid, "selector": {"kind": "serial", "value": "value"}},
            {**valid, "selector": {"kind": "ip", "value": 7}},
            {**valid, "selector": {"kind": "ip", "value": "hostname"}},
            {**valid, "selector": {"kind": "ip", "value": " 192.0.2.9 "}},
            {**valid, "selector": {"kind": "mac", "value": "aa:bb:cc:dd:ee:ff"}},
            {**valid, "channel": "2"},
            {**valid, "channel": 0},
        ]
        for payload in malformed:
            self.assertIsNone(parse_disable_matching_generic_shelly_once(payload))

    def test_parser_rejects_every_mutated_semantic_envelope_field(self) -> None:
        valid = disable_matching_generic_shelly_once_command(_request())
        mutations = {
            "kind": "other",
            "source": "other",
            "execution": "repeat",
            "persistence": "volatile",
            "priority": "diagnostic",
            "coalesce_key": "wrong",
        }
        for field, value in mutations.items():
            self.assertIsNone(parse_disable_matching_generic_shelly_once({**valid, field: value}))

    def test_parser_rejects_every_invalid_transport_header(self) -> None:
        valid = {
            **disable_matching_generic_shelly_once_command(_request()),
            "schema_version": 1,
            "id": "command-1",
            "created_at": 10.0,
            "queue_class": GENERIC_SHELLY_CONFIGURATION_QUEUE_CLASS,
            "lifecycle_state": "queued",
        }
        mutations = (
            ("schema_version", True),
            ("schema_version", 0),
            ("schema_version", 2),
            ("id", " "),
            ("created_at", True),
            ("created_at", 0.0),
            ("created_at", math.inf),
            ("queue_class", "diagnostic"),
            ("lifecycle_state", 1),
            ("lifecycle_state", "unknown"),
            ("updated_at", 0.0),
            ("updated_at", math.nan),
        )
        for field, value in mutations:
            self.assertIsNone(parse_disable_matching_generic_shelly_once({**valid, field: value}))
        self.assertIsNotNone(parse_disable_matching_generic_shelly_once({**valid, "updated_at": None}))


class GenericShellyConfigurationClientTests(unittest.TestCase):
    def test_gateway_client_returns_exact_acceptance_receipts(self) -> None:
        transport = MagicMock(spec=GatewayClient)
        transport.enqueue_command.return_value = "/run/gateway/command-17.json"
        client = GatewayGenericShellyConfigurationClient(cast(GatewayClient, transport))

        receipt = client.disable_matching_device_channel_once(_request())

        self.assertEqual(receipt, GenericShellyConfigurationReceipt(True, "command-17"))
        command = transport.enqueue_command.call_args.args[0]
        self.assertEqual(command["kind"], DISABLE_MATCHING_GENERIC_SHELLY_ONCE_KIND)
        self.assertNotIn("service", command)
        self.assertNotIn("path", command)

        transport.enqueue_command.return_value = ""
        rejected = client.disable_matching_device_channel_once(_request())
        self.assertFalse(rejected.accepted)
        self.assertEqual(rejected.reason, "gateway did not accept the configuration command")

    def test_entrypoint_composes_the_gateway_port_once(self) -> None:
        gateway = object()
        port = object()
        with (
            patch.object(entrypoint, "GatewayClient", return_value=gateway) as gateway_factory,
            patch.object(
                entrypoint,
                "GatewayGenericShellyConfigurationClient",
                return_value=port,
            ) as port_factory,
            patch.object(entrypoint, "configuration_main", return_value=7) as workflow,
        ):
            result = entrypoint.main(("config.ini",))

        self.assertEqual(result, 7)
        gateway_factory.assert_called_once_with()
        port_factory.assert_called_once_with(gateway)
        workflow.assert_called_once_with(("config.ini",), configuration_port=port)

    def test_deployment_uses_only_the_composition_entrypoint(self) -> None:
        root = Path(__file__).resolve().parents[1]
        entrypoint_path = "venus_evcharger_generic_shelly_configuration.py"
        for relative_path in (
            "deploy/venus/install_venus_evcharger_service.sh",
            "deploy/venus/boot_venus_evcharger_service.sh",
        ):
            text = (root / relative_path).read_text(encoding="utf-8")
            self.assertIn(entrypoint_path, text)
            active_lines = [line for line in text.splitlines() if line.startswith("GENERIC_SHELLY_HELPER=")]
            self.assertEqual(len(active_lines), 1)
            self.assertNotIn("venus_evcharger/ops/disable_generic_shelly_once.py", active_lines[0])


if __name__ == "__main__":
    unittest.main()
