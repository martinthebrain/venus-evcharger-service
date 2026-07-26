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
    physical_priority: int = 0,
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
        physical_priority=physical_priority,
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
            physical_priority=10,
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

        self.assertEqual(selected, (gateway,))
        self.assertEqual(cluster.combined_soc, 40.0)
        self.assertEqual(cluster.combined_usable_capacity_wh, 5000.0)
        self.assertEqual(cluster.valid_soc_source_count, 1)

    def test_preferred_source_is_stable_while_observation_timestamps_alternate(self) -> None:
        first_cycle = (
            _battery(
                "preferred",
                40.0,
                5000.0,
                physical_id="house",
                physical_priority=100,
                captured_at=10.0,
            ),
            _battery(
                "secondary",
                60.0,
                5000.0,
                physical_id="house",
                physical_priority=50,
                captured_at=11.0,
            ),
        )
        second_cycle = (
            _battery(
                "preferred",
                41.0,
                5000.0,
                physical_id="house",
                physical_priority=100,
                captured_at=12.0,
            ),
            _battery(
                "secondary",
                61.0,
                5000.0,
                physical_id="house",
                physical_priority=50,
                captured_at=13.0,
            ),
        )

        self.assertEqual(unique_weighted_soc_sources(first_cycle)[0].source_id, "preferred")
        self.assertEqual(unique_weighted_soc_sources(second_cycle)[0].source_id, "preferred")

    def test_online_secondary_replaces_offline_preferred_source(self) -> None:
        preferred = _battery(
            "preferred",
            40.0,
            5000.0,
            physical_id="house",
            physical_priority=100,
            online=False,
        )
        secondary = _battery(
            "secondary",
            60.0,
            5000.0,
            physical_id="house",
            physical_priority=50,
        )

        self.assertEqual(unique_weighted_soc_sources((preferred, secondary)), (secondary,))

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
