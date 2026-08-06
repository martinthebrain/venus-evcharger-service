# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import configparser
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from venus_evcharger.bootstrap.wizard_models import WizardAnswers
from venus_evcharger.bootstrap import wizard_render
from venus_evcharger.bootstrap import wizard_render_io
from venus_evcharger.bootstrap import wizard_render_live
from venus_evcharger.bootstrap import wizard_render_secrets


_TEMPLATE = """[DEFAULT]
Host=old
DeviceInstance=0
DigestAuth=0
Username=old
Password=old
Phase=L1
Mode=0
AutoStartSurplusWatts=0
AutoStopSurplusWatts=0
AutoMinSoc=0
AutoResumeSoc=0
AutoScheduledEnabledDays=Mon
AutoScheduledLatestEndTime=01:00
AutoScheduledNightCurrentAmps=6

[Backends]
Old=1
"""


class _FakeWritableHandle:
    def __init__(self) -> None:
        self.writes: list[str] = []

    def __enter__(self) -> "_FakeWritableHandle":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def write(self, content: str) -> int:
        self.writes.append(content)
        return len(content)


def _answers(**overrides: object) -> WizardAnswers:
    values: dict[str, object] = {
        "profile": "simple_relay",
        "host_input": "http://charger.local/",
        "meter_host_input": None,
        "switch_host_input": None,
        "charger_host_input": None,
        "device_instance": 71,
        "phase": "L2",
        "policy_mode": "scheduled",
        "digest_auth": True,
        "username": " user ",
        "password": " secret ",
        "auto_start_surplus_watts": 1234.0,
        "auto_stop_surplus_watts": 234.5,
        "auto_min_soc": 20.0,
        "auto_resume_soc": 55.0,
        "scheduled_enabled_days": "Mon,Tue",
        "scheduled_latest_end_time": "04:30",
        "scheduled_night_current_amps": 8.5,
    }
    values.update(overrides)
    return WizardAnswers(**cast(Any, values))


class WizardRenderContractTests(unittest.TestCase):
    def test_render_wizard_config_replaces_core_defaults_and_removes_empty_legacy_backends(self) -> None:
        config_text, adapter_files, role_hosts = wizard_render.render_wizard_config(_TEMPLATE, _answers())

        self.assertEqual(adapter_files, {})
        self.assertEqual(role_hosts, {"meter": "http://charger.local/", "switch": "http://charger.local/"})
        self.assertIn("Host=charger.local\n", config_text)
        self.assertIn("DeviceInstance=71\n", config_text)
        self.assertIn("DigestAuth=1\n", config_text)
        self.assertIn("Username=user\n", config_text)
        self.assertIn("Password=secret\n", config_text)
        self.assertIn("Phase=L2\n", config_text)
        self.assertIn("Mode=2\n", config_text)
        self.assertIn("AutoStartSurplusWatts=1234\n", config_text)
        self.assertIn("AutoStopSurplusWatts=234.5\n", config_text)
        self.assertIn("AutoMinSoc=20\n", config_text)
        self.assertIn("AutoResumeSoc=55\n", config_text)
        self.assertIn("AutoScheduledEnabledDays=Mon,Tue\n", config_text)
        self.assertIn("AutoScheduledLatestEndTime=04:30\n", config_text)
        self.assertIn("AutoScheduledNightCurrentAmps=8.5\n", config_text)
        self.assertNotIn("[Backends]", config_text)

    def test_render_wizard_config_writes_digest_auth_disabled_value(self) -> None:
        config_text, _adapter_files, _role_hosts = wizard_render.render_wizard_config(_TEMPLATE, _answers(digest_auth=False))

        self.assertIn("DigestAuth=0\n", config_text)
        self.assertNotIn("DigestAuth=XX0XX\n", config_text)

    def test_render_wizard_config_appends_split_backends_and_adapter_files(self) -> None:
        config_text, adapter_files, role_hosts = wizard_render.render_wizard_config(
            _TEMPLATE,
            _answers(
                profile="multi_adapter_topology",
                topology_preset="template-stack",
                meter_host_input="http://meter.local",
                switch_host_input="http://switch.local",
                charger_host_input="http://charger.local",
                charger_backend="template_charger",
            ),
        )

        self.assertEqual(role_hosts, {"meter": "http://meter.local", "switch": "http://switch.local", "charger": "http://charger.local"})
        self.assertEqual(set(adapter_files), {"wizard-meter.ini", "wizard-switch.ini", "wizard-charger.ini"})
        self.assertIn("[Backends]\nMode=split\n", config_text)
        self.assertIn("MeterType=template_meter\n", config_text)
        self.assertIn("SwitchType=template_switch\n", config_text)
        self.assertIn("ChargerType=template_charger\n", config_text)

    def test_materialization_and_redaction_contracts(self) -> None:
        self.assertEqual(
            wizard_render.materialized_config_text("Path=adapter/b.ini\nPath=adapter/a.ini\n", Path("/tmp/out"), {"adapter/b.ini": "", "adapter/a.ini": ""}),
            "Path=/tmp/out/adapter/b.ini\nPath=/tmp/out/adapter/a.ini\n",
        )
        self.assertEqual(
            wizard_render.redact_sensitive_assignments(
                "Password=secret\n"
                "UserPasswordHint=keep\n"
                "ControlApiAuthToken=legacy\n"
                "ControlApiReadToken=read\n"
                "ControlApiControlToken=control\n"
                "ControlApiAdminToken=admin\n"
                "ControlApiUpdateToken=update\n"
            ),
            "UserPasswordHint=keep\n",
        )
        self.assertEqual(
            wizard_render.sensitive_defaults_from_config_text("Username=user\nPassword=secret\nDigestAuth=1\nControlApiAuthToken=token\n"),
            {"Username": "user", "Password": "secret", "DigestAuth": "1", "ControlApiAuthToken": "token"},
        )
        self.assertEqual(
            wizard_render.redact_sensitive_rendered_setup("Password=secret\n", {"a.ini": "ControlApiAuthToken=token\n"}),
            ("\n", {"a.ini": "\n"}),
        )

    def test_secret_render_helpers_cover_defaults_and_fallbacks_exactly(self) -> None:
        self.assertEqual(
            wizard_render_secrets.redact_sensitive_assignments(
                " Password = spaced-key-is-not-secret\nPassword=a=b\nControlApiAuthToken=token\nHost=keep\nUrl=a=b\nFlag"
            ),
            "Host=keep\nUrl=a=b\nFlag",
        )
        self.assertEqual(wizard_render_secrets.redact_sensitive_assignments("Host=keep\nUrl=a=b\n"), "Host=keep\nUrl=a=b\n")
        self.assertEqual(
            wizard_render_secrets.sensitive_defaults_from_config_text("[DEFAULT]\nusername=ignored\nUsername= user \n"),
            {"Username": "user", "Password": "", "DigestAuth": "0", "ControlApiAuthToken": ""},
        )
        self.assertEqual(
            wizard_render_secrets.sensitive_defaults_from_config_text(
                "[DEFAULT]\n"
                "Username= user \n"
                "Password= pass \n"
                "DigestAuth= yes \n"
                "ControlApiAuthToken= token \n"
            ),
            {"Username": "user", "Password": "pass", "DigestAuth": "yes", "ControlApiAuthToken": "token"},
        )

        parser = configparser.ConfigParser()
        parser.read_string("[DEFAULT]\nConfigured=configured\nEmpty=\n")
        defaults = parser["DEFAULT"]
        self.assertEqual(wizard_render_secrets.secret_default(defaults, {"Configured": "secret"}, "Configured"), "secret")
        self.assertEqual(wizard_render_secrets.secret_default(defaults, {"Configured": ""}, "Configured"), "configured")
        self.assertEqual(wizard_render_secrets.secret_default(defaults, None, "Missing", "fallback"), "fallback")
        self.assertEqual(wizard_render_secrets.secret_default(defaults, None, "Empty", "fallback"), "")

    def test_probe_service_digest_aliases_and_empty_numeric_defaults(self) -> None:
        for token in ("1", "true", "yes", "on", "TRUE", "ON"):
            parser = configparser.ConfigParser()
            parser.read_string(f"[DEFAULT]\nDigestAuth={token}\n")
            self.assertTrue(wizard_render_secrets.probe_service_from_wallbox_config(parser).use_digest_auth)

        for token in ("0", "false", "no", "off", "maybe", ""):
            parser = configparser.ConfigParser()
            parser.read_string(f"[DEFAULT]\nDigestAuth={token}\n")
            self.assertFalse(wizard_render_secrets.probe_service_from_wallbox_config(parser).use_digest_auth)

        parser = configparser.ConfigParser()
        parser.read_string("[DEFAULT]\nShellyRequestTimeoutSeconds=\nShellyId=\nMaxCurrent=\n")
        service = wizard_render_secrets.probe_service_from_wallbox_config(parser)
        self.assertEqual(service.shelly_request_timeout_seconds, 2.0)
        self.assertEqual(service.pm_id, 0)
        self.assertEqual(service.max_current, 16.0)

        parser = configparser.ConfigParser()
        parser.read_string("[DEFAULT]\nDigestAuth=0\n")
        self.assertTrue(wizard_render_secrets.probe_service_from_wallbox_config(parser, {"DigestAuth": "on"}).use_digest_auth)

    def test_private_writes_materialization_validation_and_backups(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "nested" / "deep" / "config.ini"
            wizard_render.write_private_text(target, "old\n")
            self.assertEqual(target.read_text(encoding="utf-8"), "old\n")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            wizard_render.write_private_text(target, "updated\n")
            self.assertEqual(target.read_text(encoding="utf-8"), "updated\n")

            main_path = wizard_render.materialize_rendered_setup(
                "Adapter=adapter.ini\n",
                root,
                {"adapter.ini": "adapter\n"},
                "main.ini",
            )
            self.assertEqual(main_path.read_text(encoding="utf-8"), f"Adapter={root / 'adapter.ini'}\n")
            self.assertEqual((root / "adapter.ini").read_text(encoding="utf-8"), "adapter\n")

            def validate_rendered(path: str) -> dict[str, object]:
                rendered_path = Path(path)
                self.assertNotEqual(path, "None")
                self.assertEqual(rendered_path.name, "config.ini")
                self.assertTrue(rendered_path.exists())
                rendered_text = rendered_path.read_text(encoding="utf-8")
                self.assertNotIn("Password=secret", rendered_text)
                self.assertIn("Adapter=", rendered_text)
                return {"ok": True}

            with patch.object(wizard_render_io, "validate_wallbox_config", side_effect=validate_rendered) as validate:
                self.assertEqual(
                    wizard_render.validate_rendered_setup(
                        "Password=secret\nAdapter=adapter.ini\n",
                        {"adapter.ini": "Password=adapter-secret\n"},
                        "config.ini",
                    ),
                    {"ok": True},
                )
            validate.assert_called_once()

            new_target = root / "new.ini"
            self.assertIsNone(wizard_render.write_with_backup(new_target, "fresh\n"))
            self.assertEqual(new_target.read_text(encoding="utf-8"), "fresh\n")

            with patch.object(wizard_render_io, "timestamp", return_value="stamp"):
                backup = wizard_render.write_with_backup(target, "new\n")
            self.assertEqual(Path(str(backup)).read_text(encoding="utf-8"), "updated\n")
            self.assertEqual(target.read_text(encoding="utf-8"), "new\n")

            generated_config = root / "generated.ini"
            generated_config.write_text("old\n", encoding="utf-8")
            (root / "adapter.ini").write_text("old-adapter\n", encoding="utf-8")
            with patch.object(wizard_render_io, "timestamp", side_effect=("one", "two")):
                backups = wizard_render.write_generated_files(generated_config, "main\n", {"adapter.ini": "adapter\n"})
            self.assertEqual(len(backups), 2)
            self.assertTrue(all(isinstance(item, str) for item in backups))
            self.assertTrue(all(Path(item).exists() for item in backups))
            self.assertEqual(generated_config.read_text(encoding="utf-8"), "main\n")
            self.assertEqual((root / "adapter.ini").read_text(encoding="utf-8"), "adapter\n")

    def test_private_write_uses_exact_low_level_open_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "missing" / "nested" / "secret.ini"
            fake_handle = _FakeWritableHandle()
            with (
                patch.object(wizard_render_io.os, "open", return_value=99) as open_call,
                patch.object(wizard_render_io.os, "fdopen", return_value=fake_handle) as fdopen_call,
                patch.object(Path, "chmod") as chmod_call,
            ):
                wizard_render.write_private_text(target, "ä=secret\n")

        open_call.assert_called_once_with(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        fdopen_call.assert_called_once_with(99, "w", encoding="utf-8")
        chmod_call.assert_called_once_with(0o600)
        self.assertEqual(fake_handle.writes, ["ä=secret\n"])

    def test_probe_service_and_live_connectivity_contracts(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            "[DEFAULT]\n"
            "Host=host.local\nUsername=config-user\nPassword=config-secret\nDigestAuth=yes\n"
            "ShellyRequestTimeoutSeconds=3.5\nShellyComponent=Relay\nShellyId=2\nPhase=L3\nMaxCurrent=12.5\n"
        )

        service = wizard_render._probe_service_from_wallbox_config(parser, {"Username": "secret-user", "Password": "secret-pass"})
        self.assertEqual(
            service.__dict__,
            {
                "config": parser,
                "session": None,
                "host": "host.local",
                "username": "secret-user",
                "password": "secret-pass",
                "use_digest_auth": True,
                "shelly_request_timeout_seconds": 3.5,
                "pm_component": "Relay",
                "pm_id": 2,
                "phase": "L3",
                "max_current": 12.5,
                "_last_voltage": None,
                "_adapter_auth_fallback_enabled": True,
            },
        )

        minimal_parser = configparser.ConfigParser()
        minimal_parser.read_string("[DEFAULT]\n")
        self.assertEqual(
            wizard_render_secrets.probe_service_from_wallbox_config(minimal_parser).__dict__,
            {
                "config": minimal_parser,
                "session": None,
                "host": "",
                "username": "",
                "password": "",
                "use_digest_auth": False,
                "shelly_request_timeout_seconds": 2.0,
                "pm_component": "Switch",
                "pm_id": 0,
                "phase": "L1",
                "max_current": 16.0,
                "_last_voltage": None,
                "_adapter_auth_fallback_enabled": False,
            },
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            main_path = root / "config.ini"
            main_path.write_text("[DEFAULT]\nHost=host.local\n", encoding="utf-8")
            runtime = SimpleNamespace(
                meter_config_path=Path("meter.ini"),
                switch_config_path=None,
                charger_config_path=Path("charger.ini"),
            )
            with (
                patch.object(wizard_render_live, "build_service_backends", return_value=SimpleNamespace(runtime=runtime)),
                patch.object(wizard_render_live, "probe_meter_backend", return_value={"meter": "ok"}) as meter_probe,
                patch.object(wizard_render_live, "read_charger_backend", side_effect=ValueError("offline")) as charger_probe,
            ):
                payload = wizard_render.live_connectivity_payload(main_path, ("meter", "switch", "charger"))

        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["checked_roles"], ("meter", "charger"))
        self.assertEqual(payload["roles"]["meter"], {"status": "ok", "payload": {"meter": "ok"}})
        self.assertEqual(payload["roles"]["switch"], {"status": "skipped", "reason": "not configured"})
        self.assertEqual(
            payload["roles"]["charger"],
            {"status": "error", "error": "ValueError: offline"},
        )
        meter_probe.assert_called_once_with(str(root / "meter.ini"))
        charger_probe.assert_called_once_with(str(root / "charger.ini"))

    def test_live_connectivity_selected_roles_and_secret_defaults_are_strict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            main_path = root / "config.ini"
            main_path.write_text("[DEFAULT]\nHost=file-host\nUsername=file-user\nDigestAuth=0\n", encoding="utf-8")
            runtime = SimpleNamespace(
                meter_config_path=Path("meter.ini"),
                switch_config_path=Path("switch.ini"),
                charger_config_path=Path("charger.ini"),
            )

            def build(service: object) -> object:
                self.assertEqual(service.host, "file-host")
                self.assertEqual(service.username, "secret-user")
                self.assertTrue(service.use_digest_auth)
                return SimpleNamespace(runtime=runtime)

            def meter_probe(path: str) -> dict[str, object]:
                self.assertEqual(path, str(root / "meter.ini"))
                return {"meter": "ok"}

            with (
                patch.object(wizard_render_live, "build_service_backends", side_effect=build) as build_call,
                patch.object(wizard_render_live, "probe_meter_backend", side_effect=meter_probe) as meter_call,
                patch.object(wizard_render_live, "probe_switch_backend") as switch_call,
                patch.object(wizard_render_live, "read_charger_backend") as charger_call,
            ):
                payload = wizard_render_live.live_connectivity_payload(
                    main_path,
                    ("meter",),
                    {"Username": "secret-user", "DigestAuth": "yes"},
                )

        build_call.assert_called_once()
        meter_call.assert_called_once_with(str(root / "meter.ini"))
        switch_call.assert_not_called()
        charger_call.assert_not_called()
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["checked_roles"], ("meter",))
        self.assertEqual(payload["roles"]["meter"], {"status": "ok", "payload": {"meter": "ok"}})
        self.assertEqual(payload["roles"]["switch"], {"status": "skipped", "reason": "not requested"})
        self.assertEqual(payload["roles"]["charger"], {"status": "skipped", "reason": "not requested"})

    def test_live_connectivity_none_selection_checks_configured_roles_and_skips_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            main_path = root / "config.ini"
            main_path.write_text("[DEFAULT]\nHost=file-host\n", encoding="utf-8")
            absolute_charger_path = root / "absolute-charger.ini"
            runtime = SimpleNamespace(
                meter_config_path=Path("meter.ini"),
                switch_config_path=None,
                charger_config_path=absolute_charger_path,
            )

            with (
                patch.object(wizard_render_live, "build_service_backends", return_value=SimpleNamespace(runtime=runtime)),
                patch.object(wizard_render_live, "probe_meter_backend", return_value={"meter": "ok"}) as meter_call,
                patch.object(wizard_render_live, "probe_switch_backend") as switch_call,
                patch.object(wizard_render_live, "read_charger_backend", return_value={"charger": "ok"}) as charger_call,
            ):
                payload = wizard_render_live.live_connectivity_payload(main_path, None)

        meter_call.assert_called_once_with(str(root / "meter.ini"))
        switch_call.assert_not_called()
        charger_call.assert_called_once_with(str(absolute_charger_path))
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["checked_roles"], ("meter", "charger"))
        self.assertEqual(payload["roles"]["switch"], {"status": "skipped", "reason": "not configured"})

    def test_live_connectivity_invokes_configured_switch_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            main_path = root / "config.ini"
            main_path.write_text("[DEFAULT]\nHost=file-host\n", encoding="utf-8")
            runtime = SimpleNamespace(
                meter_config_path=None,
                switch_config_path=Path("switch.ini"),
                charger_config_path=None,
            )

            with (
                patch.object(wizard_render_live, "build_service_backends", return_value=SimpleNamespace(runtime=runtime)),
                patch.object(wizard_render_live, "probe_meter_backend") as meter_call,
                patch.object(wizard_render_live, "probe_switch_backend", return_value={"switch": "ok"}) as switch_call,
                patch.object(wizard_render_live, "read_charger_backend") as charger_call,
            ):
                payload = wizard_render_live.live_connectivity_payload(main_path, None)

        meter_call.assert_not_called()
        switch_call.assert_called_once_with(str(root / "switch.ini"))
        charger_call.assert_not_called()
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["checked_roles"], ("switch",))
        self.assertEqual(payload["roles"]["switch"], {"status": "ok", "payload": {"switch": "ok"}})

    def test_live_check_rendered_setup_passes_redacted_material_and_secret_defaults(self) -> None:
        def live_payload(path: Path, selected_roles: tuple[str, ...] | None, secret_defaults: dict[str, str] | None) -> dict[str, object]:
            self.assertIsInstance(path, Path)
            self.assertTrue(path.exists())
            self.assertEqual(selected_roles, ("meter",))
            self.assertEqual(
                secret_defaults,
                {"Username": "user", "Password": "secret", "DigestAuth": "1", "ControlApiAuthToken": "token"},
            )
            self.assertNotIn("Password=secret", path.read_text(encoding="utf-8"))
            adapter_path = path.parent / "adapter.ini"
            self.assertTrue(adapter_path.exists())
            self.assertEqual(adapter_path.read_text(encoding="utf-8"), "\n")
            return {"ok": True}

        with patch.object(wizard_render_live, "live_connectivity_payload", side_effect=live_payload) as live_call:
            self.assertEqual(
                wizard_render_live.live_check_rendered_setup(
                    "[DEFAULT]\nUsername=user\nPassword=secret\nDigestAuth=1\nControlApiAuthToken=token\nAdapter=adapter.ini\n",
                    {"adapter.ini": "Password=adapter-secret\n"},
                    "main.ini",
                    ("meter",),
                ),
                {"ok": True},
            )
        live_call.assert_called_once()

    def test_live_check_defaults_and_backup_path_contracts(self) -> None:
        answers = _answers(
            password="",
            topology_preset="template-stack",
            charger_backend="goe_charger",
            charger_preset="go-e-v3",
            request_timeout_seconds=4.5,
            cerbo_relay_index=2,
            cerbo_relay_contact_mode="NC",
            switch_group_supported_phase_selections="P1,P1_P2",
            transport_kind="tcp",
            transport_host="modbus.local",
            transport_port=1502,
            transport_device="/dev/ttyUSB9",
            transport_unit_id=7,
        )

        self.assertEqual(
            wizard_render.answer_defaults(answers),
            {
                "profile": "simple_relay",
                "host_input": "http://charger.local/",
                "meter_host_input": None,
                "switch_host_input": None,
                "charger_host_input": None,
                "device_instance": 71,
                "phase": "L2",
                "policy_mode": "scheduled",
                "digest_auth": True,
                "username": " user ",
                "password_present": False,
                "topology_preset": "template-stack",
                "charger_backend": "goe_charger",
                "charger_preset": "go-e-v3",
                "request_timeout_seconds": 4.5,
                "cerbo_relay_index": 2,
                "cerbo_relay_contact_mode": "NC",
                "switch_group_supported_phase_selections": "P1,P1_P2",
                "auto_start_surplus_watts": 1234.0,
                "auto_stop_surplus_watts": 234.5,
                "auto_min_soc": 20.0,
                "auto_resume_soc": 55.0,
                "scheduled_enabled_days": "Mon,Tue",
                "scheduled_latest_end_time": "04:30",
                "scheduled_night_current_amps": 8.5,
                "transport_kind": "tcp",
                "transport_host": "modbus.local",
                "transport_port": 1502,
                "transport_device": "/dev/ttyUSB9",
                "transport_unit_id": 7,
            },
        )
        self.assertTrue(wizard_render.answer_defaults(_answers(password="present"))["password_present"])

        with patch.object(wizard_render_io, "timestamp", return_value="stamp"):
            self.assertEqual(wizard_render.backup_path(Path("/tmp/config.ini")), Path("/tmp/config.ini.wizard-backup-stamp"))

        with patch.object(wizard_render_live, "live_connectivity_payload", return_value={"ok": True}) as live_payload:
            self.assertEqual(
                wizard_render.live_check_rendered_setup("[DEFAULT]\nPassword=secret\n", {"adapter.ini": "Password=secret\n"}, "main.ini", None),
                {"ok": True},
            )
        live_payload.assert_called_once()


if __name__ == "__main__":
    unittest.main()
