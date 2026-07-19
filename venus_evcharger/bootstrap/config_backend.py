# SPDX-License-Identifier: GPL-3.0-or-later
"""Canonical backend-configuration bootstrap component."""

from __future__ import annotations

import configparser

from venus_evcharger.backend.config import load_runtime_backend_summary
from venus_evcharger.topology.config import parse_topology_config


def _service_config(service: object) -> configparser.ConfigParser:
    config = getattr(service, "config", None)
    if not isinstance(config, configparser.ConfigParser):
        raise TypeError("bootstrap service config is not a ConfigParser")
    return config


class BackendConfigLoader:
    """Resolve topology and backend selection once at the config boundary."""

    def __init__(self, service: object) -> None:
        self._service = service

    def load(self) -> None:
        """Apply the canonical topology and backend runtime summary."""
        config = _service_config(self._service)
        if config.has_section("Topology"):
            setattr(self._service, "_topology_config", parse_topology_config(config))
        setattr(self._service, "_backend_runtime_summary", load_runtime_backend_summary(config))
