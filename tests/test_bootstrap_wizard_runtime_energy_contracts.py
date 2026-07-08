# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from venus_evcharger.bootstrap import wizard_runtime_energy


class WizardRuntimeEnergyContractTests(unittest.TestCase):
    def test_suggested_energy_state_returns_originals_without_prefixes(self) -> None:
        result = wizard_runtime_energy.suggested_energy_state(
            Path("/tmp/config.ini"),
            "config",
            {"adapter.ini": "adapter"},
            ("review",),
            tuple(),
            apply_suggested_energy_merge=True,
            suggested_energy_capacity_wh=1234.0,
            suggested_energy_capacity_overrides={"source": 5678.0},
        )

        self.assertEqual(result, ("config", {"adapter.ini": "adapter"}, ("review",), {}, tuple(), None))

    def test_suggested_energy_state_merges_bundle_and_preserves_apply_contract(self) -> None:
        config_path = Path("/tmp/config.ini")
        source = {"source_id": "huawei", "capacityConfigKey": "AutoEnergySource.huawei.UsableCapacityWh"}
        with (
            patch(
                "venus_evcharger.bootstrap.wizard_runtime_energy.huawei_bundle_files",
                return_value=({"bundle.ini": "bundle"}, ("bundle review",), {"hint": "text"}, (source,)),
            ) as bundle,
            patch(
                "venus_evcharger.bootstrap.wizard_runtime_energy.build_suggested_energy_merge",
                return_value=({"merge": "raw"}, {"merge.ini": "merge"}),
            ) as merge,
            patch(
                "venus_evcharger.bootstrap.wizard_runtime_energy.config_text_with_suggested_energy_merge",
                return_value=("merged config", {"merge": "applied"}),
            ) as apply_merge,
        ):
            result = wizard_runtime_energy.suggested_energy_state(
                config_path,
                "config",
                {"adapter.ini": "adapter"},
                ("review",),
                ("bundle-prefix",),
                apply_suggested_energy_merge=True,
                suggested_energy_capacity_wh=9000.0,
                suggested_energy_capacity_overrides=None,
            )

        bundle.assert_called_once_with("bundle-prefix")
        expected_source = {
            "source_id": "huawei",
            "capacityConfigKey": "AutoEnergySource.huawei.UsableCapacityWh",
            "usableCapacityWh": 9000.0,
        }
        merge.assert_called_once_with(config_path, (expected_source,))
        apply_merge.assert_called_once_with(config_path, "config", (expected_source,), {"merge": "raw"})
        self.assertEqual(
            result,
            (
                "merged config",
                {"adapter.ini": "adapter", "bundle.ini": "bundle", "merge.ini": "merge"},
                ("review", "bundle review"),
                {"hint": "text"},
                (expected_source,),
                {"merge": "applied"},
            ),
        )

    def test_suggested_energy_state_does_not_apply_merge_when_not_requested(self) -> None:
        config_path = Path("/tmp/config.ini")
        source = {"source_id": "huawei", "capacityConfigKey": "AutoEnergySource.huawei.UsableCapacityWh"}
        with (
            patch(
                "venus_evcharger.bootstrap.wizard_runtime_energy.huawei_bundle_files",
                return_value=({"bundle.ini": "bundle"}, tuple(), {}, (source,)),
            ),
            patch(
                "venus_evcharger.bootstrap.wizard_runtime_energy.build_suggested_energy_merge",
                return_value=({"merge": "raw"}, {"merge.ini": "merge"}),
            ),
            patch("venus_evcharger.bootstrap.wizard_runtime_energy.config_text_with_suggested_energy_merge") as apply_merge,
        ):
            result = wizard_runtime_energy.suggested_energy_state(
                config_path,
                "config",
                {},
                tuple(),
                ("bundle-prefix",),
                apply_suggested_energy_merge=False,
                suggested_energy_capacity_wh=None,
                suggested_energy_capacity_overrides=None,
            )

        apply_merge.assert_not_called()
        self.assertEqual(result[0], "config")
        self.assertEqual(result[1], {"bundle.ini": "bundle", "merge.ini": "merge"})
        self.assertEqual(result[5], {"merge": "raw"})

    def test_capacity_is_global_only_for_one_source_and_overrides_by_source_id(self) -> None:
        sources = (
            {"source_id": "one", "capacityConfigKey": "AutoEnergySource.one.UsableCapacityWh"},
            {"source_id": "two", "capacityConfigKey": "AutoEnergySource.two.UsableCapacityWh"},
        )

        unchanged = wizard_runtime_energy.suggested_energy_sources_with_requested_capacity(
            sources,
            111.0,
            None,
        )
        self.assertEqual(unchanged, sources)

        overridden = wizard_runtime_energy.suggested_energy_sources_with_requested_capacity(
            sources,
            None,
            {"two": 222.0},
        )
        self.assertEqual(
            overridden,
            (
                {"source_id": "one", "capacityConfigKey": "AutoEnergySource.one.UsableCapacityWh"},
                {
                    "source_id": "two",
                    "capacityConfigKey": "AutoEnergySource.two.UsableCapacityWh",
                    "usableCapacityWh": 222.0,
                },
            ),
        )


if __name__ == "__main__":
    unittest.main()
