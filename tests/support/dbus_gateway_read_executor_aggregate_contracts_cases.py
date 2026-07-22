# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter aggregate read executor contracts."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    DbusAdapter,
    GatewayAdapterContractCase,
    MagicMock,
    Path,
    gateway_paths,
    install_mock,
    patch,
    read_aggregate_module,
    read_module,
    read_pv_module,
    read_targets_module,
    tempfile,
    unittest,
)


class GatewayReadExecutorAggregateContractCases(GatewayAdapterContractCase):
    """Exercise aggregate read executor contracts."""

    def test_read_executor_pv_total_reuses_in_progress_members_without_rediscovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.read_executor._aggregates.state_for(
                "pv_power_w",
                (read_aggregate_module.PV_TOTAL_AGGREGATE, (("pv.cached", "/Ac/Power"),)),
                0.75,
            )
            aggregate = install_mock(adapter.read_executor, "_poll_aggregate_step", MagicMock(return_value="deferred"))

            with patch.object(adapter.energy_discovery, "pv_members") as discover:
                self.assertEqual(
                    adapter.read_executor._poll_pv_total_step(
                        "pv_power_w",
                        {
                            "aggregate": "pv-total",
                            "prefix": "pv.",
                            "path": "/Ac/Power",
                            "optional_confidence": 0.75,
                        },
                    ),
                    "deferred",
                )

            discover.assert_not_called()
            aggregate.assert_called_once_with(
                "pv_power_w",
                (read_aggregate_module.PV_TOTAL_AGGREGATE, (("pv.cached", "/Ac/Power"),)),
                [("pv.cached", "/Ac/Power")],
                ignore_member_errors=True,
                empty_confidence=0.75,
            )

    def test_read_executor_update_and_complete_cache_contracts_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            target = read_targets_module.read_target("svc.update", "/Value")
            self.assertIsNotNone(target)
            assert target is not None

            with patch.object(adapter.cache, "update_value") as update_value:
                adapter.read_executor._update_read_value(
                    "semantic_value",
                    target,
                    42.0,
                    spec={"interval": 2.0},
                )
                update_value.assert_has_calls(
                    [
                        unittest.mock.call(
                            "path:svc.update/Value", 42.0, source="svc.update/Value", freshness_kind="external_read"
                        ),
                        unittest.mock.call(
                            "semantic_value",
                            42.0,
                            source="svc.update/Value",
                            freshness_kind="external_read",
                            stale_after_seconds=6.0,
                        ),
                    ]
                )
                self.assertEqual(update_value.call_count, 2)

                update_value.reset_mock()
                adapter.read_executor._update_read_value("path:svc.update/Value", target, 43.0)
                update_value.assert_called_once_with(
                    "path:svc.update/Value", 43.0, source="svc.update/Value", freshness_kind="external_read"
                )

            state = read_aggregate_module.AggregateState(("sum", (("svc.a", "/A"),)), empty_confidence=0.35)
            adapter.read_executor._record_aggregate_member(state, "svc.a", "/A", 2.5)
            state.record_error("svc.b", "/B", RuntimeError("offline"))
            adapter.read_executor._aggregates.state_for("aggregate_key", state.signature, 0.35)
            adapter.read_executor._stale_after_by_key = {"aggregate_key": 6.0, "other": 9.0}

            with patch.object(adapter.cache, "update_value") as update_value:
                update_value.reset_mock()
                adapter.read_executor._complete_aggregate("aggregate_key", state)
                update_value.assert_called_once_with(
                    "aggregate_key",
                    2.5,
                    source="svc.a/A",
                    confidence=1.0,
                    last_error="svc.b/B: offline",
                    freshness_kind="external_read",
                    stale_after_seconds=6.0,
                )
                self.assertEqual(adapter.read_executor._stale_after_by_key, {"other": 9.0})

                empty_state = read_aggregate_module.AggregateState(
                    ("sum", (("svc.empty", "/A"),)), empty_confidence=0.35
                )
                adapter.read_executor._stale_after_by_key["empty_aggregate"] = 8.0
                update_value.reset_mock()
                adapter.read_executor._complete_aggregate("empty_aggregate", empty_state)
                update_value.assert_called_once_with(
                    "empty_aggregate",
                    0.0,
                    source="empty_aggregate",
                    confidence=0.35,
                    last_error="",
                    freshness_kind="external_read",
                    stale_after_seconds=8.0,
                )

    def test_read_executor_aggregate_default_confidence_and_prefix_defaults_are_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            install_mock(
                adapter.read_executor, "read_optional_busitem", MagicMock(side_effect=RuntimeError("optional asleep"))
            )

            self.assertEqual(
                adapter.read_executor._poll_aggregate_step(
                    "empty_optional",
                    ("pv-total", (("svc.optional", "/Power"),)),
                    [("svc.optional", "/Power")],
                    ignore_member_errors=True,
                ),
                "applied",
            )
            self.assertEqual(adapter.cache.values["empty_optional"]["confidence"], 1.0)
            self.assertEqual(adapter.cache.values["empty_optional"]["source"], "empty_optional")

            adapter.cache.update_services(["svc.z", "svc.a"])
            adapter.energy_discovery.update_services(["svc.z", "svc.a"], now=1.0)
            install_mock(adapter.read_executor, "read_busitem", MagicMock(return_value=77.0))
            self.assertEqual(
                adapter.read_executor._poll_first_service(
                    "first_default",
                    {"aggregate": "first-service", "prefix": "svc.", "path": "/Soc", "interval": 2.0},
                ),
                "applied",
            )
            adapter.read_executor.read_busitem.assert_called_once_with("svc.a", "/Soc")
            self.assertEqual(adapter.cache.values["first_default"]["value"], 77.0)
            self.assertEqual(adapter.cache.values["first_default"]["stale_after_s"], 6.0)
            self.assertEqual(adapter.cache.values["first_default"]["freshness_kind"], "external_read")

            with self.assertRaisesRegex(RuntimeError, "No cached services for prefix ''"):
                empty_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-empty")))
                empty_adapter.read_executor._poll_first_service(
                    "first_missing_default",
                    {"aggregate": "first-service", "path": "/Soc"},
                )

    def test_read_executor_optional_aggregate_error_contract_is_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            state = read_aggregate_module.AggregateState(("pv-total", (("svc.optional", "/Power"),)), 0.2)
            error = RuntimeError("sleeping")

            with (
                patch.object(adapter.cache, "mark_unavailable") as mark_unavailable,
                patch.object(read_module.logging, "debug") as log_debug,
            ):
                adapter.read_executor._record_optional_aggregate_error("svc.optional", "/Power", state, error)

            mark_unavailable.assert_called_once_with(
                "path:svc.optional/Power",
                source="svc.optional/Power",
                error=error,
                retry_after_seconds=read_pv_module.PV_MEMBER_ERROR_BACKOFF_SECONDS,
            )
            self.assertEqual(state.errors, ["svc.optional/Power: sleeping"])
            log_debug.assert_called_once_with(
                "DBus adapter optional aggregate member failed %s%s: %s",
                "svc.optional",
                "/Power",
                error,
            )
