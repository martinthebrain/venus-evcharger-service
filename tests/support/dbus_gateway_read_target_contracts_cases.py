# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter read target and aggregate value contracts."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    GatewayAdapterContractCase,
    read_aggregate_module,
    read_targets_module,
)


class GatewayReadTargetContractCases(GatewayAdapterContractCase):
    """Exercise read target and aggregate value contracts."""

    def test_read_target_contract_requires_service_and_absolute_path(self) -> None:
        target = read_targets_module.read_target(" svc ", " /Path ")
        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.service, "svc")
        self.assertEqual(target.path, "/Path")
        self.assertEqual(target.source, "svc/Path")
        self.assertEqual(target.cache_key, "path:svc/Path")
        self.assertIsNone(read_targets_module.read_target("", "/Path"))
        self.assertIsNone(read_targets_module.read_target("svc", "Path"))
        self.assertIsNone(read_targets_module.read_target("svc", ""))

    def test_aggregate_signature_members_accepts_only_matching_complete_signatures(self) -> None:
        self.assertIsNone(read_aggregate_module.aggregate_signature_members(None, "pv-total"))
        self.assertIsNone(read_aggregate_module.aggregate_signature_members(("pv-total", (), "extra"), "pv-total"))
        self.assertIsNone(read_aggregate_module.aggregate_signature_members(("sum", (("svc", "/Path"),)), "pv-total"))
        self.assertIsNone(read_aggregate_module.aggregate_signature_members(("pv-total", ["svc"]), "pv-total"))
        self.assertIsNone(read_aggregate_module.aggregate_signature_members(("pv-total", ("svc",)), "pv-total"))
        self.assertIsNone(
            read_aggregate_module.aggregate_signature_members(("pv-total", (("svc", "/Path", "x"),)), "pv-total")
        )
        self.assertEqual(
            read_aggregate_module.aggregate_signature_members(("pv-total", (("svc", "/Path"),)), "pv-total"),
            [("svc", "/Path")],
        )

    def test_aggregate_member_float_rejects_non_numeric_values(self) -> None:
        self.assertEqual(read_aggregate_module.aggregate_member_float(True), 1.0)
        self.assertEqual(read_aggregate_module.aggregate_member_float("2.5"), 2.5)
        self.assertEqual(read_aggregate_module.aggregate_member_float(b"3.5"), 3.5)
        with self.assertRaises(TypeError):
            read_aggregate_module.aggregate_member_float(object())
