# SPDX-License-Identifier: GPL-3.0-or-later
from tests.wizard_branch_runtime_cases_common import (
    Path,
    SimpleNamespace,
    _namespace,
    _result,
    io,
    patch,
    redirect_stdout,
    runpy,
    tempfile,
    wizard,
    wizard_main,
    wizard_render,
    wizard_runtime,
)


class _WizardBranchRuntimeCoreCases:
    def test_wizard_core_helpers_cover_live_checks_write_paths_and_main_guard(self) -> None:
        from venus_evcharger.bootstrap.wizard_render import redact_sensitive_assignments

        with self.assertRaisesRegex(ValueError, "missing required key"):
            wizard_render.replace_assignment("Host=foo\n", "DeviceInstance", "60")
        self.assertEqual(wizard_render.append_backends("[Backends]\nX=1\n\n[Other]\nA=1\n", []), "[Other]\nA=1\n")
        self.assertEqual(
            redact_sensitive_assignments(
                "[DEFAULT]\nUsername=user\nPassword=secret\nControlApiAuthToken=token\nHost=demo\n"
            ),
            "[DEFAULT]\nUsername=user\nHost=demo\n",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            main_path = self._write_main_config(temp_path)
            self._assert_wizard_live_connectivity_paths(temp_path, main_path)
            self._assert_wizard_write_confirmation_paths(temp_path)
            self._assert_wizard_topology_render_paths()

        self._assert_wizard_main_guard_paths()

    @staticmethod
    def _write_main_config(temp_path: Path) -> Path:
        main_path = temp_path / "config.ini"
        main_path.write_text("[DEFAULT]\nHost=demo\n", encoding="utf-8")
        return main_path

    def _assert_wizard_live_connectivity_paths(self, temp_path: Path, main_path: Path) -> None:
        with self.assertRaisesRegex(TypeError, "did not render to a JSON object"):
            wizard_runtime._json_ready_dict(["not", "an", "object"], "test payload")

        runtime = SimpleNamespace(
            meter_config_path=Path("meter.ini"),
            switch_config_path=None,
            charger_config_path=Path("charger.ini"),
            backend_mode="split",
        )
        (temp_path / "meter.ini").write_text("meter", encoding="utf-8")
        (temp_path / "charger.ini").write_text("charger", encoding="utf-8")
        self._assert_split_live_connectivity(temp_path, main_path, runtime)
        self._assert_combined_live_connectivity(main_path)
        self._assert_rendered_live_checks(main_path, wizard_runtime)
        self._assert_live_check_secret_overlay_paths(main_path, wizard_runtime)

    def _assert_split_live_connectivity(
        self,
        temp_path: Path,
        main_path: Path,
        runtime: SimpleNamespace,
    ) -> None:
        with (
            patch(
                "venus_evcharger.bootstrap.wizard_runtime.build_service_backends",
                return_value=SimpleNamespace(runtime=runtime, meter=None, switch=None, charger=None),
            ),
            patch("venus_evcharger.bootstrap.wizard_runtime.probe_meter_backend", return_value={"type": "meter"}) as meter_probe,
            patch("venus_evcharger.bootstrap.wizard_runtime.read_charger_backend", side_effect=RuntimeError("boom")),
        ):
            payload = wizard_runtime._live_connectivity_payload(main_path, ("meter", "charger"))

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["roles"]["switch"]["status"], "skipped")
        self.assertEqual(payload["roles"]["charger"]["status"], "error")
        meter_probe.assert_called_once_with(str(temp_path / "meter.ini"))

        with (
            patch(
                "venus_evcharger.bootstrap.wizard_runtime.build_service_backends",
                return_value=SimpleNamespace(runtime=runtime, meter=None, switch=None, charger=None),
            ),
            patch("venus_evcharger.bootstrap.wizard_runtime.probe_meter_backend", return_value={"type": "meter"}),
            patch("venus_evcharger.bootstrap.wizard_runtime.probe_switch_backend", return_value={"type": "switch"}),
            patch("venus_evcharger.bootstrap.wizard_runtime.read_charger_backend", return_value={"type": "charger"}),
        ):
            skipped_payload = wizard_runtime._live_connectivity_payload(main_path, None)
        self.assertEqual(skipped_payload["roles"]["switch"]["status"], "skipped")
        self.assertEqual(skipped_payload["roles"]["switch"]["reason"], "not configured")

    def _assert_combined_live_connectivity(self, main_path: Path) -> None:
        combined_runtime = SimpleNamespace(
            backend_mode="combined",
            meter_config_path=None,
            switch_config_path=None,
            charger_config_path=None,
        )
        combined_backends = self._combined_backends(combined_runtime)
        with patch("venus_evcharger.bootstrap.wizard_runtime.build_service_backends", return_value=combined_backends):
            combined_payload = wizard_runtime._live_connectivity_payload(main_path, None)

        self.assertTrue(combined_payload["ok"])
        self.assertEqual(combined_payload["roles"]["meter"]["status"], "ok")
        self.assertEqual(combined_payload["roles"]["meter"]["payload"]["type"], "shelly_meter")
        self.assertEqual(combined_payload["roles"]["switch"]["status"], "ok")
        self.assertEqual(combined_payload["roles"]["switch"]["payload"]["switch_state"]["enabled"], True)
        self.assertEqual(combined_payload["roles"]["charger"]["reason"], "not configured")

        combined_backends_missing_meter = SimpleNamespace(
            runtime=combined_runtime,
            meter=None,
            switch=combined_backends.switch,
            charger=None,
        )
        with patch("venus_evcharger.bootstrap.wizard_runtime.build_service_backends", return_value=combined_backends_missing_meter):
            missing_combined_payload = wizard_runtime._live_connectivity_payload(main_path, ("meter",))
        self.assertEqual(missing_combined_payload["roles"]["meter"]["reason"], "not configured")

        combined_backends_charger = SimpleNamespace(
            runtime=combined_runtime,
            meter=combined_backends.meter,
            switch=combined_backends.switch,
            charger=SimpleNamespace(read_charger_state=lambda: {"online": True}),
        )
        combined_role = wizard_runtime._combined_role_payload(
            "charger",
            combined_backends_charger.charger,
            main_path,
            "goe_charger",
        )
        self.assertEqual(combined_role["charger_state"]["online"], True)
        self._assert_combined_switch_error(main_path, combined_runtime, combined_backends.meter)

    @staticmethod
    def _combined_backends(combined_runtime: SimpleNamespace) -> SimpleNamespace:
        return SimpleNamespace(
            runtime=combined_runtime,
            meter=SimpleNamespace(read_meter=lambda: {"power_w": 1234.0}),
            switch=SimpleNamespace(
                capabilities=lambda: {"switching_mode": "direct"},
                read_switch_state=lambda: {"enabled": True},
            ),
            charger=None,
        )

    def _assert_combined_switch_error(
        self,
        main_path: Path,
        combined_runtime: SimpleNamespace,
        meter: SimpleNamespace,
    ) -> None:
        error_switch_backends = SimpleNamespace(
            runtime=combined_runtime,
            meter=meter,
            switch=SimpleNamespace(
                capabilities=lambda: (_ for _ in ()).throw(RuntimeError("capabilities boom")),
                read_switch_state=lambda: {"enabled": True},
            ),
            charger=None,
        )
        with patch("venus_evcharger.bootstrap.wizard_runtime.build_service_backends", return_value=error_switch_backends):
            error_payload = wizard_runtime._live_connectivity_payload(main_path, ("switch",))
        self.assertFalse(error_payload["ok"])
        self.assertEqual(error_payload["roles"]["switch"]["status"], "error")

    def _assert_rendered_live_checks(self, main_path: Path, wizard_runtime: object) -> None:
        with patch(
            "venus_evcharger.bootstrap.wizard_runtime._live_connectivity_payload",
            side_effect=lambda path, roles, *args: self._rendered_live_result(path, roles, *args),
        ):
            live_payload = wizard_runtime._live_check_rendered_setup(
                "[DEFAULT]\nPassword=main-secret\nControlApiAuthToken=token-secret\nAdapter=adapter.ini\n",
                {"adapter.ini": "[Adapter]\nPassword=adapter-secret\n"},
                "config.ini",
                ("meter",),
            )
        self.assertTrue(live_payload["ok"])

        with patch(
            "venus_evcharger.bootstrap.wizard_runtime._live_connectivity_payload",
            side_effect=lambda path, roles, *args: self._rendered_live_result(path, roles, *args),
        ):
            runtime_live_payload = wizard_runtime._live_check_rendered_setup(
                "[DEFAULT]\nPassword=main-secret\nControlApiAuthToken=token-secret\nAdapter=adapter.ini\n",
                {"adapter.ini": "[Adapter]\nPassword=adapter-secret\n"},
                "config.ini",
                ("meter",),
            )
        self.assertTrue(runtime_live_payload["ok"])

        with patch(
            "venus_evcharger.bootstrap.wizard_runtime._live_connectivity_payload_with_hooks",
            return_value={"ok": True, "checked_roles": ("meter",), "roles": {}},
        ) as runtime_hooks:
            direct_runtime_payload = wizard_runtime._live_connectivity_payload(main_path, ("meter",))
        self.assertTrue(direct_runtime_payload["ok"])
        runtime_hooks.assert_called_once()

    def _assert_live_check_secret_overlay_paths(self, main_path: Path, wizard_runtime: object) -> None:
        from venus_evcharger.bootstrap import wizard_render

        defaults = wizard_runtime.configparser.ConfigParser()["DEFAULT"]
        self.assertEqual(wizard_runtime._secret_default(defaults, {"Password": "secret"}, "Password"), "secret")
        self.assertEqual(wizard_render._secret_default(defaults, {"Password": "secret"}, "Password"), "secret")

        runtime = SimpleNamespace(
            backend_mode="split",
            meter_type="template_meter",
            meter_config_path=Path("meter.ini"),
            switch_type=None,
            switch_config_path=None,
            charger_type="",
            charger_config_path=None,
        )
        meter_backend = SimpleNamespace(read_meter=lambda: {"power_w": 12.0})

        def build_backends(service: object) -> object:
            self.assertEqual(service.password, "secret")
            return SimpleNamespace(runtime=runtime, meter=meter_backend, switch=None, charger=None)

        payload = wizard_runtime._live_connectivity_payload_with_hooks(
            main_path,
            ("meter",),
            secret_defaults={"Password": "secret"},
            build_backends_fn=build_backends,
            probe_meter_fn=lambda path: {"unexpected": path},
            probe_switch_fn=lambda path: {"unexpected": path},
            read_charger_fn=lambda path: {"unexpected": path},
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["roles"]["meter"]["payload"]["meter"]["power_w"], 12.0)

        missing_backend_payload = wizard_runtime._live_connectivity_payload_with_hooks(
            main_path,
            ("meter",),
            secret_defaults={"Password": "secret"},
            build_backends_fn=lambda service: SimpleNamespace(runtime=runtime, meter=None, switch=None, charger=None),
            probe_meter_fn=lambda path: {"unexpected": path},
            probe_switch_fn=lambda path: {"unexpected": path},
            read_charger_fn=lambda path: {"unexpected": path},
        )
        self.assertEqual(missing_backend_payload["roles"]["meter"]["reason"], "not configured")

    @staticmethod
    def _rendered_live_result(path: Path, roles: object, *args: object) -> dict[str, object]:
        adapter_path = path.parent / "adapter.ini"
        materialized_content = path.read_text(encoding="utf-8") + adapter_path.read_text(encoding="utf-8")
        ok = all(
            (
                _WizardBranchRuntimeCoreCases._live_check_files_exist(path, adapter_path),
                _WizardBranchRuntimeCoreCases._live_check_files_are_private(path, adapter_path),
                _WizardBranchRuntimeCoreCases._live_check_materialized_content_is_sanitized(materialized_content),
                _WizardBranchRuntimeCoreCases._live_check_secret_defaults_match(args),
            )
        )
        return {
            "ok": ok,
            "checked_roles": roles or (),
            "roles": {},
        }

    @staticmethod
    def _live_check_files_exist(path: Path, adapter_path: Path) -> bool:
        return path.exists() and adapter_path.exists()

    @staticmethod
    def _live_check_files_are_private(path: Path, adapter_path: Path) -> bool:
        return (path.stat().st_mode & 0o777) == 0o600 and (adapter_path.stat().st_mode & 0o777) == 0o600

    @staticmethod
    def _live_check_materialized_content_is_sanitized(materialized_content: str) -> bool:
        return "secret" not in materialized_content and "Password=" not in materialized_content

    @staticmethod
    def _live_check_secret_defaults_match(args: tuple[object, ...]) -> bool:
        if not args or not isinstance(args[0], dict):
            return False
        return args[0].get("Password") == "main-secret"

    def _assert_wizard_write_confirmation_paths(self, temp_path: Path) -> None:
        from venus_evcharger.bootstrap import wizard_runtime

        preview = self._assert_generated_file_backups(temp_path)
        self._assert_wizard_write_guard_paths(preview)
        self._assert_runtime_write_guard_paths(preview, wizard_runtime)

    def _assert_generated_file_backups(self, temp_path: Path) -> object:
        config_path = temp_path / "written.ini"
        adapter_path = temp_path / "adapter.ini"
        config_path.write_text("old\n", encoding="utf-8")
        adapter_path.write_text("old\n", encoding="utf-8")
        backups = wizard_render.write_generated_files(config_path, "new\n", {"adapter.ini": "new\n"})
        self.assertEqual(len(backups), 2)
        self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(adapter_path.stat().st_mode & 0o777, 0o600)
        return _result()

    def _assert_wizard_write_guard_paths(self, preview: object) -> None:
        self.assertFalse(wizard_runtime._non_interactive_write_allowed(_namespace(), tuple()))
        with self.assertRaisesRegex(ValueError, "Refusing to overwrite existing files"):
            wizard_runtime._non_interactive_write_allowed(_namespace(non_interactive=True), ("existing.ini",))
        self.assertTrue(wizard_runtime._non_interactive_write_allowed(_namespace(non_interactive=True, force=True), ("existing.ini",)))

        with patch("venus_evcharger.bootstrap.wizard_runtime._interactive_write_confirmed", return_value=False):
            with self.assertRaisesRegex(ValueError, "cancelled"):
                wizard_runtime._confirm_write(_namespace(), preview, ("existing.ini",))
        with patch("venus_evcharger.bootstrap.wizard_runtime._interactive_write_confirmed", return_value=True):
            wizard_runtime._confirm_write(_namespace(), preview, ("existing.ini",))
        with (
            patch("venus_evcharger.bootstrap.wizard_runtime.prompt_yes_no", return_value=True),
            redirect_stdout(io.StringIO()),
        ):
            self.assertTrue(wizard_runtime._interactive_write_confirmed(preview, ("existing.ini",)))

        with patch("venus_evcharger.bootstrap.wizard_main.prompt_yes_no", return_value=True):
            self.assertTrue(wizard_main.resolve_live_check(_namespace()))
        self.assertFalse(wizard_main.resolve_live_check(_namespace(non_interactive=True)))
        self.assertTrue(wizard_runtime._skip_write_confirmation(_namespace(dry_run=True), ("existing.ini",)))
        self.assertTrue(wizard_runtime._skip_write_confirmation(_namespace(non_interactive=True, force=True), ("existing.ini",)))
        self.assertTrue(wizard_runtime._skip_write_confirmation(_namespace(yes=True), tuple()))
        self.assertFalse(wizard_runtime._skip_write_confirmation(_namespace(), tuple()))
        with (
            patch("venus_evcharger.bootstrap.wizard_runtime.prompt_yes_no", return_value=True),
            redirect_stdout(io.StringIO()),
        ):
            self.assertTrue(wizard_runtime._interactive_write_confirmed(preview, tuple()))
        wizard_runtime._confirm_write(_namespace(dry_run=True), preview, ("existing.ini",))

    def _assert_runtime_write_guard_paths(self, preview: object, wizard_runtime: object) -> None:
        self.assertFalse(wizard_runtime._non_interactive_write_allowed(_namespace(), tuple()))
        with self.assertRaisesRegex(ValueError, "Refusing to overwrite existing files"):
            wizard_runtime._non_interactive_write_allowed(_namespace(non_interactive=True), ("existing.ini",))
        self.assertTrue(wizard_runtime._non_interactive_write_allowed(_namespace(non_interactive=True, force=True), ("existing.ini",)))
        self.assertTrue(wizard_runtime._skip_write_confirmation(_namespace(dry_run=True), ("existing.ini",)))
        self.assertTrue(wizard_runtime._skip_write_confirmation(_namespace(non_interactive=True, force=True), ("existing.ini",)))
        self.assertTrue(wizard_runtime._skip_write_confirmation(_namespace(yes=True), tuple()))
        self.assertFalse(wizard_runtime._skip_write_confirmation(_namespace(), tuple()))
        with (
            patch("venus_evcharger.bootstrap.wizard_runtime.prompt_yes_no", return_value=True),
            redirect_stdout(io.StringIO()),
        ):
            self.assertTrue(wizard_runtime._interactive_write_confirmed(preview, ("existing.ini",)))
        with (
            patch("venus_evcharger.bootstrap.wizard_runtime.prompt_yes_no", return_value=True),
            redirect_stdout(io.StringIO()),
        ):
            self.assertTrue(wizard_runtime._interactive_write_confirmed(preview, tuple()))
        wizard_runtime._confirm_write(_namespace(dry_run=True), preview, ("existing.ini",))
        with patch("venus_evcharger.bootstrap.wizard_runtime._interactive_write_confirmed", return_value=True):
            wizard_runtime._confirm_write(_namespace(), preview, ("existing.ini",))
        with patch("venus_evcharger.bootstrap.wizard_runtime._interactive_write_confirmed", return_value=False):
            with self.assertRaisesRegex(ValueError, "cancelled"):
                wizard_runtime._confirm_write(_namespace(), preview, ("existing.ini",))

    def _assert_wizard_topology_render_paths(self) -> None:
        topology_answers = wizard.WizardAnswers(
            profile="multi_adapter_topology",
            host_input="adapter.local",
            meter_host_input=None,
            switch_host_input="switch.local",
            charger_host_input="charger.local",
            device_instance=60,
            phase="3P",
            policy_mode="manual",
            digest_auth=False,
            username="",
            password="",
            topology_preset="goe-external-switch-group",
            charger_backend="goe_charger",
            transport_kind="serial_rtu",
            transport_host="adapter.local",
            transport_port=502,
            transport_device="/dev/ttyUSB0",
            transport_unit_id=1,
        )
        topology_config = wizard_render.build_wizard_topology_config(topology_answers)
        rendered_files = wizard_render.render_adapter_files_from_topology(
            topology_config,
            topology_answers,
            {"charger": "charger.local", "switch": "switch.local"},
        )
        self.assertIn("Type=goe_charger", rendered_files["wizard-charger.ini"])
        self.assertIn("BaseUrl=http://charger.local", rendered_files["wizard-charger.ini"])
        self.assertIn("Type=switch_group", rendered_files["wizard-switch-group.ini"])
        self.assertEqual(
            wizard_render.render_legacy_backends_from_topology(topology_config, rendered_files),
            [
                "Mode=split",
                "MeterType=none",
                "SwitchType=switch_group",
                "SwitchConfigPath=wizard-switch-group.ini",
                "ChargerType=goe_charger",
                "ChargerConfigPath=wizard-charger.ini",
            ],
        )
        from venus_evcharger.topology.schema import EvChargerTopologyConfig, TopologyConfig

        self.assertEqual(
            wizard_render.render_adapter_files_from_topology(
                EvChargerTopologyConfig(topology=TopologyConfig(type="native_device")),
                topology_answers,
                {},
            ),
            {},
        )
        self.assertEqual(
            wizard_render.render_adapter_files_from_topology(
                EvChargerTopologyConfig(topology=TopologyConfig(type="hybrid_topology")),
                topology_answers,
                {},
            ),
            {},
        )

    def _assert_wizard_main_guard_paths(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as raised:
                with patch("sys.argv", ["wizard.py", "--non-interactive", "--dry-run", "--json", "--profile", "simple_relay", "--host", "192.168.1.44"]):
                    runpy.run_module("venus_evcharger.bootstrap.wizard", run_name="__main__")
        self.assertEqual(raised.exception.code, 0)
        self.assertIn('"profile": "simple_relay"', stdout.getvalue())

        error_stdout = io.StringIO()
        with redirect_stdout(error_stdout):
            rc = wizard.main(["--non-interactive", "--json"])
        self.assertEqual(rc, 2)
        self.assertIn('"error"', error_stdout.getvalue())
