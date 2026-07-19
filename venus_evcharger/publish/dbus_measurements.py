# SPDX-License-Identifier: GPL-3.0-or-later
"""Live and accumulated EV charger measurement publishing."""

from __future__ import annotations

from collections.abc import Mapping

from venus_evcharger.publish.dbus_ports import FieldPublisherPort
from venus_evcharger.publish.dbus_shared import DbusPublishContext, PhaseData, PublishServicePort


class DbusMeasurementPublisher:
    """Build and publish the two measurement snapshots exposed by EVCS."""

    PHASE_NAMES: tuple[str, str, str] = ("L1", "L2", "L3")

    def __init__(self, context: DbusPublishContext, core: FieldPublisherPort) -> None:
        self.service: PublishServicePort = context.service
        self.core = core

    @classmethod
    def live_measurement_fields(
        cls,
        power: float,
        voltage: float,
        total_current: float,
        phase_data: PhaseData,
    ) -> dict[str, float]:
        """Return fast-moving AC values keyed by semantic gateway field."""
        values: dict[str, float] = {
            "ac_power_w": power,
            "ac_voltage_v": voltage,
            "ac_current_a": total_current,
            "charge_current_a": total_current,
        }
        for phase_name in cls.PHASE_NAMES:
            normalized = phase_name.lower()
            values[f"{normalized}_power_w"] = phase_data[phase_name]["power"]
            values[f"{normalized}_current_a"] = phase_data[phase_name]["current"]
            values[f"{normalized}_voltage_v"] = phase_data[phase_name]["voltage"]
        return values

    def publish_live_measurements(
        self,
        power: float,
        voltage: float,
        total_current: float,
        phase_data: PhaseData,
        now: float | None,
    ) -> bool:
        """Publish fast-changing AC measurements at the configured interval."""
        return self.core.publish_fields(
            "live-measurements",
            self.live_measurement_fields(power, voltage, total_current, phase_data),
            now,
            interval_seconds=self.service._dbus_live_publish_interval_seconds,
        )

    @staticmethod
    def energy_time_fields(
        energy_forward: float,
        phase_energies: Mapping[str, float],
        charging_time: int,
        session_energy: float,
    ) -> dict[str, float | int]:
        """Return accumulated energy and session values keyed by semantic field."""
        return {
            "energy_forward_kwh": energy_forward,
            "l1_energy_forward_kwh": phase_energies["L1"],
            "l2_energy_forward_kwh": phase_energies["L2"],
            "l3_energy_forward_kwh": phase_energies["L3"],
            "charging_time_s": charging_time,
            "session_energy_kwh": session_energy,
            "session_time_s": charging_time,
        }

    def publish_energy_time_measurements(
        self,
        energy_forward: float,
        phase_energies: Mapping[str, float],
        charging_time: int,
        session_energy: float,
        now: float | None,
    ) -> bool:
        """Publish accumulated energy and time at the configured slow interval."""
        return self.core.publish_fields(
            "energy-time",
            self.energy_time_fields(energy_forward, phase_energies, charging_time, session_energy),
            now,
            interval_seconds=self.service._dbus_slow_publish_interval_seconds,
        )
