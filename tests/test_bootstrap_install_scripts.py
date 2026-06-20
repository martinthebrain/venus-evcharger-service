# SPDX-License-Identifier: GPL-3.0-or-later
from tests.bootstrap_install_scripts_cases_common import _BootstrapInstallScriptsBase
from tests.bootstrap_install_scripts_cases_bundle import _BootstrapInstallScriptsBundleCases
from tests.bootstrap_install_scripts_cases_installer import _BootstrapInstallScriptsInstallerCases
from tests.bootstrap_install_scripts_cases_manifest import _BootstrapInstallScriptsManifestCases
from tests.bootstrap_install_scripts_cases_sync import _BootstrapInstallScriptsSyncCases


class TestBootstrapInstallScripts(
    _BootstrapInstallScriptsSyncCases,
    _BootstrapInstallScriptsBundleCases,
    _BootstrapInstallScriptsManifestCases,
    _BootstrapInstallScriptsInstallerCases,
    _BootstrapInstallScriptsBase,
):
    pass
