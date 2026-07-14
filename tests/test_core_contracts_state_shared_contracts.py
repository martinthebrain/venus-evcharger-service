# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact contracts for shared State API normalization."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from venus_evcharger.core import contracts_state_shared
from venus_evcharger.core.contracts_state_shared import (
    _normalized_generic_mapping,
    _normalized_state_mapping_fields,
    normalized_state_api_kind,
    normalized_state_api_runtime_fields,
    normalized_state_api_summary_fields,
    normalized_state_api_version,
)


class TestCoreContractsStateSharedContracts(unittest.TestCase):
    def test_kind_and_version_normalizers_forward_supported_values(self) -> None:
        self.assertEqual(normalized_state_api_kind(None), "summary")
        self.assertEqual(normalized_state_api_kind("invalid"), "summary")
        self.assertEqual(normalized_state_api_kind("invalid", default="runtime"), "runtime")
        self.assertEqual(normalized_state_api_kind(" HEALTH ", default="runtime"), "health")
        with patch.object(contracts_state_shared, "STATE_API_VERSIONS", frozenset({"v1", "v2"})):
            self.assertEqual(normalized_state_api_version(" v2 "), "v2")

    def test_summary_envelope_defaults_and_explicit_values(self) -> None:
        self.assertEqual(
            normalized_state_api_summary_fields(None),
            {"ok": True, "api_version": "v1", "kind": "summary", "summary": ""},
        )
        with patch.object(contracts_state_shared, "STATE_API_VERSIONS", frozenset({"v1", "v2"})):
            payload = normalized_state_api_summary_fields(
                {"ok": 0, "api_version": "v2", "kind": "runtime", "summary": " value "}
            )
        self.assertEqual(
            payload,
            {"ok": False, "api_version": "v2", "kind": "runtime", "summary": "value"},
        )

    def test_runtime_envelope_defaults_and_explicit_values(self) -> None:
        self.assertEqual(
            normalized_state_api_runtime_fields(None),
            {"ok": True, "api_version": "v1", "kind": "runtime", "state": {}},
        )
        with patch.object(contracts_state_shared, "STATE_API_VERSIONS", frozenset({"v1", "v2"})):
            payload = normalized_state_api_runtime_fields(
                {"ok": 0, "api_version": "v2", "kind": "health", "state": {1: "value"}}
            )
        self.assertEqual(
            payload,
            {"ok": False, "api_version": "v2", "kind": "health", "state": {1: "value"}},
        )

    def test_generic_mapping_and_mapping_envelope_are_exact(self) -> None:
        self.assertEqual(_normalized_generic_mapping(None), {})
        self.assertEqual(_normalized_generic_mapping({1: "value"}), {"1": "value"})
        self.assertEqual(
            _normalized_state_mapping_fields(None, kind="topology"),
            {"ok": True, "api_version": "v1", "kind": "topology", "state": {}},
        )
        with patch.object(contracts_state_shared, "STATE_API_VERSIONS", frozenset({"v1", "v2"})):
            payload = _normalized_state_mapping_fields(
                {"ok": 0, "api_version": "v2", "kind": "health", "state": {1: "value"}},
                kind="topology",
            )
        self.assertEqual(
            payload,
            {"ok": False, "api_version": "v2", "kind": "health", "state": {"1": "value"}},
        )


if __name__ == "__main__":
    unittest.main()
