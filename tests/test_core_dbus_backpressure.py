# SPDX-License-Identifier: GPL-3.0-or-later
import json
import tempfile
import unittest
from pathlib import Path

import venus_evcharger.core.dbus_backpressure as backpressure_module
from venus_evcharger.core.dbus_backpressure import (
    CoreDbusBackpressurePolicy,
    normalized_core_backpressure_state,
    service_dbus_backpressure_policy,
)


def _write_health(path: str, payload: dict[str, object]) -> None:
    Path(path).write_text(json.dumps(payload), encoding="utf-8")


class _NoSetattrService:
    __slots__ = ("dbus_gateway_health_path",)

    def __init__(self, path: str) -> None:
        self.dbus_gateway_health_path = path


class TestCoreDbusBackpressurePolicy(unittest.TestCase):
    def test_normalized_core_backpressure_state_accepts_known_and_degraded_values(self) -> None:
        self.assertEqual(normalized_core_backpressure_state("ok"), "ok")
        self.assertEqual(normalized_core_backpressure_state(" OK "), "ok")
        self.assertEqual(normalized_core_backpressure_state("congested"), "congested")
        self.assertEqual(normalized_core_backpressure_state("slow"), "slow")
        self.assertEqual(normalized_core_backpressure_state("protective"), "protective")
        self.assertEqual(normalized_core_backpressure_state("degraded"), "slow")
        self.assertEqual(normalized_core_backpressure_state(""), "unknown")
        self.assertEqual(normalized_core_backpressure_state(None), "unknown")
        self.assertEqual(normalized_core_backpressure_state("mystery"), "unknown")

    def test_constructor_normalizes_runtime_bounds_and_initial_snapshot(self) -> None:
        default_policy = CoreDbusBackpressurePolicy("")
        self.assertEqual(default_policy.max_age_seconds, 10.0)
        self.assertEqual(default_policy.cache_seconds, 1.0)

        now = lambda: 123.0
        policy = CoreDbusBackpressurePolicy(
            " /tmp/dbus-health.json ",
            now=now,
            max_age_seconds=-1.0,
            cache_seconds=-2.0,
        )

        self.assertEqual(policy.health_path, "/tmp/dbus-health.json")
        self.assertIs(policy._now, now)
        self.assertEqual(policy.max_age_seconds, 0.0)
        self.assertEqual(policy.cache_seconds, 0.0)
        self.assertEqual(policy._cached_at, 0.0)
        self.assertEqual(policy._cached_snapshot.state, "unknown")
        self.assertEqual(policy._cached_snapshot.captured_at, 0.0)
        self.assertEqual(policy._cached_snapshot.age_s, 0.0)
        self.assertIs(policy._cached_snapshot.stale, False)
        self.assertEqual(policy._cached_snapshot.source, "unread")

    def test_missing_path_and_missing_file_fail_open_as_unknown(self) -> None:
        missing_path = CoreDbusBackpressurePolicy("", now=lambda: 100.0).snapshot()
        self.assertEqual(missing_path.state, "unknown")
        self.assertEqual(missing_path.source, "missing-path")
        self.assertEqual(missing_path.captured_at, 0.0)
        self.assertEqual(missing_path.age_s, 0.0)
        self.assertIs(missing_path.stale, False)
        snapshot = CoreDbusBackpressurePolicy("/tmp/does-not-exist", now=lambda: 100.0).snapshot()
        self.assertEqual(snapshot.state, "unknown")
        self.assertEqual(snapshot.source, "missing-health")

    def test_nested_backpressure_state_is_used_and_cached(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/dbus-health.json"
            current_time = [100.0]
            _write_health(
                path,
                {
                    "captured_at": 99.0,
                    "dbus_health": {"backpressure": {"state": "slow"}},
                },
            )
            policy = CoreDbusBackpressurePolicy(path, now=lambda: current_time[0], cache_seconds=2.0)

            first = policy.snapshot()
            self.assertEqual(first.state, "slow")
            self.assertEqual(first.age_s, 1.0)
            self.assertEqual(first.source, "backpressure")
            self.assertTrue(policy.should_throttle_optional_work())

            _write_health(path, {"captured_at": 100.0, "dbus_health": {"backpressure": {"state": "ok"}}})
            current_time[0] = 101.0
            self.assertEqual(policy.state(), "slow")
            current_time[0] = 102.0
            self.assertEqual(policy.state(), "ok")
            _write_health(path, {"captured_at": 103.0, "dbus_health": {"backpressure": {"state": "protective"}}})
            current_time[0] = 103.1
            self.assertEqual(policy.state(), "ok")
            current_time[0] = 104.1
            self.assertEqual(policy.state(), "protective")

    def test_cache_fresh_rejects_disabled_expired_and_future_cache_times(self) -> None:
        policy = CoreDbusBackpressurePolicy("", cache_seconds=2.0)
        policy._cached_at = 100.0

        self.assertFalse(policy._cache_fresh(99.9))
        self.assertTrue(policy._cache_fresh(100.0))
        self.assertTrue(policy._cache_fresh(101.999))
        self.assertFalse(policy._cache_fresh(102.0))

        short_cache = CoreDbusBackpressurePolicy("", cache_seconds=0.5)
        short_cache._cached_at = 100.0
        self.assertTrue(short_cache._cache_fresh(100.49))
        self.assertFalse(short_cache._cache_fresh(100.5))

        disabled = CoreDbusBackpressurePolicy("", cache_seconds=0.0)
        disabled._cached_at = 100.0
        self.assertFalse(disabled._cache_fresh(99.9))
        self.assertFalse(disabled._cache_fresh(100.0))

    def test_stale_health_slows_core_without_entering_protective(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/dbus-health.json"
            _write_health(path, {"captured_at": 80.0, "dbus_health": {"backpressure": {"state": "ok"}}})

            snapshot = CoreDbusBackpressurePolicy(path, now=lambda: 100.0, max_age_seconds=10.0).snapshot()

            self.assertEqual(snapshot.state, "slow")
            self.assertTrue(snapshot.stale)
            self.assertEqual(snapshot.source, "stale-health")
            self.assertEqual(snapshot.captured_at, 80.0)
            self.assertEqual(snapshot.age_s, 20.0)

    def test_stale_boundaries_are_exact_and_can_be_disabled(self) -> None:
        self.assertFalse(backpressure_module._payload_stale(0.0, 100.0, 10.0))
        self.assertTrue(backpressure_module._payload_stale(0.5, 100.0, 10.0))
        self.assertFalse(backpressure_module._payload_stale(80.0, 10.0, 10.0))
        self.assertTrue(backpressure_module._payload_stale(80.0, 10.01, 10.0))
        self.assertTrue(backpressure_module._payload_stale(0.5, 1.0, 0.5))
        self.assertFalse(backpressure_module._payload_stale(80.0, 100.0, 0.0))

        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/dbus-health.json"
            _write_health(path, {"captured_at": 80.0, "dbus_health": {"backpressure": {"state": "ok"}}})
            snapshot = CoreDbusBackpressurePolicy(
                path,
                now=lambda: 100.0,
                max_age_seconds=0.0,
                cache_seconds=0.0,
            ).snapshot()

            self.assertEqual(snapshot.state, "ok")
            self.assertIs(snapshot.stale, False)
            self.assertEqual(snapshot.age_s, 20.0)

            _write_health(path, {"captured_at": 99.5, "dbus_health": {"backpressure": {"state": "ok"}}})
            fresh = CoreDbusBackpressurePolicy(
                path,
                now=lambda: 100.0,
                max_age_seconds=10.0,
                cache_seconds=0.0,
            ).snapshot()
            self.assertEqual(fresh.age_s, 0.5)
            self.assertIs(fresh.stale, False)

            _write_health(path, {"captured_at": 0.5, "dbus_health": {"backpressure": {"state": "ok"}}})
            early_epoch_fresh = CoreDbusBackpressurePolicy(
                path,
                now=lambda: 1.0,
                max_age_seconds=10.0,
                cache_seconds=0.0,
            ).snapshot()
            self.assertEqual(early_epoch_fresh.state, "ok")
            self.assertEqual(early_epoch_fresh.captured_at, 0.5)
            self.assertEqual(early_epoch_fresh.age_s, 0.5)
            self.assertIs(early_epoch_fresh.stale, False)

            _write_health(path, {"dbus_health": {"backpressure": {"state": "ok"}}})
            missing_capture_time = CoreDbusBackpressurePolicy(
                path,
                now=lambda: 100.0,
                max_age_seconds=10.0,
                cache_seconds=0.0,
            ).snapshot()
            self.assertEqual(missing_capture_time.state, "ok")
            self.assertEqual(missing_capture_time.captured_at, 0.0)
            self.assertEqual(missing_capture_time.age_s, 0.0)
            self.assertIs(missing_capture_time.stale, False)

    def test_fallback_state_uses_circuit_state_then_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/dbus-health.json"

            _write_health(
                path,
                {
                    "captured_at": 100.0,
                    "dbus_health": {
                        "backpressure": {"state": "congested"},
                        "state": "protective",
                        "resources": {"state": "ok"},
                    },
                },
            )
            self.assertEqual(CoreDbusBackpressurePolicy(path, now=lambda: 100.0, cache_seconds=0.0).state(), "congested")

            _write_health(path, {"captured_at": 100.0, "dbus_health": {"state": "protective"}})
            self.assertEqual(CoreDbusBackpressurePolicy(path, now=lambda: 100.0, cache_seconds=0.0).state(), "protective")

            _write_health(path, {"captured_at": 100.0, "dbus_health": {"state": "degraded"}})
            self.assertEqual(CoreDbusBackpressurePolicy(path, now=lambda: 100.0, cache_seconds=0.0).state(), "slow")

            _write_health(path, {"captured_at": 100.0, "dbus_health": {"resources": {"state": "busy"}}})
            self.assertEqual(CoreDbusBackpressurePolicy(path, now=lambda: 100.0, cache_seconds=0.0).state(), "congested")

            _write_health(path, {"captured_at": 100.0, "dbus_health": {"resources": {"state": "constrained"}}})
            self.assertEqual(CoreDbusBackpressurePolicy(path, now=lambda: 100.0, cache_seconds=0.0).state(), "slow")

            _write_health(path, {"captured_at": 100.0, "dbus_health": {"resources": {"state": "ok"}}})
            self.assertEqual(CoreDbusBackpressurePolicy(path, now=lambda: 100.0, cache_seconds=0.0).state(), "ok")

            _write_health(path, {"captured_at": 100.0, "dbus_health": {"resources": {"state": "odd"}}})
            self.assertEqual(CoreDbusBackpressurePolicy(path, now=lambda: 100.0, cache_seconds=0.0).state(), "unknown")

    def test_mapping_and_health_helpers_keep_source_contracts_exact(self) -> None:
        self.assertEqual(backpressure_module._mapping([(1, 2)]), {})
        self.assertEqual(backpressure_module._mapping({1: "one", "two": 2}), {"1": "one", "two": 2})
        self.assertEqual(backpressure_module._health_mapping({"dbus_health": {"state": "ok"}}), {"state": "ok"})
        self.assertEqual(backpressure_module._health_mapping({"dbus_health": [], "state": "slow"}), {"dbus_health": [], "state": "slow"})
        self.assertEqual(backpressure_module._state_from_health({"backpressure": {"state": "slow"}}), ("slow", "backpressure"))
        self.assertEqual(backpressure_module._state_from_health({"state": "degraded"}), ("slow", "dbus-health"))
        self.assertEqual(backpressure_module._state_from_health({"resources": {"state": "busy"}}), ("congested", "resources"))
        self.assertEqual(backpressure_module._resource_state({"state": "constrained"}), ("slow", "resources"))
        self.assertEqual(backpressure_module._resource_state({"state": "busy"}), ("congested", "resources"))
        self.assertEqual(backpressure_module._resource_state({"state": "ok"}), ("ok", "resources"))
        self.assertEqual(backpressure_module._resource_state({"state": "offline"}), ("unknown", "unknown"))
        self.assertEqual(backpressure_module._resource_state({"state": None}), ("unknown", "unknown"))

    def test_direct_health_payload_and_non_mapping_payload_are_handled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/dbus-health.json"
            _write_health(path, {"captured_at": 100.0, "backpressure": {"state": "congested"}})
            self.assertEqual(CoreDbusBackpressurePolicy(path, now=lambda: 100.0).snapshot().source, "backpressure")

            Path(path).write_text("[1, 2, 3]", encoding="utf-8")
            snapshot = CoreDbusBackpressurePolicy(path, now=lambda: 100.0, cache_seconds=0.0).snapshot()
            self.assertEqual(snapshot.state, "unknown")
            self.assertEqual(snapshot.source, "missing-health")
            self.assertEqual(snapshot.captured_at, 0.0)
            self.assertEqual(snapshot.age_s, 0.0)
            self.assertIs(snapshot.stale, False)

    def test_interval_multipliers_preserve_zero_and_separate_live_optional_and_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/dbus-health.json"
            _write_health(path, {"captured_at": 100.0, "dbus_health": {"backpressure": {"state": "protective"}}})
            policy = CoreDbusBackpressurePolicy(path, now=lambda: 100.0, cache_seconds=0.0)

            self.assertEqual(policy.publish_interval_seconds(0.0, group="live-measurements"), 0.0)
            self.assertEqual(policy.publish_interval_seconds(-1.0, group="live-measurements"), 0.0)
            self.assertEqual(policy.publish_interval_seconds(1.0, group="live-measurements"), 5.0)
            self.assertEqual(policy.publish_interval_seconds(5.0, group="diagnostic-ages"), 60.0)
            self.assertEqual(policy.audit_repeat_seconds(30.0), 240.0)
            self.assertEqual(policy.audit_cleanup_interval_seconds(300.0), 2400.0)
            self.assertEqual(policy.optional_work_interval_seconds(10.0), 120.0)
            self.assertEqual(policy.liveness_timeout_seconds(10.0), 50.0)
            self.assertEqual(policy.audit_repeat_seconds(0.0), 0.0)
            self.assertEqual(policy.audit_cleanup_interval_seconds(0.0), 0.0)
            self.assertEqual(policy.optional_work_interval_seconds(0.0), 0.0)
            self.assertEqual(policy.liveness_timeout_seconds(0.0), 0.0)

    def test_interval_multipliers_for_all_states_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/dbus-health.json"
            cases = {
                "unknown": (1.0, 1.0, 1.0),
                "ok": (1.0, 1.0, 1.0),
                "congested": (2.0, 3.0, 2.0),
                "slow": (3.0, 6.0, 4.0),
                "protective": (5.0, 12.0, 8.0),
            }
            for state, (live_multiplier, optional_multiplier, audit_multiplier) in cases.items():
                with self.subTest(state=state):
                    _write_health(path, {"captured_at": 100.0, "dbus_health": {"backpressure": {"state": state}}})
                    policy = CoreDbusBackpressurePolicy(path, now=lambda: 100.0, cache_seconds=0.0)
                    self.assertEqual(policy.publish_interval_seconds(10.0, group="live-measurements"), 10.0 * live_multiplier)
                    self.assertEqual(policy.publish_interval_seconds(10.0, group="diagnostics"), 10.0 * optional_multiplier)
                    self.assertEqual(policy.optional_work_interval_seconds(10.0), 10.0 * optional_multiplier)
                    self.assertEqual(policy.audit_repeat_seconds(10.0), 10.0 * audit_multiplier)
                    self.assertEqual(policy.audit_cleanup_interval_seconds(10.0), 10.0 * audit_multiplier)
                    self.assertEqual(policy.liveness_timeout_seconds(10.0), 10.0 * live_multiplier)

    def test_service_policy_is_cached_when_possible_and_safe_for_slots(self) -> None:
        service = type("Service", (), {"dbus_gateway_health_path": "/tmp/health.json"})()
        first = service_dbus_backpressure_policy(service)
        second = service_dbus_backpressure_policy(service)
        self.assertIs(first, second)
        self.assertEqual(first.health_path, "/tmp/health.json")

        no_setattr = _NoSetattrService("")
        self.assertIsInstance(service_dbus_backpressure_policy(no_setattr), CoreDbusBackpressurePolicy)

        service._dbus_backpressure_policy = object()
        replaced = service_dbus_backpressure_policy(service)
        self.assertIsInstance(replaced, CoreDbusBackpressurePolicy)
        self.assertIs(service._dbus_backpressure_policy, replaced)

        bad_path_service = type("BadPathService", (), {"dbus_gateway_health_path": 42})()
        self.assertEqual(service_dbus_backpressure_policy(bad_path_service).health_path, "")

        no_path_service = type("NoPathService", (), {})()
        self.assertEqual(service_dbus_backpressure_policy(no_path_service).health_path, "")


if __name__ == "__main__":
    unittest.main()
