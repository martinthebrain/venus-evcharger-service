# SPDX-License-Identifier: GPL-3.0-or-later
"""Forensic artifact and removable-storage IO for the observer."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable, Mapping
from pathlib import Path

from venus_evcharger.ops import forensic_observer_schema as schema
from venus_evcharger.ops.removable_storage_coordination import (
    removable_storage_write_lease,
)


_SECRET_KEYS = ("password", "token", "secret", "auth")
_MOUNT_PREFIXES = ("/media/", "/run/media/", "/mnt/")
_DEVICE_PREFIXES = ("/dev/sd", "/dev/mmcblk", "/dev/disk/")


def redact_config_text(text: str) -> str:
    """Redact secret-bearing configuration assignments."""
    return "\n".join(_redacted_config_line(line) for line in text.splitlines()) + "\n"


def _redacted_config_line(line: str) -> str:
    """Redact one configuration line when its key denotes a secret."""
    if "=" not in line:
        return line
    key, _value = line.split("=", 1)
    if any(secret in key.strip().lower() for secret in _SECRET_KEYS):
        return f"{key}=<redacted>"
    return line


def mounted_storage_candidates(mounts_text: str) -> list[str]:
    """Return mounted removable-storage paths accepted for forensic artifacts."""
    candidates: list[str] = []
    for raw_line in mounts_text.splitlines():
        parts = raw_line.split()
        if len(parts) < 2:
            continue
        device, mount_point = parts[0], parts[1].replace("\\040", " ")
        if not device.startswith(_DEVICE_PREFIXES):
            continue
        if not mount_point.startswith(_MOUNT_PREFIXES):
            continue
        candidates.append(mount_point)
    return candidates


def read_mounts(path: str = schema.DEFAULT_MOUNTS_PATH) -> str:
    """Read the current mount table or return an empty snapshot."""
    try:
        return Path(path).read_text(encoding=schema.UTF8)
    except OSError:
        return ""


def first_writable_log_dir(
    candidates: Iterable[str],
    subdir: str = schema.DEFAULT_FORENSIC_SUBDIR,
) -> str:
    """Return the first candidate that permits an artifact write probe."""
    for candidate in candidates:
        log_dir = Path(candidate) / subdir
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            probe_path = log_dir / schema.WRITE_PROBE_FILENAME
            probe_path.touch()
            probe_path.unlink()
            return str(log_dir)
        except OSError:
            continue
    return ""


def read_text_safe(path: str) -> str:
    """Read UTF-8 text while preserving an explicit unavailable marker."""
    try:
        return Path(path).read_text(encoding=schema.UTF8)
    except OSError as error:
        return f"<unavailable: {error}>\n"


def slug_text(text: str) -> str:
    """Normalize diagnostic text for an incident path component."""
    return (
        re.sub(r"[^a-z0-9]+", "-", text.lower()).strip(schema.SLUG_TRIM_CHARS)
        or "event"
    )


def write_incident(
    log_dir: str,
    snapshot: Mapping[str, object],
    config_path: str,
    reasons: list[str],
) -> str:
    """Persist one immutable forensic incident bundle."""
    stamp = time.strftime(
        schema.INCIDENT_TIME_FORMAT,
        time.localtime(_artifact_timestamp(snapshot)),
    )
    incident_dir = Path(log_dir) / (
        f"incident-{stamp}-{slug_text('-'.join(reasons))[:80]}"
    )
    incident_dir.mkdir(parents=True, exist_ok=True)
    payload = dict(snapshot)
    payload["reasons"] = list(reasons)
    (incident_dir / schema.SNAPSHOT_FILENAME).write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding=schema.UTF8,
    )
    (incident_dir / schema.REDACTED_CONFIG_FILENAME).write_text(
        redact_config_text(read_text_safe(config_path)),
        encoding=schema.UTF8,
    )
    return str(incident_dir)


def _artifact_timestamp(snapshot: Mapping[str, object]) -> float:
    """Return the validated numeric timestamp of an artifact snapshot."""
    value = snapshot.get("timestamp")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("forensic snapshot timestamp must be numeric")
    return float(value)


def write_incident_with_storage_lease(
    config_path: str,
    snapshot: Mapping[str, object],
    reasons: list[str],
    *,
    mounts_path: str,
    storage_lock_path: str,
) -> bool:
    """Write an incident only while storage is leased and still mounted."""
    with removable_storage_write_lease(storage_lock_path) as acquired:
        if not acquired:
            return False
        current_mounts = read_mounts(mounts_path)
        log_dir = first_writable_log_dir(
            mounted_storage_candidates(current_mounts)
        )
        if not log_dir:
            return False
        write_incident(log_dir, snapshot, config_path, reasons)
        return True


__all__ = [
    "first_writable_log_dir",
    "mounted_storage_candidates",
    "read_mounts",
    "read_text_safe",
    "redact_config_text",
    "slug_text",
    "write_incident",
    "write_incident_with_storage_lease",
]
