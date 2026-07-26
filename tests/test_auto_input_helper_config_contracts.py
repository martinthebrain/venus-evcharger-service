# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

from venus_evcharger.dbus_gateway_core import gateway_paths
from venus_evcharger.energy.grid_fusion_contracts import GridFusionConfig
from venus_evcharger.inputs.helper.config_runtime import (
    AutoInputHelperSettings,
    load_auto_input_helper_settings,
)
from venus_evcharger.inputs.helper.external_contracts import (
    ExternalPollingPolicy,
    PvProjectionPolicy,
    PvSourcePolicyName,
)


class AutoInputHelperConfigContracts(unittest.TestCase):
    def _load(
        self,
        config: str = "[DEFAULT]\n",
        *,
        snapshot_path: str | None = None,
        parent_pid: object = None,
        helper_generation: object = None,
        runtime_instance_id: object = "instance",
    ) -> tuple[AutoInputHelperSettings, str]:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "config.ini"
            path.write_text(config, encoding="utf-8")
            settings = load_auto_input_helper_settings(
                str(path),
                snapshot_path,
                parent_pid,
                helper_generation,
                runtime_instance_id,
            )
            return settings, str(path)

    def _assert_config_error(self, config: str, expected: str) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "config.ini"
            path.write_text(config, encoding="utf-8")
            with self.assertRaises(ValueError) as raised:
                load_auto_input_helper_settings(str(path), None, None, None, "error-case")
        self.assertEqual(str(raised.exception), expected)

    def test_missing_config_fails_fast_and_names_the_path(self) -> None:
        missing = "/missing/config.ini"
        with self.assertRaises(ValueError) as raised:
            load_auto_input_helper_settings(missing, None, None, None, None)
        self.assertEqual(str(raised.exception), f"Unable to read config file: {missing}")

    def test_empty_config_has_one_exact_complete_default_contract(self) -> None:
        with patch("venus_evcharger.inputs.helper.config_runtime.uuid.uuid4") as uuid4:
            uuid4.return_value.hex = "generated"
            settings, config_path = self._load(runtime_instance_id=None)

        self.assertEqual(
            settings,
            AutoInputHelperSettings(
                config_path=config_path,
                snapshot_path="/run/dbus-venus-evcharger-auto.json",
                parent_pid=None,
                helper_generation=0,
                runtime_instance_id="generated",
                gateway_paths=gateway_paths(),
                gateway_max_age_seconds=10.0,
                gateway_error_retry_seconds=30.0,
                auto_pv_poll_interval_seconds=1.0,
                auto_grid_poll_interval_seconds=1.0,
                auto_battery_poll_interval_seconds=1.0,
                poll_interval_seconds=1.0,
                grid_fusion_config=GridFusionConfig(
                    enabled=False,
                    primary_source_id="",
                    backup_source_id="victron",
                    primary_max_age_seconds=15.0,
                    backup_max_age_seconds=10.0,
                    minimum_confidence=0.5,
                    failover_samples=3,
                    recovery_samples=15,
                    failover_hold_seconds=6.0,
                    mismatch_absolute_watts=300.0,
                    mismatch_relative=0.15,
                    mismatch_samples=3,
                    future_tolerance_seconds=1.0,
                ),
                gateway_energy_source=None,
                energy_sources=(),
                use_combined_battery_soc=True,
                energy_source_request_timeout_seconds=2.0,
                external_polling_policy=ExternalPollingPolicy(),
                pv_projection_policy=PvProjectionPolicy(),
                validation_poll_seconds=30.0,
                topology_refresh_seconds=60.0,
            ),
        )

    def test_paths_are_trimmed_and_explicit_snapshot_argument_wins(self) -> None:
        config = """[DEFAULT]
AutoInputSnapshotPath = /run/from-config.json
DbusGatewayRunDir = /run/custom-gateway
DbusGatewayCachePath = /tmp/custom-cache.json
"""
        configured, _ = self._load(config)
        overridden, _ = self._load(config, snapshot_path="/run/from-argument.json")
        empty_override, _ = self._load(config, snapshot_path="")

        self.assertEqual(configured.snapshot_path, "/run/from-config.json")
        self.assertEqual(configured.gateway_paths.run_dir, "/run/custom-gateway")
        self.assertEqual(configured.gateway_paths.cache_path, "/tmp/custom-cache.json")
        self.assertEqual(overridden.snapshot_path, "/run/from-argument.json")
        self.assertEqual(empty_override.snapshot_path, "/run/from-config.json")

    def test_default_cache_path_follows_the_configured_gateway_run_directory(self) -> None:
        settings, _ = self._load("[DEFAULT]\nDbusGatewayRunDir=/run/isolated\n")
        self.assertEqual(
            settings.gateway_paths.cache_path,
            "/run/isolated/dbus-cache.json",
        )

    def test_config_keys_remain_case_insensitive(self) -> None:
        settings, _ = self._load(
            """[DEFAULT]
autoinputsnapshotpath=/run/lowercase.json
dbusgatewayrundir=/run/lowercase-gateway
dbusgatewaymaxageseconds=9
autopvsourcepolicy=GATEWAY-ONLY
"""
        )
        self.assertEqual(settings.snapshot_path, "/run/lowercase.json")
        self.assertEqual(
            settings.gateway_paths.cache_path,
            "/run/lowercase-gateway/dbus-cache.json",
        )
        self.assertEqual(settings.gateway_max_age_seconds, 9.0)
        self.assertEqual(settings.pv_projection_policy.name, "gateway_only")

    def test_runtime_identity_is_normalized_without_persistence(self) -> None:
        explicit, _ = self._load(
            parent_pid="12",
            helper_generation="-2",
            runtime_instance_id=" runtime ",
        )
        with patch("venus_evcharger.inputs.helper.config_runtime.uuid.uuid4") as uuid4:
            uuid4.return_value.hex = "generated"
            generated, _ = self._load(
                parent_pid=True,
                helper_generation=True,
                runtime_instance_id=" ",
            )
        with patch("venus_evcharger.inputs.helper.config_runtime.uuid.uuid4") as uuid4:
            uuid4.return_value.hex = "fallback"
            unsupported, _ = self._load(
                parent_pid=object(),
                helper_generation=2.5,
                runtime_instance_id=None,
            )

        self.assertEqual(
            (explicit.parent_pid, explicit.helper_generation, explicit.runtime_instance_id),
            (12, 0, "runtime"),
        )
        self.assertEqual(
            (generated.parent_pid, generated.helper_generation, generated.runtime_instance_id),
            (1, 1, "generated"),
        )
        self.assertEqual(
            (unsupported.parent_pid, unsupported.helper_generation, unsupported.runtime_instance_id),
            (None, 0, "fallback"),
        )

    def test_invalid_numeric_identity_fails_fast(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid literal"):
            self._load(parent_pid="not-a-pid")
        with self.assertRaisesRegex(ValueError, "invalid literal"):
            self._load(helper_generation="not-a-generation")

    def test_poll_fallback_and_independent_poll_intervals_are_preserved(self) -> None:
        fallback, _ = self._load("[DEFAULT]\nPollIntervalMs=2500\n")
        independent, _ = self._load(
            """[DEFAULT]
PollIntervalMs=9000
AutoInputPollIntervalMs=500
AutoPvPollIntervalMs=200
AutoGridPollIntervalMs=700
AutoBatteryPollIntervalMs=900
"""
        )

        self.assertEqual(
            (
                fallback.auto_pv_poll_interval_seconds,
                fallback.auto_grid_poll_interval_seconds,
                fallback.auto_battery_poll_interval_seconds,
                fallback.poll_interval_seconds,
            ),
            (2.5, 2.5, 2.5, 2.5),
        )
        self.assertEqual(
            (
                independent.auto_pv_poll_interval_seconds,
                independent.auto_grid_poll_interval_seconds,
                independent.auto_battery_poll_interval_seconds,
                independent.poll_interval_seconds,
            ),
            (0.2, 0.7, 0.9, 0.2),
        )
        self.assertEqual(independent.external_polling_policy.poll_interval_seconds, 0.2)

    def test_poll_and_refresh_floors_are_enforced(self) -> None:
        settings, _ = self._load(
            """[DEFAULT]
AutoInputPollIntervalMs=0
AutoPvPollIntervalMs=0
AutoGridPollIntervalMs=-1
AutoBatteryPollIntervalMs=100
AutoInputValidationPollSeconds=0
EnergyTopologyRefreshSeconds=-1
"""
        )
        self.assertEqual(
            (
                settings.auto_pv_poll_interval_seconds,
                settings.auto_grid_poll_interval_seconds,
                settings.auto_battery_poll_interval_seconds,
                settings.poll_interval_seconds,
                settings.validation_poll_seconds,
                settings.topology_refresh_seconds,
            ),
            (0.2, 0.2, 0.2, 0.2, 5.0, 5.0),
        )

    def test_each_poll_candidate_can_independently_control_the_loop(self) -> None:
        cases = (
            (300, 1000, 2000, 3000, 0.3, 1.0),
            (2000, 300, 1000, 1500, 0.3, 0.3),
            (2000, 1000, 300, 1500, 0.3, 0.3),
            (2000, 1000, 1500, 300, 0.3, 0.3),
        )
        for auto_ms, pv_ms, grid_ms, battery_ms, expected_loop, expected_external in cases:
            with self.subTest(
                auto_ms=auto_ms,
                pv_ms=pv_ms,
                grid_ms=grid_ms,
                battery_ms=battery_ms,
            ):
                settings, _ = self._load(
                    "[DEFAULT]\n"
                    f"AutoInputPollIntervalMs={auto_ms}\n"
                    f"AutoPvPollIntervalMs={pv_ms}\n"
                    f"AutoGridPollIntervalMs={grid_ms}\n"
                    f"AutoBatteryPollIntervalMs={battery_ms}\n"
                )
                self.assertEqual(settings.poll_interval_seconds, expected_loop)
                self.assertEqual(
                    settings.external_polling_policy.poll_interval_seconds,
                    expected_external,
                )

    def test_gateway_freshness_retry_and_external_timeout_bounds(self) -> None:
        lower, _ = self._load(
            """[DEFAULT]
DbusGatewayMaxAgeSeconds=-1
DbusGatewayErrorRetrySeconds=0
ExternalEnergySourceRequestTimeoutSeconds=0
"""
        )
        upper, _ = self._load(
            """[DEFAULT]
DbusGatewayMaxAgeSeconds=17
DbusGatewayErrorRetrySeconds=999
ExternalEnergySourceRequestTimeoutSeconds=4
"""
        )
        middle, _ = self._load("[DEFAULT]\nDbusGatewayErrorRetrySeconds=12\n")

        self.assertEqual(
            (
                lower.gateway_max_age_seconds,
                lower.gateway_error_retry_seconds,
                lower.energy_source_request_timeout_seconds,
            ),
            (0.0, 1.0, 0.1),
        )
        self.assertEqual(
            (
                upper.gateway_max_age_seconds,
                upper.gateway_error_retry_seconds,
                upper.energy_source_request_timeout_seconds,
            ),
            (17.0, 300.0, 4.0),
        )
        self.assertEqual(middle.gateway_error_retry_seconds, 12.0)

    def test_custom_grid_fusion_contract_preserves_every_parameter(self) -> None:
        settings, _ = self._load(
            """[DEFAULT]
AutoEnergySources=huawei
AutoEnergySource.huawei.Type=modbus
AutoGridFusionEnabled=yes
AutoGridFusionPrimarySource=huawei
AutoGridFusionBackupSource=backup
AutoGridFusionPrimaryMaxAgeSeconds=12
AutoGridFusionBackupMaxAgeSeconds=7
AutoGridFusionMinimumConfidence=0.75
AutoGridFusionFailoverSamples=4
AutoGridFusionRecoverySamples=5
AutoGridFusionFailoverHoldSeconds=8
AutoGridFusionMismatchAbsoluteWatts=400
AutoGridFusionMismatchRelative=0.2
AutoGridFusionMismatchSamples=6
AutoGridFusionFutureToleranceSeconds=2
"""
        )
        self.assertEqual(
            settings.grid_fusion_config,
            GridFusionConfig(
                enabled=True,
                primary_source_id="huawei",
                backup_source_id="backup",
                primary_max_age_seconds=12.0,
                backup_max_age_seconds=7.0,
                minimum_confidence=0.75,
                failover_samples=4,
                recovery_samples=5,
                failover_hold_seconds=8.0,
                mismatch_absolute_watts=400.0,
                mismatch_relative=0.2,
                mismatch_samples=6,
                future_tolerance_seconds=2.0,
            ),
        )

    def test_disabled_grid_fusion_uses_gateway_freshness_for_backup(self) -> None:
        settings, _ = self._load(
            """[DEFAULT]
DbusGatewayMaxAgeSeconds=11
AutoGridFusionEnabled=off
AutoGridFusionBackupMaxAgeSeconds=6
"""
        )
        self.assertFalse(settings.grid_fusion_config.enabled)
        self.assertEqual(settings.grid_fusion_config.backup_max_age_seconds, 11.0)

    def test_enabled_grid_fusion_uses_its_own_backup_freshness_default(self) -> None:
        settings, _ = self._load(
            """[DEFAULT]
AutoEnergySources=huawei
AutoEnergySource.huawei.Type=modbus
AutoGridFusionEnabled=1
AutoGridFusionPrimarySource=huawei
"""
        )
        self.assertTrue(settings.grid_fusion_config.enabled)
        self.assertEqual(settings.grid_fusion_config.backup_max_age_seconds, 6.0)

    def test_grid_fusion_freshness_must_cover_battery_polling(self) -> None:
        self._assert_config_error(
            """[DEFAULT]
AutoEnergySources=huawei
AutoEnergySource.huawei.Type=modbus
AutoGridFusionEnabled=1
AutoGridFusionPrimarySource=huawei
AutoGridFusionPrimaryMaxAgeSeconds=1
AutoBatteryPollIntervalMs=2000
""",
            "AutoGridFusionPrimaryMaxAgeSeconds must cover AutoBatteryPollIntervalMs",
        )

    def test_grid_fusion_freshness_may_equal_battery_polling(self) -> None:
        settings, _ = self._load(
            """[DEFAULT]
AutoEnergySources=huawei
AutoEnergySource.huawei.Type=modbus
AutoGridFusionEnabled=1
AutoGridFusionPrimarySource=huawei
AutoGridFusionPrimaryMaxAgeSeconds=2
AutoBatteryPollIntervalMs=2000
"""
        )
        self.assertEqual(settings.grid_fusion_config.primary_max_age_seconds, 2.0)
        self.assertEqual(settings.auto_battery_poll_interval_seconds, 2.0)

    def test_each_invalid_grid_fusion_parameter_fails_its_contract(self) -> None:
        cases = (
            ("AutoGridFusionPrimarySource", "", "Grid fusion requires a primary source id"),
            ("AutoGridFusionBackupSource", "", "Grid fusion requires a backup source id"),
            ("AutoGridFusionFailoverSamples", "0", "Grid fusion sample thresholds must be positive"),
            ("AutoGridFusionRecoverySamples", "0", "Grid fusion sample thresholds must be positive"),
            ("AutoGridFusionMismatchSamples", "0", "Grid fusion sample thresholds must be positive"),
            ("AutoGridFusionPrimaryMaxAgeSeconds", "-1", "Grid fusion freshness limits must be non-negative"),
            ("AutoGridFusionBackupMaxAgeSeconds", "-1", "Grid fusion freshness limits must be non-negative"),
            (
                "AutoGridFusionMinimumConfidence",
                "-0.1",
                "Grid fusion minimum confidence must be between zero and one",
            ),
            (
                "AutoGridFusionMinimumConfidence",
                "1.1",
                "Grid fusion minimum confidence must be between zero and one",
            ),
            ("AutoGridFusionFailoverHoldSeconds", "-1", "Grid fusion time tolerances must be non-negative"),
            ("AutoGridFusionFutureToleranceSeconds", "-1", "Grid fusion time tolerances must be non-negative"),
            ("AutoGridFusionMismatchAbsoluteWatts", "-1", "Grid fusion mismatch tolerances must be non-negative"),
            ("AutoGridFusionMismatchRelative", "-1", "Grid fusion mismatch tolerances must be non-negative"),
        )
        base = {
            "AutoEnergySources": "huawei",
            "AutoEnergySource.huawei.Type": "modbus",
            "AutoGridFusionEnabled": "1",
            "AutoGridFusionPrimarySource": "huawei",
        }
        for key, value, expected in cases:
            with self.subTest(key=key, value=value):
                options = {**base, key: value}
                config = "[DEFAULT]\n" + "".join(
                    f"{option}={configured}\n" for option, configured in options.items()
                )
                self._assert_config_error(config, expected)

    def test_grid_fusion_primary_must_reference_an_external_source(self) -> None:
        self._assert_config_error(
            """[DEFAULT]
AutoEnergySources=victron,huawei
AutoEnergySource.victron.Type=dbus
AutoEnergySource.huawei.Type=modbus
AutoGridFusionEnabled=1
AutoGridFusionPrimarySource=victron
""",
            "AutoGridFusionPrimarySource 'victron' is not present in external AutoEnergySources",
        )

    def test_external_polling_policy_preserves_every_parameter(self) -> None:
        settings, _ = self._load(
            """[DEFAULT]
AutoEnergySources=huawei
AutoEnergySource.huawei.Type=modbus
ExternalEnergySourcePollIntervalSeconds=2
ExternalEnergySourceBackoffBaseSeconds=3
ExternalEnergySourceBackoffMaxSeconds=12
ExternalEnergySourceLastGoodMaxAgeSeconds=20
ExternalEnergySourceCycleBudgetSeconds=0.75
"""
        )
        self.assertEqual(
            settings.external_polling_policy,
            ExternalPollingPolicy(
                poll_interval_seconds=2.0,
                backoff_base_seconds=3.0,
                backoff_max_seconds=12.0,
                last_good_max_age_seconds=20.0,
                cycle_budget_seconds=0.75,
            ),
        )

    def test_external_polling_policy_enforces_each_floor(self) -> None:
        settings, _ = self._load(
            """[DEFAULT]
ExternalEnergySourcePollIntervalSeconds=0
ExternalEnergySourceBackoffBaseSeconds=0
ExternalEnergySourceBackoffMaxSeconds=0
ExternalEnergySourceLastGoodMaxAgeSeconds=-1
ExternalEnergySourceCycleBudgetSeconds=0
"""
        )
        self.assertEqual(
            settings.external_polling_policy,
            ExternalPollingPolicy(
                poll_interval_seconds=0.2,
                backoff_base_seconds=0.1,
                backoff_max_seconds=0.1,
                last_good_max_age_seconds=0.0,
                cycle_budget_seconds=0.05,
            ),
        )

    def test_external_backoff_maximum_must_cover_base(self) -> None:
        self._assert_config_error(
            """[DEFAULT]
ExternalEnergySourceBackoffBaseSeconds=5
ExternalEnergySourceBackoffMaxSeconds=4
""",
            "External energy-source backoff maximum must cover its base",
        )

    def test_all_pv_source_policies_are_normalized_and_typed(self) -> None:
        cases = (
            ("gateway_only", "", "gateway_only"),
            ("gateway-preferred", "", "gateway_preferred"),
            ("external-preferred", "huawei", "external_preferred"),
            ("external_only", " huawei ", "external_only"),
        )
        for raw_policy, raw_source, expected_policy in cases:
            with self.subTest(policy=raw_policy):
                source_config = ""
                if expected_policy.startswith("external"):
                    source_config = "AutoEnergySources=huawei\nAutoEnergySource.huawei.Type=modbus\n"
                settings, _ = self._load(
                    f"[DEFAULT]\n{source_config}"
                    f"AutoPvSourcePolicy={raw_policy}\n"
                    f"AutoPvExternalSource={raw_source}\n"
                )
                self.assertEqual(
                    settings.pv_projection_policy,
                    PvProjectionPolicy(
                        name=cast(PvSourcePolicyName, expected_policy),
                        external_source_id=raw_source.strip(),
                    ),
                )

    def test_invalid_pv_policy_and_external_references_fail_fast(self) -> None:
        cases = (
            (
                "[DEFAULT]\nAutoPvSourcePolicy=magic\n",
                "Unsupported AutoPvSourcePolicy 'magic'",
            ),
            (
                "[DEFAULT]\n"
                "AutoEnergySources=huawei\n"
                "AutoEnergySource.huawei.Type=modbus\n"
                "AutoPvExternalSource=missing\n",
                "AutoPvExternalSource 'missing' is not present in external AutoEnergySources",
            ),
            (
                "[DEFAULT]\nAutoPvSourcePolicy=external_preferred\n",
                "AutoPvSourcePolicy 'external_preferred' requires an external energy source",
            ),
            (
                "[DEFAULT]\nAutoPvSourcePolicy=external_only\n",
                "AutoPvSourcePolicy 'external_only' requires an external energy source",
            ),
            (
                "[DEFAULT]\n"
                "AutoEnergySources=victron\n"
                "AutoEnergySource.victron.Type=dbus\n"
                "AutoPvSourcePolicy=external_only\n",
                "AutoPvSourcePolicy 'external_only' requires an external energy source",
            ),
        )
        for config, expected in cases:
            with self.subTest(expected=expected):
                self._assert_config_error(config, expected)

    def test_external_source_contract_and_combined_soc_flag_are_loaded(self) -> None:
        settings, _ = self._load(
            """[DEFAULT]
AutoEnergySources=huawei
AutoUseCombinedBatterySoc=0
AutoEnergySource.huawei.Profile=modbus-hybrid
AutoEnergySource.huawei.ConfigPath=/data/etc/huawei.ini
"""
        )
        source = settings.energy_sources[0]
        self.assertEqual(len(settings.energy_sources), 1)
        self.assertEqual(source.source_id, "huawei")
        self.assertEqual(source.profile_name, "modbus-hybrid")
        self.assertEqual(source.connector_type, "modbus")
        self.assertEqual(source.config_path, "/data/etc/huawei.ini")
        self.assertFalse(settings.use_combined_battery_soc)

    def test_unsupported_direct_dbus_source_is_rejected_at_the_boundary(self) -> None:
        self._assert_config_error(
            """[DEFAULT]
AutoEnergySources=legacy
AutoEnergySource.legacy.Type=dbus
""",
            "Auto energy source 'legacy' profile '<none>' has no supported non-DBus connector; "
            "Victron/DBus values must come from the semantic DBus gateway snapshot",
        )

    def test_duplicate_source_ids_are_rejected_before_alias_partitioning(self) -> None:
        for source_id, source_type in (("one", "modbus"), ("victron", "dbus")):
            with self.subTest(source_id=source_id):
                self._assert_config_error(
                    "[DEFAULT]\n"
                    f"AutoEnergySources={source_id},{source_id}\n"
                    f"AutoEnergySource.{source_id}.Type={source_type}\n",
                    "AutoEnergySources contains duplicate source ids",
                )

    def test_victron_is_a_semantic_gateway_alias_with_preserved_metadata(self) -> None:
        settings, _ = self._load(
            """[DEFAULT]
AutoEnergySources=victron,huawei
AutoEnergySource.victron.Profile=dbus
AutoEnergySource.victron.Type=dbus
AutoEnergySource.victron.UsableCapacityWh=5000
AutoEnergySource.victron.Chemistry=LFP
AutoEnergySource.victron.PhysicalId=house-battery
AutoEnergySource.huawei.Type=modbus
AutoEnergySource.huawei.PhysicalId=house-battery
"""
        )
        gateway = settings.gateway_energy_source
        self.assertIsNotNone(gateway)
        assert gateway is not None
        self.assertEqual(gateway.source_id, "victron")
        self.assertEqual(gateway.profile_name, "dbus")
        self.assertEqual(gateway.usable_capacity_wh, 5000.0)
        self.assertEqual(gateway.battery_chemistry, "lfp")
        self.assertEqual(gateway.physical_id, "house-battery")
        self.assertEqual(
            tuple(
                (source.source_id, source.physical_id)
                for source in settings.energy_sources
            ),
            (("huawei", "house-battery"),),
        )


if __name__ == "__main__":
    unittest.main()
