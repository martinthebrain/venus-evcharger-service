# SPDX-License-Identifier: GPL-3.0-or-later
"""Wizard import key names and legacy-backend profile defaults."""

from __future__ import annotations

from venus_evcharger.bootstrap.wizard_models import WizardChargerBackend, WizardProfile

PROFILE_DEFAULTS_BY_BACKENDS: dict[tuple[str, str, str], tuple[WizardProfile, str | None, WizardChargerBackend | None]] = {
    ("template_meter", "template_switch", "template_charger"): ("multi_adapter_topology", "template-stack", "template_charger"),
    ("shelly_meter", "shelly_switch", "template_charger"): ("multi_adapter_topology", "shelly-io-template-charger", "template_charger"),
    ("tasmota_meter", "tasmota_switch", "template_charger"): ("multi_adapter_topology", "tasmota-io-template-charger", "template_charger"),
    ("tuya_meter", "tuya_switch", "template_charger"): ("multi_adapter_topology", "tuya-io-template-charger", "template_charger"),
    ("shelly_meter", "shelly_switch", "modbus_charger"): ("multi_adapter_topology", "shelly-io-modbus-charger", "modbus_charger"),
    ("tasmota_meter", "tasmota_switch", "modbus_charger"): ("multi_adapter_topology", "tasmota-io-modbus-charger", "modbus_charger"),
    ("tuya_meter", "tuya_switch", "modbus_charger"): ("multi_adapter_topology", "tuya-io-modbus-charger", "modbus_charger"),
    ("template_meter", "cerbo_gx_relay_switch", ""): ("multi_adapter_topology", "template-meter-cerbo-relay", None),
    ("shelly_meter", "cerbo_gx_relay_switch", ""): ("multi_adapter_topology", "shelly-meter-cerbo-relay", None),
    ("tasmota_meter", "cerbo_gx_relay_switch", ""): ("multi_adapter_topology", "tasmota-meter-cerbo-relay", None),
    ("tuya_meter", "cerbo_gx_relay_switch", ""): ("multi_adapter_topology", "tuya-meter-cerbo-relay", None),
    ("shelly_meter", "none", "goe_charger"): ("multi_adapter_topology", "shelly-meter-goe", "goe_charger"),
    ("tasmota_meter", "none", "goe_charger"): ("multi_adapter_topology", "tasmota-meter-goe", "goe_charger"),
    ("tuya_meter", "none", "goe_charger"): ("multi_adapter_topology", "tuya-meter-goe", "goe_charger"),
    ("shelly_meter", "none", "modbus_charger"): ("multi_adapter_topology", "shelly-meter-modbus-charger", "modbus_charger"),
    ("tasmota_meter", "none", "modbus_charger"): ("multi_adapter_topology", "tasmota-meter-modbus-charger", "modbus_charger"),
    ("tuya_meter", "none", "modbus_charger"): ("multi_adapter_topology", "tuya-meter-modbus-charger", "modbus_charger"),
    ("none", "switch_group", "goe_charger"): ("multi_adapter_topology", "goe-external-switch-group", "goe_charger"),
    ("template_meter", "switch_group", "goe_charger"): ("multi_adapter_topology", "template-meter-goe-switch-group", "goe_charger"),
    ("shelly_meter", "switch_group", "goe_charger"): ("multi_adapter_topology", "shelly-meter-goe-switch-group", "goe_charger"),
    ("shelly_meter", "switch_group", "modbus_charger"): ("multi_adapter_topology", "shelly-meter-modbus-switch-group", "modbus_charger"),
}

SECTION_DEFAULT = "DEFAULT"
SECTION_ADAPTER = "Adapter"
SECTION_BACKENDS = "Backends"
SECTION_CAPABILITIES = "Capabilities"
SECTION_MEMBERS = "Members"
SECTION_TRANSPORT = "Transport"

KEY_BASE_URL = "BaseUrl"
KEY_AUTO_MIN_SOC = "AutoMinSoc"
KEY_AUTO_RESUME_SOC = "AutoResumeSoc"
KEY_AUTO_SCHEDULED_DAYS = "AutoScheduledEnabledDays"
KEY_AUTO_SCHEDULED_LATEST_END = "AutoScheduledLatestEndTime"
KEY_AUTO_SCHEDULED_NIGHT_AMPS = "AutoScheduledNightCurrentAmps"
KEY_AUTO_START_SURPLUS = "AutoStartSurplusWatts"
KEY_AUTO_STOP_SURPLUS = "AutoStopSurplusWatts"
KEY_CHARGER_CONFIG = "ChargerConfigPath"
KEY_CHARGER_TYPE = "ChargerType"
KEY_DEVICE = "Device"
KEY_DEVICE_INSTANCE = "DeviceInstance"
KEY_DIGEST_AUTH = "DigestAuth"
KEY_HOST = "Host"
KEY_METER_CONFIG = "MeterConfigPath"
KEY_METER_TYPE = "MeterType"
KEY_MODE = "Mode"
KEY_P1 = "P1"
KEY_PASSWORD = "Password"
KEY_PHASE = "Phase"
KEY_PORT = "Port"
KEY_PRESET = "Preset"
KEY_REQUEST_TIMEOUT = "RequestTimeoutSeconds"
KEY_SUPPORTED_PHASES = "SupportedPhaseSelections"
KEY_SWITCH_CONFIG = "SwitchConfigPath"
KEY_SWITCH_TYPE = "SwitchType"
KEY_TRANSPORT = "Transport"
KEY_UNIT_ID = "UnitId"
KEY_USERNAME = "Username"
