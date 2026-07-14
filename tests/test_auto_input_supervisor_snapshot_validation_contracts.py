# SPDX-License-Identifier: GPL-3.0-or-later
import math
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from venus_evcharger.inputs.supervisor_snapshot_validation import _AutoInputSupervisorSnapshotValidation


class _ValidationHarness(_AutoInputSupervisorSnapshotValidation):
    SNAPSHOT_SCHEMA_VERSION = 1
    SNAPSHOT_SOURCE_KEYS = ("pv", "battery", "grid")
    SNAPSHOT_REQUIRED_KEYS = frozenset(
        {
            "snapshot_version",
            "captured_at",
            "heartbeat_at",
            "pv_captured_at",
            "pv_power",
            "battery_captured_at",
            "battery_soc",
            "grid_captured_at",
            "grid_power",
            "writer_pid",
            "helper_generation",
            "runtime_instance_id",
        }
    )

    def __init__(self, service: object) -> None:
        self.service = service


def _snapshot(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "snapshot_version": 1,
        "captured_at": 100.0,
        "heartbeat_at": 100.0,
        "pv_captured_at": 100.0,
        "pv_power": 2300.0,
        "battery_captured_at": 100.0,
        "battery_soc": 57.0,
        "grid_captured_at": 100.0,
        "grid_power": -2100.0,
        "writer_pid": 4321,
        "helper_generation": 1,
        "runtime_instance_id": "instance-1",
    }
    payload.update(overrides)
    return payload


def _harness() -> tuple[_ValidationHarness, SimpleNamespace]:
    service = SimpleNamespace(auto_input_helper_restart_seconds=0.5, _warning_throttled=MagicMock())
    return _ValidationHarness(service), service


class TestAutoInputSupervisorSnapshotValidationContracts(unittest.TestCase):
    def test_scalar_coercers_reject_boolean_non_finite_and_invalid_values(self) -> None:
        for value in (None, True, False, "bad", math.nan, math.inf, -math.inf):
            self.assertIsNone(_ValidationHarness._coerce_snapshot_timestamp(value))
        self.assertEqual(_ValidationHarness._coerce_snapshot_timestamp("2.5"), 2.5)
        self.assertEqual(_ValidationHarness._coerce_snapshot_number(-3), -3.0)
        self.assertEqual(_ValidationHarness._validate_snapshot_version("2"), 2)
        self.assertIsNone(_ValidationHarness._validate_snapshot_version(True))
        self.assertIsNone(_ValidationHarness._validate_snapshot_version("bad"))
        for value in (None, True, False, "bad"):
            self.assertIsNone(_ValidationHarness._coerce_snapshot_int(value))
        self.assertEqual(_ValidationHarness._coerce_snapshot_int("7"), 7)

    def test_invalid_snapshot_emits_the_complete_warning_contract(self) -> None:
        harness, service = _harness()
        self.assertIsNone(harness._invalid_snapshot("warning", "path", "message %s %s", "a", 2))
        service._warning_throttled.assert_called_once_with("warning", 1.0, "message %s %s", "path", "a", 2)

    def test_field_normalization_covers_success_absence_and_exact_failure(self) -> None:
        harness, service = _harness()
        snapshot = {"a": "2.5", "b": None}
        normalized: dict[str, object] = {}
        self.assertTrue(
            harness._normalize_snapshot_fields(
                "path", snapshot, normalized, ("a", "b"), harness._coerce_snapshot_number, "numeric"
            )
        )
        self.assertEqual(normalized, {"a": 2.5, "b": None})

        normalized = {}
        self.assertFalse(
            harness._normalize_snapshot_fields(
                "path", {"a": "bad"}, normalized, ("a",), harness._coerce_snapshot_number, "numeric"
            )
        )
        self.assertEqual(normalized, {})
        service._warning_throttled.assert_called_once_with(
            "auto-input-helper-schema-invalid",
            1.0,
            "Auto input helper snapshot %s has invalid %s field %s=%r",
            "path",
            "numeric",
            "a",
            "bad",
        )

    def test_temporal_order_requires_both_timestamps_and_accepts_equality(self) -> None:
        harness, service = _harness()
        valid = {"captured_at": 10.0, "heartbeat_at": 10.0}
        self.assertIs(harness._validate_snapshot_temporal_order("path", valid), valid)
        for invalid in (
            {"captured_at": None, "heartbeat_at": 10.0},
            {"captured_at": 10.0, "heartbeat_at": None},
        ):
            service._warning_throttled.reset_mock()
            self.assertIsNone(harness._validate_snapshot_temporal_order("path", invalid))
            self.assertIn("requires numeric", service._warning_throttled.call_args.args[2])
        service._warning_throttled.reset_mock()
        self.assertIsNone(
            harness._validate_snapshot_temporal_order("path", {"captured_at": 10.0, "heartbeat_at": 9.999})
        )
        self.assertIn("older than", service._warning_throttled.call_args.args[2])

    def test_each_source_timestamp_must_not_exceed_capture_or_heartbeat(self) -> None:
        harness, service = _harness()
        base = {
            "captured_at": 10.0,
            "heartbeat_at": 11.0,
            "pv_captured_at": None,
            "battery_captured_at": None,
            "grid_captured_at": None,
        }
        self.assertIs(harness._validate_source_timestamps("path", base), base)
        for key in ("pv_captured_at", "battery_captured_at", "grid_captured_at"):
            candidate = dict(base, captured_at=10.0, heartbeat_at=10.0)
            candidate[key] = 10.0
            self.assertIs(harness._validate_source_timestamps("path", candidate), candidate)
        for key in ("pv_captured_at", "battery_captured_at", "grid_captured_at"):
            for value in (10.001, 11.001):
                candidate = dict(base, captured_at=11.0, heartbeat_at=10.0)
                candidate[key] = value
                service._warning_throttled.reset_mock()
                self.assertIsNone(harness._validate_source_timestamps("path", candidate))
                self.assertEqual(service._warning_throttled.call_args.args[-1], key)

    def test_each_source_requires_value_and_timestamp_as_a_pair(self) -> None:
        harness, service = _harness()
        normalized = {
            "pv_captured_at": None,
            "pv_power": None,
            "battery_captured_at": None,
            "battery_soc": None,
            "grid_captured_at": None,
            "grid_power": None,
        }
        self.assertIs(harness._validate_source_value_timestamp_pairs("path", normalized), normalized)
        pairs = (
            ("pv_power", "pv_captured_at"),
            ("battery_soc", "battery_captured_at"),
            ("grid_power", "grid_captured_at"),
        )
        for value_key, timestamp_key in pairs:
            for value, timestamp in ((1.0, None), (None, 1.0)):
                candidate = dict(normalized)
                candidate[value_key] = value
                candidate[timestamp_key] = timestamp
                service._warning_throttled.reset_mock()
                self.assertIsNone(harness._validate_source_value_timestamp_pairs("path", candidate))
                self.assertEqual(service._warning_throttled.call_args.args[-2:], (value_key, timestamp_key))

    def test_battery_soc_contract_accepts_boundaries_and_rejects_each_invalid_field(self) -> None:
        harness, service = _harness()
        snapshot = {"battery_soc": 50.0, "battery_combined_soc": 60.0}
        for soc in (None, 0.0, 100.0):
            normalized = {"battery_soc": soc, "battery_combined_soc": None}
            self.assertIs(harness._validate_snapshot_battery_soc("path", snapshot, normalized), normalized)
        for soc in (-0.001, 100.001):
            service._warning_throttled.reset_mock()
            self.assertIsNone(
                harness._validate_snapshot_battery_soc(
                    "path", {**snapshot, "battery_soc": soc}, {"battery_soc": soc, "battery_combined_soc": None}
                )
            )
            self.assertIn("battery_soc", service._warning_throttled.call_args.args[2])
        for combined_soc in (0.0, 100.0):
            normalized = {"battery_soc": 50.0, "battery_combined_soc": combined_soc}
            self.assertIs(harness._validate_snapshot_battery_soc("path", snapshot, normalized), normalized)
        service._warning_throttled.reset_mock()
        self.assertIsNone(
            harness._validate_snapshot_battery_soc(
                "path", snapshot, {"battery_soc": 50.0, "battery_combined_soc": 100.001}
            )
        )
        self.assertIn("battery_combined_soc", service._warning_throttled.call_args.args[2])

    def test_shape_contract_checks_object_every_required_key_and_version(self) -> None:
        harness, service = _harness()
        self.assertIsNone(harness._validate_snapshot_shape("path", []))
        self.assertEqual(service._warning_throttled.call_args.args[:3], (
            "auto-input-helper-invalid", 1.0, "Auto input helper snapshot %s is not a JSON object"
        ))
        base = _snapshot()
        for key in sorted(harness.SNAPSHOT_REQUIRED_KEYS):
            candidate = dict(base)
            candidate.pop(key)
            service._warning_throttled.reset_mock()
            self.assertIsNone(harness._validate_snapshot_shape("path", candidate))
            self.assertEqual(service._warning_throttled.call_args.args[-1], key)
        for version in (True, "bad", 0, 2):
            service._warning_throttled.reset_mock()
            self.assertIsNone(harness._validate_snapshot_shape("path", {**base, "snapshot_version": version}))
            self.assertEqual(service._warning_throttled.call_args.args[-1], version)
        self.assertEqual(harness._validate_snapshot_shape("path", base), 1)
        missing_two = dict(base)
        missing_two.pop("pv_power")
        missing_two.pop("grid_power")
        service._warning_throttled.reset_mock()
        self.assertIsNone(harness._validate_snapshot_shape("path", missing_two))
        self.assertEqual(service._warning_throttled.call_args.args[-1], "grid_power, pv_power")

    def test_normalization_specs_and_optional_field_sets_are_exact(self) -> None:
        harness, _service = _harness()
        specs = harness._snapshot_normalization_specs()
        self.assertEqual(
            [(keys, label) for keys, _coercer, label in specs],
            [
                (("captured_at", "heartbeat_at", "pv_captured_at", "battery_captured_at", "grid_captured_at"), "timestamp"),
                (("pv_power", "battery_soc", "grid_power"), "numeric"),
                (harness.OPTIONAL_NUMERIC_FIELDS, "numeric"),
            ],
        )
        self.assertEqual(specs[0][1]("2"), 2.0)
        self.assertEqual(specs[1][1]("3"), 3.0)
        self.assertEqual(specs[2][1]("4"), 4.0)

    def test_identity_normalization_has_strict_integer_and_string_boundaries(self) -> None:
        harness, service = _harness()
        for raw in (None, True, 0, -1, "bad"):
            service._warning_throttled.reset_mock()
            self.assertFalse(harness._normalize_snapshot_writer("path", {"writer_pid": raw}, {}))
        normalized: dict[str, object] = {}
        self.assertTrue(harness._normalize_snapshot_writer("path", {"writer_pid": "2"}, normalized))
        self.assertEqual(normalized["writer_pid"], 2)
        normalized = {}
        self.assertTrue(harness._normalize_snapshot_writer("path", {"writer_pid": 1}, normalized))
        self.assertEqual(normalized["writer_pid"], 1)

        for raw in (None, True, -1, "bad"):
            service._warning_throttled.reset_mock()
            self.assertFalse(harness._normalize_snapshot_generation("path", {"helper_generation": raw}, {}))
        normalized = {}
        self.assertTrue(harness._normalize_snapshot_generation("path", {"helper_generation": "0"}, normalized))
        self.assertEqual(normalized["helper_generation"], 0)

        for raw in (None, 1, "", "   "):
            service._warning_throttled.reset_mock()
            self.assertIsNone(harness._normalize_snapshot_runtime_instance("path", {"runtime_instance_id": raw}, {}))
        normalized = {}
        self.assertIs(
            harness._normalize_snapshot_runtime_instance(
                "path", {"runtime_instance_id": " instance "}, normalized
            ),
            normalized,
        )
        self.assertEqual(normalized["runtime_instance_id"], "instance")

    def test_all_count_fields_default_normalize_and_reject_invalid_values(self) -> None:
        harness, service = _harness()
        normalized: dict[str, object] = {}
        self.assertTrue(harness._normalize_snapshot_count_fields("path", {}, normalized))
        self.assertEqual(normalized, dict.fromkeys(harness.OPTIONAL_COUNT_FIELDS, 0))
        for key in harness.OPTIONAL_COUNT_FIELDS:
            normalized = {}
            self.assertTrue(harness._normalize_snapshot_count_fields("path", {key: "3"}, normalized))
            self.assertEqual(normalized[key], 3)
            normalized = {}
            self.assertTrue(harness._normalize_snapshot_count_fields("path", {key: -2}, normalized))
            self.assertEqual(normalized[key], 0)
            for invalid in (True, "bad"):
                service._warning_throttled.reset_mock()
                self.assertFalse(harness._normalize_snapshot_count_fields("path", {key: invalid}, {}))
                self.assertEqual(service._warning_throttled.call_args.args[-2:], (key, invalid))

    def test_structured_fields_copy_containers_and_reject_wrong_shapes(self) -> None:
        harness, service = _harness()
        source_item = {"id": 1}
        profile_item = {"samples": 2}
        snapshot = {
            "battery_sources": [source_item, "opaque"],
            "battery_learning_profiles": {1: profile_item, "raw": 3},
        }
        sources = harness._normalized_snapshot_battery_sources("path", snapshot)
        profiles = harness._normalized_snapshot_learning_profiles("path", snapshot)
        self.assertEqual(sources, [{"id": 1}, "opaque"])
        self.assertEqual(profiles, {"1": {"samples": 2}, "raw": 3})
        self.assertIsNot(sources[0], source_item)
        self.assertIsNot(profiles["1"], profile_item)
        self.assertEqual(harness._normalized_snapshot_battery_sources("path", {}), [])
        self.assertEqual(harness._normalized_snapshot_learning_profiles("path", {}), {})
        for key, method in (
            ("battery_sources", harness._normalized_snapshot_battery_sources),
            ("battery_learning_profiles", harness._normalized_snapshot_learning_profiles),
        ):
            service._warning_throttled.reset_mock()
            self.assertIsNone(method("path", {key: "bad"}))
            self.assertEqual(service._warning_throttled.call_args.args[-1], key)

        harness._invalid_structured_snapshot_field = MagicMock()
        self.assertIsNone(harness._normalized_snapshot_battery_sources("path", {"battery_sources": "bad"}))
        harness._invalid_structured_snapshot_field.assert_called_once_with("path", "battery_sources")
        harness._invalid_structured_snapshot_field.reset_mock()
        self.assertIsNone(
            harness._normalized_snapshot_learning_profiles("path", {"battery_learning_profiles": "bad"})
        )
        harness._invalid_structured_snapshot_field.assert_called_once_with("path", "battery_learning_profiles")

        normalized: dict[str, object] = {}
        self.assertTrue(harness._normalize_snapshot_structured_fields("path", snapshot, normalized))
        self.assertEqual(set(normalized), {"battery_sources", "battery_learning_profiles"})

    def test_pipeline_orchestration_preserves_order_and_short_circuits(self) -> None:
        harness, _service = _harness()
        snapshot = _snapshot()
        normalized = dict(snapshot)

        harness._normalize_snapshot_writer = MagicMock(return_value=True)
        harness._normalize_snapshot_generation = MagicMock(return_value=True)
        harness._normalize_snapshot_runtime_instance = MagicMock(return_value=normalized)
        self.assertIs(harness._validate_snapshot_identity("path", snapshot, normalized), normalized)
        self.assertEqual(
            harness._normalize_snapshot_writer.call_args_list + harness._normalize_snapshot_generation.call_args_list,
            [call("path", snapshot, normalized), call("path", snapshot, normalized)],
        )

        harness._validate_snapshot_identity = MagicMock(return_value=normalized)
        harness._normalize_snapshot_scalar_fields = MagicMock(return_value=True)
        harness._normalize_snapshot_remaining_fields = MagicMock(return_value=True)
        self.assertEqual(harness._normalize_snapshot_payload("path", snapshot, 1), normalized)
        harness._normalize_snapshot_scalar_fields.assert_called_once()
        harness._normalize_snapshot_remaining_fields.assert_called_once()

        harness._validate_snapshot_temporal_order = MagicMock(return_value=normalized)
        harness._validate_source_value_timestamp_pairs = MagicMock(return_value=normalized)
        harness._validate_source_timestamps = MagicMock(return_value=normalized)
        harness._validate_snapshot_battery_soc = MagicMock(return_value=normalized)
        self.assertIs(harness._validate_snapshot_semantics("path", snapshot, normalized), normalized)
        harness._validate_snapshot_battery_soc.assert_called_once_with("path", snapshot, normalized)

        harness._validate_snapshot_shape = MagicMock(return_value=1)
        harness._normalize_snapshot_payload = MagicMock(return_value=normalized)
        harness._validate_snapshot_semantics = MagicMock(return_value=normalized)
        self.assertIs(harness._validate_snapshot_dict("path", snapshot), normalized)
        harness._validate_snapshot_semantics.assert_called_once_with("path", snapshot, normalized)

    def test_pipeline_short_circuits_at_every_failed_stage(self) -> None:
        harness, _service = _harness()
        snapshot = _snapshot()
        normalized = dict(snapshot)

        for failing in ("writer", "generation", "runtime"):
            harness._normalize_snapshot_writer = MagicMock(return_value=failing != "writer")
            harness._normalize_snapshot_generation = MagicMock(return_value=failing != "generation")
            harness._normalize_snapshot_runtime_instance = MagicMock(
                return_value=None if failing == "runtime" else normalized
            )
            self.assertIsNone(harness._validate_snapshot_identity("path", snapshot, normalized))

        for scalar_ok, remaining_ok in ((False, True), (True, False)):
            harness._validate_snapshot_identity = MagicMock(return_value=normalized)
            harness._normalize_snapshot_scalar_fields = MagicMock(return_value=scalar_ok)
            harness._normalize_snapshot_remaining_fields = MagicMock(return_value=remaining_ok)
            self.assertIsNone(harness._normalize_snapshot_payload("path", snapshot, 1))

        stages = (
            "_validate_snapshot_temporal_order",
            "_validate_source_value_timestamp_pairs",
            "_validate_source_timestamps",
        )
        for failing in stages:
            for stage in stages:
                setattr(harness, stage, MagicMock(return_value=None if stage == failing else normalized))
            harness._validate_snapshot_battery_soc = MagicMock(return_value=normalized)
            self.assertIsNone(harness._validate_snapshot_semantics("path", snapshot, normalized))

        harness._validate_snapshot_shape = MagicMock(return_value=None)
        harness._normalize_snapshot_payload = MagicMock(return_value=normalized)
        self.assertIsNone(harness._validate_snapshot_dict("path", snapshot))
        harness._normalize_snapshot_payload.assert_not_called()
        harness._validate_snapshot_shape.return_value = 1
        harness._normalize_snapshot_payload.return_value = None
        harness._validate_snapshot_semantics = MagicMock(return_value=normalized)
        self.assertIsNone(harness._validate_snapshot_dict("path", snapshot))
        harness._validate_snapshot_semantics.assert_not_called()

    def test_normalization_pipeline_calls_are_exact_and_use_a_payload_copy(self) -> None:
        harness, _service = _harness()
        snapshot = _snapshot(snapshot_version=9)
        normalized: dict[str, object] = {}
        specs = (
            (("a",), harness._coerce_snapshot_timestamp, "timestamp"),
            (("b",), harness._coerce_snapshot_number, "numeric"),
        )
        harness._snapshot_normalization_specs = MagicMock(return_value=specs)
        harness._normalize_snapshot_fields = MagicMock(side_effect=[True, True])
        self.assertTrue(harness._normalize_snapshot_scalar_fields("path", snapshot, normalized))
        self.assertEqual(
            harness._normalize_snapshot_fields.call_args_list,
            [
                call("path", snapshot, normalized, ("a",), specs[0][1], "timestamp"),
                call("path", snapshot, normalized, ("b",), specs[1][1], "numeric"),
            ],
        )
        harness._normalize_snapshot_fields = MagicMock(side_effect=[False, True])
        self.assertFalse(harness._normalize_snapshot_scalar_fields("path", snapshot, normalized))
        self.assertEqual(harness._normalize_snapshot_fields.call_count, 1)

        harness._normalize_snapshot_count_fields = MagicMock(return_value=True)
        harness._normalize_snapshot_structured_fields = MagicMock(return_value=True)
        self.assertTrue(harness._normalize_snapshot_remaining_fields("path", snapshot, normalized))
        harness._normalize_snapshot_count_fields.assert_called_once_with("path", snapshot, normalized)
        harness._normalize_snapshot_structured_fields.assert_called_once_with("path", snapshot, normalized)
        harness._normalize_snapshot_count_fields = MagicMock(return_value=False)
        harness._normalize_snapshot_structured_fields = MagicMock(return_value=True)
        self.assertFalse(harness._normalize_snapshot_remaining_fields("path", snapshot, normalized))
        harness._normalize_snapshot_structured_fields.assert_not_called()

        harness._validate_snapshot_identity = MagicMock(side_effect=lambda _path, _snapshot, value: value)
        harness._normalize_snapshot_scalar_fields = MagicMock(return_value=True)
        harness._normalize_snapshot_remaining_fields = MagicMock(return_value=True)
        result = harness._normalize_snapshot_payload("path", snapshot, 1)
        self.assertIsNot(result, snapshot)
        self.assertEqual(result, {**snapshot, "snapshot_version": 1})
        identity_fields = harness._validate_snapshot_identity.call_args.args[2]
        self.assertIs(result, identity_fields)
        harness._validate_snapshot_identity.assert_called_once_with("path", snapshot, result)
        harness._normalize_snapshot_scalar_fields.assert_called_once_with("path", snapshot, result)
        harness._normalize_snapshot_remaining_fields.assert_called_once_with("path", snapshot, result)

    def test_identity_structured_semantic_and_public_pipeline_forward_exact_objects(self) -> None:
        harness, _service = _harness()
        snapshot = _snapshot()
        normalized = {"stage": "normalized"}

        harness._normalize_snapshot_writer = MagicMock(return_value=True)
        harness._normalize_snapshot_generation = MagicMock(return_value=True)
        harness._normalize_snapshot_runtime_instance = MagicMock(return_value=normalized)
        self.assertIs(harness._validate_snapshot_identity("path", snapshot, normalized), normalized)
        harness._normalize_snapshot_writer.assert_called_once_with("path", snapshot, normalized)
        harness._normalize_snapshot_generation.assert_called_once_with("path", snapshot, normalized)
        harness._normalize_snapshot_runtime_instance.assert_called_once_with("path", snapshot, normalized)

        sources = [{"id": 1}]
        profiles = {"id": {"count": 1}}
        harness._normalized_snapshot_battery_sources = MagicMock(return_value=sources)
        harness._normalized_snapshot_learning_profiles = MagicMock(return_value=profiles)
        structured: dict[str, object] = {}
        self.assertTrue(harness._normalize_snapshot_structured_fields("path", snapshot, structured))
        self.assertIs(structured["battery_sources"], sources)
        self.assertIs(structured["battery_learning_profiles"], profiles)
        harness._normalized_snapshot_battery_sources.assert_called_once_with("path", snapshot)
        harness._normalized_snapshot_learning_profiles.assert_called_once_with("path", snapshot)
        harness._normalized_snapshot_battery_sources = MagicMock(return_value=None)
        harness._normalized_snapshot_learning_profiles = MagicMock(return_value=profiles)
        self.assertFalse(harness._normalize_snapshot_structured_fields("path", snapshot, {}))
        harness._normalized_snapshot_learning_profiles.assert_not_called()
        harness._normalized_snapshot_battery_sources = MagicMock(return_value=sources)
        harness._normalized_snapshot_learning_profiles = MagicMock(return_value=None)
        self.assertFalse(harness._normalize_snapshot_structured_fields("path", snapshot, {}))

        temporal = {"stage": "temporal"}
        pairs = {"stage": "pairs"}
        timestamps = {"stage": "timestamps"}
        final = {"stage": "final"}
        harness._validate_snapshot_temporal_order = MagicMock(return_value=temporal)
        harness._validate_source_value_timestamp_pairs = MagicMock(return_value=pairs)
        harness._validate_source_timestamps = MagicMock(return_value=timestamps)
        harness._validate_snapshot_battery_soc = MagicMock(return_value=final)
        self.assertIs(harness._validate_snapshot_semantics("path", snapshot, normalized), final)
        harness._validate_snapshot_temporal_order.assert_called_once_with("path", normalized)
        harness._validate_source_value_timestamp_pairs.assert_called_once_with("path", temporal)
        harness._validate_source_timestamps.assert_called_once_with("path", pairs)
        harness._validate_snapshot_battery_soc.assert_called_once_with("path", snapshot, timestamps)

        harness._validate_snapshot_shape = MagicMock(return_value=7)
        harness._normalize_snapshot_payload = MagicMock(return_value=normalized)
        harness._validate_snapshot_semantics = MagicMock(return_value=final)
        self.assertIs(harness._validate_snapshot_dict("path", snapshot), final)
        harness._validate_snapshot_shape.assert_called_once_with("path", snapshot)
        harness._normalize_snapshot_payload.assert_called_once_with("path", snapshot, 7)
        harness._validate_snapshot_semantics.assert_called_once_with("path", snapshot, normalized)

    def test_every_validation_failure_has_an_exact_diagnostic_contract(self) -> None:
        harness, _service = _harness()
        harness._invalid_snapshot = MagicMock(return_value=None)

        missing_time = {"captured_at": None, "heartbeat_at": 10.0}
        self.assertIsNone(harness._validate_snapshot_temporal_order("path", missing_time))
        harness._invalid_snapshot.assert_called_once_with(
            "auto-input-helper-schema-invalid",
            "path",
            "Auto input helper snapshot %s requires numeric captured_at and heartbeat_at fields",
        )
        harness._invalid_snapshot.reset_mock()
        self.assertIsNone(
            harness._validate_snapshot_temporal_order("path", {"captured_at": 10.0, "heartbeat_at": 9.0})
        )
        harness._invalid_snapshot.assert_called_once_with(
            "auto-input-helper-schema-invalid",
            "path",
            "Auto input helper snapshot %s has heartbeat_at older than captured_at",
        )

        for key in ("pv_captured_at", "battery_captured_at", "grid_captured_at"):
            harness._invalid_snapshot.reset_mock()
            normalized = {
                "captured_at": 10.0,
                "heartbeat_at": 10.0,
                "pv_captured_at": None,
                "battery_captured_at": None,
                "grid_captured_at": None,
                key: 11.0,
            }
            self.assertIsNone(harness._validate_source_timestamps("path", normalized))
            harness._invalid_snapshot.assert_called_once_with(
                "auto-input-helper-schema-invalid",
                "path",
                "Auto input helper snapshot %s has %s newer than captured_at/heartbeat_at",
                key,
            )

        empty_pairs = {
            "pv_captured_at": None,
            "pv_power": None,
            "battery_captured_at": None,
            "battery_soc": None,
            "grid_captured_at": None,
            "grid_power": None,
        }
        for value_key, timestamp_key in (
            ("pv_power", "pv_captured_at"),
            ("battery_soc", "battery_captured_at"),
            ("grid_power", "grid_captured_at"),
        ):
            harness._invalid_snapshot.reset_mock()
            normalized = dict(empty_pairs)
            normalized[value_key] = 1.0
            self.assertIsNone(harness._validate_source_value_timestamp_pairs("path", normalized))
            harness._invalid_snapshot.assert_called_once_with(
                "auto-input-helper-schema-invalid",
                "path",
                "Auto input helper snapshot %s must provide %s and %s together",
                value_key,
                timestamp_key,
            )

        snapshot = {"battery_soc": 101.0, "battery_combined_soc": 102.0}
        harness._invalid_snapshot.reset_mock()
        self.assertIsNone(
            harness._validate_snapshot_battery_soc(
                "path", snapshot, {"battery_soc": 101.0, "battery_combined_soc": None}
            )
        )
        harness._invalid_snapshot.assert_called_once_with(
            "auto-input-helper-schema-invalid",
            "path",
            "Auto input helper snapshot %s has out-of-range battery_soc=%r",
            101.0,
        )
        harness._invalid_snapshot.reset_mock()
        self.assertIsNone(
            harness._validate_snapshot_battery_soc(
                "path", snapshot, {"battery_soc": 50.0, "battery_combined_soc": 102.0}
            )
        )
        harness._invalid_snapshot.assert_called_once_with(
            "auto-input-helper-schema-invalid",
            "path",
            "Auto input helper snapshot %s has out-of-range battery_combined_soc=%r",
            102.0,
        )

        harness._invalid_snapshot.reset_mock()
        self.assertIsNone(harness._validate_snapshot_shape("path", []))
        harness._invalid_snapshot.assert_called_once_with(
            "auto-input-helper-invalid",
            "path",
            "Auto input helper snapshot %s is not a JSON object",
        )
        harness._invalid_snapshot.reset_mock()
        missing = _snapshot()
        missing.pop("pv_power")
        self.assertIsNone(harness._validate_snapshot_shape("path", missing))
        harness._invalid_snapshot.assert_called_once_with(
            "auto-input-helper-schema-invalid",
            "path",
            "Auto input helper snapshot %s is missing required keys: %s",
            "pv_power",
        )
        harness._invalid_snapshot.reset_mock()
        self.assertIsNone(harness._validate_snapshot_shape("path", _snapshot(snapshot_version=2)))
        harness._invalid_snapshot.assert_called_once_with(
            "auto-input-helper-version-invalid",
            "path",
            "Auto input helper snapshot %s has unsupported snapshot_version=%s",
            2,
        )

        harness._invalid_snapshot.reset_mock()
        self.assertFalse(harness._normalize_snapshot_writer("path", {"writer_pid": 0}, {}))
        harness._invalid_snapshot.assert_called_once_with(
            "auto-input-helper-schema-invalid",
            "path",
            "Auto input helper snapshot %s requires positive integer writer_pid field",
        )
        harness._invalid_snapshot.reset_mock()
        self.assertFalse(harness._normalize_snapshot_generation("path", {"helper_generation": -1}, {}))
        harness._invalid_snapshot.assert_called_once_with(
            "auto-input-helper-schema-invalid",
            "path",
            "Auto input helper snapshot %s requires non-negative integer helper_generation field",
        )
        harness._invalid_snapshot.reset_mock()
        self.assertIsNone(harness._normalize_snapshot_runtime_instance("path", {"runtime_instance_id": " "}, {}))
        harness._invalid_snapshot.assert_called_once_with(
            "auto-input-helper-schema-invalid",
            "path",
            "Auto input helper snapshot %s requires non-empty runtime_instance_id field",
        )

        for invalid in (True, "bad"):
            harness._invalid_snapshot.reset_mock()
            self.assertFalse(harness._normalize_snapshot_count_fields("path", {"battery_source_count": invalid}, {}))
            harness._invalid_snapshot.assert_called_once_with(
                "auto-input-helper-schema-invalid",
                "path",
                "Auto input helper snapshot %s has invalid count field %s=%r",
                "battery_source_count",
                invalid,
            )

        harness._invalid_snapshot.reset_mock()
        harness._invalid_structured_snapshot_field("path", "battery_sources")
        harness._invalid_snapshot.assert_called_once_with(
            "auto-input-helper-schema-invalid",
            "path",
            "Auto input helper snapshot %s has invalid %s payload",
            "battery_sources",
        )


if __name__ == "__main__":
    unittest.main()
