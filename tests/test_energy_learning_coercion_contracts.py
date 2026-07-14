# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact contracts for runtime energy-learning profile coercion."""

from __future__ import annotations

import unittest

from venus_evcharger.energy.learning_coercion import (
    _coerce_learning_profile,
    _coerced_int,
    _normalized_activity_state,
    _normalized_direction,
    _normalized_profile_iter,
    _optional_float,
)
from venus_evcharger.energy.models import EnergyLearningProfile


_INT_FIELDS = (
    "sample_count",
    "active_sample_count",
    "charge_sample_count",
    "discharge_sample_count",
    "import_support_sample_count",
    "import_charge_sample_count",
    "export_charge_sample_count",
    "export_discharge_sample_count",
    "export_idle_sample_count",
    "day_active_sample_count",
    "night_active_sample_count",
    "day_charge_sample_count",
    "night_charge_sample_count",
    "day_discharge_sample_count",
    "night_discharge_sample_count",
    "response_sample_count",
    "smoothing_sample_count",
    "direction_change_count",
)
_FLOAT_FIELDS = (
    "observed_max_charge_power_w",
    "observed_max_discharge_power_w",
    "observed_max_ac_power_w",
    "observed_max_pv_input_power_w",
    "observed_max_grid_import_w",
    "observed_max_grid_export_w",
    "observed_min_discharge_soc",
    "observed_max_charge_soc",
    "average_active_charge_power_w",
    "average_active_discharge_power_w",
    "average_active_power_delta_w",
    "typical_response_delay_seconds",
    "last_active_at",
    "last_inactive_at",
    "last_change_at",
)


class EnergyLearningCoercionContractTests(unittest.TestCase):
    def test_complete_mapping_is_coerced_without_field_loss_or_aliasing(self) -> None:
        int_values = {name: index for index, name in enumerate(_INT_FIELDS, start=1)}
        float_values = {name: index + 0.25 for index, name in enumerate(_FLOAT_FIELDS, start=1)}
        raw: dict[str, object] = {
            "source_id": "embedded-source",
            **{name: str(value) for name, value in int_values.items()},
            **float_values,
            "last_direction": " DISCHARGE ",
            "last_activity_state": " ACTIVE ",
        }

        self.assertEqual(
            _coerce_learning_profile("mapping-key", raw),
            EnergyLearningProfile(
                source_id="embedded-source",
                **int_values,
                **float_values,
                last_direction="discharge",
                last_activity_state="active",
            ),
        )

    def test_profile_iteration_preserves_instances_and_uses_mapping_key_fallback(self) -> None:
        existing = EnergyLearningProfile(source_id="existing", sample_count=7)
        normalized = _normalized_profile_iter(
            {
                "existing-key": existing,
                "mapped": {"sample_count": "3"},
            }
        )
        self.assertIs(normalized[0], existing)
        self.assertEqual(normalized[1], EnergyLearningProfile(source_id="mapped", sample_count=3))
        self.assertEqual(_normalized_profile_iter(None), ())
        self.assertEqual(_normalized_profile_iter({}), ())

    def test_scalar_and_enum_fallbacks_are_exact(self) -> None:
        self.assertEqual(_coerced_int({}, "missing"), 0)
        self.assertEqual(_coerced_int({"value": None}, "value"), 0)
        self.assertEqual(_coerced_int({"value": "4"}, "value"), 4)
        self.assertEqual(_coerced_int({"value": -2}, "value"), -2)

        self.assertIsNone(_optional_float(None))
        self.assertIsNone(_optional_float("invalid"))
        self.assertIsNone(_optional_float("-2.5"))
        self.assertEqual(_optional_float(-2.5), -2.5)
        self.assertEqual(_optional_float(0.25), 0.25)

        for value, expected in (
            (" CHARGE ", "charge"),
            ("DISCHARGE", "discharge"),
            ("idle", "idle"),
            ("invalid", "idle"),
            (None, "idle"),
        ):
            with self.subTest(direction=value):
                self.assertEqual(_normalized_direction(value), expected)
        for value, expected in (
            (" ACTIVE ", "active"),
            ("idle", "idle"),
            ("invalid", "idle"),
            (None, "idle"),
        ):
            with self.subTest(activity=value):
                self.assertEqual(_normalized_activity_state(value), expected)


if __name__ == "__main__":
    unittest.main()
