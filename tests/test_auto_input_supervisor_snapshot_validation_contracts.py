# SPDX-License-Identifier: GPL-3.0-or-later
"""Public contract scenarios for auto-input snapshot validation."""

from __future__ import annotations

import math
import unittest

from tests.support.auto_input_supervisor import AutoInputSupervisorServiceFake, valid_snapshot
from venus_evcharger.inputs.supervisor import AutoInputSupervisor
from venus_evcharger.inputs.supervisor_contracts import SnapshotPayload
from venus_evcharger.inputs.supervisor_snapshot_validation import AutoInputSnapshotValidator
from venus_evcharger.inputs.supervisor_snapshot_values import (
    copied_object_mapping,
    is_object_list,
    is_object_mapping,
    snapshot_int,
    snapshot_number,
    snapshot_payload,
    snapshot_timestamp,
)


class TestAutoInputSupervisorSnapshotValidationContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.service = AutoInputSupervisorServiceFake()
        self.validator = AutoInputSnapshotValidator(self.service, AutoInputSupervisor.SCHEMA)

    def assert_invalid(self, payload: object, warning_key: str) -> None:
        before = len(self.service.runtime.warnings)
        self.assertIsNone(self.validator.validate("snapshot.json", payload))
        self.assertEqual(len(self.service.runtime.warnings), before + 1)
        self.assertEqual(self.service.runtime.warnings[-1][0], warning_key)
        self.assertEqual(self.service.runtime.warnings[-1][1], 5.0)

    def test_scalar_normalizers_reject_non_finite_boolean_and_container_values(self) -> None:
        invalid_values: tuple[object, ...] = (None, True, False, "bad", math.nan, math.inf, {}, [])
        for value in invalid_values:
            self.assertIsNone(snapshot_timestamp(value))
        self.assertEqual(snapshot_timestamp("12.5"), 12.5)
        self.assertEqual(snapshot_number(bytearray(b"3.5")), 3.5)
        self.assertEqual(snapshot_int(b"7"), 7)
        self.assertEqual(snapshot_int(7.9), 7)
        invalid_int_values: tuple[object, ...] = (None, True, "bad", {}, [])
        for value in invalid_int_values:
            self.assertIsNone(snapshot_int(value))

    def test_json_boundary_normalizers_copy_only_supported_shapes(self) -> None:
        source: dict[object, object] = {"a": 1, 2: "ignored"}
        self.assertTrue(is_object_mapping(source))
        self.assertFalse(is_object_mapping([]))
        self.assertTrue(is_object_list([1]))
        self.assertFalse(is_object_list((1,)))
        self.assertEqual(snapshot_payload(source), {"a": 1})
        self.assertIsNone(snapshot_payload([]))
        copied = copied_object_mapping({"a": 1})
        self.assertEqual(copied, {"a": 1})
        self.assertIsNone(copied_object_mapping("bad"))

    def test_valid_snapshot_is_normalized_without_aliasing_nested_objects(self) -> None:
        source_item: dict[str, object] = {"source_id": "battery"}
        profile_item: dict[str, object] = {"sample_count": 2}
        payload = valid_snapshot(
            captured_at="100",
            heartbeat_at=b"100",
            writer_pid="4321",
            helper_generation="1",
            battery_combined_soc="63.5",
            battery_source_count=-3,
            battery_sources=[source_item, "opaque"],
            battery_learning_profiles={1: profile_item, "opaque": 4},
        )
        normalized = self.validator.validate("snapshot.json", payload)
        self.assertIsNotNone(normalized)
        assert normalized is not None
        self.assertEqual(normalized["captured_at"], 100.0)
        self.assertEqual(normalized["battery_combined_soc"], 63.5)
        self.assertEqual(normalized["battery_source_count"], 0)
        self.assertEqual(normalized["battery_online_source_count"], 0)
        sources = normalized["battery_sources"]
        profiles = normalized["battery_learning_profiles"]
        self.assertTrue(is_object_list(sources))
        self.assertTrue(is_object_mapping(profiles))
        assert is_object_list(sources)
        assert is_object_mapping(profiles)
        self.assertIsNot(sources[0], source_item)
        self.assertIsNot(profiles["1"], profile_item)

    def test_shape_contract_rejects_non_object_missing_keys_and_versions(self) -> None:
        self.assert_invalid([], "auto-input-helper-invalid")
        missing = valid_snapshot()
        del missing["grid_power"]
        self.assert_invalid(missing, "auto-input-helper-schema-invalid")
        for version in (True, None, "bad", AutoInputSupervisor.SCHEMA.version + 1):
            self.assert_invalid(valid_snapshot(snapshot_version=version), "auto-input-helper-version-invalid")

    def test_identity_contract_rejects_each_invalid_boundary(self) -> None:
        for writer_pid in (None, True, 0, -1, "bad"):
            self.assert_invalid(valid_snapshot(writer_pid=writer_pid), "auto-input-helper-schema-invalid")
        for generation in (None, True, -1, "bad"):
            self.assert_invalid(valid_snapshot(helper_generation=generation), "auto-input-helper-schema-invalid")
        for runtime_instance in (None, 1, "", "   "):
            self.assert_invalid(
                valid_snapshot(runtime_instance_id=runtime_instance),
                "auto-input-helper-schema-invalid",
            )

    def test_numeric_and_count_contracts_reject_malformed_values(self) -> None:
        for key in ("captured_at", "pv_power", "battery_combined_soc"):
            self.assert_invalid(valid_snapshot(**{key: "bad"}), "auto-input-helper-schema-invalid")
        for count in (True, "bad", object()):
            self.assert_invalid(
                valid_snapshot(battery_source_count=count),
                "auto-input-helper-schema-invalid",
            )

    def test_temporal_contracts_reject_reversal_and_newer_source_samples(self) -> None:
        self.assert_invalid(
            valid_snapshot(captured_at=101.0, heartbeat_at=100.0),
            "auto-input-helper-schema-invalid",
        )
        self.assert_invalid(
            valid_snapshot(pv_captured_at=101.0),
            "auto-input-helper-schema-invalid",
        )
        normalized = self.validator.validate(
            "snapshot.json",
            valid_snapshot(captured_at=100.0, heartbeat_at=100.0, pv_captured_at=100.0),
        )
        self.assertIsNotNone(normalized)

    def test_source_values_and_timestamps_are_atomic_pairs(self) -> None:
        cases: tuple[SnapshotPayload, ...] = (
            valid_snapshot(pv_power=None),
            valid_snapshot(pv_captured_at=None),
            valid_snapshot(battery_soc=None),
            valid_snapshot(battery_captured_at=None),
            valid_snapshot(grid_power=None),
            valid_snapshot(grid_captured_at=None),
        )
        for payload in cases:
            self.assert_invalid(payload, "auto-input-helper-schema-invalid")
        self.assertIsNotNone(
            self.validator.validate(
                "snapshot.json",
                valid_snapshot(pv_power=None, pv_captured_at=None),
            )
        )

    def test_battery_soc_contract_accepts_boundaries_and_rejects_out_of_range(self) -> None:
        for soc in (0.0, 100.0):
            self.assertIsNotNone(self.validator.validate("snapshot.json", valid_snapshot(battery_soc=soc)))
        for soc in (-0.1, 100.1):
            self.assert_invalid(valid_snapshot(battery_soc=soc), "auto-input-helper-schema-invalid")
        self.assert_invalid(
            valid_snapshot(battery_combined_soc=100.1),
            "auto-input-helper-schema-invalid",
        )
        self.assertIsNotNone(
            self.validator.validate("snapshot.json", valid_snapshot(battery_combined_soc=None))
        )

    def test_structured_payload_contract_rejects_wrong_container_types(self) -> None:
        self.assert_invalid(valid_snapshot(battery_sources="bad"), "auto-input-helper-schema-invalid")
        self.assert_invalid(
            valid_snapshot(battery_learning_profiles=[]),
            "auto-input-helper-schema-invalid",
        )


if __name__ == "__main__":
    unittest.main()
