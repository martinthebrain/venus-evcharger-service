# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from venus_evcharger.bootstrap import wizard_runtime_live


class WizardRuntimeLiveContractTests(unittest.TestCase):
    def test_json_ready_dict_coerces_keys_and_rejects_non_objects(self) -> None:
        self.assertEqual(
            wizard_runtime_live.json_ready_dict({1: Path("/tmp/meter.ini"), "plain": 2}, "payload"),
            {"1": "/tmp/meter.ini", "plain": 2},
        )

        with self.assertRaisesRegex(TypeError, "probe result did not render to a JSON object"):
            wizard_runtime_live.json_ready_dict(["not", "an", "object"], "probe result")

    def test_combined_role_payload_has_exact_role_shapes(self) -> None:
        main_path = Path("/tmp/config.ini")
        meter = SimpleNamespace(read_meter=lambda: {"power_w": 123.0})
        switch = SimpleNamespace(
            capabilities=lambda: {"mode": "relay"},
            read_switch_state=lambda: {"enabled": True},
        )
        charger = SimpleNamespace(read_charger_state=lambda: {"connected": False})

        self.assertEqual(
            wizard_runtime_live.combined_role_payload("meter", meter, main_path, "meter_type"),
            {"path": "/tmp/config.ini", "type": "meter_type", "meter": {"power_w": 123.0}},
        )
        self.assertEqual(
            wizard_runtime_live.combined_role_payload("switch", switch, main_path, "switch_type"),
            {
                "path": "/tmp/config.ini",
                "type": "switch_type",
                "capabilities": {"mode": "relay"},
                "switch_state": {"enabled": True},
            },
        )
        self.assertEqual(
            wizard_runtime_live.combined_role_payload("charger", charger, main_path, "charger_type"),
            {"path": "/tmp/config.ini", "type": "charger_type", "charger_state": {"connected": False}},
        )

    def test_live_connectivity_payload_passes_default_hooks_and_secret_defaults(self) -> None:
        with patch(
            "venus_evcharger.bootstrap.wizard_runtime_live.live_connectivity_payload_with_hooks",
            return_value={"ok": True, "checked_roles": tuple(), "roles": {}},
        ) as hooks:
            payload = wizard_runtime_live.live_connectivity_payload(
                Path("/tmp/config.ini"),
                ("meter",),
                {"Password": "secret"},
            )

        self.assertTrue(payload["ok"])
        self.assertEqual(hooks.call_args.args, (Path("/tmp/config.ini"), ("meter",)))
        self.assertEqual(
            hooks.call_args.kwargs,
            {
                "secret_defaults": {"Password": "secret"},
                "build_backends_fn": wizard_runtime_live.build_service_backends,
                "probe_meter_fn": wizard_runtime_live.probe_meter_backend,
                "probe_switch_fn": wizard_runtime_live.probe_switch_backend,
                "read_charger_fn": wizard_runtime_live.read_charger_backend,
            },
        )

    def test_live_connectivity_with_hooks_split_mode_uses_probe_paths_and_skips_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            main_path = Path(temp_dir) / "config.ini"
            main_path.write_text("[DEFAULT]\nHost=device.local\n", encoding="utf-8")
            absolute_switch_path = Path(temp_dir) / "switch.ini"
            runtime = SimpleNamespace(
                backend_mode="split",
                meter_type="template_meter",
                meter_config_path=Path("meter.ini"),
                switch_type="template_switch",
                switch_config_path=absolute_switch_path,
                charger_type="goe_charger",
                charger_config_path=None,
            )
            backends = SimpleNamespace(runtime=runtime, meter=None, switch=None, charger=None)
            meter_probe = Mock(return_value={"meter": "ok"})
            switch_probe = Mock(return_value={"switch": "ok"})

            payload = wizard_runtime_live.live_connectivity_payload_with_hooks(
                main_path,
                None,
                build_backends_fn=Mock(return_value=backends),
                probe_meter_fn=meter_probe,
                probe_switch_fn=switch_probe,
                read_charger_fn=Mock(return_value={"charger": "unexpected"}),
            )

        meter_probe.assert_called_once_with(str(main_path.parent / "meter.ini"))
        switch_probe.assert_called_once_with(str(absolute_switch_path))
        self.assertEqual(
            payload,
            {
                "ok": True,
                "checked_roles": ("meter", "switch"),
                "roles": {
                    "meter": {"status": "ok", "payload": {"meter": "ok"}},
                    "switch": {"status": "ok", "payload": {"switch": "ok"}},
                    "charger": {"status": "skipped", "reason": "not configured"},
                },
            },
        )

    def test_live_connectivity_with_hooks_selected_roles_and_secret_defaults_use_backends(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            main_path = Path(temp_dir) / "config.ini"
            main_path.write_text("[DEFAULT]\nHost=device.local\nPassword=redacted\n", encoding="utf-8")
            runtime = SimpleNamespace(
                backend_mode="split",
                meter_type="template_meter",
                meter_config_path=Path("meter.ini"),
                switch_type="template_switch",
                switch_config_path=Path("switch.ini"),
                charger_type="goe_charger",
                charger_config_path=Path("charger.ini"),
            )
            meter_backend = SimpleNamespace(read_meter=lambda: {"power_w": 456.0})
            build_backends = Mock(return_value=SimpleNamespace(runtime=runtime, meter=meter_backend, switch=None, charger=None))

            payload = wizard_runtime_live.live_connectivity_payload_with_hooks(
                main_path,
                ("meter",),
                secret_defaults={"Password": "secret"},
                build_backends_fn=build_backends,
                probe_meter_fn=Mock(return_value={"unexpected": True}),
                probe_switch_fn=Mock(return_value={"unexpected": True}),
                read_charger_fn=Mock(return_value={"unexpected": True}),
            )

        service = build_backends.call_args.args[0]
        self.assertEqual(service.host, "device.local")
        self.assertEqual(service.password, "secret")
        self.assertEqual(
            payload,
            {
                "ok": True,
                "checked_roles": ("meter",),
                "roles": {
                    "meter": {
                        "status": "ok",
                        "payload": {
                            "path": str(main_path.parent / "meter.ini"),
                            "type": "template_meter",
                            "meter": {"power_w": 456.0},
                        },
                    },
                    "switch": {"status": "skipped", "reason": "not requested"},
                    "charger": {"status": "skipped", "reason": "not requested"},
                },
            },
        )

    def test_live_connectivity_with_hooks_combined_mode_and_errors_are_explicit(self) -> None:
        main_path = Path("/tmp/config.ini")
        runtime = SimpleNamespace(
            backend_mode="combined",
            meter_type="combined_meter",
            meter_config_path=None,
            switch_type="combined_switch",
            switch_config_path=None,
            charger_type="",
            charger_config_path=None,
        )
        switch = SimpleNamespace(
            capabilities=lambda: (_ for _ in ()).throw(RuntimeError("capability boom")),
            read_switch_state=lambda: {"enabled": True},
        )
        payload = wizard_runtime_live.live_connectivity_payload_with_hooks(
            main_path,
            None,
            build_backends_fn=Mock(
                return_value=SimpleNamespace(
                    runtime=runtime,
                    meter=SimpleNamespace(read_meter=lambda: {"power_w": 1.0}),
                    switch=switch,
                    charger=None,
                )
            ),
            probe_meter_fn=Mock(return_value={"unexpected": True}),
            probe_switch_fn=Mock(return_value={"unexpected": True}),
            read_charger_fn=Mock(return_value={"unexpected": True}),
        )

        self.assertIs(payload["ok"], False)
        self.assertEqual(payload["checked_roles"], ("meter", "switch"))
        self.assertEqual(
            payload["roles"]["meter"],
            {
                "status": "ok",
                "payload": {
                    "path": "/tmp/config.ini",
                    "type": "combined_meter",
                    "meter": {"power_w": 1.0},
                },
            },
        )
        self.assertEqual(
            payload["roles"]["switch"],
            {"status": "error", "error": "RuntimeError: capability boom"},
        )
        self.assertEqual(payload["roles"]["charger"], {"status": "skipped", "reason": "not configured"})

    def test_combined_mode_missing_backend_is_skipped_and_not_checked(self) -> None:
        runtime = SimpleNamespace(
            backend_mode="combined",
            meter_type="combined_meter",
            meter_config_path=None,
            switch_type="combined_switch",
            switch_config_path=None,
            charger_type="",
            charger_config_path=None,
        )
        payload = wizard_runtime_live.live_connectivity_payload_with_hooks(
            Path("/tmp/config.ini"),
            ("switch",),
            build_backends_fn=Mock(return_value=SimpleNamespace(runtime=runtime, meter=None, charger=None)),
            probe_meter_fn=Mock(return_value={"unexpected": True}),
            probe_switch_fn=Mock(return_value={"unexpected": True}),
            read_charger_fn=Mock(return_value={"unexpected": True}),
        )

        self.assertEqual(
            payload,
            {
                "ok": True,
                "checked_roles": (),
                "roles": {
                    "meter": {"status": "skipped", "reason": "not requested"},
                    "switch": {"status": "skipped", "reason": "not configured"},
                    "charger": {"status": "skipped", "reason": "not requested"},
                },
            },
        )

    def test_combined_mode_charger_with_config_uses_probe_path_not_backend_payload(self) -> None:
        runtime = SimpleNamespace(
            backend_mode="combined",
            meter_type="combined_meter",
            meter_config_path=None,
            switch_type="combined_switch",
            switch_config_path=None,
            charger_type="goe_charger",
            charger_config_path=Path("charger.ini"),
        )
        charger_reader = Mock(return_value={"charger": "ok"})

        with tempfile.TemporaryDirectory() as temp_dir:
            payload = wizard_runtime_live.live_connectivity_payload_with_hooks(
                Path(temp_dir) / "config.ini",
                ("charger",),
                build_backends_fn=Mock(
                    return_value=SimpleNamespace(
                        runtime=runtime,
                        meter=None,
                        switch=None,
                        charger=SimpleNamespace(read_charger_state=lambda: {"backend": "unused"}),
                    )
                ),
                probe_meter_fn=Mock(return_value={"unexpected": True}),
                probe_switch_fn=Mock(return_value={"unexpected": True}),
                read_charger_fn=charger_reader,
            )

        charger_reader.assert_called_once_with(str(Path(temp_dir) / "charger.ini"))
        self.assertEqual(
            payload,
            {
                "ok": True,
                "checked_roles": ("charger",),
                "roles": {
                    "meter": {"status": "skipped", "reason": "not requested"},
                    "switch": {"status": "skipped", "reason": "not requested"},
                    "charger": {"status": "ok", "payload": {"charger": "ok"}},
                },
            },
        )

    def test_secret_default_split_mode_skips_missing_backend_exactly(self) -> None:
        runtime = SimpleNamespace(
            backend_mode="split",
            meter_type="template_meter",
            meter_config_path=Path("meter.ini"),
            switch_type="template_switch",
            switch_config_path=Path("switch.ini"),
            charger_type="goe_charger",
            charger_config_path=Path("charger.ini"),
        )

        payload = wizard_runtime_live.live_connectivity_payload_with_hooks(
            Path("/tmp/config.ini"),
            ("switch",),
            secret_defaults={"Password": "secret"},
            build_backends_fn=Mock(return_value=SimpleNamespace(runtime=runtime, meter=None, charger=None)),
            probe_meter_fn=Mock(return_value={"unexpected": True}),
            probe_switch_fn=Mock(return_value={"unexpected": True}),
            read_charger_fn=Mock(return_value={"unexpected": True}),
        )

        self.assertEqual(
            payload,
            {
                "ok": True,
                "checked_roles": (),
                "roles": {
                    "meter": {"status": "skipped", "reason": "not requested"},
                    "switch": {"status": "skipped", "reason": "not configured"},
                    "charger": {"status": "skipped", "reason": "not requested"},
                },
            },
        )

    def test_secret_default_split_mode_uses_runtime_type_defaults_and_configured_types(self) -> None:
        runtime = SimpleNamespace(
            backend_mode="split",
            meter_config_path=Path("meter.ini"),
            switch_config_path=Path("switch.ini"),
            charger_type="goe_charger",
            charger_config_path=Path("charger.ini"),
        )
        meter = SimpleNamespace(read_meter=lambda: {"power_w": 9.0})
        switch = SimpleNamespace(
            capabilities=lambda: {"relay": True},
            read_switch_state=lambda: {"closed": False},
        )
        charger = SimpleNamespace(read_charger_state=lambda: {"connected": True})

        with tempfile.TemporaryDirectory() as temp_dir:
            main_path = Path(temp_dir) / "config.ini"
            payload = wizard_runtime_live.live_connectivity_payload_with_hooks(
                main_path,
                ("meter", "switch", "charger"),
                secret_defaults={"Password": "secret"},
                build_backends_fn=Mock(return_value=SimpleNamespace(runtime=runtime, meter=meter, switch=switch, charger=charger)),
                probe_meter_fn=Mock(return_value={"unexpected": True}),
                probe_switch_fn=Mock(return_value={"unexpected": True}),
                read_charger_fn=Mock(return_value={"unexpected": True}),
            )

        self.assertEqual(
            payload,
            {
                "ok": True,
                "checked_roles": ("meter", "switch", "charger"),
                "roles": {
                    "meter": {
                        "status": "ok",
                        "payload": {
                            "path": str(Path(temp_dir) / "meter.ini"),
                            "type": "shelly_meter",
                            "meter": {"power_w": 9.0},
                        },
                    },
                    "switch": {
                        "status": "ok",
                        "payload": {
                            "path": str(Path(temp_dir) / "switch.ini"),
                            "type": "shelly_contactor_switch",
                            "capabilities": {"relay": True},
                            "switch_state": {"closed": False},
                        },
                    },
                    "charger": {
                        "status": "ok",
                        "payload": {
                            "path": str(Path(temp_dir) / "charger.ini"),
                            "type": "goe_charger",
                            "charger_state": {"connected": True},
                        },
                    },
                },
            },
        )

    def test_missing_backend_mode_is_treated_as_split_probe_mode(self) -> None:
        runtime = SimpleNamespace(
            meter_type="template_meter",
            meter_config_path=Path("meter.ini"),
            switch_type="template_switch",
            switch_config_path=Path("switch.ini"),
            charger_type="goe_charger",
            charger_config_path=Path("charger.ini"),
        )
        meter_probe = Mock(return_value={"meter": "ok"})

        with tempfile.TemporaryDirectory() as temp_dir:
            main_path = Path(temp_dir) / "config.ini"
            payload = wizard_runtime_live.live_connectivity_payload_with_hooks(
                main_path,
                ("meter",),
                build_backends_fn=Mock(return_value=SimpleNamespace(runtime=runtime, meter=None, switch=None, charger=None)),
                probe_meter_fn=meter_probe,
                probe_switch_fn=Mock(return_value={"unexpected": True}),
                read_charger_fn=Mock(return_value={"unexpected": True}),
            )

        meter_probe.assert_called_once_with(str(Path(temp_dir) / "meter.ini"))
        self.assertEqual(
            payload,
            {
                "ok": True,
                "checked_roles": ("meter",),
                "roles": {
                    "meter": {"status": "ok", "payload": {"meter": "ok"}},
                    "switch": {"status": "skipped", "reason": "not requested"},
                    "charger": {"status": "skipped", "reason": "not requested"},
                },
            },
        )

    def test_secret_default_split_mode_uses_configured_switch_type(self) -> None:
        runtime = SimpleNamespace(
            backend_mode="split",
            meter_config_path=Path("meter.ini"),
            switch_type="custom_switch",
            switch_config_path=Path("switch.ini"),
            charger_config_path=Path("charger.ini"),
        )
        switch = SimpleNamespace(
            capabilities=lambda: {"kind": "custom"},
            read_switch_state=lambda: {"enabled": True},
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            payload = wizard_runtime_live.live_connectivity_payload_with_hooks(
                Path(temp_dir) / "config.ini",
                ("switch",),
                secret_defaults={"Password": "secret"},
                build_backends_fn=Mock(return_value=SimpleNamespace(runtime=runtime, meter=None, switch=switch, charger=None)),
                probe_meter_fn=Mock(return_value={"unexpected": True}),
                probe_switch_fn=Mock(return_value={"unexpected": True}),
                read_charger_fn=Mock(return_value={"unexpected": True}),
            )

        self.assertEqual(
            payload,
            {
                "ok": True,
                "checked_roles": ("switch",),
                "roles": {
                    "meter": {"status": "skipped", "reason": "not requested"},
                    "switch": {
                        "status": "ok",
                        "payload": {
                            "path": str(Path(temp_dir) / "switch.ini"),
                            "type": "custom_switch",
                            "capabilities": {"kind": "custom"},
                            "switch_state": {"enabled": True},
                        },
                    },
                    "charger": {"status": "skipped", "reason": "not requested"},
                },
            },
        )

    def test_secret_default_split_mode_uses_empty_charger_type_default(self) -> None:
        runtime = SimpleNamespace(
            backend_mode="split",
            meter_config_path=Path("meter.ini"),
            switch_config_path=Path("switch.ini"),
            charger_config_path=Path("charger.ini"),
        )
        charger = SimpleNamespace(read_charger_state=lambda: {"connected": True})

        with tempfile.TemporaryDirectory() as temp_dir:
            payload = wizard_runtime_live.live_connectivity_payload_with_hooks(
                Path(temp_dir) / "config.ini",
                ("charger",),
                secret_defaults={"Password": "secret"},
                build_backends_fn=Mock(return_value=SimpleNamespace(runtime=runtime, meter=None, switch=None, charger=charger)),
                probe_meter_fn=Mock(return_value={"unexpected": True}),
                probe_switch_fn=Mock(return_value={"unexpected": True}),
                read_charger_fn=Mock(return_value={"unexpected": True}),
            )

        self.assertEqual(
            payload,
            {
                "ok": True,
                "checked_roles": ("charger",),
                "roles": {
                    "meter": {"status": "skipped", "reason": "not requested"},
                    "switch": {"status": "skipped", "reason": "not requested"},
                    "charger": {
                        "status": "ok",
                        "payload": {
                            "path": str(Path(temp_dir) / "charger.ini"),
                            "type": "",
                            "charger_state": {"connected": True},
                        },
                    },
                },
            },
        )

    def test_split_probe_errors_are_exact_and_mark_payload_not_ok(self) -> None:
        runtime = SimpleNamespace(
            backend_mode="split",
            meter_type="template_meter",
            meter_config_path=Path("meter.ini"),
            switch_type="template_switch",
            switch_config_path=Path("switch.ini"),
            charger_type="goe_charger",
            charger_config_path=Path("charger.ini"),
        )

        payload = wizard_runtime_live.live_connectivity_payload_with_hooks(
            Path("/tmp/config.ini"),
            ("charger",),
            build_backends_fn=Mock(return_value=SimpleNamespace(runtime=runtime, meter=None, switch=None, charger=None)),
            probe_meter_fn=Mock(return_value={"unexpected": True}),
            probe_switch_fn=Mock(return_value={"unexpected": True}),
            read_charger_fn=Mock(side_effect=RuntimeError("charger probe boom")),
        )

        self.assertEqual(
            payload,
            {
                "ok": False,
                "checked_roles": ("charger",),
                "roles": {
                    "meter": {"status": "skipped", "reason": "not requested"},
                    "switch": {"status": "skipped", "reason": "not requested"},
                    "charger": {"status": "error", "error": "RuntimeError: charger probe boom"},
                },
            },
        )

    def test_live_check_rendered_setup_redacts_materializes_and_restores_secret_defaults(self) -> None:
        with (
            patch(
                "venus_evcharger.bootstrap.wizard_runtime_live.redact_sensitive_rendered_setup",
                return_value=("redacted config", {"adapter.ini": "redacted adapter"}),
            ) as redact,
            patch("venus_evcharger.bootstrap.wizard_runtime_live.materialize_rendered_setup", return_value=Path("/tmp/live.ini")) as materialize,
            patch(
                "venus_evcharger.bootstrap.wizard_runtime_live.sensitive_defaults_from_config_text",
                return_value={"Password": "secret"},
            ) as secrets,
            patch("venus_evcharger.bootstrap.wizard_runtime_live.live_connectivity_payload", return_value={"ok": True}) as live,
        ):
            payload = wizard_runtime_live.live_check_rendered_setup(
                "[DEFAULT]\nPassword=secret\n",
                {"adapter.ini": "Password=adapter-secret\n"},
                "config.ini",
                ("meter",),
            )

        self.assertEqual(payload, {"ok": True})
        redact.assert_called_once_with("[DEFAULT]\nPassword=secret\n", {"adapter.ini": "Password=adapter-secret\n"})
        materialize.assert_called_once()
        self.assertEqual(materialize.call_args.args[0], "redacted config")
        self.assertEqual(materialize.call_args.args[2], {"adapter.ini": "redacted adapter"})
        self.assertEqual(materialize.call_args.args[3], "config.ini")
        secrets.assert_called_once_with("[DEFAULT]\nPassword=secret\n")
        live.assert_called_once_with(Path("/tmp/live.ini"), ("meter",), {"Password": "secret"})


if __name__ == "__main__":
    unittest.main()
