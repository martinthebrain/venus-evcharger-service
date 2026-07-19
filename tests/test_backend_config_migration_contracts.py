# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for the one-way historical backend-config migration boundary."""

from __future__ import annotations

import configparser
import unittest
from unittest.mock import patch

from venus_evcharger.backend.config_loader import load_runtime_backend_summary
from venus_evcharger.backend.config_migration import migrate_legacy_backend_config
from venus_evcharger.backend.config_diagnostics import backend_selection_view
from venus_evcharger.backend.models import BackendMode, BackendRuntimeSummary
from venus_evcharger.topology.config import TopologyConfigError, legacy_topology_from_config


class BackendConfigMigrationContracts(unittest.TestCase):
    def test_legacy_input_is_migrated_to_typed_topology_and_runtime_fields(self) -> None:
        parser = _legacy_split_parser()
        sections_before = tuple(parser.sections())

        migrated = migrate_legacy_backend_config(parser)

        self.assertEqual(tuple(parser.sections()), sections_before)
        self.assertEqual(migrated.topology.topology.type, "hybrid_topology")
        actuator = migrated.topology.actuator
        measurement = migrated.topology.measurement
        self.assertIsNotNone(actuator)
        self.assertIsNotNone(measurement)
        assert actuator is not None
        assert measurement is not None
        self.assertEqual(actuator.type, "template_switch")
        self.assertEqual(measurement.type, "external_meter")
        self.assertEqual(migrated.backend_mode, "split")
        self.assertEqual(migrated.meter_type, "template_meter")
        self.assertEqual(str(migrated.meter_config_path), "/data/etc/meter.ini")
        self.assertEqual(migrated.switch_type, "template_switch")
        self.assertEqual(str(migrated.switch_config_path), "/data/etc/switch.ini")
        self.assertEqual(migrated.charger_type, "goe_charger")
        self.assertEqual(str(migrated.charger_config_path), "/data/etc/charger.ini")

    def test_runtime_loader_invokes_legacy_migration_once(self) -> None:
        parser = _legacy_split_parser()

        with patch(
            "venus_evcharger.backend.config_loader.migrate_legacy_backend_config",
            wraps=migrate_legacy_backend_config,
        ) as migrate:
            summary = load_runtime_backend_summary(parser)

        migrate.assert_called_once_with(parser)
        self.assertEqual(summary.backend_mode, "split")
        self.assertEqual(summary.meter_type, "template_meter")
        self.assertEqual(summary.switch_type, "template_switch")
        self.assertEqual(summary.charger_type, "goe_charger")

    def test_canonical_topology_bypasses_legacy_migration(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=native_device

[Charger]
Type=goe_charger
ConfigPath=/data/etc/charger.ini

[Measurement]
Type=charger_native
"""
        )

        with patch(
            "venus_evcharger.backend.config_loader.migrate_legacy_backend_config",
            side_effect=AssertionError("legacy migration must not run"),
        ):
            summary = load_runtime_backend_summary(parser)

        self.assertEqual(summary.backend_mode, "split")
        self.assertEqual(summary.charger_type, "goe_charger")
        self.assertIsNone(summary.meter_type)
        self.assertIsNone(summary.switch_type)

    def test_public_legacy_topology_entry_uses_the_same_migration(self) -> None:
        parser = _legacy_split_parser()

        with patch(
            "venus_evcharger.topology.config.migrate_legacy_backend_config",
            wraps=migrate_legacy_backend_config,
        ) as migrate:
            topology = legacy_topology_from_config(parser)

        migrate.assert_called_once_with(parser)
        self.assertEqual(topology.topology.type, "hybrid_topology")

    def test_public_migration_translates_invalid_legacy_charger_errors(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string("[Backends]\nChargerType=unsupported_charger\n")

        with self.assertRaisesRegex(TopologyConfigError, "invalid Charger.Type"):
            legacy_topology_from_config(parser)

    def test_legacy_view_maps_empty_canonical_roles_by_backend_mode(self) -> None:
        expected_roles: tuple[tuple[BackendMode, tuple[str, str]], ...] = (
            ("combined", ("shelly_meter", "shelly_contactor_switch")),
            ("split", ("none", "none")),
        )
        for mode, expected in expected_roles:
            with self.subTest(mode=mode):
                runtime = _empty_runtime_summary(mode)
                view = backend_selection_view(runtime)
                self.assertIsNotNone(view)
                assert view is not None
                self.assertEqual((view["meter_type"], view["switch_type"]), expected)


def _legacy_split_parser() -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read_string(
        """
[DEFAULT]
Mode=1
Phase=L1

[Backends]
Mode=split
MeterType=template_meter
MeterConfigPath=/data/etc/meter.ini
SwitchType=template_switch
SwitchConfigPath=/data/etc/switch.ini
ChargerType=goe_charger
ChargerConfigPath=/data/etc/charger.ini
"""
    )
    return parser


def _empty_runtime_summary(mode: BackendMode) -> BackendRuntimeSummary:
    return BackendRuntimeSummary(
        backend_mode=mode,
        meter_type="",
        meter_config_path=None,
        switch_type="",
        switch_config_path=None,
        charger_type=None,
        charger_config_path=None,
        topology_configured=False,
        primary_rpc_configured=False,
    )


if __name__ == "__main__":
    unittest.main()
