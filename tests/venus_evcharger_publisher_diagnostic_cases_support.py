# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
from tests.gateway_diagnostics_fixtures import gateway_diagnostics_reader
from tests.venus_evcharger_publisher_support import (
    DbusPublishControllerTestCase,
    MagicMock,
    SimpleNamespace,
    build_publish_controller,
)


def _with_backends_config(
    service: SimpleNamespace,
    *,
    mode: str,
    meter_type: str,
    switch_type: str,
    charger_type: str | None,
    host: str = "192.168.1.20",
) -> SimpleNamespace:
    parser = configparser.ConfigParser()
    parser.read_string(
        f"""
[DEFAULT]
Host={host}

[Backends]
Mode={mode}
MeterType={meter_type}
SwitchType={switch_type}
ChargerType={charger_type or ""}
"""
    )
    service.config = parser
    return service


__all__ = [name for name in globals() if not name.startswith("__")]
