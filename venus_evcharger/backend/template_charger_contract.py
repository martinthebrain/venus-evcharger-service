# SPDX-License-Identifier: GPL-3.0-or-later
"""Template-charger adapter contract names, tokens, and defaults."""

from __future__ import annotations

from typing import Final

from .models import PhaseSelection

DEFAULT_CHARGER_CONFIG_PATH: Final = ""
DEFAULT_CHARGER_PHASE_SELECTIONS: Final[tuple[PhaseSelection, ...]] = ("P1",)
DEFAULT_STATE_METHOD: Final = "GET"
DEFAULT_ENABLE_METHOD: Final = "POST"
DEFAULT_CURRENT_METHOD: Final = "POST"
DEFAULT_PHASE_METHOD: Final = "POST"
DEFAULT_ENABLE_JSON_TEMPLATE: Final = '{"enabled": $enabled_json}'
DEFAULT_CURRENT_JSON_TEMPLATE: Final = '{"amps": $amps}'
DEFAULT_PHASE_JSON_TEMPLATE: Final = '{"phase_selection": "$phase_selection"}'

ADAPTER_BASE_URL_KEY: Final = "BaseUrl"
CAPABILITIES_PHASE_SELECTIONS_KEY: Final = "SupportedPhaseSelections"
REQUEST_METHOD_KEY: Final = "Method"
REQUEST_URL_KEY: Final = "Url"
REQUEST_JSON_TEMPLATE_KEY: Final = "JsonTemplate"
STATE_RESPONSE_ENABLED_KEY: Final = "EnabledPath"
STATE_RESPONSE_CURRENT_KEY: Final = "CurrentPath"
STATE_RESPONSE_PHASE_SELECTION_KEY: Final = "PhaseSelectionPath"
STATE_RESPONSE_ACTUAL_CURRENT_KEY: Final = "ActualCurrentPath"
STATE_RESPONSE_POWER_WATTS_KEY: Final = "PowerWattsPath"
STATE_RESPONSE_ENERGY_KWH_KEY: Final = "EnergyKwhPath"
STATE_RESPONSE_STATUS_KEY: Final = "StatusPath"
STATE_RESPONSE_FAULT_KEY: Final = "FaultPath"

CHARGER_CONTEXT_ENABLED_JSON: Final = "enabled_json"
CHARGER_CONTEXT_ENABLED_INT: Final = "enabled_int"
CHARGER_CONTEXT_ENABLED_TEXT: Final = "enabled_text"
CHARGER_CONTEXT_AMPS: Final = "amps"
CHARGER_CONTEXT_PHASE_SELECTION: Final = "phase_selection"
CHARGER_TRUE_JSON: Final = "true"
CHARGER_FALSE_JSON: Final = "false"
CHARGER_TRUE_INT: Final = "1"
CHARGER_FALSE_INT: Final = "0"
CHARGER_TRUE_TEXT: Final = "on"
CHARGER_FALSE_TEXT: Final = "off"
