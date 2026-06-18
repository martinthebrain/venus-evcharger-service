# SPDX-License-Identifier: GPL-3.0-or-later
"""General small helper values shared across the wallbox service."""

from __future__ import annotations

import logging
import math
import os
from typing import Any

from venus_evcharger.core.common_types import PhaseMeasurements


def read_version(file_name: str) -> str:
    """Read the version file shipped with the script, if present."""
    module_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(module_dir, os.pardir, os.pardir))
    for file_path in (os.path.join(repo_root, file_name), os.path.join(module_dir, file_name)):
        try:
            with open(file_path, "r", encoding="utf-8") as file:
                line = file.readline()
                return line.split(":")[-1].strip()
        except FileNotFoundError:
            continue
    logging.warning("File %s not found in the service root.", file_name)
    return "0.1"


def phase_values(
    total_power: float | int,
    voltage: float | int,
    phase: str,
    voltage_mode: str,
) -> PhaseMeasurements:
    """Split total power into per-phase values and currents."""
    total_power = float(total_power)
    voltage = float(voltage)
    if not voltage:
        raise ValueError("Invalid voltage")
    if phase == "3P":
        per_phase_power = total_power / 3.0
        phase_voltage = voltage if voltage_mode == "phase" else voltage / math.sqrt(3)
        phase_current = per_phase_power / phase_voltage
        return {
            "L1": {"power": per_phase_power, "voltage": phase_voltage, "current": phase_current},
            "L2": {"power": per_phase_power, "voltage": phase_voltage, "current": phase_current},
            "L3": {"power": per_phase_power, "voltage": phase_voltage, "current": phase_current},
        }
    result = {
        "L1": {"power": 0.0, "voltage": voltage, "current": 0.0},
        "L2": {"power": 0.0, "voltage": voltage, "current": 0.0},
        "L3": {"power": 0.0, "voltage": voltage, "current": 0.0},
    }
    result[phase] = {"power": total_power, "voltage": voltage, "current": total_power / voltage}
    return result


def normalize_phase(phase: Any) -> str:
    """Normalize phase configuration values and validate them."""
    phase_name = str(phase).strip().upper()
    if phase_name == "1P":
        return "L1"
    if phase_name not in ("L1", "L2", "L3", "3P"):
        raise ValueError(f"Invalid Phase '{phase_name}'. Use L1, L2, L3 or 3P.")
    return phase_name


def normalize_mode(mode: Any) -> int:
    """Normalize supported charger modes to a known integer range."""
    try:
        mode_int = int(mode)
    except (TypeError, ValueError):
        return 0
    return mode_int if mode_int in (0, 1, 2) else 0


def mode_uses_auto_logic(mode: Any) -> bool:
    return normalize_mode(mode) in (1, 2)


def mode_uses_scheduled_logic(mode: Any) -> bool:
    return normalize_mode(mode) == 2
