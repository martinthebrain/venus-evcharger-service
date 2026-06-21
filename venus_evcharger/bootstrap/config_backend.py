# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from venus_evcharger.backend.config import load_runtime_backend_summary
from venus_evcharger.topology.config import parse_topology_config
from venus_evcharger.core.split_mixins import ComposableControllerMixin as _ComposableControllerMixin


class _ServiceBootstrapBackendConfigMixin(_ComposableControllerMixin):
    def _load_backend_config(self) -> None:
        """Load normalized meter/switch/charger backend selection.

        Backend selection is normalized early because many later config
        decisions depend on the topology shape. A charger-native setup and a
        relay-driven setup expose different valid combinations of meter,
        switch, and charger roles.
        """
        svc = self.service
        if svc.config.has_section("Topology"):
            svc._topology_config = parse_topology_config(svc.config)
        # Validate backend topology early, but do not mirror any compat view
        # onto the service object anymore.
        load_runtime_backend_summary(svc.config)
