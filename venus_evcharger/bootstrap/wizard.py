# SPDX-License-Identifier: GPL-3.0-or-later
"""Public setup-wizard facade and module entrypoint."""

from __future__ import annotations

from venus_evcharger.bootstrap.wizard_models import WizardAnswers
from venus_evcharger.bootstrap.wizard_main import main
from venus_evcharger.bootstrap.wizard_render import default_config_path, default_template_path
from venus_evcharger.bootstrap.wizard_runtime import configure_wallbox


__all__ = [
    "WizardAnswers",
    "configure_wallbox",
    "default_template_path",
    "default_config_path",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
