# SPDX-License-Identifier: GPL-3.0-or-later
"""Semantic gateway transport config loading for the core service."""

from __future__ import annotations

import configparser
from typing import Any

from venus_evcharger.bootstrap.config_shared import _config_value
from venus_evcharger.ipc.gateway_path_config import configured_gateway_paths


DBUS_GATEWAY_MAX_AGE_KEY = "DbusGatewayMaxAgeSeconds"


def load_gateway_path_config(svc: Any, defaults: configparser.SectionProxy) -> None:
    paths = configured_gateway_paths(defaults)
    svc.dbus_gateway_run_dir = paths.run_dir
    svc.dbus_gateway_cache_path = paths.cache_path
    svc.gateway_health_path = paths.health_path
    svc.dbus_gateway_socket_path = paths.socket_path
    svc.dbus_gateway_command_dir = paths.command_dir
    svc.core_command_mailbox_dir = paths.core_command_dir
    svc.dbus_gateway_max_age_seconds = max(
        0.0,
        float(_config_value(defaults, DBUS_GATEWAY_MAX_AGE_KEY, 10.0)),
    )


def load_gateway_config(svc: Any, defaults: configparser.SectionProxy) -> None:
    load_gateway_path_config(svc, defaults)
