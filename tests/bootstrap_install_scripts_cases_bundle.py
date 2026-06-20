# SPDX-License-Identifier: GPL-3.0-or-later
from tests.bootstrap_install_scripts_cases_common import REPO_ROOT, Path, _BootstrapInstallScriptsBase, subprocess, tarfile, tempfile


BUILD_BUNDLE_SCRIPT = REPO_ROOT / "deploy/venus/build_bootstrap_bundle.sh"


class _BootstrapInstallScriptsBundleCases(_BootstrapInstallScriptsBase):
    def test_bootstrap_bundle_contains_all_runtime_entrypoints_and_services(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            subprocess.run(["bash", str(BUILD_BUNDLE_SCRIPT), str(output_dir), str(REPO_ROOT)], check=True)

            with tarfile.open(output_dir / "wallbox-bundle.tar.gz", "r:gz") as bundle:
                bundle_names = set(bundle.getnames())

            for rel_path in (
                "./venus_evcharger_service.py",
                "./venus_evcharger_dbus_adapter.py",
                "./venus_evcharger_observer.py",
                "./venus_evcharger_auto_input_helper.py",
                "./venus_evchargerctl.py",
                "./deploy/venus/service_venus_evcharger/run",
                "./deploy/venus/service_venus_evcharger_dbus_adapter/run",
                "./deploy/venus/service_venus_evcharger_observer/run",
            ):
                self.assertIn(rel_path, bundle_names)


__all__ = [name for name in globals() if not name.startswith("__")]
