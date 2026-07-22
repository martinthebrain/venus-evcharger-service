# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact contracts for named external-energy profiles."""

from __future__ import annotations

import unittest

from venus_evcharger.energy import profiles


_PROFILE_NAMES = (
    "template-http-hybrid",
    "modbus-hybrid",
    "command-json-hybrid",
    "opendtu-pvinverter",
    "huawei_ma_native_ap",
    "huawei_ma_native_lan",
    "huawei_ma_sdongle",
    "huawei_ma_smartlogger_modbus_tcp",
    "huawei_mb_native_ap",
    "huawei_mb_native_lan",
    "huawei_mb_sdongle",
    "huawei_mb_smartlogger_modbus_tcp",
    "huawei_mb_unit1",
    "huawei_mb_unit2",
    "huawei_smartlogger_modbus_tcp",
    "huawei_l1_native_ap",
    "huawei_l1_native_lan",
    "huawei_l1_sdongle",
    "huawei_l1_smartlogger_modbus_tcp",
    "huawei_lc0_native_ap",
    "huawei_lc0_native_lan",
    "huawei_lc0_sdongle",
    "huawei_lc0_smartlogger_modbus_tcp",
    "huawei_lb0_native_ap",
    "huawei_lb0_native_lan",
    "huawei_lb0_sdongle",
    "huawei_lb0_smartlogger_modbus_tcp",
    "huawei_m1_native_ap",
    "huawei_m1_native_lan",
    "huawei_m1_sdongle",
    "huawei_m1_smartlogger_modbus_tcp",
    "huawei_map0_native_ap",
    "huawei_map0_native_lan",
    "huawei_map0_sdongle",
    "huawei_map0_smartlogger_modbus_tcp",
    "huawei_map0_unit1",
    "huawei_map0_unit2",
    "huawei_mb0_native_ap",
    "huawei_mb0_native_lan",
    "huawei_mb0_sdongle",
    "huawei_mb0_smartlogger_modbus_tcp",
    "huawei_mb0_unit1",
    "huawei_mb0_unit2",
)


def _base_profile(**overrides: object) -> dict[str, object]:
    expected: dict[str, object] = {
        "profile_name": "",
        "role": "",
        "connector_type": "",
        "vendor_name": "",
        "platform": "",
        "family_name": "",
        "access_mode": "",
        "firmware_class": "",
        "battery_chemistry": "lfp",
        "capacity_auto_estimate": True,
        "capacity_estimate_min_soc": 95.0,
        "capacity_startup_recheck_seconds": 300.0,
        "default_host": "",
        "default_port_candidates": (),
        "default_unit_id_candidates": (),
        "read_support": "supported",
        "write_support": "unsupported",
        "probe_required": False,
        "idle_unreachable_policy": "strict",
    }
    expected.update(overrides)
    return expected


class EnergyProfilesContractTests(unittest.TestCase):
    def test_huawei_profile_and_variant_builders_preserve_every_argument(self) -> None:
        profile = profiles._huawei_profile(
            "profile-x",
            platform="PX",
            access_mode="direct",
            firmware_class="firmware-x",
            family_name="Family X",
            default_host="host-x",
            default_port_candidates=(1502, 2502),
            default_unit_id_candidates=(4, 7),
        )
        self.assertEqual(
            vars(profile),
            _base_profile(
                profile_name="profile-x",
                role="hybrid-inverter",
                connector_type="modbus",
                vendor_name="Huawei",
                platform="PX",
                family_name="Family X",
                access_mode="direct",
                firmware_class="firmware-x",
                default_host="host-x",
                default_port_candidates=(1502, 2502),
                default_unit_id_candidates=(4, 7),
                write_support="experimental",
                probe_required=True,
            ),
        )
        platform_family = profiles._huawei_profile(
            "profile-y",
            platform="PY",
            access_mode="logger",
            firmware_class="firmware-y",
        )
        self.assertEqual(platform_family.family_name, "PY")
        self.assertEqual(platform_family.default_host, "")
        variant = profiles._profile_variant(profile, "variant-x", family_name="Variant Family")
        self.assertEqual(
            vars(variant),
            {
                **vars(profile),
                "profile_name": "variant-x",
                "family_name": "Variant Family",
            },
        )
        self.assertEqual(profile.profile_name, "profile-x")
        self.assertEqual(profile.family_name, "Family X")

    def test_profile_catalog_order_and_aliases_are_exact(self) -> None:
        self.assertEqual(profiles.available_energy_source_profiles(), _PROFILE_NAMES)
        expected_aliases = {
            "template-http": "template-http-hybrid",
            "http-hybrid": "template-http-hybrid",
            "modbus": "modbus-hybrid",
            "command-json": "command-json-hybrid",
            "helper": "command-json-hybrid",
            "opendtu": "opendtu-pvinverter",
            "opendtu-inverter": "opendtu-pvinverter",
            "growatt-opendtu": "opendtu-pvinverter",
            "huawei_sun5000_lb0_native_ap": "huawei_lb0_native_ap",
            "huawei_sun5000_lb0_native_lan": "huawei_lb0_native_lan",
            "huawei_sun5000_lb0_sdongle": "huawei_lb0_sdongle",
            "huawei_sun5000_lb0_smartlogger_modbus_tcp": "huawei_lb0_smartlogger_modbus_tcp",
            "huawei_sun5000_map0_native_ap": "huawei_map0_native_ap",
            "huawei_sun5000_map0_native_lan": "huawei_map0_native_lan",
            "huawei_sun5000_map0_sdongle": "huawei_map0_sdongle",
            "huawei_sun5000_map0_smartlogger_modbus_tcp": "huawei_map0_smartlogger_modbus_tcp",
            "huawei_sun5000_map0_unit1": "huawei_map0_unit1",
            "huawei_sun5000_map0_unit2": "huawei_map0_unit2",
        }
        self.assertEqual(profiles._ALIASES, expected_aliases)
        for alias, canonical in expected_aliases.items():
            with self.subTest(alias=alias):
                resolved = profiles.resolve_energy_source_profile(f" {alias.upper()} ")
                self.assertIsNotNone(resolved)
                self.assertEqual(resolved.profile_name, canonical)
        self.assertIsNone(profiles.resolve_energy_source_profile(" "))
        self.assertIsNone(profiles.resolve_energy_source_profile("unknown"))
        self.assertIsNone(profiles.resolve_energy_source_profile("dbus-battery"))
        self.assertIsNone(profiles.resolve_energy_source_profile("dbus-hybrid"))
        self.assertIsNone(profiles.resolve_energy_source_profile("battery"))
        self.assertIsNone(profiles.resolve_energy_source_profile("hybrid"))

    def test_core_profile_payloads_are_exact(self) -> None:
        expected = {
            "template-http-hybrid": _base_profile(
                profile_name="template-http-hybrid",
                role="hybrid-inverter",
                connector_type="template_http",
            ),
            "modbus-hybrid": _base_profile(
                profile_name="modbus-hybrid",
                role="hybrid-inverter",
                connector_type="modbus",
            ),
            "command-json-hybrid": _base_profile(
                profile_name="command-json-hybrid",
                role="hybrid-inverter",
                connector_type="command_json",
            ),
            "opendtu-pvinverter": _base_profile(
                profile_name="opendtu-pvinverter",
                role="inverter",
                connector_type="opendtu_http",
                vendor_name="OpenDTU",
                platform="OpenDTU",
                family_name="OpenDTU",
                access_mode="http_json",
                firmware_class="status_api",
                idle_unreachable_policy="allow_plausible_idle",
            ),
        }
        for profile_name, payload in expected.items():
            with self.subTest(profile_name=profile_name):
                profile = profiles.resolve_energy_source_profile(profile_name)
                self.assertIsNotNone(profile)
                self.assertEqual(vars(profile), payload)

    def test_every_huawei_profile_has_the_exact_family_transport_contract(self) -> None:
        family_platforms = {
            "ma": ("MA", "MA"),
            "mb": ("MB", "MB"),
            "l1": ("MA", "L1"),
            "lc0": ("MA", "LC0"),
            "lb0": ("MA", "LB0"),
            "m1": ("MA", "M1"),
            "map0": ("MB", "MAP0"),
            "mb0": ("MB", "MB0"),
        }
        access_contracts = {
            "native_ap": ("native_ap", "local_ap_6607", "192.168.200.1", (6607, 502), (0, 1)),
            "native_lan": ("native_lan", "legacy_lan_open", "", (502, 6607), (0, 1)),
            "sdongle": ("sdongle", "sdongle_third_party", "", (502, 6607), (0, 1)),
            "smartlogger_modbus_tcp": ("smartlogger", "smartlogger_502", "", (502,), (0, 1)),
        }
        for family, (platform, family_name) in family_platforms.items():
            for suffix, contract in access_contracts.items():
                profile_name = f"huawei_{family}_{suffix}"
                profile = profiles.resolve_energy_source_profile(profile_name)
                self.assertIsNotNone(profile, profile_name)
                access_mode, firmware, host, ports, units = contract
                self.assertEqual(
                    vars(profile),
                    _base_profile(
                        profile_name=profile_name,
                        role="hybrid-inverter",
                        connector_type="modbus",
                        vendor_name="Huawei",
                        platform=platform,
                        family_name=family_name,
                        access_mode=access_mode,
                        firmware_class=firmware,
                        default_host=host,
                        default_port_candidates=ports,
                        default_unit_id_candidates=units,
                        write_support="experimental",
                        probe_required=True,
                    ),
                )
        for family, family_name in (("mb", "MB"), ("map0", "MAP0"), ("mb0", "MB0")):
            for unit in (1, 2):
                profile_name = f"huawei_{family}_unit{unit}"
                profile = profiles.resolve_energy_source_profile(profile_name)
                self.assertIsNotNone(profile)
                self.assertEqual(profile.family_name, family_name)
                self.assertEqual(profile.access_mode, "unit_split")
                self.assertEqual(profile.firmware_class, "mb_unit_split")
                self.assertEqual(profile.default_port_candidates, (502, 6607))
                self.assertEqual(profile.default_unit_id_candidates, (0, 1))
        smartlogger = profiles.resolve_energy_source_profile("huawei_smartlogger_modbus_tcp")
        self.assertIsNotNone(smartlogger)
        self.assertEqual(smartlogger.platform, "smartlogger")
        self.assertEqual(smartlogger.family_name, "generic")
        self.assertEqual(smartlogger.default_port_candidates, (502,))

    def test_defaults_details_and_probe_plans_are_exact(self) -> None:
        self.assertEqual(profiles.energy_source_profile_defaults("unknown"), {})
        self.assertEqual(profiles.energy_source_profile_details("unknown"), {})
        self.assertEqual(profiles.energy_source_profile_probe_plan("unknown"), {})
        self.assertEqual(
            profiles.energy_source_profile_defaults("template-http-hybrid"),
            {
                "Profile": "template-http-hybrid",
                "Role": "hybrid-inverter",
                "Type": "template_http",
                "BatteryChemistry": "lfp",
                "CapacityAutoEstimate": True,
                "CapacityEstimateMinSoc": 95.0,
                "CapacityStartupRecheckSeconds": 300.0,
            },
        )
        self.assertEqual(
            profiles.energy_source_profile_details("huawei_l1_native_ap"),
            {
                "profile_name": "huawei_l1_native_ap",
                "vendor_name": "Huawei",
                "platform": "MA",
                "family_name": "L1",
                "access_mode": "native_ap",
                "firmware_class": "local_ap_6607",
                "connector_type": "modbus",
                "role": "hybrid-inverter",
                "default_host": "192.168.200.1",
                "default_port_candidates": [6607, 502],
                "default_unit_id_candidates": [0, 1],
                "read_support": "supported",
                "write_support": "experimental",
                "probe_required": True,
                "idle_unreachable_policy": "strict",
            },
        )
        self.assertEqual(
            profiles.energy_source_profile_probe_plan(
                "huawei_l1_native_ap",
                configured_host=" custom-host ",
                configured_port=" 1502 ",
                configured_unit_id=3,
            ),
            {
                "profile_name": "huawei_l1_native_ap",
                "connector_type": "modbus",
                "host": "custom-host",
                "port_candidates": [1502],
                "unit_id_candidates": [3],
                "probe_required": True,
            },
        )
        self.assertEqual(
            profiles.energy_source_profile_probe_plan("huawei_l1_native_ap"),
            {
                "profile_name": "huawei_l1_native_ap",
                "connector_type": "modbus",
                "host": "192.168.200.1",
                "port_candidates": [6607, 502],
                "unit_id_candidates": [0, 1],
                "probe_required": True,
            },
        )

    def test_candidate_parsing_rejects_ambiguous_values(self) -> None:
        for value, expected in (
            (True, None),
            (False, None),
            (7, 7),
            (" 8 ", 8),
            ("", None),
            ("bad", None),
            (7.0, None),
            (None, None),
        ):
            with self.subTest(value=value):
                self.assertEqual(profiles._candidate_int_value(value), expected)
        self.assertEqual(profiles._candidate_values("9", (1, 2)), [9])
        self.assertEqual(profiles._candidate_values("bad", (1, 2)), [1, 2])


if __name__ == "__main__":
    unittest.main()
