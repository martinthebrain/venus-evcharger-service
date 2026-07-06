# SPDX-License-Identifier: GPL-3.0-or-later
"""Template-meter adapter contract names and defaults."""

from __future__ import annotations

from typing import Final

DEFAULT_METER_TIMEOUT_SECONDS: Final = 2.0
DEFAULT_METER_METHOD: Final = "GET"
DEFAULT_POWER_PATH: Final = "power_w"
DEFAULT_SINGLE_PHASE_LINE: Final = "L1"

ADAPTER_BASE_URL_KEY: Final = "BaseUrl"
ADAPTER_TIMEOUT_KEY: Final = "RequestTimeoutSeconds"
PHASE_MEASURED_SELECTION_KEY: Final = "MeasuredPhaseSelection"
PHASE_MEASURED_PHASE_KEY: Final = "MeasuredPhase"
METER_REQUEST_METHOD_KEY: Final = "Method"
METER_REQUEST_URL_KEY: Final = "Url"
METER_RESPONSE_RELAY_ENABLED_KEY: Final = "RelayEnabledPath"
METER_RESPONSE_POWER_KEY: Final = "PowerPath"
METER_RESPONSE_VOLTAGE_KEY: Final = "VoltagePath"
METER_RESPONSE_CURRENT_KEY: Final = "CurrentPath"
METER_RESPONSE_ENERGY_KWH_KEY: Final = "EnergyKwhPath"
METER_RESPONSE_ENERGY_WH_KEY: Final = "EnergyWhPath"
METER_RESPONSE_PHASE_SELECTION_KEY: Final = "PhaseSelectionPath"
METER_RESPONSE_PHASE_POWERS_KEY: Final = "PhasePowersPath"
METER_RESPONSE_PHASE_CURRENTS_KEY: Final = "PhaseCurrentsPath"
