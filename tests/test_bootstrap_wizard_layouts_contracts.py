# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest

from venus_evcharger.bootstrap.wizard_layouts import resolve_role_hosts


class WizardLayoutContractTests(unittest.TestCase):
    def test_profile_role_hosts_resolve_to_primary_or_role_specific_hosts(self) -> None:
        self.assertEqual(
            resolve_role_hosts(
                profile="simple_relay",
                primary_host_input="http://primary.local",
                meter_host_input="http://meter.local",
                switch_host_input=None,
                charger_host_input="http://charger.local",
                topology_preset=None,
            ),
            {
                "meter": "http://meter.local",
                "switch": "http://primary.local",
            },
        )
        self.assertEqual(
            resolve_role_hosts(
                profile="native_device",
                primary_host_input="http://primary.local",
                meter_host_input="http://meter.local",
                switch_host_input="http://switch.local",
                charger_host_input="http://charger.local",
                topology_preset=None,
            ),
            {"charger": "http://charger.local"},
        )
        self.assertEqual(
            resolve_role_hosts(
                profile="hybrid_topology",
                primary_host_input="http://primary.local",
                meter_host_input="http://meter.local",
                switch_host_input="http://switch.local",
                charger_host_input=None,
                topology_preset=None,
            ),
            {
                "charger": "http://primary.local",
                "switch": "http://switch.local",
            },
        )

    def test_multi_adapter_topology_resolves_roles_from_topology_preset(self) -> None:
        self.assertEqual(
            resolve_role_hosts(
                profile="multi_adapter_topology",
                primary_host_input="http://primary.local",
                meter_host_input="http://meter.local",
                switch_host_input="http://switch.local",
                charger_host_input="http://charger.local",
                topology_preset="template-stack",
            ),
            {
                "meter": "http://meter.local",
                "switch": "http://switch.local",
                "charger": "http://charger.local",
            },
        )
        self.assertEqual(
            resolve_role_hosts(
                profile="multi_adapter_topology",
                primary_host_input="http://primary.local",
                meter_host_input=None,
                switch_host_input="http://switch.local",
                charger_host_input="http://charger.local",
                topology_preset="shelly-io-modbus-charger",
            ),
            {
                "meter": "http://primary.local",
                "switch": "http://switch.local",
            },
        )

    def test_unknown_profile_or_missing_topology_preset_resolves_to_no_role_hosts(self) -> None:
        self.assertEqual(
            resolve_role_hosts(
                profile="advanced_manual",
                primary_host_input="http://primary.local",
                meter_host_input="http://meter.local",
                switch_host_input="http://switch.local",
                charger_host_input="http://charger.local",
                topology_preset=None,
            ),
            {},
        )
        self.assertEqual(
            resolve_role_hosts(
                profile="multi_adapter_topology",
                primary_host_input="http://primary.local",
                meter_host_input="http://meter.local",
                switch_host_input="http://switch.local",
                charger_host_input="http://charger.local",
                topology_preset="unknown-preset",
            ),
            {},
        )
        self.assertEqual(
            resolve_role_hosts(
                profile="multi_adapter_topology",
                primary_host_input="http://primary.local",
                meter_host_input="http://meter.local",
                switch_host_input="http://switch.local",
                charger_host_input="http://charger.local",
                topology_preset=None,
            ),
            {},
        )


if __name__ == "__main__":
    unittest.main()
