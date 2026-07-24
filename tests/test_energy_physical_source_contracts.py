# SPDX-License-Identifier: GPL-3.0-or-later
"""Physical identity contracts for weighted battery aggregation."""

from __future__ import annotations

import unittest

from venus_evcharger.energy.aggregate import aggregate_energy_sources
from venus_evcharger.energy.models import EnergySourceSnapshot
from venus_evcharger.energy.physical_sources import unique_weighted_soc_sources


def _battery(
    source_id: str,
    soc: float,
    capacity: float,
    *,
    physical_id: str = "",
    online: bool = True,
    captured_at: float | None = 10.0,
    confidence: float = 1.0,
) -> EnergySourceSnapshot:
    return EnergySourceSnapshot(
        source_id=source_id,
        role="battery",
        service_name=source_id,
        physical_id=physical_id,
        soc=soc,
        usable_capacity_wh=capacity,
        online=online,
        captured_at=captured_at,
        confidence=confidence,
    )


class EnergyPhysicalSourceContracts(unittest.TestCase):
    def test_same_physical_battery_is_counted_once_using_best_observation(self) -> None:
        gateway = _battery(
            "victron",
            40.0,
            5000.0,
            physical_id="house",
            captured_at=9.0,
            confidence=1.0,
        )
        external = _battery(
            "huawei",
            60.0,
            5000.0,
            physical_id="house",
            captured_at=10.0,
            confidence=0.5,
        )

        selected = unique_weighted_soc_sources((gateway, external))
        cluster = aggregate_energy_sources((gateway, external))

        self.assertEqual(selected, (external,))
        self.assertEqual(cluster.combined_soc, 60.0)
        self.assertEqual(cluster.combined_usable_capacity_wh, 5000.0)
        self.assertEqual(cluster.valid_soc_source_count, 1)

    def test_online_finite_quality_wins_and_ties_are_deterministic(self) -> None:
        online = _battery(
            "online",
            50.0,
            1000.0,
            physical_id="battery",
            captured_at=1.0,
            confidence=0.1,
        )
        offline = _battery(
            "offline",
            70.0,
            1000.0,
            physical_id="battery",
            online=False,
            captured_at=100.0,
        )
        invalid = _battery(
            "invalid",
            80.0,
            1000.0,
            physical_id="battery",
            captured_at=float("nan"),
            confidence=float("inf"),
        )
        missing_time = _battery(
            "missing-time",
            90.0,
            1000.0,
            physical_id="battery",
            captured_at=None,
        )
        tie_a = _battery("a", 10.0, 1.0, physical_id="tie")
        tie_b = _battery("b", 20.0, 1.0, physical_id="tie")

        self.assertEqual(
            unique_weighted_soc_sources(
                (offline, invalid, missing_time, online)
            ),
            (online,),
        )
        self.assertEqual(
            unique_weighted_soc_sources((tie_b, tie_a)),
            (tie_b,),
        )

    def test_missing_physical_id_preserves_independent_legacy_aggregation(self) -> None:
        first = _battery("first", 20.0, 1000.0)
        second = _battery("second", 80.0, 3000.0)

        cluster = aggregate_energy_sources((first, second))

        self.assertEqual(unique_weighted_soc_sources((first, second)), (first, second))
        self.assertEqual(cluster.combined_soc, 65.0)
        self.assertEqual(cluster.combined_usable_capacity_wh, 4000.0)
        self.assertEqual(cluster.valid_soc_source_count, 2)


if __name__ == "__main__":
    unittest.main()
