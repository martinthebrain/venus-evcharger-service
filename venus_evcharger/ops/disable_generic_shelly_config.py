# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration vocabulary for the generic Shelly one-shot helper."""

HOST_KEY = "Host"
ENABLED_KEY = "DisableGenericShellyDevice"
ALLOW_PERSISTENT_DISABLE_KEY = "GenericShellyAllowPersistentDisable"
SERVICE_KEY = "GenericShellyService"
TARGET_IP_KEY = "GenericShellyDisableIp"
TARGET_MAC_KEY = "GenericShellyDisableMac"
CHANNEL_KEY = "GenericShellyDisableChannel"
DELAY_SECONDS_KEY = "GenericShellyDisableDelaySeconds"
GATEWAY_RUN_DIR_KEY = "DbusGatewayRunDir"
GATEWAY_CACHE_PATH_KEY = "DbusGatewayCachePath"

DEFAULT_SERVICE = "com.victronenergy.shelly"
DEFAULT_GATEWAY_RUN_DIR = "/run/venus-evcharger"
