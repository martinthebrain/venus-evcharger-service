# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast

from venus_evcharger.bootstrap.wizard_import import ImportedWizardDefaults
from venus_evcharger.bootstrap import wizard_policy_guidance as policy


def _namespace(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "auto_start_surplus_watts": None,
        "auto_stop_surplus_watts": None,
        "auto_min_soc": None,
        "auto_resume_soc": None,
        "scheduled_enabled_days": None,
        "scheduled_latest_end_time": None,
        "scheduled_night_current_amps": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _imported(**overrides: object) -> ImportedWizardDefaults:
    values: dict[str, object] = {
        "imported_from": "",
        "profile": None,
        "host_input": None,
        "meter_host_input": None,
        "switch_host_input": None,
        "charger_host_input": None,
        "device_instance": None,
        "phase": None,
        "policy_mode": None,
        "digest_auth": None,
        "username": None,
        "password": None,
        "topology_preset": None,
        "charger_backend": None,
        "charger_preset": None,
        "request_timeout_seconds": None,
        "switch_group_phase_layout": None,
        "auto_start_surplus_watts": None,
        "auto_stop_surplus_watts": None,
        "auto_min_soc": None,
        "auto_resume_soc": None,
        "scheduled_enabled_days": None,
        "scheduled_latest_end_time": None,
        "scheduled_night_current_amps": None,
        "transport_kind": None,
        "transport_host": None,
        "transport_port": None,
        "transport_device": None,
        "transport_unit_id": None,
        "inventory_path": None,
    }
    values.update(overrides)
    return ImportedWizardDefaults(**cast(Any, values))


class WizardPolicyGuidanceContractTests(unittest.TestCase):
    def test_policy_defaults_manual_preserves_explicit_namespace_values_without_auto_defaults(self) -> None:
        self.assertEqual(
            policy.policy_defaults(
                "manual",
                _imported(
                    auto_start_surplus_watts=1800.0,
                    scheduled_enabled_days="Mon",
                    scheduled_latest_end_time="07:00",
                    scheduled_night_current_amps=6.0,
                ),
                _namespace(
                    auto_start_surplus_watts="1111.5",
                    auto_stop_surplus_watts=999,
                    auto_min_soc="22.5",
                    auto_resume_soc=44,
                    scheduled_enabled_days="Tue",
                    scheduled_latest_end_time="08:15",
                    scheduled_night_current_amps="5.5",
                ),
            ),
            (1111.5, 999.0, 22.5, 44.0, "Tue", "08:15", 5.5),
        )

    def test_policy_defaults_auto_uses_namespace_then_imported_then_builtin_defaults(self) -> None:
        self.assertEqual(
            policy.policy_defaults(
                "auto",
                _imported(auto_stop_surplus_watts=1200.0, auto_resume_soc=66.0),
                _namespace(auto_start_surplus_watts="1900", auto_min_soc=None),
            ),
            (1900.0, 1200.0, 30.0, 66.0, None, None, None),
        )

    def test_policy_defaults_scheduled_resolves_auto_and_scheduled_defaults(self) -> None:
        self.assertEqual(
            policy.policy_defaults(
                "scheduled",
                _imported(
                    auto_start_surplus_watts=1700.0,
                    auto_stop_surplus_watts=900.0,
                    scheduled_enabled_days="Sat,Sun",
                    scheduled_latest_end_time="05:45",
                    scheduled_night_current_amps=7.5,
                ),
                _namespace(auto_min_soc="40", auto_resume_soc="45"),
            ),
            (1700.0, 900.0, 40.0, 45.0, "Sat,Sun", "05:45", 7.5),
        )
        self.assertEqual(
            policy.policy_defaults("scheduled", _imported(), _namespace()),
            (1850.0, 1350.0, 30.0, 33.0, "Mon,Tue,Wed,Thu,Fri", "06:30", 0.0),
        )
        self.assertEqual(
            policy.policy_defaults(
                "scheduled",
                _imported(scheduled_night_current_amps=7.5),
                _namespace(scheduled_night_current_amps="9.5"),
            ),
            (1850.0, 1350.0, 30.0, 33.0, "Mon,Tue,Wed,Thu,Fri", "06:30", 9.5),
        )

    def test_prompt_policy_defaults_prompts_auto_and_scheduled_missing_values_in_order(self) -> None:
        calls: list[tuple[str, str]] = []
        answers = iter(["2000", "1000", "35", "38", "Mon,Fri", "06:15", "8"])

        def prompt_text(label: str, default: str) -> str:
            calls.append((label, default))
            return next(answers)

        self.assertEqual(
            policy.prompt_policy_defaults("scheduled", _imported(), _namespace(), prompt_text=prompt_text),
            (2000.0, 1000.0, 35.0, 38.0, "Mon,Fri", "06:15", 8.0),
        )
        self.assertEqual(
            calls,
            [
                ("Auto start surplus watts", "1850"),
                ("Auto stop surplus watts", "1350"),
                ("Battery minimum SOC for Auto", "30"),
                ("Battery resume SOC for Auto", "33"),
                ("Scheduled weekdays", "Mon,Tue,Wed,Thu,Fri"),
                ("Scheduled latest end time (HH:MM)", "06:30"),
                ("Scheduled fallback night current amps", "0"),
            ],
        )

    def test_prompt_policy_defaults_uses_imported_defaults_as_prompt_defaults(self) -> None:
        calls: list[tuple[str, str]] = []

        def prompt_text(label: str, default: str) -> str:
            calls.append((label, default))
            return default

        self.assertEqual(
            policy.prompt_policy_defaults(
                "scheduled",
                _imported(
                    auto_start_surplus_watts=2100.0,
                    auto_stop_surplus_watts=1100.0,
                    auto_min_soc=41.0,
                    auto_resume_soc=46.0,
                    scheduled_enabled_days="Sat,Sun",
                    scheduled_latest_end_time="05:45",
                    scheduled_night_current_amps=7.5,
                ),
                _namespace(),
                prompt_text=prompt_text,
            ),
            (2100.0, 1100.0, 41.0, 46.0, "Sat,Sun", "05:45", 7.5),
        )
        self.assertEqual(
            calls,
            [
                ("Auto start surplus watts", "2100"),
                ("Auto stop surplus watts", "1100"),
                ("Battery minimum SOC for Auto", "41"),
                ("Battery resume SOC for Auto", "46"),
                ("Scheduled weekdays", "Sat,Sun"),
                ("Scheduled latest end time (HH:MM)", "05:45"),
                ("Scheduled fallback night current amps", "7.5"),
            ],
        )

    def test_prompt_policy_defaults_skips_existing_namespace_values(self) -> None:
        calls: list[tuple[str, str]] = []

        def prompt_text(label: str, default: str) -> str:
            calls.append((label, default))
            return default

        self.assertEqual(
            policy.prompt_policy_defaults(
                "auto",
                _imported(auto_stop_surplus_watts=1234.0),
                _namespace(auto_start_surplus_watts=1999.0, auto_min_soc=44.0, auto_resume_soc=55.0),
                prompt_text=prompt_text,
            ),
            (1999.0, 1234.0, 44.0, 55.0, None, None, None),
        )
        self.assertEqual(calls, [("Auto stop surplus watts", "1234")])

    def test_prompt_policy_defaults_manual_never_prompts(self) -> None:
        def fail_prompt(label: str, default: str) -> str:
            raise AssertionError(f"unexpected prompt {label}={default}")

        self.assertEqual(
            policy.prompt_policy_defaults("manual", _imported(), _namespace(auto_min_soc="22"), prompt_text=fail_prompt),
            (None, None, 22.0, None, None, None, None),
        )

    def test_format_prompt_default_requires_resolved_numeric_value(self) -> None:
        self.assertEqual(policy._format_prompt_default(42.0), "42")
        with self.assertRaises(ValueError) as error:
            policy._format_prompt_default(None)
        self.assertEqual(str(error.exception), "Policy prompt default must be resolved before prompting")


if __name__ == "__main__":
    unittest.main()
