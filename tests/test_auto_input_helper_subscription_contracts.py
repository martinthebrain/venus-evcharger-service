# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from venus_evcharger.energy import EnergySourceDefinition
from venus_evcharger.inputs.helper.subscriptions import SubscriptionManager
from tests.support.auto_input_helper import (
    FakeCatalog,
    FakeGateway,
    FakePvGrid,
    FakeResolver,
    FakeSnapshots,
    WarningRecorder,
    helper_settings,
    run_callback,
)


def _manager(*, stop: bool = False) -> tuple[SubscriptionManager, FakeGateway, FakeSnapshots, WarningRecorder]:
    source = EnergySourceDefinition(source_id="battery", service_name="battery.svc", soc_path="/Soc")
    settings = replace(
        helper_settings(),
        auto_pv_service="",
        auto_energy_sources=(source,),
        auto_use_dc_pv=True,
    )
    gateway = FakeGateway()
    snapshots = FakeSnapshots()
    warnings = WarningRecorder()
    manager = SubscriptionManager(
        settings,
        gateway,
        FakePvGrid(["pv.svc"]),
        FakeCatalog(source),
        FakeResolver("battery.svc"),
        snapshots,
        warnings,
        lambda: stop,
    )
    return manager, gateway, snapshots, warnings


class AutoInputHelperSubscriptionContracts(unittest.TestCase):
    def test_desired_specs_cover_ac_dc_pv_battery_and_grid(self) -> None:
        manager, _gateway, _snapshots, _warnings = _manager()
        specs = manager.desired_specs()
        self.assertIn(("pv", "pv.svc", manager.settings.auto_pv_path), specs)
        self.assertIn(("pv", manager.settings.auto_dc_pv_service, manager.settings.auto_dc_pv_path), specs)
        self.assertIn(("battery", "battery.svc", "/Soc"), specs)
        self.assertIn(("grid", manager.settings.auto_grid_service, manager.settings.auto_grid_l1_path), specs)

    def test_refresh_enqueues_each_new_spec_and_refreshes_sources(self) -> None:
        manager, gateway, snapshots, _warnings = _manager()
        self.assertFalse(manager.refresh())
        self.assertEqual(len(gateway.requests), len(manager.desired_specs()))
        self.assertEqual(gateway.service_refreshes, 1)
        self.assertEqual(snapshots.refresh_all_calls, 1)
        manager.refresh()
        self.assertEqual(gateway.service_refreshes, 1)

    def test_active_backoff_reschedules_without_work(self) -> None:
        manager, gateway, snapshots, _warnings = _manager()
        manager._backoff_until = 110.0
        with patch("venus_evcharger.inputs.helper.subscriptions.time.time", return_value=100.0), patch.object(
            manager, "schedule_refresh"
        ) as schedule:
            self.assertFalse(manager.refresh())
        schedule.assert_called_once_with()
        self.assertEqual(gateway.requests, [])
        self.assertEqual(snapshots.refresh_all_calls, 0)

    def test_stale_specs_are_removed_and_reset_advances_generation(self) -> None:
        manager, _gateway, _snapshots, _warnings = _manager()
        manager._matches.add(("pv", "old", "/P"))
        manager._monitored[("pv", "old", "/P")] = {
            "source": "pv",
            "service_name": "old",
            "path": "/P",
        }
        manager._clear_missing(set())
        self.assertEqual(manager._matches, set())
        self.assertEqual(manager._monitored, {})
        generation = manager._generation
        manager._matches.add(("grid", "active", "/P"))
        manager.reset()
        self.assertEqual(manager._generation, generation + 1)

    def test_callbacks_refresh_current_generation_only(self) -> None:
        manager, _gateway, snapshots, _warnings = _manager()
        manager.source_changed("pv", manager._generation)
        manager.source_changed("grid", manager._generation + 1)
        self.assertEqual(snapshots.refreshed, [("pv", None)])
        snapshots.refresh_error = ValueError("bad source")
        with patch.object(manager, "_handle_error") as handle:
            manager.source_changed("pv")
        handle.assert_called_once()

    def test_error_enters_backoff_warns_resets_and_schedules(self) -> None:
        manager, _gateway, _snapshots, warnings = _manager()
        with patch.object(manager, "desired_specs", side_effect=ValueError("bad topology")), patch.object(
            manager, "schedule_refresh"
        ) as schedule, patch(
            "venus_evcharger.inputs.helper.subscriptions.time.time", return_value=100.0
        ):
            manager.refresh()
        self.assertEqual(warnings.calls[0][0], "auto-helper-refresh-subscriptions")
        self.assertGreater(manager._backoff_until, 100.0)
        schedule.assert_called_once_with()

    def test_schedule_uses_idle_or_timeout_and_honours_stop(self) -> None:
        manager, _gateway, _snapshots, _warnings = _manager(stop=True)
        with patch("venus_evcharger.inputs.helper.subscriptions.GLIB_RUNTIME.idle_add", side_effect=run_callback):
            manager.schedule_refresh()
        self.assertFalse(manager._refresh_scheduled)
        manager._backoff_until = 110.0
        with patch("venus_evcharger.inputs.helper.subscriptions.time.time", return_value=100.0), patch(
            "venus_evcharger.inputs.helper.subscriptions.GLIB_RUNTIME.timeout_add"
        ) as timeout_add:
            manager.schedule_refresh()
        timeout_add.assert_called_once()
        manager._refresh_scheduled = True
        with patch("venus_evcharger.inputs.helper.subscriptions.GLIB_RUNTIME.idle_add") as idle_add:
            manager.schedule_refresh()
        idle_add.assert_not_called()
        self.assertFalse(manager.timer_tick())

    def test_owner_matching_is_explicit_and_prefix_aware(self) -> None:
        manager, _gateway, _snapshots, _warnings = _manager()
        with patch.object(manager, "schedule_refresh") as schedule:
            manager.owner_changed(manager.settings.auto_grid_service)
            manager.owner_changed("com.victronenergy.pvinverter.extra")
            manager.owner_changed("unrelated")
        self.assertEqual(schedule.call_count, 2)
        with patch.object(manager, "_relevant_owner", side_effect=ValueError("bad owner")), patch.object(
            manager, "_handle_error"
        ) as handle:
            manager.owner_changed("svc")
            manager.owner_changed("svc", manager._generation + 1)
        handle.assert_called_once()

    def test_disabled_optional_specs_and_resolution_errors_are_omitted(self) -> None:
        manager, _gateway, _snapshots, _warnings = _manager()
        manager.settings = replace(
            manager.settings,
            auto_use_dc_pv=False,
            auto_grid_service="",
            auto_energy_sources=(),
        )
        pv_grid = FakePvGrid()
        pv_grid.resolve_error = ValueError("offline")
        manager.pv_grid = pv_grid
        resolver = FakeResolver()
        resolver.resolve_error = ValueError("offline")
        manager.resolver = resolver
        specs = manager.desired_specs()
        self.assertFalse(any(spec[0] == "pv" and spec[1] == manager.settings.auto_dc_pv_service for spec in specs))
        self.assertFalse(any(spec[0] == "grid" for spec in specs))
        self.assertFalse(any(spec[0] == "battery" for spec in specs))

    def test_explicit_pv_incomplete_dc_and_energy_owner_branches(self) -> None:
        manager, _gateway, _snapshots, _warnings = _manager()
        source = manager.settings.auto_energy_sources[0]
        manager.settings = replace(
            manager.settings,
            auto_pv_service="pv.explicit",
            auto_dc_pv_path="",
            auto_energy_sources=(replace(source, service_name="energy.explicit"),),
        )
        specs = manager.desired_specs()
        self.assertIn(("pv", "pv.explicit", manager.settings.auto_pv_path), specs)
        self.assertFalse(any(spec[0] == "pv" and spec[1] == manager.settings.auto_dc_pv_service for spec in specs))
        self.assertTrue(manager._explicit_owner(manager.settings.auto_energy_sources[0].service_name))

if __name__ == "__main__":
    unittest.main()
