# SPDX-License-Identifier: GPL-3.0-or-later
"""Boundary contracts for the public common-module wrappers."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from unittest.mock import call, patch
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from venus_evcharger.core import common


class TestCoreCommonBoundaryContracts(unittest.TestCase):
    def test_local_datetime_uses_named_zone_and_returns_naive_wall_clock(self) -> None:
        timestamp = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc).timestamp()
        self.assertEqual(
            common.local_datetime_from_timestamp(timestamp, " Europe/Berlin "),
            datetime(2026, 7, 1, 14, 0),
        )
        self.assertEqual(
            common.local_datetime_from_timestamp(timestamp, ""),
            datetime(2026, 7, 1, 12, 0),
        )

    def test_default_falsy_and_whitespace_zones_resolve_directly_to_utc(self) -> None:
        timestamp = 100.0
        for invoke in (
            lambda: common.local_datetime_from_timestamp(timestamp),
            lambda: common.local_datetime_from_timestamp(timestamp, None),
            lambda: common.local_datetime_from_timestamp(timestamp, "   "),
        ):
            with self.subTest(invoke=invoke), patch.object(
                common,
                "ZoneInfo",
                return_value=ZoneInfo("UTC"),
            ) as zone:
                invoke()
            zone.assert_called_once_with("UTC")

    def test_unknown_zone_falls_back_to_utc_and_missing_database_uses_timezone_utc(self) -> None:
        timestamp = 100.0
        with patch.object(common, "ZoneInfo", side_effect=(ZoneInfoNotFoundError(), ZoneInfo("UTC"))) as zone:
            result = common.local_datetime_from_timestamp(timestamp, "Unknown/Zone")
        self.assertEqual(zone.call_args_list, [call("Unknown/Zone"), call("UTC")])
        self.assertEqual(result, datetime.fromtimestamp(timestamp, timezone.utc).replace(tzinfo=None))

        with patch.object(common, "ZoneInfo", side_effect=ZoneInfoNotFoundError()) as zone:
            result = common.local_datetime_from_timestamp(timestamp, "Unknown/Zone")
        self.assertEqual(zone.call_args_list, [call("Unknown/Zone"), call("UTC")])
        self.assertEqual(result, datetime.fromtimestamp(timestamp, timezone.utc).replace(tzinfo=None))

    def test_schedule_wrapper_forwards_explicit_arguments_and_target_function(self) -> None:
        when = datetime(2026, 7, 1, 12, 0)
        windows = {7: (("20:00", "23:00"), ("00:00", "06:00"))}

        def target(_when: datetime, _windows: object) -> date:
            return date(2026, 7, 1)

        with patch.object(
            common._common_schedule_module,
            "scheduled_mode_snapshot",
            return_value="snapshot",
        ) as scheduled:
            self.assertEqual(
                common.scheduled_mode_snapshot(
                    when,
                    windows,
                    (0, 2),
                    delay_seconds=123.0,
                    latest_end_time="05:45",
                    target_day_func=target,
                ),
                "snapshot",
            )
        scheduled.assert_called_once_with(
            when,
            windows,
            (0, 2),
            delay_seconds=123.0,
            latest_end_time="05:45",
            target_day_func=target,
        )

    def test_schedule_wrapper_defaults_are_part_of_the_public_contract(self) -> None:
        when = datetime(2026, 7, 1, 12, 0)
        with patch.object(
            common._common_schedule_module,
            "scheduled_mode_snapshot",
            return_value="snapshot",
        ) as scheduled:
            self.assertEqual(common.scheduled_mode_snapshot(when, None, "days"), "snapshot")
        scheduled.assert_called_once_with(
            when,
            None,
            "days",
            delay_seconds=3600.0,
            latest_end_time="06:30",
            target_day_func=common._scheduled_target_day,
        )


if __name__ == "__main__":
    unittest.main()
