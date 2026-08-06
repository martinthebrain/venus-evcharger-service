# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import ANY, patch

from venus_evcharger.bootstrap.wizard_import import ImportedWizardDefaults
from venus_evcharger.bootstrap import wizard_transport_guidance as guidance


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
    }
    values.update(overrides)
    return ImportedWizardDefaults(**values)


def _namespace(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "transport": None,
        "transport_host": None,
        "transport_port": None,
        "transport_device": None,
        "transport_unit_id": None,
        "request_timeout_seconds": None,
        "switch_group_phase_layout": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class WizardTransportGuidanceContractTests(unittest.TestCase):
    def test_prompt_transport_inputs_uses_tcp_defaults_and_prompt_order(self) -> None:
        choice_calls: list[tuple[str, tuple[str, ...], dict[str, str] | None, str | None]] = []
        text_calls: list[tuple[str, str]] = []

        def prompt_choice(label: str, values: tuple[str, ...], labels: dict[str, str] | None, default: str | None) -> str:
            choice_calls.append((label, values, labels, default))
            return "tcp"

        def prompt_text(label: str, default: str) -> str:
            text_calls.append((label, default))
            return {"Modbus TCP host": "198.51.100.8", "Modbus TCP port": "2502", "Modbus unit id": "9"}[label]

        self.assertEqual(
            guidance.prompt_transport_inputs(
                "modbus_charger",
                "openwb-modbus-secondary",
                "http://fallback.local/path",
                _imported(),
                prompt_choice=prompt_choice,
                prompt_text=prompt_text,
            ),
            ("tcp", "198.51.100.8", 2502, "/dev/ttyUSB0", 9),
        )
        self.assertEqual(choice_calls, [("Choose the transport:", ("serial_rtu", "tcp"), None, "tcp")])
        self.assertEqual(
            text_calls,
            [("Modbus TCP host", "fallback.local"), ("Modbus TCP port", "1502"), ("Modbus unit id", "1")],
        )

    def test_prompt_transport_inputs_uses_imported_default_kind_and_plain_fallbacks(self) -> None:
        choice_calls: list[tuple[str, tuple[str, ...], dict[str, str] | None, str | None]] = []
        text_calls: list[tuple[str, str]] = []

        def prompt_choice(label: str, values: tuple[str, ...], labels: dict[str, str] | None, default: str | None) -> str:
            choice_calls.append((label, values, labels, default))
            return default or "tcp"

        def prompt_text(label: str, default: str) -> str:
            text_calls.append((label, default))
            return default

        self.assertEqual(
            guidance.prompt_transport_inputs(
                "modbus_charger",
                None,
                "charger.local",
                _imported(transport_kind="serial_rtu", transport_device="/dev/imported", transport_unit_id=7),
                prompt_choice=prompt_choice,
                prompt_text=prompt_text,
            ),
            ("serial_rtu", "charger.local", 502, "/dev/imported", 7),
        )
        self.assertEqual(choice_calls, [("Choose the transport:", ("serial_rtu", "tcp"), None, "serial_rtu")])
        self.assertEqual(text_calls, [("Serial device", "/dev/imported"), ("Modbus unit id", "7")])

        tcp_text_calls: list[tuple[str, str]] = []

        def tcp_prompt_text(label: str, default: str) -> str:
            tcp_text_calls.append((label, default))
            return default

        self.assertEqual(
            guidance.prompt_transport_inputs(
                "modbus_charger",
                None,
                "charger.local",
                _imported(),
                prompt_choice=lambda _label, _values, _labels, default: default or "tcp",
                prompt_text=tcp_prompt_text,
            ),
            ("tcp", "charger.local", 502, "/dev/ttyUSB0", 1),
        )
        self.assertEqual(tcp_text_calls, [("Modbus TCP host", "charger.local"), ("Modbus TCP port", "502"), ("Modbus unit id", "1")])

    def test_prompt_transport_inputs_honors_preset_unit_id_default(self) -> None:
        text_calls: list[tuple[str, str]] = []

        def prompt_text(label: str, default: str) -> str:
            text_calls.append((label, default))
            return default

        with patch("venus_evcharger.bootstrap.wizard_transport_guidance.preset_transport_unit_id", return_value=7) as preset_unit_id:
            self.assertEqual(
                guidance.prompt_transport_inputs(
                    "modbus_charger",
                    "custom-preset",
                    "charger.local",
                    _imported(),
                    prompt_choice=lambda _label, _values, _labels, default: default or "tcp",
                    prompt_text=prompt_text,
                ),
                ("tcp", "charger.local", 502, "/dev/ttyUSB0", 7),
            )

        preset_unit_id.assert_called_once_with("custom-preset")
        self.assertEqual(text_calls[-1], ("Modbus unit id", "7"))

    def test_prompt_transport_inputs_uses_serial_defaults_and_host_input(self) -> None:
        def prompt_choice(_label: str, _values: tuple[str, ...], _labels: dict[str, str] | None, _default: str | None) -> str:
            return "serial_rtu"

        def prompt_text(label: str, default: str) -> str:
            return {"Serial device": "/dev/ttyUSB9", "Modbus unit id": "4"}[label]

        self.assertEqual(
            guidance.prompt_transport_inputs(
                "simpleevse_charger",
                None,
                "http://serial.local/status",
                _imported(transport_port=1702),
                prompt_choice=prompt_choice,
                prompt_text=prompt_text,
            ),
            ("serial_rtu", "serial.local", 1702, "/dev/ttyUSB9", 4),
        )

    def test_prompt_transport_inputs_rejects_invalid_transport_with_label(self) -> None:
        with self.assertRaisesRegex(ValueError, r"^Transport must not be empty$"):
            guidance._required_transport_kind(None)
        with self.assertRaisesRegex(ValueError, r"^Unsupported transport: bluetooth$"):
            guidance.prompt_transport_inputs(
                "modbus_charger",
                None,
                "charger.local",
                _imported(),
                prompt_choice=lambda *_args: "bluetooth",
                prompt_text=lambda label, default: default,
            )
        with self.assertRaisesRegex(ValueError, r"^Unsupported transport: bluetooth$"):
            guidance.prompt_transport_inputs(
                "modbus_charger",
                None,
                "charger.local",
                _imported(transport_kind="bluetooth"),
                prompt_choice=lambda _label, _values, _labels, default: default or "tcp",
                prompt_text=lambda label, default: default,
            )

    def test_non_interactive_transport_inputs_precedence_and_preset_defaults(self) -> None:
        self.assertEqual(
            guidance.non_interactive_transport_inputs(
                _namespace(transport="tcp", transport_host="cli.local", transport_port=2502, transport_device="/dev/cli", transport_unit_id=8),
                "modbus_charger",
                "cfos-power-brain-modbus",
                "fallback.local",
                _imported(transport_kind="serial_rtu", transport_host="import.local", transport_device="/dev/import"),
            ),
            ("tcp", "cli.local", 2502, "/dev/cli", 8),
        )
        self.assertEqual(
            guidance.non_interactive_transport_inputs(
                _namespace(),
                "modbus_charger",
                "cfos-power-brain-modbus",
                "http://fallback.local/path",
                _imported(),
            ),
            ("tcp", "fallback.local", 4701, "/dev/ttyUSB0", 1),
        )
        self.assertEqual(
            guidance.non_interactive_transport_inputs(
                _namespace(),
                "simpleevse_charger",
                None,
                "http://fallback.local/path",
                _imported(transport_kind="serial_rtu", transport_host="import.local", transport_device="/dev/import", transport_port=1702, transport_unit_id=7),
            ),
            ("serial_rtu", "import.local", 1702, "/dev/import", 7),
        )
        self.assertEqual(
            guidance.non_interactive_transport_inputs(
                _namespace(),
                "modbus_charger",
                None,
                "fallback.local",
                _imported(),
            ),
            ("tcp", "fallback.local", 502, "/dev/ttyUSB0", 1),
        )
        with self.assertRaisesRegex(ValueError, r"^Unsupported transport: bluetooth$"):
            guidance.non_interactive_transport_inputs(
                _namespace(transport="bluetooth"),
                "modbus_charger",
                None,
                "fallback.local",
                _imported(),
            )

    def test_non_interactive_transport_inputs_prioritizes_cli_import_and_preset_defaults(self) -> None:
        with (
            patch("venus_evcharger.bootstrap.wizard_transport_guidance.preset_transport_port", return_value=1701) as preset_port,
            patch("venus_evcharger.bootstrap.wizard_transport_guidance.preset_transport_unit_id", return_value=6) as preset_unit_id,
        ):
            self.assertEqual(
                guidance.non_interactive_transport_inputs(
                    _namespace(),
                    "modbus_charger",
                    "vendor-preset",
                    "fallback.local",
                    _imported(),
                ),
                ("tcp", "fallback.local", 1701, "/dev/ttyUSB0", 6),
            )
            self.assertEqual(
                guidance.non_interactive_transport_inputs(
                    _namespace(),
                    "modbus_charger",
                    "vendor-preset",
                    "fallback.local",
                    _imported(transport_port=1602, transport_unit_id=9),
                ),
                ("tcp", "fallback.local", 1602, "/dev/ttyUSB0", 9),
            )
            self.assertEqual(
                guidance.non_interactive_transport_inputs(
                    _namespace(transport_port=2602, transport_unit_id=10),
                    "modbus_charger",
                    "vendor-preset",
                    "fallback.local",
                    _imported(transport_port=1602, transport_unit_id=9),
                ),
                ("tcp", "fallback.local", 2602, "/dev/ttyUSB0", 10),
            )

        self.assertEqual(preset_port.call_args_list[0].args, ("vendor-preset", "tcp"))
        self.assertEqual(preset_unit_id.call_args_list[0].args, ("vendor-preset",))

    def test_preset_specific_defaults_cover_timeout_and_phase_layout_precedence(self) -> None:
        self.assertEqual(
            guidance.preset_specific_defaults(
                _namespace(request_timeout_seconds=7.5, switch_group_phase_layout="P1,P1_P2"),
                _imported(request_timeout_seconds=6.5, switch_group_phase_layout="P1,P1_P2_P3"),
                backend="goe_charger",
                topology_preset="goe-external-switch-group",
            ),
            (7.5, "P1,P1_P2"),
        )
        self.assertEqual(
            guidance.preset_specific_defaults(
                _namespace(),
                _imported(request_timeout_seconds=6.5, switch_group_phase_layout="P1,P1_P2"),
                backend="goe_charger",
                topology_preset="not-a-switch-layout",
            ),
            (6.5, "P1,P1_P2,P1_P2_P3"),
        )
        self.assertEqual(
            guidance.preset_specific_defaults(
                _namespace(),
                _imported(),
                backend="goe_charger",
                topology_preset="template-meter-goe-switch-group",
            ),
            (2.0, "P1,P1_P2,P1_P2_P3"),
        )
        self.assertEqual(
            guidance.preset_specific_defaults(
                _namespace(),
                _imported(),
                backend="template_charger",
                topology_preset=None,
            ),
            (None, "P1,P1_P2,P1_P2_P3"),
        )

    def test_prompt_preset_specific_defaults_prompts_only_when_supported(self) -> None:
        choice_calls: list[tuple[str, tuple[str, ...], dict[str, str] | None, str | None]] = []
        text_calls: list[tuple[str, str]] = []

        def prompt_choice(label: str, values: tuple[str, ...], labels: dict[str, str] | None, default: str | None) -> str:
            choice_calls.append((label, values, labels, default))
            return "P1,P1_P2_P3"

        def prompt_text(label: str, default: str) -> str:
            text_calls.append((label, default))
            return "3.5"

        self.assertEqual(
            guidance.prompt_preset_specific_defaults(
                _namespace(),
                _imported(),
                profile="multi_adapter_topology",
                backend="goe_charger",
                topology_preset="goe-external-switch-group",
                prompt_choice=prompt_choice,
                prompt_text=prompt_text,
            ),
            (3.5, "P1,P1_P2_P3"),
        )
        self.assertEqual(text_calls, [("go-e request timeout seconds", "2")])
        self.assertEqual(choice_calls[0][0], "Choose the external phase-switch layout:")
        self.assertEqual(choice_calls[0][1], ("P1,P1_P2,P1_P2_P3", "P1,P1_P2_P3"))
        self.assertEqual(
            choice_calls[0][2],
            {
                "P1,P1_P2,P1_P2_P3": "1P -> 2P -> 3P staged switching",
                "P1,P1_P2_P3": "1P -> 3P switching only",
            },
        )
        self.assertEqual(choice_calls[0][3], "P1,P1_P2,P1_P2_P3")

        self.assertEqual(
            guidance.prompt_preset_specific_defaults(
                _namespace(request_timeout_seconds=4.0, switch_group_phase_layout="P1,P1_P2"),
                _imported(),
                profile="native_device",
                backend="goe_charger",
                topology_preset="goe-external-switch-group",
                prompt_choice=lambda *_args: "unexpected",
                prompt_text=lambda _label, _default: "unexpected",
            ),
            (4.0, "P1,P1_P2"),
        )

        imported_timeout_calls: list[tuple[str, str]] = []

        def imported_timeout_text(label: str, default: str) -> str:
            imported_timeout_calls.append((label, default))
            return "7.5"

        self.assertEqual(
            guidance.prompt_preset_specific_defaults(
                _namespace(),
                _imported(request_timeout_seconds=6.5),
                profile="multi_adapter_topology",
                backend="goe_charger",
                topology_preset="goe-external-switch-group",
                prompt_choice=lambda *_args: "P1,P1_P2_P3",
                prompt_text=imported_timeout_text,
            ),
            (7.5, "P1,P1_P2_P3"),
        )
        self.assertEqual(imported_timeout_calls, [("go-e request timeout seconds", "6.5")])

        self.assertEqual(
            guidance.prompt_preset_specific_defaults(
                _namespace(),
                _imported(),
                profile="multi_adapter_topology",
                backend="template_charger",
                topology_preset=None,
                prompt_choice=lambda *_args: "unexpected",
                prompt_text=lambda _label, _default: "unexpected",
            ),
            (None, "P1,P1_P2,P1_P2_P3"),
        )

        with patch(
            "venus_evcharger.bootstrap.wizard_transport_guidance.preset_specific_defaults",
            return_value=(None, "P1,P1_P2,P1_P2_P3"),
        ) as preset_defaults:
            self.assertEqual(
                guidance.prompt_preset_specific_defaults(
                    _namespace(),
                    _imported(),
                    profile="native_device",
                    backend="template_charger",
                    topology_preset="template-stack",
                    prompt_choice=lambda *_args: "unexpected",
                    prompt_text=lambda _label, _default: "unexpected",
                ),
                (None, "P1,P1_P2,P1_P2_P3"),
            )
        preset_defaults.assert_called_once_with(
            ANY,
            ANY,
            backend="template_charger",
            topology_preset="template-stack",
            charger_preset=None,
        )

        with patch(
            "venus_evcharger.bootstrap.wizard_transport_guidance.preset_specific_defaults",
            return_value=(None, "P1,P1_P2,P1_P2_P3"),
        ) as preset_defaults_with_charger:
            self.assertEqual(
                guidance.prompt_preset_specific_defaults(
                    _namespace(),
                    _imported(),
                    profile="native_device",
                    backend="template_charger",
                    topology_preset="template-stack",
                    charger_preset="abb-terra-ac-modbus",
                    prompt_choice=lambda *_args: "unexpected",
                    prompt_text=lambda _label, _default: "unexpected",
                ),
                (None, "P1,P1_P2,P1_P2_P3"),
            )
        preset_defaults_with_charger.assert_called_once_with(
            ANY,
            ANY,
            backend="template_charger",
            topology_preset="template-stack",
            charger_preset="abb-terra-ac-modbus",
        )

        self.assertEqual(
            guidance.prompt_preset_specific_defaults(
                _namespace(),
                _imported(),
                profile="hybrid_topology",
                backend="template_charger",
                topology_preset="custom-switch-group-layout",
                prompt_choice=lambda *_args: "P1,P1_P2_P3",
                prompt_text=lambda _label, _default: "unexpected",
            ),
            (None, "P1,P1_P2_P3"),
        )


if __name__ == "__main__":
    unittest.main()
