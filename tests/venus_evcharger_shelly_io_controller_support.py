# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import threading
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests

from venus_evcharger.backend.modbus_transport import ModbusSlaveOfflineError
from venus_evcharger.backend.shelly_io import (
    ShellyIoController,
    _phase_currents_for_selection,
    _phase_powers_for_selection,
    _single_phase_vector,
)
from venus_evcharger.backend.smartevse_charger import SmartEvseChargerBackend
from venus_evcharger.backend.models import ChargerState, MeterReading

__all__ = [
    "ChargerState",
    "MagicMock",
    "json",
    "MeterReading",
    "ModbusSlaveOfflineError",
    "Path",
    "ShellyIoController",
    "ShellyIoControllerTestBase",
    "SimpleNamespace",
    "SmartEvseChargerBackend",
    "requests",
    "_runtime_bundle",
    "_phase_currents_for_selection",
    "_phase_powers_for_selection",
    "_single_phase_vector",
    "patch",
    "tempfile",
    "threading",
]


def _runtime_bundle(
    mode: str,
    *,
    meter_type: str | None = None,
    switch_type: str | None = None,
    charger_type: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        runtime=SimpleNamespace(
            backend_mode=mode,
            meter_type=meter_type,
            switch_type=switch_type,
            charger_type=charger_type,
        )
    )


class ShellyIoControllerTestBase(unittest.TestCase):
    @staticmethod
    def _write_config(directory: str, content: str) -> str:
        path = Path(directory) / "smartevse-charger.ini"
        path.write_text(content, encoding="utf-8")
        return str(path)
