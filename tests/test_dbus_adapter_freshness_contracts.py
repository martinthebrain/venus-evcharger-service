#!/usr/bin/env python3
"""Behavioral contracts for DBus adapter cache freshness metrics."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from venus_evcharger import dbus_gateway_cache
from venus_evcharger.dbus_adapter.health.freshness import (
    cache_freshness,
    cached_entry_age,
    cached_entry_float,
    count_status,
    important_freshness,
    max_cached_path_age,
    missing_cached_path_count,
    optional_source_error_count,
    optional_source_unavailable_count,
    status_counts,
    values_for_kinds,
)
from venus_evcharger.dbus_gateway import (
    CacheValueMetadata,
    CacheFreshnessKind,
    DbusCacheStore,
    dbus_path_key,
    evcs_path_freshness_kind,
)


class DbusAdapterFreshnessContractTests(unittest.TestCase):
    def test_status_counts_include_unknown_and_accumulate(self) -> None:
        values = {
            "a": {"status": "fresh"},
            "b": {"status": "fresh"},
            "c": {"status": "error"},
            "d": {},
            "e": {"status": None},
        }
        self.assertEqual(status_counts(values), {"fresh": 2, "error": 1, "unknown": 1, "None": 1})
        self.assertEqual(status_counts({}), {})

    def test_important_freshness_has_all_and_only_fast_read_fields(self) -> None:
        values = {
            "grid_power_w": {"age_s": "1.25", "status": "fresh"},
            "pv_power_w": {"age_s": 2.5, "status": "error"},
            "battery_soc": {"age_s": object(), "status": None},
            "unrelated": {"age_s": 99.0, "status": "fresh"},
        }
        self.assertEqual(
            important_freshness(values),
            {
                "grid_power_w_age_s": 1.25,
                "pv_power_w_age_s": 2.5,
                "battery_soc_age_s": 0.0,
                "grid_power_w_status": "fresh",
                "pv_power_w_status": "error",
                "battery_soc_status": "None",
            },
        )
        self.assertEqual(
            important_freshness({}),
            {
                "grid_power_w_age_s": 0.0,
                "pv_power_w_age_s": 0.0,
                "battery_soc_age_s": 0.0,
                "grid_power_w_status": "missing",
                "pv_power_w_status": "missing",
                "battery_soc_status": "missing",
            },
        )

    def test_cached_entry_age_rejects_invalid_zero_and_future_timestamps(self) -> None:
        self.assertEqual(cached_entry_age({"updated_at": 90.0}, 100.0), 10.0)
        self.assertEqual(cached_entry_age({"updated_at": "99.5"}, 100.0), 0.5)
        self.assertEqual(cached_entry_age({"updated_at": 0.5}, 1.0), 0.5)
        for entry in (None, object(), [], {}, {"updated_at": 0.0}, {"updated_at": -1.0}):
            with self.subTest(entry=entry):
                self.assertEqual(cached_entry_age(entry, 100.0), 0.0)
        self.assertEqual(cached_entry_age({"updated_at": 110.0}, 100.0), 0.0)

    def test_cached_path_metrics_use_service_qualified_keys(self) -> None:
        values = {
            dbus_path_key("svc", "/Fresh"): {"updated_at": 90.0},
            dbus_path_key("svc", "/Recent"): {"updated_at": 98.0},
            dbus_path_key("other", "/Fresh"): {"updated_at": 1.0},
        }
        paths = {"/Fresh", "/Recent", "/Missing"}
        self.assertEqual(max_cached_path_age(values, "svc", paths, 100.0), 10.0)
        self.assertEqual(max_cached_path_age(values, "missing", paths, 100.0), 0.0)
        self.assertEqual(max_cached_path_age(values, "svc", set(), 100.0), 0.0)
        self.assertEqual(missing_cached_path_count(values, "svc", paths), 1.0)
        self.assertEqual(missing_cached_path_count(values, "missing", paths), 3.0)
        self.assertEqual(missing_cached_path_count(values, "svc", set()), 0.0)

    def test_cached_entry_float_requires_mapping_and_normalizes_value(self) -> None:
        self.assertEqual(cached_entry_float({"value": "12.5"}), 12.5)
        self.assertEqual(cached_entry_float({"value": -3}), -3.0)
        for entry in (None, object(), [], {}, {"value": object()}):
            with self.subTest(entry=entry):
                self.assertEqual(cached_entry_float(entry), 0.0)

    def test_cache_freshness_combines_count_status_and_important_fields(self) -> None:
        cache = DbusCacheStore(stale_after_seconds=10.0)
        cache.update_value("grid_power_w", 123.0, source="grid", status="fresh", now=99.0)
        cache.update_value("pv_power_w", 45.0, source="pv", status="cached", now=98.0)
        cache.mark_error("pv_power_w", source="pv", error="no reply", now=100.0)

        self.assertEqual(
            cache_freshness(cache, 100.0),
            {
                "value_count": 2,
                "status_counts": {"fresh": 1, "error": 1},
                "all_status_counts": {"fresh": 1, "error": 1},
                "external_read_status_counts": {"fresh": 1, "error": 1},
                "local_publish_status_counts": {},
                "static_status_counts": {},
                "diagnostic_status_counts": {},
                "critical_stale_count": 0,
                "optional_source_error_count": 0,
                "optional_source_unavailable_count": 0,
                "grid_power_w_age_s": 1.0,
                "pv_power_w_age_s": 2.0,
                "battery_soc_age_s": 0.0,
                "grid_power_w_status": "fresh",
                "pv_power_w_status": "error",
                "battery_soc_status": "missing",
            },
        )

    def test_freshness_groups_separate_critical_local_static_diagnostic_and_source_errors(self) -> None:
        cache = DbusCacheStore(stale_after_seconds=5.0)
        cache.set_local_service_registered(True, service_name="com.victronenergy.evcharger.test")
        cache.update_value("grid_power_w", 10.0, source="grid", now=90.0, freshness_kind="external_read")
        cache.update_value(
            dbus_path_key("com.victronenergy.pvinverter.a", "/Ac/Power"),
            0.0,
            source="com.victronenergy.pvinverter.a/Ac/Power",
            now=99.0,
            freshness_kind="external_read",
        )
        cache.mark_unavailable(
            dbus_path_key("com.victronenergy.pvinverter.b", "/Ac/Power"),
            source="com.victronenergy.pvinverter.b/Ac/Power",
            error="sleeping",
            retry_after_seconds=300.0,
            now=100.0,
        )
        cache.update_value(
            dbus_path_key("com.victronenergy.evcharger.test", "/Mode"),
            2,
            source="com.victronenergy.evcharger.test/Mode",
            now=1.0,
            freshness_kind="local_owned",
        )
        cache.update_value(
            dbus_path_key("com.victronenergy.evcharger.test", "/ProductName"),
            "EVCS",
            source="com.victronenergy.evcharger.test/ProductName",
            now=1.0,
            freshness_kind="static",
        )
        cache.update_value(
            "introspection:svc:/",
            "<node/>",
            source="svc/",
            now=1.0,
            freshness_kind="diagnostic",
        )

        snapshot = cache_freshness(cache, 100.0)
        self.assertEqual(snapshot["status_counts"], {"stale": 1})
        self.assertEqual(snapshot["critical_stale_count"], 1)
        self.assertEqual(snapshot["optional_source_error_count"], 0)
        self.assertEqual(snapshot["optional_source_unavailable_count"], 1)
        self.assertEqual(snapshot["external_read_status_counts"], {"stale": 1, "fresh": 1, "unavailable": 1})
        self.assertEqual(snapshot["local_publish_status_counts"], {"fresh": 2})
        self.assertEqual(snapshot["static_status_counts"], {"fresh": 1})
        self.assertEqual(snapshot["diagnostic_status_counts"], {"fresh": 1})

    def test_freshness_group_helpers_use_explicit_metadata(self) -> None:
        values = {
            "read": {"freshness_kind": "external_read", "status": "error"},
            "legacy": {"status": "fresh"},
            "local": {"freshness_kind": "local_owned", "status": "fresh"},
        }
        external = values_for_kinds(values, {"external_read"})
        self.assertEqual(set(external), {"read", "legacy"})
        self.assertEqual(optional_source_error_count(external), 1)
        self.assertEqual(optional_source_unavailable_count(external), 0)

    def test_status_helpers_distinguish_critical_and_optional_entries_exactly(self) -> None:
        values = {
            "grid_power_w": {"status": "error"},
            "pv_power_w": {"status": "error"},
            "battery_soc": {"status": "stale"},
            "optional-error": {"status": "error"},
            "optional-fresh": {"status": "fresh"},
            "optional-unknown": {},
            "optional-unavailable": {"status": "unavailable"},
        }
        self.assertEqual(count_status(values, "error"), 3)
        self.assertEqual(count_status(values, "stale"), 1)
        self.assertEqual(count_status(values, "fresh"), 1)
        self.assertEqual(count_status(values, "unknown"), 1)
        self.assertEqual(count_status(values, "missing"), 0)
        self.assertEqual(optional_source_error_count(values), 1)
        self.assertEqual(optional_source_unavailable_count(values), 1)

    def test_cache_separates_value_change_from_successful_confirmation(self) -> None:
        cache = DbusCacheStore(stale_after_seconds=10.0)
        cache.update_value("grid_power_w", 100.0, source="grid", now=10.0, stale_after_seconds=3.0)
        cache.update_value("grid_power_w", 100.0, source="grid", now=12.0, stale_after_seconds=3.0)

        confirmed = cache.snapshot(now=14.0)["values"]["grid_power_w"]
        self.assertEqual(confirmed["changed_at"], 10.0)
        self.assertEqual(confirmed["confirmed_at"], 12.0)
        self.assertEqual(confirmed["updated_at"], 12.0)
        self.assertEqual(confirmed["age_s"], 2.0)
        self.assertEqual(confirmed["change_age_s"], 4.0)
        self.assertEqual(confirmed["status"], "fresh")
        self.assertEqual(cache.snapshot(now=16.0)["values"]["grid_power_w"]["status"], "stale")

        cache.update_value("grid_power_w", 110.0, source="grid", now=17.0, stale_after_seconds=3.0)
        changed = cache.snapshot(now=17.5)["values"]["grid_power_w"]
        self.assertEqual(changed["changed_at"], 17.0)
        self.assertEqual(changed["confirmed_at"], 17.0)

    def test_cache_metadata_contract_preserves_typed_freshness_fields(self) -> None:
        base = CacheValueMetadata(
            source="old",
            status="cached",
            confidence=0.4,
            last_error="old-error",
            now=7.0,
            freshness_kind="static",
            stale_after_seconds=9.0,
        )
        self.assertIs(dbus_gateway_cache._cache_value_metadata(base, {}), base)
        self.assertEqual(
            dbus_gateway_cache._cache_value_metadata(
                base,
                {
                    "source": "new",
                    "status": "fresh",
                    "confidence": "0.8",
                    "last_error": "",
                    "now": "8.5",
                    "freshness_kind": "diagnostic",
                    "source_state": "unavailable",
                    "stale_after_seconds": "4.5",
                },
            ),
            CacheValueMetadata(
                source="new",
                status="fresh",
                confidence=0.8,
                last_error="",
                now=8.5,
                freshness_kind="diagnostic",
                source_state="unavailable",
                stale_after_seconds=4.5,
            ),
        )
        self.assertEqual(
            dbus_gateway_cache._cache_value_metadata(
                base,
                {"freshness_kind": "invalid", "stale_after_seconds": object()},
            ),
            base,
        )
        self.assertEqual(
            dbus_gateway_cache._cache_value_metadata(
                None,
                {
                    "source": "field",
                    "freshness_kind": "local_owned",
                    "source_state": "error",
                    "stale_after_seconds": -2,
                },
            ),
            CacheValueMetadata(
                source="field",
                freshness_kind="local_owned",
                source_state="error",
                stale_after_seconds=0.0,
            ),
        )
        kinds: tuple[CacheFreshnessKind, ...] = (
            "external_read",
            "local_owned",
            "static",
            "diagnostic",
        )
        for kind in kinds:
            with self.subTest(kind=kind):
                self.assertEqual(
                    dbus_gateway_cache._metadata_freshness_kind(kind, "external_read"),
                    kind,
                )
        self.assertEqual(
            dbus_gateway_cache._metadata_freshness_kind(None, "static"),
            "static",
        )
        self.assertEqual(
            dbus_gateway_cache._metadata_freshness_kind("invalid", "diagnostic"),
            "diagnostic",
        )
        self.assertEqual(dbus_gateway_cache._metadata_source_state("active", "error"), "active")
        self.assertEqual(dbus_gateway_cache._metadata_source_state("unavailable", "active"), "unavailable")
        self.assertEqual(dbus_gateway_cache._metadata_source_state("error", "active"), "error")
        self.assertEqual(dbus_gateway_cache._metadata_source_state("invalid", "active"), "active")
        self.assertIsNone(dbus_gateway_cache._metadata_optional_float(None))
        self.assertEqual(dbus_gateway_cache._metadata_optional_float(None, 3.0), 3.0)
        self.assertEqual(dbus_gateway_cache._metadata_optional_float(-2), 0.0)
        self.assertEqual(dbus_gateway_cache._metadata_optional_float("2.5"), 2.5)
        self.assertEqual(dbus_gateway_cache._metadata_optional_float(object()), 0.0)
        self.assertEqual(dbus_gateway_cache._metadata_optional_float(object(), 3.0), 3.0)

    def test_cache_value_lifecycle_metadata_is_exact(self) -> None:
        cache = DbusCacheStore(stale_after_seconds=-1.0)
        self.assertIs(cache.local_service_registered, False)
        self.assertEqual(cache.local_service_name, "")
        self.assertEqual(cache.stale_after_seconds, 0.0)

        with patch.object(cache, "update_value") as update_value:
            cache.update_external_read("external", 3.0, source="svc/Power", stale_after_seconds=4.0)
        update_value.assert_called_once_with(
            "external",
            3.0,
            freshness_kind="external_read",
            source="svc/Power",
            stale_after_seconds=4.0,
        )

        cache.update_value(
            "key",
            4,
            source="svc/Path",
            now=10.0,
            freshness_kind="external_read",
            stale_after_seconds=5.0,
        )
        self.assertEqual(
            cache.values["key"],
            {
                "value": 4,
                "source": "svc/Path",
                "changed_at": 10.0,
                "confirmed_at": 10.0,
                "updated_at": 10.0,
                "age_s": 0.0,
                "status": "fresh",
                "last_error": "",
                "confidence": 1.0,
                "freshness_kind": "external_read",
                "source_state": "active",
                "stale_after_s": 5.0,
            },
        )
        cache.update_value(
            "key",
            4,
            source="svc/Path",
            now=12.0,
            freshness_kind="external_read",
            stale_after_seconds=5.0,
        )
        cache.mark_error("key", source="svc/Path", error="offline", now=14.0)
        self.assertEqual(
            cache.values["key"],
            {
                "value": 4,
                "source": "svc/Path",
                "changed_at": 10.0,
                "confirmed_at": 12.0,
                "updated_at": 12.0,
                "error_at": 14.0,
                "age_s": 2.0,
                "status": "error",
                "last_error": "offline",
                "confidence": 0.0,
                "freshness_kind": "external_read",
                "source_state": "error",
                "stale_after_s": 5.0,
            },
        )
        self.assertEqual(
            cache.value_snapshot(cache.values["key"], 15.0),
            {
                **cache.values["key"],
                "age_s": 3.0,
                "change_age_s": 5.0,
                "status": "error",
            },
        )

        cache.mark_error(
            "missing",
            source="svc/Missing",
            error=RuntimeError("missing"),
            now=20.0,
            freshness_kind="diagnostic",
        )
        self.assertEqual(cache.values["missing"]["freshness_kind"], "diagnostic")
        self.assertEqual(cache.values["missing"]["changed_at"], 0.0)
        self.assertEqual(cache.values["missing"]["confirmed_at"], 0.0)

        cache.update_value(
            "diagnostic",
            "ok",
            source="svc/Diagnostics",
            now=21.0,
            freshness_kind="diagnostic",
        )
        cache.mark_error(
            "diagnostic",
            source="svc/Diagnostics",
            error="failed",
            now=22.0,
        )
        self.assertEqual(cache.values["diagnostic"]["freshness_kind"], "diagnostic")

        sequence = cache.sequence
        cache.set_local_service_registered(True, service_name="svc")
        self.assertEqual(cache.sequence, sequence + 1)
        self.assertIs(cache.local_service_registered, True)
        self.assertEqual(cache.local_service_name, "svc")

        zero_timestamp = DbusCacheStore()
        zero_timestamp.update_value("zero", 1, source="svc/Zero", now=0.0)
        zero_timestamp.update_value("zero", 1, source="svc/Zero", now=2.0)
        self.assertEqual(zero_timestamp.values["zero"]["changed_at"], 2.0)

        boundary_timestamp = DbusCacheStore()
        boundary_timestamp.update_value("one", 1, source="svc/One", now=1.0)
        boundary_timestamp.update_value("one", 1, source="svc/One", now=2.0)
        self.assertEqual(boundary_timestamp.values["one"]["changed_at"], 1.0)

    def test_unavailable_source_contract_retains_value_and_schedules_retry(self) -> None:
        cache = DbusCacheStore(stale_after_seconds=10.0)
        cache.update_value(
            "pv-member",
            125.0,
            source="com.victronenergy.pvinverter.test/Ac/Power",
            now=10.0,
            freshness_kind="external_read",
            stale_after_seconds=30.0,
        )
        initial_sequence = cache.sequence

        cache.mark_unavailable(
            "pv-member",
            source="com.victronenergy.pvinverter.test/Ac/Power",
            error=RuntimeError("NoReply"),
            retry_after_seconds=300.0,
            now=20.0,
        )

        self.assertEqual(cache.sequence, initial_sequence + 1)
        self.assertEqual(
            cache.values["pv-member"],
            {
                "value": 125.0,
                "source": "com.victronenergy.pvinverter.test/Ac/Power",
                "changed_at": 10.0,
                "confirmed_at": 10.0,
                "updated_at": 10.0,
                "error_at": 20.0,
                "age_s": 10.0,
                "status": "unavailable",
                "last_error": "NoReply",
                "confidence": 0.0,
                "freshness_kind": "external_read",
                "source_state": "unavailable",
                "stale_after_s": 30.0,
                "next_probe_at": 320.0,
            },
        )

        cache.mark_unavailable(
            "missing-member",
            source="com.victronenergy.pvinverter.missing/Ac/Power",
            error="sleeping",
            retry_after_seconds=-5.0,
            now=25.0,
        )
        self.assertEqual(
            cache.values["missing-member"],
            {
                "value": None,
                "source": "com.victronenergy.pvinverter.missing/Ac/Power",
                "changed_at": 0.0,
                "confirmed_at": 0.0,
                "updated_at": 0.0,
                "error_at": 25.0,
                "age_s": 25.0,
                "status": "unavailable",
                "last_error": "sleeping",
                "confidence": 0.0,
                "freshness_kind": "external_read",
                "source_state": "unavailable",
                "stale_after_s": None,
                "next_probe_at": 25.0,
            },
        )

    def test_local_cache_value_classifier_is_bound_to_the_owned_service(self) -> None:
        service = "com.victronenergy.evcharger.test"
        self.assertFalse(dbus_gateway_cache._is_local_cache_value({}, "local_owned", ""))
        self.assertTrue(dbus_gateway_cache._is_local_cache_value({}, "local_owned", service))
        self.assertTrue(dbus_gateway_cache._is_local_cache_value({}, "static", service))
        self.assertTrue(
            dbus_gateway_cache._is_local_cache_value(
                {"source": f"{service}/Auto/State"},
                "diagnostic",
                service,
            )
        )
        self.assertFalse(
            dbus_gateway_cache._is_local_cache_value(
                {"source": "com.victronenergy.system/State"},
                "diagnostic",
                service,
            )
        )
        self.assertFalse(
            dbus_gateway_cache._is_local_cache_value(
                {"source": 42},
                "diagnostic",
                service,
            )
        )
        self.assertFalse(
            dbus_gateway_cache._is_local_cache_value(
                {},
                "diagnostic",
                service,
            )
        )
        self.assertFalse(
            dbus_gateway_cache._is_local_cache_value(
                {"source": f"{service}/Mode"},
                "external_read",
                service,
            )
        )

    def test_owned_static_and_diagnostic_values_follow_their_own_liveness_policies(self) -> None:
        cache = DbusCacheStore(stale_after_seconds=1.0)
        service = "com.victronenergy.evcharger.test"
        cache.set_local_service_registered(True, service_name=service)
        registered_sequence = cache.sequence
        cache.set_local_service_registered(True, service_name=service)
        self.assertEqual(cache.sequence, registered_sequence)
        cache.update_value(
            "owned",
            1,
            source=f"{service}/Mode",
            now=1.0,
            freshness_kind="local_owned",
        )
        cache.update_value(
            "static",
            "EVCS",
            source=f"{service}/ProductName",
            now=1.0,
            freshness_kind="static",
        )
        cache.update_value(
            "local-diagnostic",
            "idle",
            source=f"{service}/Auto/State",
            now=1.0,
            freshness_kind="diagnostic",
        )
        cache.update_value(
            "external-diagnostic",
            "<node/>",
            source="com.victronenergy.system/",
            now=1.0,
            freshness_kind="diagnostic",
        )
        cache.update_value(
            "owned-error",
            None,
            source=f"{service}/Mode",
            status="error",
            now=1.0,
            freshness_kind="local_owned",
        )

        registered = cache.snapshot(now=100.0)["values"]
        self.assertEqual(registered["owned-error"]["status"], "error")
        self.assertEqual(
            {registered[key]["status"] for key in registered if key != "owned-error"},
            {"fresh"},
        )
        cache.set_local_service_registered(False, service_name=service)
        unavailable = cache.snapshot(now=101.0)["values"]
        self.assertEqual(unavailable["owned"]["status"], "unavailable")
        self.assertEqual(unavailable["static"]["status"], "unavailable")
        self.assertEqual(unavailable["local-diagnostic"]["status"], "unavailable")
        self.assertEqual(unavailable["external-diagnostic"]["status"], "fresh")
        self.assertEqual(unavailable["owned-error"]["status"], "error")

        legacy_external = {
            "source": "grid",
            "confirmed_at": 1.0,
            "changed_at": 1.0,
            "status": "fresh",
            "stale_after_s": 1.0,
        }
        self.assertEqual(cache.value_snapshot(legacy_external, 10.0)["status"], "stale")

    def test_owned_path_classification_is_gateway_surface_knowledge(self) -> None:
        self.assertEqual(evcs_path_freshness_kind("/ProductName"), "static")
        self.assertEqual(evcs_path_freshness_kind("/Auto/State"), "diagnostic")
        self.assertEqual(evcs_path_freshness_kind("/Mode"), "local_owned")


if __name__ == "__main__":
    unittest.main()
