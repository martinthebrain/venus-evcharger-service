# SPDX-License-Identifier: GPL-3.0-or-later
"""Source selection and activation contracts for Victron ESS balancing."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.update.victron_ess_balance_apply_sources import (
    VictronEssSourceResolver,
)


class VictronEssBalanceApplySourcesContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sources = VictronEssSourceResolver()

    def test_metric_merge_replaces_invalid_state_and_updates_mapping_in_place(self) -> None:
        svc = SimpleNamespace(_last_auto_metrics=None)
        metrics = {"new": 2}
        self.assertIsNone(self.sources._merge_victron_ess_balance_metrics(svc, metrics))
        self.assertEqual(svc._last_auto_metrics, metrics)
        self.assertIsNot(svc._last_auto_metrics, metrics)

        missing = SimpleNamespace()
        self.sources._merge_victron_ess_balance_metrics(missing, metrics)
        self.assertEqual(missing._last_auto_metrics, metrics)

        existing = {"old": 1, "new": 0}
        svc._last_auto_metrics = existing
        self.sources._merge_victron_ess_balance_metrics(svc, metrics)
        self.assertIs(svc._last_auto_metrics, existing)
        self.assertEqual(existing, {"old": 1, "new": 2})

    def test_boundary_normalizers_preserve_only_supported_shapes(self) -> None:
        self.assertIsNone(self.sources._optional_float(None))
        self.assertIsNone(self.sources._optional_float("1"))
        self.assertEqual(self.sources._optional_float(2), 2.0)
        self.assertEqual(self.sources._optional_float(2.5), 2.5)
        mapping = {"a": 1}
        self.assertIs(self.sources._normalized_mapping(mapping), mapping)
        self.assertEqual(self.sources._normalized_mapping([]), {})
        self.assertEqual(self.sources._normalized_text(None), "")
        self.assertEqual(self.sources._normalized_text(""), "")
        self.assertEqual(self.sources._normalized_text(0), "")
        self.assertEqual(self.sources._normalized_text(" value "), "value")

    def test_cluster_source_filter_and_configured_id_are_exact(self) -> None:
        first = {"source_id": "a"}
        second = {"source_id": "b"}
        self.assertEqual(
            self.sources._victron_ess_balance_cluster_sources(
                {"battery_sources": [first, None, "bad", second]}
            ),
            [first, second],
        )
        self.assertEqual(self.sources._victron_ess_balance_cluster_sources({}), [])
        self.assertEqual(
            self.sources._victron_ess_balance_configured_source_id(
                SimpleNamespace(auto_battery_discharge_balance_victron_bias_source_id=" source-a ")
            ),
            "source-a",
        )
        self.assertEqual(self.sources._victron_ess_balance_configured_source_id(SimpleNamespace()), "")

    def test_matching_and_dbus_candidate_filters_normalize_values(self) -> None:
        a = {"source_id": " a ", "discharge_balance_control_connector_type": " DBUS "}
        b = {"source_id": "b", "discharge_balance_control_connector_type": "http"}
        missing = {}
        self.assertIs(self.sources._victron_ess_balance_matching_source([a, b, missing], "a"), a)
        self.assertIsNone(self.sources._victron_ess_balance_matching_source([a, b], "missing"))
        self.assertEqual(self.sources._victron_ess_balance_dbus_source_candidates([a, b, missing]), [a])

    def test_source_resolution_covers_configured_and_auto_detected_outcomes(self) -> None:
        dbus_a = {"source_id": "a", "discharge_balance_control_connector_type": "dbus"}
        dbus_b = {"source_id": "b", "discharge_balance_control_connector_type": "DBUS"}
        http = {"source_id": "h", "discharge_balance_control_connector_type": "http"}
        configured = SimpleNamespace(auto_battery_discharge_balance_victron_bias_source_id="a")
        self.assertEqual(
            self.sources._victron_ess_balance_source({"battery_sources": [dbus_a]}, configured),
            (dbus_a, "configured-source"),
        )
        configured.auto_battery_discharge_balance_victron_bias_source_id = "missing"
        self.assertEqual(
            self.sources._victron_ess_balance_source({"battery_sources": [dbus_a]}, configured),
            (None, "victron-source-not-found"),
        )
        automatic = SimpleNamespace(auto_battery_discharge_balance_victron_bias_source_id="")
        self.assertEqual(
            self.sources._victron_ess_balance_source({"battery_sources": [http, dbus_a]}, automatic),
            (dbus_a, "auto-detected-dbus-source"),
        )
        self.assertEqual(
            self.sources._victron_ess_balance_source({"battery_sources": [http]}, automatic),
            (None, "victron-source-not-detected"),
        )
        self.assertEqual(
            self.sources._victron_ess_balance_source({"battery_sources": [dbus_a, dbus_b]}, automatic),
            (None, "victron-source-ambiguous"),
        )

    def test_support_mode_and_source_support_matrix(self) -> None:
        mode_attr = "auto_battery_discharge_balance_victron_bias_support_mode"
        for raw, expected in [
            (" supported_only ", "supported_only"),
            ("ALLOW_EXPERIMENTAL", "allow_experimental"),
            ("invalid", "allow_experimental"),
            (None, "allow_experimental"),
        ]:
            self.assertEqual(self.sources._victron_ess_balance_support_mode(SimpleNamespace(**{mode_attr: raw})), expected)
        self.assertEqual(self.sources._victron_ess_balance_support_mode(SimpleNamespace()), "allow_experimental")

        supported_only = SimpleNamespace(**{mode_attr: "supported_only"})
        experimental = SimpleNamespace(**{mode_attr: "allow_experimental"})
        for support, allowed in [("supported", True), ("", True), ("experimental", False), ("other", False)]:
            self.assertIs(
                self.sources._victron_ess_balance_source_support_allowed(
                    {"discharge_balance_control_support": support}, supported_only
                ),
                allowed,
            )
        for support, allowed in [("SUPPORTED", True), (" experimental ", True), ("", True), ("other", False)]:
            self.assertIs(
                self.sources._victron_ess_balance_source_support_allowed(
                    {"discharge_balance_control_support": support}, experimental
                ),
                allowed,
            )
        self.assertIs(self.sources._victron_ess_balance_source_support_allowed({}, experimental), True)

    def test_activation_mode_normalization_and_gate_matrices(self) -> None:
        attr = "auto_battery_discharge_balance_victron_bias_activation_mode"
        valid = ("always", "export_only", "above_reserve_band", "export_and_above_reserve_band")
        for mode in valid:
            self.assertEqual(self.sources._victron_ess_balance_activation_mode(SimpleNamespace(**{attr: mode.upper()})), mode)
        self.assertEqual(self.sources._victron_ess_balance_activation_mode(SimpleNamespace(**{attr: "bad"})), "always")
        self.assertEqual(self.sources._victron_ess_balance_activation_mode(SimpleNamespace()), "always")

        for mode in valid:
            self.assertIs(
                self.sources._victron_ess_balance_activation_site_regime_matches(mode, "export"),
                True,
            )
            self.assertIs(
                self.sources._victron_ess_balance_activation_reserve_phase_matches(mode, "above_reserve_band"),
                True,
            )
        self.assertIs(self.sources._victron_ess_balance_activation_site_regime_matches("export_only", "import"), False)
        self.assertIs(
            self.sources._victron_ess_balance_activation_site_regime_matches(
                "export_and_above_reserve_band", "self_consumption"
            ),
            False,
        )
        self.assertIs(
            self.sources._victron_ess_balance_activation_reserve_phase_matches("above_reserve_band", "reserve_band"),
            False,
        )
        self.assertIs(
            self.sources._victron_ess_balance_activation_reserve_phase_matches(
                "export_and_above_reserve_band", "below_reserve_band"
            ),
            False,
        )

    def test_activation_allowed_short_circuits_always_and_combines_both_gates(self) -> None:
        svc = object()
        with (
            patch.object(self.sources, "_victron_ess_balance_activation_mode", return_value="always") as mode,
            patch.object(self.sources, "_victron_ess_balance_activation_site_regime_matches") as site,
            patch.object(self.sources, "_victron_ess_balance_activation_reserve_phase_matches") as reserve,
        ):
            self.assertIs(self.sources._victron_ess_balance_activation_allowed({}, svc), True)
        mode.assert_called_once_with(svc)
        site.assert_not_called()
        reserve.assert_not_called()

        profile = {"site_regime": "export", "reserve_phase": "above_reserve_band"}
        with (
            patch.object(
                self.sources,
                "_victron_ess_balance_activation_mode",
                return_value="export_and_above_reserve_band",
            ),
            patch.object(self.sources, "_victron_ess_balance_activation_site_regime_matches", return_value=True) as site,
            patch.object(self.sources, "_victron_ess_balance_activation_reserve_phase_matches", return_value=False) as reserve,
        ):
            self.assertIs(self.sources._victron_ess_balance_activation_allowed(profile, svc), False)
        site.assert_called_once_with("export_and_above_reserve_band", "export")
        reserve.assert_called_once_with("export_and_above_reserve_band", "above_reserve_band")

        with (
            patch.object(self.sources, "_victron_ess_balance_activation_mode", return_value="export_only"),
            patch.object(self.sources, "_victron_ess_balance_activation_site_regime_matches", return_value=False) as site,
            patch.object(self.sources, "_victron_ess_balance_activation_reserve_phase_matches") as reserve,
        ):
            self.assertIs(self.sources._victron_ess_balance_activation_allowed({}, svc), False)
        site.assert_called_once_with("export_only", "")
        reserve.assert_not_called()

        with (
            patch.object(self.sources, "_victron_ess_balance_activation_mode", return_value="above_reserve_band"),
            patch.object(self.sources, "_victron_ess_balance_activation_site_regime_matches", return_value=True) as site,
            patch.object(self.sources, "_victron_ess_balance_activation_reserve_phase_matches", return_value=False) as reserve,
        ):
            self.assertIs(self.sources._victron_ess_balance_activation_allowed({}, svc), False)
        site.assert_called_once_with("above_reserve_band", "")
        reserve.assert_called_once_with("above_reserve_band", "")


if __name__ == "__main__":
    unittest.main()
