# SPDX-License-Identifier: GPL-3.0-or-later
from tests.test_branch_coverage_hotspots_support import *  # noqa: F401,F403

class _BranchCoverageWizardRenderCasesPart1:
    def test_wizard_render_helper_branches(self) -> None:
        self.assertEqual(upsert_default_assignments("[DEFAULT]\nHost=a\n", {}), "[DEFAULT]\nHost=a\n")
        self.assertEqual(
            upsert_default_assignments("[DEFAULT]\n[Other]\n", {"AutoEnergySources": "alpha"}),
            "[DEFAULT]\nAutoEnergySources=alpha\n[Other]\n",
        )
        self.assertEqual(_remaining_default_assignment_lines(["Host=a"], {}), [])
        self.assertEqual(
            _remaining_default_assignment_lines(["Host=a"], {"AutoEnergySources": "alpha"}),
            ["", "AutoEnergySources=alpha"],
        )
        self.assertEqual(
            _remaining_default_assignment_lines([""], {"AutoEnergySources": "alpha"}),
            ["AutoEnergySources=alpha"],
        )

    def test_wizard_render_live_connectivity_branches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            main_path = temp_path / "config.ini"
            main_path.write_text("[DEFAULT]\n", encoding="utf-8")

            runtime = SimpleNamespace(
                meter_config_path=Path("meter.ini"),
                switch_config_path=None,
                charger_config_path=Path("charger.ini"),
            )
            resolved = SimpleNamespace(runtime=runtime)
            with patch("venus_evcharger.bootstrap.wizard_render_live.build_service_backends", return_value=resolved), patch(
                "venus_evcharger.bootstrap.wizard_render_live.probe_meter_backend",
                return_value={"meter": "ok"},
            ), patch(
                "venus_evcharger.bootstrap.wizard_render_live.read_charger_backend",
                side_effect=RuntimeError("boom"),
            ):
                payload = live_connectivity_payload(main_path, ("meter", "charger"))
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["roles"]["switch"]["reason"], "not requested")
            self.assertEqual(payload["roles"]["meter"]["status"], "ok")
            self.assertEqual(payload["roles"]["charger"]["status"], "error")

            runtime = SimpleNamespace(
                meter_config_path=None,
                switch_config_path=None,
                charger_config_path=None,
            )
            with patch(
                "venus_evcharger.bootstrap.wizard_render_live.build_service_backends",
                return_value=SimpleNamespace(runtime=runtime),
            ):
                payload = live_connectivity_payload(main_path, None)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["roles"]["meter"]["reason"], "not configured")

            with patch(
                "venus_evcharger.bootstrap.wizard_render_live.live_connectivity_payload",
                return_value={"ok": True, "checked_roles": (), "roles": {}},
            ):
                live_payload = live_check_rendered_setup(
                    "Host=example\nAdapter=adapter.ini\n",
                    {"adapter.ini": "backend=true\n"},
                    "config.ini",
                    ("meter",),
                )
            self.assertTrue(live_payload["ok"])


