# SPDX-License-Identifier: GPL-3.0-or-later
"""Stable vocabulary for forensic observer inputs and artifacts."""

UTF8 = "utf-8"
DEFAULT_DEVICE_INSTANCE = 60
DEFAULT_SERVICE_NAME = "com.victronenergy.evcharger"
DEFAULT_GATEWAY_RUN_DIR = "/run/venus-evcharger"
RUNTIME_LOG_DIR = "/var/volatile/log/dbus-venus-evcharger"
DEFAULT_MOUNTS_PATH = "/proc/mounts"
DEFAULT_FORENSIC_SUBDIR = "venus-evcharger-forensics"
INCIDENT_TIME_FORMAT = "%Y%m%d-%H%M%S"
SNAPSHOT_FILENAME = "snapshot.json"
REDACTED_CONFIG_FILENAME = "config.redacted.ini"
WRITE_PROBE_FILENAME = ".write-test"
SLUG_TRIM_CHARS = "-"

DEVICE_INSTANCE_KEY = "DeviceInstance"
SERVICE_NAME_KEY = "ServiceName"
AUTO_INPUT_SNAPSHOT_PATH_KEY = "AutoInputSnapshotPath"
INTROSPECTION_SNAPSHOT_PATH_KEY = "DbusIntrospectionSnapshotPath"
GATEWAY_CACHE_PATH_KEY = "DbusGatewayCachePath"
GATEWAY_RUN_DIR_KEY = "DbusGatewayRunDir"
HOST_KEY = "Host"
