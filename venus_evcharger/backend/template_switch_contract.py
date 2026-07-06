# SPDX-License-Identifier: GPL-3.0-or-later
"""Template-switch adapter contract names, tokens, and defaults."""

from __future__ import annotations

from typing import Final

from .models import PhaseSelection, SwitchingMode

DEFAULT_SWITCH_PHASE_SELECTION: Final[PhaseSelection] = "P1"
DEFAULT_SWITCH_PHASE_SELECTIONS: Final[tuple[PhaseSelection, ...]] = ("P1",)
DEFAULT_SWITCHING_MODE: Final[SwitchingMode] = "direct"
DEFAULT_SWITCH_CONFIG_PATH: Final = ""
DEFAULT_STATE_METHOD: Final = "GET"
DEFAULT_COMMAND_METHOD: Final = "POST"
DEFAULT_PHASE_METHOD: Final = "POST"
DEFAULT_ENABLED_PATH: Final = "enabled"
DEFAULT_PHASE_JSON_TEMPLATE: Final = '{"phase_selection": "$phase_selection"}'

ADAPTER_BASE_URL_KEY: Final = "BaseUrl"
CAPABILITIES_PHASE_SELECTIONS_KEY: Final = "SupportedPhaseSelections"
CAPABILITIES_SWITCHING_MODE_KEY: Final = "SwitchingMode"
CAPABILITIES_MAX_DIRECT_POWER_KEY: Final = "MaxDirectSwitchPowerWatts"
CAPABILITIES_REQUIRES_PAUSE_KEY: Final = "RequiresChargePauseForPhaseChange"
REQUEST_METHOD_KEY: Final = "Method"
REQUEST_URL_KEY: Final = "Url"
REQUEST_JSON_TEMPLATE_KEY: Final = "JsonTemplate"
STATE_RESPONSE_ENABLED_KEY: Final = "EnabledPath"
STATE_RESPONSE_PHASE_SELECTION_KEY: Final = "PhaseSelectionPath"
STATE_RESPONSE_FEEDBACK_CLOSED_KEY: Final = "FeedbackClosedPath"
STATE_RESPONSE_INTERLOCK_OK_KEY: Final = "InterlockOkPath"

SWITCH_CONTEXT_ENABLED_JSON: Final = "enabled_json"
SWITCH_CONTEXT_ENABLED_INT: Final = "enabled_int"
SWITCH_CONTEXT_ENABLED_TEXT: Final = "enabled_text"
SWITCH_CONTEXT_PHASE_SELECTION: Final = "phase_selection"
SWITCH_TRUE_JSON: Final = "true"
SWITCH_FALSE_JSON: Final = "false"
SWITCH_TRUE_INT: Final = "1"
SWITCH_FALSE_INT: Final = "0"
SWITCH_TRUE_TEXT: Final = "on"
SWITCH_FALSE_TEXT: Final = "off"

SWITCH_TRUE_TEXT_VALUES: Final = frozenset(("1", "true", "on", "yes", "enabled"))
SWITCH_FALSE_TEXT_VALUES: Final = frozenset(("0", "false", "off", "no", "disabled"))
