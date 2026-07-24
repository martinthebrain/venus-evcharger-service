# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter queue activity and adaptive tick contracts."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    DbusAdapter,
    GatewayAdapterContractCase,
    MagicMock,
    Path,
    gateway_paths,
    health_queue_module,
    install_mock,
    tempfile,
    time,
)


class GatewayAdaptiveTickCases(GatewayAdapterContractCase):
    """Exercise queue activity and adaptive tick contracts."""

    def test_queue_age_uses_updated_at_for_coalesced_activity(self) -> None:
        commands = [
            ("fresh", {"created_at": 10.0, "updated_at": 95.0}),
            ("old", {"created_at": 90.0}),
            ("bad", {"created_at": "bad"}),
        ]

        self.assertEqual(health_queue_module.oldest_command_age(commands, 100.0), 10.0)

    def test_adaptive_tick_uses_fast_default_and_slows_under_pressure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            self.assertEqual(adapter.min_tick_seconds, 0.2)
            self.assertEqual(adapter.tick_seconds, 0.2)
            self.assertEqual(adapter.loop_role.adaptive_tick_seconds(circuit_state="ok", resource_state="ok"), 0.2)
            self.assertAlmostEqual(adapter.loop_role.adaptive_tick_seconds(circuit_state="ok", resource_state="busy"), 0.3)
            self.assertEqual(adapter.loop_role.adaptive_tick_seconds(circuit_state="degraded", resource_state="ok"), 0.5)
            self.assertEqual(adapter.loop_role.adaptive_tick_seconds(circuit_state="ok", resource_state="constrained"), 1.0)
            self.assertEqual(adapter.loop_role.adaptive_tick_seconds(circuit_state="protective", resource_state="ok"), 1.0)

            install_mock(adapter.resource_monitor, "snapshot", MagicMock(return_value={"state": "busy"}))
            adapter.loop_role.update_adaptive_tick()

            self.assertAlmostEqual(adapter.tick_seconds, 0.3)
            self.assertEqual(adapter._last_resource_snapshot["state"], "busy")

    def test_tick_skips_work_until_adaptive_interval_is_due(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter._next_work_tick_monotonic = time.monotonic() + 10.0
            install_mock(adapter.socket_role, "process_socket_once", MagicMock())
            install_mock(adapter.loop_role, "process_one_dbus_operation_once", MagicMock())
            install_mock(adapter.io_role, "publish_cache", MagicMock())

            self.assertTrue(adapter.tick())

            adapter.socket_role.process_socket_once.assert_not_called()
            adapter.loop_role.process_one_dbus_operation_once.assert_not_called()
            adapter.io_role.publish_cache.assert_not_called()
            adapter.tick_health.record(duration_ms=10000.0, expected_interval_s=0.1, now=time.monotonic())
            adapter.loop_role.update_adaptive_tick()
            self.assertAlmostEqual(adapter.tick_seconds, 0.3)
