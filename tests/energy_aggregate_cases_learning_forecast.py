# SPDX-License-Identifier: GPL-3.0-or-later
from tests.energy_aggregate_cases_common import *


class _EnergyAggregateLearningForecastCases:
    def test_empty_learning_profile_exposes_neutral_derived_values(self) -> None:
        profile = EnergyLearningProfile(source_id="empty")

        self.assertIsNone(profile.support_bias)
        self.assertIsNone(profile.import_support_bias)
        self.assertIsNone(profile.export_bias)
        self.assertIsNone(profile.battery_first_export_bias)
        self.assertIsNone(profile.day_support_bias)
        self.assertIsNone(profile.night_support_bias)
        self.assertIsNone(profile.reserve_band_width_soc)
        self.assertEqual(profile.as_dict()["source_id"], "empty")

    def test_update_energy_learning_profiles_tracks_observed_maxima(self) -> None:
        with patch("venus_evcharger.energy.learning._sample_period", side_effect=("day", "night")):
            profiles = update_energy_learning_profiles(
                {},
                (
                    EnergySourceSnapshot(
                        source_id="hybrid",
                        role="hybrid-inverter",
                        service_name="svc",
                        soc=70.0,
                        usable_capacity_wh=10000.0,
                        net_battery_power_w=1500.0,
                        ac_power_w=3200.0,
                        grid_interaction_w=600.0,
                        online=True,
                        confidence=1.0,
                        captured_at=75590.0,
                    ),
                ),
                75590.0,
            )
            profiles = update_energy_learning_profiles(
                profiles,
                (
                    EnergySourceSnapshot(
                        source_id="hybrid",
                        role="hybrid-inverter",
                        service_name="svc",
                        soc=68.0,
                        usable_capacity_wh=10000.0,
                        net_battery_power_w=-1800.0,
                        ac_power_w=3500.0,
                        pv_input_power_w=2200.0,
                        grid_interaction_w=-900.0,
                        online=True,
                        confidence=1.0,
                        captured_at=75600.0,
                    ),
                ),
                75600.0,
            )

        profile = profiles["hybrid"]
        self.assertEqual(profile.sample_count, 2)
        self.assertEqual(profile.active_sample_count, 2)
        self.assertEqual(profile.observed_max_discharge_power_w, 1500.0)
        self.assertEqual(profile.observed_max_charge_power_w, 1800.0)
        self.assertEqual(profile.observed_max_ac_power_w, 3500.0)
        self.assertEqual(profile.observed_max_pv_input_power_w, 2200.0)
        self.assertEqual(profile.observed_max_grid_import_w, 600.0)
        self.assertEqual(profile.observed_max_grid_export_w, 900.0)
        self.assertEqual(profile.average_active_charge_power_w, 1800.0)
        self.assertEqual(profile.average_active_discharge_power_w, 1500.0)
        self.assertEqual(profile.import_support_sample_count, 1)
        self.assertEqual(profile.export_charge_sample_count, 1)
        self.assertEqual(profile.typical_response_delay_seconds, 10.0)
        self.assertEqual(profile.observed_min_discharge_soc, 70.0)
        self.assertEqual(profile.observed_max_charge_soc, 68.0)
        self.assertEqual(profile.support_bias, 0.0)
        self.assertEqual(profile.export_bias, 1.0)
        self.assertEqual(profile.import_support_bias, 1.0)
        self.assertEqual(profile.export_idle_sample_count, 0)
        self.assertEqual(profile.day_active_sample_count, 1)
        self.assertEqual(profile.night_active_sample_count, 1)
        self.assertEqual(profile.day_discharge_sample_count, 1)
        self.assertEqual(profile.night_charge_sample_count, 1)
        self.assertEqual(profile.battery_first_export_bias, 1.0)
        self.assertEqual(profile.day_support_bias, 1.0)
        self.assertEqual(profile.night_support_bias, -1.0)
        self.assertEqual(profile.reserve_band_floor_soc, 70.0)
        self.assertEqual(profile.reserve_band_ceiling_soc, 68.0)
        self.assertIsNone(profile.reserve_band_width_soc)
        self.assertEqual(profile.last_change_at, 75600.0)

    def test_summarize_energy_learning_profiles_sums_observed_maxima(self) -> None:
        summary = summarize_energy_learning_profiles(self._summary_profiles_fixture())
        self._assert_summary_profile_counts(summary)
        self._assert_summary_power_totals(summary)
        self._assert_summary_learning_biases(summary)
        self._assert_summary_reserve_band(summary)

    @staticmethod
    def _summary_profiles_fixture() -> dict[str, dict[str, float | int]]:
        return {
            "victron": {
                "sample_count": 2,
                "active_sample_count": 2,
                "day_active_sample_count": 2,
                "night_active_sample_count": 0,
                "charge_sample_count": 1,
                "discharge_sample_count": 1,
                "response_sample_count": 1,
                "observed_max_charge_power_w": 700.0,
                "observed_max_discharge_power_w": 900.0,
                "observed_max_ac_power_w": 1100.0,
                "observed_max_pv_input_power_w": 1500.0,
                "observed_max_grid_import_w": 400.0,
                "average_active_charge_power_w": 700.0,
                "average_active_discharge_power_w": 900.0,
                "typical_response_delay_seconds": 4.0,
                "import_support_sample_count": 1,
                "import_charge_sample_count": 0,
                "export_charge_sample_count": 0,
                "export_discharge_sample_count": 1,
                "export_idle_sample_count": 0,
                "day_charge_sample_count": 1,
                "day_discharge_sample_count": 0,
                "night_charge_sample_count": 0,
                "night_discharge_sample_count": 1,
                "average_active_power_delta_w": 100.0,
                "smoothing_sample_count": 1,
                "observed_min_discharge_soc": 35.0,
                "observed_max_charge_soc": 85.0,
                "direction_change_count": 1,
            },
            "hybrid": {
                "sample_count": 3,
                "active_sample_count": 3,
                "day_active_sample_count": 1,
                "night_active_sample_count": 3,
                "charge_sample_count": 2,
                "discharge_sample_count": 1,
                "response_sample_count": 1,
                "observed_max_charge_power_w": 500.0,
                "observed_max_discharge_power_w": 1300.0,
                "observed_max_ac_power_w": 1500.0,
                "observed_max_pv_input_power_w": 2000.0,
                "observed_max_grid_export_w": 900.0,
                "average_active_charge_power_w": 450.0,
                "average_active_discharge_power_w": 1300.0,
                "typical_response_delay_seconds": 8.0,
                "import_support_sample_count": 0,
                "import_charge_sample_count": 1,
                "export_charge_sample_count": 2,
                "export_discharge_sample_count": 0,
                "export_idle_sample_count": 1,
                "day_charge_sample_count": 1,
                "day_discharge_sample_count": 1,
                "night_charge_sample_count": 1,
                "night_discharge_sample_count": 0,
                "average_active_power_delta_w": 200.0,
                "smoothing_sample_count": 2,
                "observed_min_discharge_soc": 45.0,
                "observed_max_charge_soc": 90.0,
                "direction_change_count": 2,
            },
        }

    def _assert_summary_profile_counts(self, summary: dict[str, float | int | None]) -> None:
        self.assertEqual(summary["profile_count"], 2)
        self.assertEqual(summary["sample_count"], 5)
        self.assertEqual(summary["active_sample_count"], 5)
        self.assertEqual(summary["direction_change_count"], 3)
        self.assertEqual(summary["day_active_sample_count"], 3)
        self.assertEqual(summary["night_active_sample_count"], 3)

    def _assert_summary_power_totals(self, summary: dict[str, float | int | None]) -> None:
        self.assertEqual(summary["observed_max_charge_power_w"], 1200.0)
        self.assertEqual(summary["observed_max_discharge_power_w"], 2200.0)
        self.assertEqual(summary["observed_max_ac_power_w"], 2600.0)
        self.assertEqual(summary["observed_max_pv_input_power_w"], 3500.0)
        self.assertEqual(summary["observed_max_grid_import_w"], 400.0)
        self.assertEqual(summary["observed_max_grid_export_w"], 900.0)
        self.assertAlmostEqual(summary["average_active_charge_power_w"] or 0.0, 533.3333333333, places=6)
        self.assertEqual(summary["average_active_discharge_power_w"], 1100.0)
        self.assertEqual(summary["typical_response_delay_seconds"], 6.0)
        self.assertAlmostEqual(summary["average_active_power_delta_w"] or 0.0, 166.6666666667, places=6)
        self.assertAlmostEqual(summary["power_smoothing_ratio"] or 0.0, 0.8059523810, places=6)

    def _assert_summary_learning_biases(self, summary: dict[str, float | int | None]) -> None:
        self.assertAlmostEqual(summary["support_bias"] or 0.0, -0.2, places=6)
        self.assertEqual(summary["import_support_bias"], 0.0)
        self.assertAlmostEqual(summary["export_bias"] or 0.0, 1.0 / 3.0, places=6)
        self.assertEqual(summary["battery_first_export_bias"], 0.0)
        self.assertAlmostEqual(summary["day_support_bias"] or 0.0, -1.0 / 3.0, places=6)
        self.assertEqual(summary["night_support_bias"], 0.0)

    def _assert_summary_reserve_band(self, summary: dict[str, float | int | None]) -> None:
        self.assertEqual(summary["reserve_band_floor_soc"], 45.0)
        self.assertEqual(summary["reserve_band_ceiling_soc"], 85.0)
        self.assertEqual(summary["reserve_band_width_soc"], 40.0)

    def test_energy_learning_sample_period_uses_local_day_boundaries(self) -> None:
        for hour, expected in ((5, "night"), (6, "day"), (21, "day"), (22, "night")):
            with self.subTest(hour=hour), patch("venus_evcharger.energy.learning.time.localtime") as localtime:
                localtime.return_value = SimpleNamespace(tm_hour=hour)
                self.assertEqual(energy_learning_mod._sample_period(123.0), expected)
                localtime.assert_called_once_with(123.0)

    def test_update_energy_learning_profiles_initializes_previous_profile_for_new_source(self) -> None:
        source = EnergySourceSnapshot(
            source_id="new-source",
            role="battery",
            service_name="svc",
            net_battery_power_w=100.0,
            online=True,
            confidence=1.0,
            captured_at=1.0,
        )
        with patch(
            "venus_evcharger.energy.learning._build_updated_learning_profile",
            return_value=EnergyLearningProfile(source_id="new-source"),
        ) as build_profile:
            profiles = update_energy_learning_profiles(None, (source,), 123.0)

        previous_profile = build_profile.call_args.args[0]
        self.assertEqual(previous_profile.source_id, "new-source")
        self.assertEqual(build_profile.call_args.args[1], source)
        self.assertEqual(build_profile.call_args.args[2], 123.0)
        self.assertIn("new-source", profiles)

    def test_summarize_energy_learning_profiles_weights_only_valid_positive_samples(self) -> None:
        summary = summarize_energy_learning_profiles(
            {
                "ignored": EnergyLearningProfile(
                    source_id="ignored",
                    charge_sample_count=1,
                    average_active_charge_power_w=None,
                    smoothing_sample_count=0,
                    average_active_power_delta_w=900.0,
                    average_active_discharge_power_w=1000.0,
                ),
                "valid": EnergyLearningProfile(
                    source_id="valid",
                    charge_sample_count=1,
                    average_active_charge_power_w=300.0,
                    smoothing_sample_count=1,
                    average_active_power_delta_w=150.0,
                ),
            }
        )

        self.assertEqual(summary["average_active_charge_power_w"], 300.0)
        self.assertEqual(summary["average_active_power_delta_w"], 150.0)
        self.assertEqual(summary["power_smoothing_ratio"], 0.5)

    def test_summarize_energy_learning_profiles_reports_zero_width_reserve_band(self) -> None:
        summary = summarize_energy_learning_profiles(
            {
                "flat": EnergyLearningProfile(
                    source_id="flat",
                    observed_min_discharge_soc=50.0,
                    observed_max_charge_soc=50.0,
                )
            }
        )

        self.assertEqual(summary["reserve_band_floor_soc"], 50.0)
        self.assertEqual(summary["reserve_band_ceiling_soc"], 50.0)
        self.assertEqual(summary["reserve_band_width_soc"], 0.0)

    def test_energy_learning_summary_weighted_average_accepts_fractional_positive_weights(self) -> None:
        self.assertEqual(
            energy_learning_summary_mod._weighted_average(((100.0, 0.0), (300.0, -1.0), (200.0, 0.5))),
            200.0,
        )

    def test_energy_learning_update_direction_activity_and_grid_contracts(self) -> None:
        self.assertEqual(
            energy_learning_update_mod._direction(
                EnergySourceSnapshot(source_id="charge", role="battery", service_name="svc", net_battery_power_w=-50.0)
            ),
            "charge",
        )
        self.assertEqual(
            energy_learning_update_mod._direction(
                EnergySourceSnapshot(source_id="discharge", role="battery", service_name="svc", net_battery_power_w=50.0)
            ),
            "discharge",
        )
        self.assertEqual(
            energy_learning_update_mod._direction(
                EnergySourceSnapshot(source_id="idle", role="battery", service_name="svc", net_battery_power_w=49.9)
            ),
            "idle",
        )
        self.assertEqual(
            energy_learning_update_mod._direction(
                EnergySourceSnapshot(source_id="unknown", role="battery", service_name="svc")
            ),
            "idle",
        )
        self.assertTrue(
            energy_learning_update_mod._is_active(
                EnergySourceSnapshot(source_id="ac", role="inverter", service_name="svc", ac_power_w=-50.0),
                "idle",
            )
        )
        self.assertFalse(
            energy_learning_update_mod._is_active(
                EnergySourceSnapshot(source_id="quiet", role="inverter", service_name="svc", pv_input_power_w=49.9),
                "idle",
            )
        )
        battery_only_metrics = energy_learning_update_mod._source_learning_metrics(
            EnergySourceSnapshot(source_id="battery-only", role="battery", service_name="svc", net_battery_power_w=-60.0)
        )
        tiny_export_metrics = energy_learning_update_mod._source_learning_metrics(
            EnergySourceSnapshot(source_id="tiny-export", role="battery", service_name="svc", grid_interaction_w=-0.5)
        )
        self.assertTrue(battery_only_metrics[1])
        self.assertEqual(tiny_export_metrics[7], 0.5)

        self.assertEqual(
            energy_learning_update_mod._grid_sample_increments("discharge", 100.0, 100.0),
            {
                "import_support": 1,
                "import_charge": 0,
                "export_charge": 0,
                "export_discharge": 1,
                "export_idle": 0,
            },
        )
        self.assertEqual(
            energy_learning_update_mod._grid_sample_increments("idle", 100.0, 100.0)["export_idle"],
            1,
        )
        self.assertEqual(energy_learning_update_mod._grid_increment("charge", None, "charge"), 0)
        self.assertEqual(energy_learning_update_mod._grid_idle_increment("idle", None), 0)

    def test_energy_learning_update_period_and_response_contracts(self) -> None:
        self.assertEqual(
            energy_learning_update_mod._period_sample_increments(True, "charge", "day"),
            {
                "day_active": 1,
                "night_active": 0,
                "day_charge": 1,
                "night_charge": 0,
                "day_discharge": 0,
                "night_discharge": 0,
            },
        )
        self.assertEqual(
            energy_learning_update_mod._period_sample_increments(True, "discharge", "night"),
            {
                "day_active": 0,
                "night_active": 1,
                "day_charge": 0,
                "night_charge": 0,
                "day_discharge": 0,
                "night_discharge": 1,
            },
        )
        self.assertEqual(
            energy_learning_update_mod._period_sample_increments(False, "charge", "day"),
            {
                "day_active": 0,
                "night_active": 0,
                "day_charge": 0,
                "night_charge": 0,
                "day_discharge": 0,
                "night_discharge": 0,
            },
        )

        previous = EnergyLearningProfile(
            source_id="hybrid",
            last_activity_state="idle",
            last_inactive_at=90.0,
            last_direction="charge",
            last_change_at=80.0,
        )
        self.assertEqual(energy_learning_update_mod._inactive_response_delay(previous, 100.0), 10.0)
        self.assertIsNone(
            energy_learning_update_mod._inactive_response_delay(
                EnergyLearningProfile(source_id="active", last_activity_state="active", last_inactive_at=90.0),
                100.0,
            )
        )
        self.assertEqual(
            energy_learning_update_mod._inactive_response_delay(
                EnergyLearningProfile(source_id="clamp", last_activity_state="idle", last_inactive_at=110.0),
                100.0,
            ),
            0.0,
        )
        self.assertEqual(energy_learning_update_mod._direction_change_response_delay(previous, "discharge", 100.0), 20.0)
        self.assertIsNone(energy_learning_update_mod._direction_change_response_delay(previous, "charge", 100.0))
        self.assertEqual(
            energy_learning_update_mod._direction_change_response_delay(
                EnergyLearningProfile(source_id="clamp", last_direction="charge", last_change_at=110.0),
                "discharge",
                100.0,
            ),
            0.0,
        )
        self.assertTrue(energy_learning_update_mod._direction_changed(previous, "discharge"))
        self.assertFalse(energy_learning_update_mod._direction_changed(previous, "idle"))
        self.assertFalse(
            energy_learning_update_mod._direction_changed(EnergyLearningProfile(source_id="idle"), "charge")
        )

    def test_energy_learning_update_activity_observation_and_average_contracts(self) -> None:
        previous = EnergyLearningProfile(
            source_id="hybrid",
            charge_sample_count=1,
            discharge_sample_count=1,
            smoothing_sample_count=1,
            response_sample_count=1,
            average_active_charge_power_w=100.0,
            average_active_discharge_power_w=80.0,
            average_active_power_delta_w=50.0,
            typical_response_delay_seconds=6.0,
            observed_max_charge_power_w=200.0,
            observed_min_discharge_soc=60.0,
            last_direction="discharge",
            last_activity_state="idle",
            last_active_at=70.0,
            last_inactive_at=90.0,
            last_change_at=80.0,
        )
        source = EnergySourceSnapshot(
            source_id="hybrid",
            role="hybrid-inverter",
            service_name="svc",
            soc=75.0,
            net_battery_power_w=-300.0,
            ac_power_w=-400.0,
            pv_input_power_w=-5.0,
            grid_interaction_w=-150.0,
            online=True,
            confidence=1.0,
            captured_at=100.0,
        )

        metrics = energy_learning_update_mod._source_learning_metrics(source)
        observations = energy_learning_update_mod._profile_observation_updates(
            previous,
            source,
            "charge",
            300.0,
            0.0,
            400.0,
            None,
            None,
            150.0,
        )
        preserved_observations = energy_learning_update_mod._profile_observation_updates(
            EnergyLearningProfile(
                source_id="previous",
                observed_max_charge_power_w=500.0,
                observed_max_ac_power_w=800.0,
                observed_max_pv_input_power_w=900.0,
                observed_max_grid_export_w=950.0,
                observed_max_charge_soc=90.0,
            ),
            source,
            "charge",
            300.0,
            0.0,
            400.0,
            500.0,
            None,
            150.0,
        )
        activity = energy_learning_update_mod._profile_activity_updates(
            previous,
            "charge",
            True,
            100.0,
            300.0,
            0.0,
            10.0,
        )
        discharge_activity = energy_learning_update_mod._profile_activity_updates(
            previous,
            "discharge",
            True,
            100.0,
            0.0,
            100.0,
            None,
        )
        idle_markers = energy_learning_update_mod._activity_markers(previous, "idle", False, 120.0)

        self.assertEqual(metrics[0], "charge")
        self.assertTrue(metrics[1])
        self.assertEqual(metrics[2], 300.0)
        self.assertEqual(metrics[4], 400.0)
        self.assertIsNone(metrics[5])
        self.assertEqual(metrics[7], 150.0)
        self.assertEqual(observations["observed_max_charge_power_w"], 300.0)
        self.assertEqual(observations["observed_max_ac_power_w"], 400.0)
        self.assertIsNone(observations["observed_max_pv_input_power_w"])
        self.assertEqual(observations["observed_max_grid_export_w"], 150.0)
        self.assertEqual(observations["observed_min_discharge_soc"], 60.0)
        self.assertEqual(observations["observed_max_charge_soc"], 75.0)
        self.assertEqual(preserved_observations["observed_max_charge_power_w"], 500.0)
        self.assertEqual(preserved_observations["observed_max_ac_power_w"], 800.0)
        self.assertEqual(preserved_observations["observed_max_pv_input_power_w"], 900.0)
        self.assertEqual(preserved_observations["observed_max_grid_export_w"], 950.0)
        self.assertEqual(preserved_observations["observed_max_charge_soc"], 90.0)
        self.assertEqual(activity["average_active_charge_power_w"], 200.0)
        self.assertEqual(activity["average_active_discharge_power_w"], 80.0)
        self.assertEqual(activity["average_active_power_delta_w"], 125.0)
        self.assertEqual(activity["typical_response_delay_seconds"], 8.0)
        self.assertEqual(activity["direction_change_count"], 1)
        self.assertEqual(activity["last_active_at"], 100.0)
        self.assertEqual(activity["last_inactive_at"], 90.0)
        self.assertEqual(activity["last_change_at"], 100.0)
        self.assertEqual(discharge_activity["average_active_power_delta_w"], 35.0)
        self.assertEqual(
            energy_learning_update_mod._profile_activity_updates(
                EnergyLearningProfile(source_id="same", direction_change_count=5, last_direction="charge"),
                "charge",
                True,
                100.0,
                10.0,
                None,
                None,
            )["direction_change_count"],
            5,
        )
        self.assertEqual(idle_markers["last_activity_state"], "idle")
        self.assertEqual(idle_markers["last_active_at"], 70.0)
        self.assertEqual(idle_markers["last_inactive_at"], 120.0)
        self.assertEqual(energy_learning_update_mod._directional_power_pair(previous, "discharge", 0.0, 100.0), (100.0, 80.0))
        self.assertEqual(energy_learning_update_mod._smoothing_delta(previous, "discharge", 0.0, 100.0), 20.0)

        self.assertEqual(energy_learning_update_mod._rolling_average(10.0, 1, 30.0), 20.0)
        self.assertAlmostEqual(energy_learning_update_mod._rolling_average(10.0, 2, 30.0) or 0.0, 50.0 / 3.0)
        self.assertEqual(energy_learning_update_mod._rolling_average(None, 1, 30.0), 30.0)
        self.assertEqual(energy_learning_update_mod._positive_optional(0.0), None)
        self.assertEqual(energy_learning_update_mod._positive_optional(1.0), 1.0)
        self.assertEqual(energy_learning_update_mod._positive_optional(2.0), 2.0)
        self.assertEqual(energy_learning_update_mod._absolute_optional(-3.0), 3.0)

    def test_energy_learning_update_sample_update_counter_contracts(self) -> None:
        previous = EnergyLearningProfile(
            source_id="hybrid",
            active_sample_count=5,
            charge_sample_count=2,
            import_charge_sample_count=3,
            export_charge_sample_count=4,
            response_sample_count=6,
            smoothing_sample_count=7,
        )

        inactive_charge = energy_learning_update_mod._profile_sample_updates(
            previous,
            False,
            "charge",
            100.0,
            100.0,
            None,
            "day",
            None,
        )
        active_charge = energy_learning_update_mod._profile_sample_updates(
            previous,
            True,
            "charge",
            100.0,
            100.0,
            1.0,
            "day",
            2.0,
        )

        self.assertEqual(inactive_charge["sample_count"], 1)
        self.assertEqual(inactive_charge["active_sample_count"], 5)
        self.assertEqual(inactive_charge["charge_sample_count"], 3)
        self.assertEqual(inactive_charge["import_charge_sample_count"], 4)
        self.assertEqual(inactive_charge["export_charge_sample_count"], 5)
        self.assertEqual(inactive_charge["day_charge_sample_count"], 0)
        self.assertEqual(inactive_charge["response_sample_count"], 6)
        self.assertEqual(inactive_charge["smoothing_sample_count"], 7)
        self.assertEqual(active_charge["active_sample_count"], 6)
        self.assertEqual(active_charge["day_charge_sample_count"], 1)
        self.assertEqual(active_charge["response_sample_count"], 7)
        self.assertEqual(active_charge["smoothing_sample_count"], 8)

    def test_energy_learning_update_builds_profile_from_one_charge_sample(self) -> None:
        previous = EnergyLearningProfile(
            source_id="hybrid",
            sample_count=2,
            active_sample_count=1,
            charge_sample_count=1,
            average_active_charge_power_w=100.0,
            last_direction="discharge",
            last_activity_state="idle",
            last_inactive_at=90.0,
            last_change_at=80.0,
        )
        source = EnergySourceSnapshot(
            source_id="hybrid",
            role="hybrid-inverter",
            service_name="svc",
            soc=75.0,
            net_battery_power_w=-300.0,
            ac_power_w=400.0,
            pv_input_power_w=500.0,
            grid_interaction_w=-150.0,
            online=True,
            confidence=1.0,
            captured_at=100.0,
        )

        profile = energy_learning_update_mod._build_updated_learning_profile(previous, source, 100.0, lambda _now: "night")

        self.assertEqual(profile.source_id, "hybrid")
        self.assertEqual(profile.sample_count, 3)
        self.assertEqual(profile.active_sample_count, 2)
        self.assertEqual(profile.charge_sample_count, 2)
        self.assertEqual(profile.export_charge_sample_count, 1)
        self.assertEqual(profile.night_active_sample_count, 1)
        self.assertEqual(profile.night_charge_sample_count, 1)
        self.assertEqual(profile.smoothing_sample_count, 1)
        self.assertEqual(profile.average_active_charge_power_w, 200.0)
        self.assertEqual(profile.average_active_power_delta_w, 200.0)
        self.assertEqual(profile.typical_response_delay_seconds, 10.0)
        self.assertEqual(profile.direction_change_count, 1)
        self.assertEqual(profile.last_activity_state, "active")
        self.assertEqual(profile.last_active_at, 100.0)
        self.assertEqual(profile.last_inactive_at, 90.0)
        self.assertEqual(profile.last_change_at, 100.0)
        self.assertEqual(profile.observed_max_pv_input_power_w, 500.0)
        self.assertEqual(profile.observed_max_grid_export_w, 150.0)
        self.assertEqual(profile.observed_max_charge_soc, 75.0)

    def test_energy_learning_update_builds_profile_with_charge_import_day_counter(self) -> None:
        source = EnergySourceSnapshot(
            source_id="hybrid",
            role="hybrid-inverter",
            service_name="svc",
            net_battery_power_w=-300.0,
            grid_interaction_w=150.0,
            online=True,
            confidence=1.0,
            captured_at=100.0,
        )

        profile = energy_learning_update_mod._build_updated_learning_profile(
            EnergyLearningProfile(source_id="hybrid"),
            source,
            100.0,
            lambda _now: "day",
        )

        self.assertEqual(profile.import_charge_sample_count, 1)
        self.assertEqual(profile.day_charge_sample_count, 1)

    def test_energy_learning_update_builds_profile_with_discharge_grid_counters(self) -> None:
        previous = EnergyLearningProfile(
            source_id="hybrid",
            discharge_sample_count=1,
            smoothing_sample_count=1,
            average_active_discharge_power_w=100.0,
            average_active_power_delta_w=20.0,
        )
        source = EnergySourceSnapshot(
            source_id="hybrid",
            role="hybrid-inverter",
            service_name="svc",
            soc=65.0,
            net_battery_power_w=300.0,
            grid_interaction_w=-150.0,
            online=True,
            confidence=1.0,
            captured_at=100.0,
        )

        profile = energy_learning_update_mod._build_updated_learning_profile(previous, source, 100.0, lambda _now: "night")

        self.assertEqual(profile.discharge_sample_count, 2)
        self.assertEqual(profile.export_discharge_sample_count, 1)
        self.assertEqual(profile.night_discharge_sample_count, 1)
        self.assertEqual(profile.smoothing_sample_count, 2)
        self.assertEqual(profile.average_active_power_delta_w, 110.0)
        self.assertEqual(profile.observed_max_discharge_power_w, 300.0)
        self.assertEqual(profile.observed_min_discharge_soc, 65.0)

    def test_update_energy_learning_profiles_uses_reactivation_delay_and_rolling_averages(self) -> None:
        profiles = update_energy_learning_profiles(
            {
                "hybrid": {
                    "source_id": "hybrid",
                    "sample_count": 1,
                    "active_sample_count": 0,
                    "charge_sample_count": 0,
                    "discharge_sample_count": 1,
                    "response_sample_count": 0,
                    "observed_min_discharge_soc": 80.0,
                    "average_active_discharge_power_w": 1000.0,
                    "last_direction": "idle",
                    "last_activity_state": "idle",
                    "last_inactive_at": 90.0,
                    "last_change_at": 90.0,
                },
            },
            (
                EnergySourceSnapshot(
                    source_id="hybrid",
                    role="hybrid-inverter",
                    service_name="svc",
                    soc=60.0,
                    net_battery_power_w=1500.0,
                    online=True,
                    confidence=1.0,
                    captured_at=100.0,
                ),
            ),
            100.0,
        )

        profile = profiles["hybrid"]
        self.assertEqual(profile.response_sample_count, 1)
        self.assertEqual(profile.typical_response_delay_seconds, 10.0)
        self.assertEqual(profile.observed_min_discharge_soc, 60.0)
        self.assertEqual(profile.average_active_discharge_power_w, 1250.0)

    def test_update_energy_learning_profiles_tracks_smoothing_and_export_first_behavior(self) -> None:
        profiles = update_energy_learning_profiles(
            {
                "hybrid": {
                    "source_id": "hybrid",
                    "sample_count": 1,
                    "active_sample_count": 1,
                    "charge_sample_count": 1,
                    "average_active_charge_power_w": 1000.0,
                    "last_direction": "charge",
                    "last_activity_state": "active",
                    "last_change_at": 43200.0,
                },
            },
            (
                EnergySourceSnapshot(
                    source_id="hybrid",
                    role="hybrid-inverter",
                    service_name="svc",
                    soc=75.0,
                    net_battery_power_w=0.0,
                    grid_interaction_w=-500.0,
                    online=True,
                    confidence=1.0,
                    captured_at=84600.0,
                ),
            ),
            84600.0,
        )

        profile = profiles["hybrid"]
        self.assertEqual(profile.export_idle_sample_count, 1)
        self.assertEqual(profile.smoothing_sample_count, 0)
        self.assertIsNone(profile.average_active_power_delta_w)
        self.assertEqual(profile.battery_first_export_bias, -1.0)

    def test_energy_learning_profile_reports_reserve_band_width_when_floor_and_ceiling_are_valid(self) -> None:
        profile = EnergyLearningProfile(
            source_id="hybrid",
            observed_min_discharge_soc=35.0,
            observed_max_charge_soc=85.0,
        )

        self.assertEqual(profile.reserve_band_width_soc, 50.0)

    def test_energy_learning_profile_power_smoothing_uses_positive_average_reference(self) -> None:
        profile = EnergyLearningProfile(
            source_id="hybrid",
            average_active_charge_power_w=1000.0,
            average_active_discharge_power_w=3000.0,
            average_active_power_delta_w=500.0,
        )

        self.assertEqual(profile.power_smoothing_ratio, 0.75)

    def test_energy_learning_profile_power_smoothing_ignores_missing_zero_and_negative_references(self) -> None:
        no_reference = EnergyLearningProfile(
            source_id="none",
            average_active_charge_power_w=0.0,
            average_active_discharge_power_w=-1.0,
            average_active_power_delta_w=10.0,
        )
        charge_only_reference = EnergyLearningProfile(
            source_id="charge",
            average_active_charge_power_w=1200.0,
            average_active_discharge_power_w=None,
            average_active_power_delta_w=300.0,
        )
        discharge_only_reference = EnergyLearningProfile(
            source_id="discharge",
            average_active_charge_power_w=None,
            average_active_discharge_power_w=800.0,
            average_active_power_delta_w=200.0,
        )

        self.assertIsNone(no_reference.power_smoothing_ratio)
        self.assertEqual(charge_only_reference.power_smoothing_ratio, 0.75)
        self.assertEqual(discharge_only_reference.power_smoothing_ratio, 0.75)

    def test_energy_learning_profile_power_smoothing_excludes_zero_from_positive_reference_average(self) -> None:
        profile = EnergyLearningProfile(
            source_id="zero-and-positive",
            average_active_charge_power_w=0.0,
            average_active_discharge_power_w=1000.0,
            average_active_power_delta_w=250.0,
        )

        self.assertEqual(profile.power_smoothing_ratio, 0.75)

    def test_energy_learning_profile_power_smoothing_treats_one_watt_as_positive_reference(self) -> None:
        profile = EnergyLearningProfile(
            source_id="one-watt-reference",
            average_active_charge_power_w=1.0,
            average_active_discharge_power_w=None,
            average_active_power_delta_w=0.25,
        )

        self.assertEqual(profile.power_smoothing_ratio, 0.75)

    def test_energy_learning_profile_normalized_smoothing_rejects_exact_zero_reference(self) -> None:
        from venus_evcharger.energy.models import _normalized_smoothing_ratio

        self.assertIsNone(_normalized_smoothing_ratio(10.0, 0.0))

    def test_energy_learning_profile_power_smoothing_requires_delta_and_handles_exact_edges(self) -> None:
        missing_delta = EnergyLearningProfile(
            source_id="missing-delta",
            average_active_charge_power_w=1000.0,
            average_active_discharge_power_w=1000.0,
            average_active_power_delta_w=None,
        )
        zero_delta = EnergyLearningProfile(
            source_id="zero-delta",
            average_active_charge_power_w=1000.0,
            average_active_discharge_power_w=1000.0,
            average_active_power_delta_w=0.0,
        )
        full_delta = EnergyLearningProfile(
            source_id="full-delta",
            average_active_charge_power_w=1000.0,
            average_active_discharge_power_w=1000.0,
            average_active_power_delta_w=1000.0,
        )

        self.assertIsNone(missing_delta.power_smoothing_ratio)
        self.assertEqual(zero_delta.power_smoothing_ratio, 1.0)
        self.assertEqual(full_delta.power_smoothing_ratio, 0.0)

    def test_energy_learning_profile_power_smoothing_clamps_to_contract_range(self) -> None:
        lower_clamped = EnergyLearningProfile(
            source_id="rough",
            average_active_charge_power_w=1000.0,
            average_active_discharge_power_w=1000.0,
            average_active_power_delta_w=1500.0,
        )
        upper_clamped = EnergyLearningProfile(
            source_id="smooth",
            average_active_charge_power_w=1000.0,
            average_active_discharge_power_w=1000.0,
            average_active_power_delta_w=-100.0,
        )

        self.assertEqual(lower_clamped.power_smoothing_ratio, 0.0)
        self.assertEqual(upper_clamped.power_smoothing_ratio, 1.0)

    def test_derive_energy_forecast_uses_reserve_band_capture_bias_and_smoothing(self) -> None:
        forecast = derive_energy_forecast(
            {
                "battery_combined_soc": 79.0,
                "battery_combined_charge_power_w": 800.0,
                "battery_combined_discharge_power_w": 1200.0,
                "battery_combined_grid_interaction_w": -400.0,
            },
            {
                "observed_max_charge_power_w": 2000.0,
                "observed_max_discharge_power_w": 3000.0,
                "average_active_charge_power_w": 1000.0,
                "average_active_discharge_power_w": 1600.0,
                "typical_response_delay_seconds": 30.0,
                "export_bias": 0.75,
                "battery_first_export_bias": 0.5,
                "power_smoothing_ratio": 0.8,
                "reserve_band_floor_soc": 35.0,
                "reserve_band_ceiling_soc": 80.0,
                "import_support_bias": 0.5,
            },
        )

        self.assertEqual(forecast["battery_headroom_charge_w"], 120.0)
        self.assertEqual(forecast["battery_headroom_discharge_w"], 1800.0)
        self.assertAlmostEqual(forecast["expected_near_term_export_w"] or 0.0, 404.6, places=6)
        self.assertEqual(forecast["expected_near_term_import_w"], 0.0)

    def test_derive_energy_forecast_returns_headroom_and_near_term_grid_estimates(self) -> None:
        forecast = derive_energy_forecast(
            {
                "battery_combined_charge_power_w": 800.0,
                "battery_combined_discharge_power_w": 1200.0,
                "battery_combined_grid_interaction_w": 400.0,
            },
            {
                "observed_max_charge_power_w": 2000.0,
                "observed_max_discharge_power_w": 3000.0,
                "average_active_charge_power_w": 1000.0,
                "average_active_discharge_power_w": 1600.0,
                "typical_response_delay_seconds": 30.0,
                "support_bias": 0.5,
                "import_support_bias": 0.5,
                "export_bias": 0.75,
            },
        )

        self.assertEqual(forecast["battery_headroom_charge_w"], 1200.0)
        self.assertEqual(forecast["battery_headroom_discharge_w"], 1800.0)
        self.assertAlmostEqual(forecast["expected_near_term_export_w"] or 0.0, 210.0, places=6)
        self.assertAlmostEqual(forecast["expected_near_term_import_w"] or 0.0, 100.0, places=6)

    def test_derive_energy_forecast_prefers_documented_charge_discharge_limits(self) -> None:
        forecast = derive_energy_forecast(
            {
                "battery_combined_charge_power_w": 800.0,
                "battery_combined_discharge_power_w": 1200.0,
                "battery_combined_charge_limit_power_w": 900.0,
                "battery_combined_discharge_limit_power_w": 1400.0,
                "battery_combined_grid_interaction_w": 400.0,
            },
            {
                "observed_max_charge_power_w": 2000.0,
                "observed_max_discharge_power_w": 3000.0,
                "average_active_charge_power_w": 1000.0,
                "average_active_discharge_power_w": 1600.0,
                "support_bias": 0.5,
                "import_support_bias": 0.5,
                "export_bias": 0.75,
            },
        )

        self.assertEqual(forecast["battery_headroom_charge_w"], 100.0)
        self.assertEqual(forecast["battery_headroom_discharge_w"], 200.0)

    def test_derive_energy_forecast_covers_average_limit_and_saturation_fallbacks(self) -> None:
        forecast = derive_energy_forecast(
            {
                "battery_combined_charge_power_w": 600.0,
                "battery_combined_discharge_power_w": 0.0,
                "battery_combined_grid_interaction_w": -50.0,
            },
            {
                "average_active_charge_power_w": 400.0,
                "average_active_discharge_power_w": 250.0,
                "export_bias": 0.5,
            },
        )

        self.assertEqual(forecast["battery_headroom_charge_w"], 0.0)
        self.assertEqual(forecast["battery_headroom_discharge_w"], 312.5)
        self.assertAlmostEqual(forecast["expected_near_term_export_w"] or 0.0, 350.0, places=6)
        self.assertEqual(forecast["expected_near_term_import_w"], 0.0)

        no_grid_forecast = derive_energy_forecast(
            {
                "battery_combined_charge_power_w": 0.0,
                "battery_combined_discharge_power_w": 0.0,
                "battery_combined_grid_interaction_w": None,
            },
            {},
        )
        self.assertIsNone(no_grid_forecast["expected_near_term_export_w"])
        self.assertIsNone(no_grid_forecast["expected_near_term_import_w"])

    def test_derive_energy_forecast_uses_average_charge_limit_when_current_is_lower(self) -> None:
        forecast = derive_energy_forecast(
            {
                "battery_combined_charge_power_w": 100.0,
                "battery_combined_grid_interaction_w": -20.0,
            },
            {
                "average_active_charge_power_w": 400.0,
                "export_bias": 1.0,
            },
        )

        self.assertEqual(forecast["battery_headroom_charge_w"], 400.0)
        self.assertAlmostEqual(forecast["expected_near_term_export_w"] or 0.0, 80.0, places=6)

    def test_derive_energy_forecast_uses_current_discharge_when_no_limit_is_known(self) -> None:
        forecast = derive_energy_forecast(
            {
                "battery_combined_discharge_power_w": 250.0,
                "battery_combined_grid_interaction_w": 50.0,
            },
            {
                "import_support_bias": 1.0,
            },
        )

        self.assertEqual(forecast["battery_headroom_discharge_w"], 0.0)
        self.assertEqual(forecast["expected_near_term_import_w"], 0.0)

    def test_derive_energy_forecast_saturates_when_only_current_charge_power_is_known(self) -> None:
        forecast = derive_energy_forecast(
            {
                "battery_combined_charge_power_w": 400.0,
                "battery_combined_grid_interaction_w": -100.0,
            },
            {
                "export_bias": 1.0,
            },
        )

        self.assertEqual(forecast["battery_headroom_charge_w"], 0.0)
        self.assertIsNone(forecast["battery_headroom_discharge_w"])
        self.assertEqual(forecast["expected_near_term_export_w"], 500.0)

    def test_derive_energy_forecast_handles_tiny_current_charge_thresholds(self) -> None:
        no_observed_limit = derive_energy_forecast(
            {
                "battery_combined_charge_power_w": 0.5,
                "battery_combined_grid_interaction_w": 0.0,
            },
            {
                "export_bias": 1.0,
            },
        )
        one_watt_observed_limit = derive_energy_forecast(
            {
                "battery_combined_charge_power_w": 0.5,
                "battery_combined_grid_interaction_w": 0.0,
            },
            {
                "observed_max_charge_power_w": 1.0,
                "export_bias": 1.0,
            },
        )

        self.assertEqual(no_observed_limit["battery_headroom_charge_w"], 0.0)
        self.assertAlmostEqual(no_observed_limit["expected_near_term_export_w"] or 0.0, 0.5, places=6)
        self.assertEqual(one_watt_observed_limit["battery_headroom_charge_w"], 0.5)
        self.assertAlmostEqual(one_watt_observed_limit["expected_near_term_export_w"] or 0.0, 0.375, places=6)

    def test_derive_energy_forecast_uses_full_known_headroom_when_current_charge_is_missing(self) -> None:
        forecast = derive_energy_forecast(
            {
                "battery_combined_grid_interaction_w": -25.0,
            },
            {
                "observed_max_charge_power_w": 100.0,
                "export_bias": 1.0,
            },
        )

        self.assertEqual(forecast["battery_headroom_charge_w"], 100.0)
        self.assertEqual(forecast["expected_near_term_export_w"], 25.0)

    def test_derive_energy_forecast_treats_missing_power_as_zero_for_import_export_risk(self) -> None:
        export_forecast = derive_energy_forecast(
            {
                "battery_combined_grid_interaction_w": -25.0,
            },
            {
                "export_bias": 1.0,
            },
        )
        import_forecast = derive_energy_forecast(
            {
                "battery_combined_grid_interaction_w": 100.0,
            },
            {
                "import_support_bias": 1.0,
            },
        )

        self.assertEqual(export_forecast["expected_near_term_export_w"], 25.0)
        self.assertEqual(import_forecast["expected_near_term_import_w"], 100.0)

    def test_derive_energy_forecast_uses_support_bias_only_as_import_fallback(self) -> None:
        fallback_forecast = derive_energy_forecast(
            {
                "battery_combined_discharge_power_w": 200.0,
                "battery_combined_grid_interaction_w": 100.0,
            },
            {
                "support_bias": 1.0,
            },
        )
        explicit_override_forecast = derive_energy_forecast(
            {
                "battery_combined_discharge_power_w": 200.0,
                "battery_combined_grid_interaction_w": 100.0,
            },
            {
                "import_support_bias": 0.0,
                "support_bias": 1.0,
            },
        )

        self.assertEqual(fallback_forecast["expected_near_term_import_w"], 0.0)
        self.assertEqual(explicit_override_forecast["expected_near_term_import_w"], 100.0)

    def test_derive_energy_forecast_clamps_bias_and_soc_scales(self) -> None:
        charge_forecast = derive_energy_forecast(
            {
                "battery_combined_soc": 60.0,
                "battery_combined_charge_power_w": 100.0,
                "battery_combined_grid_interaction_w": -10.0,
            },
            {
                "observed_max_charge_power_w": 500.0,
                "reserve_band_ceiling_soc": 80.0,
                "export_bias": 2.0,
                "power_smoothing_ratio": 2.0,
            },
        )
        discharge_forecast = derive_energy_forecast(
            {
                "battery_combined_soc": 45.0,
                "battery_combined_discharge_power_w": 100.0,
                "battery_combined_grid_interaction_w": 100.0,
            },
            {
                "reserve_band_floor_soc": 40.0,
                "import_support_bias": 1.0,
                "power_smoothing_ratio": 2.0,
            },
        )

        self.assertEqual(charge_forecast["battery_headroom_charge_w"], 400.0)
        self.assertAlmostEqual(charge_forecast["expected_near_term_export_w"] or 0.0, 40.0, places=6)
        self.assertEqual(discharge_forecast["battery_headroom_discharge_w"], 0.0)
        self.assertAlmostEqual(discharge_forecast["expected_near_term_import_w"] or 0.0, 62.5, places=6)

    def test_energy_numeric_optional_int_rejects_missing_and_bool_values(self) -> None:
        self.assertIsNone(optional_int(None))
        self.assertIsNone(optional_int(True))
        self.assertIsNone(optional_int(False))
        self.assertIsNone(optional_int("bad"))
        self.assertEqual(optional_int(" 7 "), 7)

    def test_energy_forecast_charge_saturation_internal_contract(self) -> None:
        self.assertEqual(energy_forecast_mod._charge_saturation(None, None, 0.0), 0.0)
        self.assertEqual(energy_forecast_mod._charge_saturation(None, None, 0.5), 1.0)
        self.assertEqual(energy_forecast_mod._charge_saturation(None, 0.0, 2.0), 1.0)
        self.assertEqual(energy_forecast_mod._charge_saturation(1.0, 0.0, 2.0), 1.0)
        self.assertEqual(energy_forecast_mod._charge_saturation(0.0, 4.0, 2.0), 1.0)
        self.assertEqual(energy_forecast_mod._charge_saturation(2.0, 4.0, 2.0), 0.5)

    def test_derive_energy_forecast_zeroes_headroom_at_reserve_band_edges(self) -> None:
        forecast = derive_energy_forecast(
            {
                "battery_combined_soc": 80.0,
                "battery_combined_charge_power_w": 200.0,
                "battery_combined_discharge_power_w": 300.0,
                "battery_combined_grid_interaction_w": 150.0,
            },
            {
                "observed_max_charge_power_w": 1200.0,
                "observed_max_discharge_power_w": 1400.0,
                "average_active_charge_power_w": 600.0,
                "average_active_discharge_power_w": 900.0,
                "reserve_band_ceiling_soc": 80.0,
                "reserve_band_floor_soc": 80.0,
                "import_support_bias": 1.0,
            },
        )

        self.assertEqual(forecast["battery_headroom_charge_w"], 0.0)
        self.assertEqual(forecast["battery_headroom_discharge_w"], 0.0)
        self.assertEqual(forecast["expected_near_term_import_w"], 150.0)

    def test_derive_energy_forecast_returns_zero_saturation_without_observed_limit(self) -> None:
        forecast = derive_energy_forecast(
            {
                "battery_combined_charge_power_w": 0.0,
                "battery_combined_grid_interaction_w": -25.0,
            },
            {
                "export_bias": 1.0,
            },
        )

        self.assertEqual(forecast["expected_near_term_export_w"], 25.0)

    def test_energy_cluster_as_dict_includes_ac_output_alias(self) -> None:
        cluster = aggregate_energy_sources(
            (
                EnergySourceSnapshot(
                    source_id="hybrid",
                    role="hybrid-inverter",
                    service_name="svc",
                    soc=50.0,
                    usable_capacity_wh=5000.0,
                    ac_power_w=1800.0,
                    online=True,
                    confidence=1.0,
                    captured_at=1.0,
                ),
            )
        )

        payload = cluster.as_dict()

        self.assertEqual(payload["combined_ac_power_w"], 1800.0)
        self.assertEqual(payload["combined_ac_output_power_w"], 1800.0)
