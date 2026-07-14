# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import unittest

from venus_evcharger.energy.grid_fusion_contracts import GridFusionConfig
from venus_evcharger.inputs.helper.config_runtime import _AutoInputHelperConfig


class TestAutoInputConfigRuntimeContracts(unittest.TestCase):
    @staticmethod
    def _owner(values: dict[str, str]) -> _AutoInputHelperConfig:
        parser = configparser.ConfigParser()
        parser["DEFAULT"] = values
        owner = object.__new__(_AutoInputHelperConfig)
        owner.config = parser["DEFAULT"]
        return owner

    def test_grid_fusion_defaults_match_the_public_contract(self) -> None:
        self.assertEqual(self._owner({})._grid_fusion_config(), GridFusionConfig())

    def test_grid_fusion_config_maps_every_setting_exactly(self) -> None:
        config = self._owner(
            {
                "AutoGridFusionEnabled": "yes",
                "AutoGridFusionPrimarySource": " huawei ",
                "AutoGridFusionBackupSource": " backup ",
                "AutoGridFusionPrimaryMaxAgeSeconds": "21.5",
                "AutoGridFusionBackupMaxAgeSeconds": "8.5",
                "AutoGridFusionMinimumConfidence": "0.75",
                "AutoGridFusionFailoverSamples": "4",
                "AutoGridFusionRecoverySamples": "18",
                "AutoGridFusionFailoverHoldSeconds": "7.5",
                "AutoGridFusionMismatchAbsoluteWatts": "425",
                "AutoGridFusionMismatchRelative": "0.22",
                "AutoGridFusionMismatchSamples": "5",
                "AutoGridFusionFutureToleranceSeconds": "1.75",
            }
        )._grid_fusion_config()

        self.assertEqual(
            config,
            GridFusionConfig(
                enabled=True,
                primary_source_id="huawei",
                backup_source_id="backup",
                primary_max_age_seconds=21.5,
                backup_max_age_seconds=8.5,
                minimum_confidence=0.75,
                failover_samples=4,
                recovery_samples=18,
                failover_hold_seconds=7.5,
                mismatch_absolute_watts=425.0,
                mismatch_relative=0.22,
                mismatch_samples=5,
                future_tolerance_seconds=1.75,
            ),
        )


if __name__ == "__main__":
    unittest.main()
