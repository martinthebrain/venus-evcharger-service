# SPDX-License-Identifier: GPL-3.0-or-later
"""Boundary contracts for scheduled-mode calendar and time helpers."""

from __future__ import annotations

import configparser
import unittest
from datetime import date, datetime
from unittest.mock import patch

from venus_evcharger.core.common_schedule import (
    _ScheduledSnapshotContext,
    _append_unique_weekday,
    _datetime_for_minutes,
    _extend_weekday_range,
    _month_in_range,
    _normalized_weekday_candidates,
    _normalized_weekday_selection,
    _normalized_weekday_tokens,
    _scheduled_daytime_window_active,
    _scheduled_post_night_state,
    _scheduled_snapshot,
    _scheduled_snapshot_context,
    _scheduled_snapshot_text_fields,
    _scheduled_target_day,
    _special_scheduled_day_selection,
    _weekday_indices_for_token,
    _weekday_range_bounds,
    _weekday_range_values,
    _weekday_text_or_empty,
    _window_minutes_for_date,
    month_in_ranges,
    month_window,
    normalize_hhmm_text,
    normalize_scheduled_enabled_days,
    parse_hhmm,
    scheduled_enabled_days_text,
    scheduled_mode_snapshot,
    scheduled_night_window_active,
)


MONTH_WINDOWS = {
    4: ((8, 15), (18, 45)),
    5: ((9, 0), (17, 0)),
}


def context(
    *,
    target_date: date = date(2026, 4, 20),
    target_day_index: int = 0,
    target_enabled: bool = True,
    start_minutes: int = 8 * 60,
    end_minutes: int = 18 * 60,
    fallback_start: datetime = datetime(2026, 4, 19, 19, 0),
    night_boost_end: datetime = datetime(2026, 4, 20, 6, 30),
) -> _ScheduledSnapshotContext:
    return _ScheduledSnapshotContext(
        target_date=target_date,
        target_day_index=target_day_index,
        target_enabled=target_enabled,
        start_minutes=start_minutes,
        end_minutes=end_minutes,
        fallback_start=fallback_start,
        night_boost_end=night_boost_end,
    )


class TestCoreCommonScheduleContracts(unittest.TestCase):
    def test_window_minutes_contract(self) -> None:
        self.assertEqual(_window_minutes_for_date(date(2026, 4, 1), MONTH_WINDOWS), (495, 1125))
        self.assertEqual(_window_minutes_for_date(date(2026, 6, 1), MONTH_WINDOWS), (480, 1080))
        self.assertEqual(_window_minutes_for_date(date(2026, 6, 1), None), (480, 1080))

    def test_datetime_for_minutes_contract(self) -> None:
        self.assertEqual(_datetime_for_minutes(date(2026, 4, 20), 0), datetime(2026, 4, 20, 0, 0))
        self.assertEqual(_datetime_for_minutes(date(2026, 4, 20), 1439), datetime(2026, 4, 20, 23, 59))
        self.assertEqual(_datetime_for_minutes(date(2026, 4, 20), "510"), datetime(2026, 4, 20, 8, 30))

    def test_weekday_text_and_candidate_contract(self) -> None:
        self.assertEqual(_weekday_text_or_empty(None), "")
        self.assertEqual(_weekday_text_or_empty(" Mon "), "Mon")
        self.assertEqual(_normalized_weekday_candidates(None), [])
        self.assertEqual(
            _normalized_weekday_candidates(" Mon;Tue|Wed/Thu Fri,Sat "),
            ["mon", "tue", "wed", "thu", "fri", "sat"],
        )
        self.assertEqual(_normalized_weekday_tokens((" Mon ", "", 2)), ["mon", "2"])
        self.assertEqual(_normalized_weekday_tokens("sun"), ["sun"])
        self.assertEqual(_normalized_weekday_candidates("monXfri"), ["monxfri"])
        self.assertEqual(_normalized_weekday_candidates("mon\\fri"), ["mon", "fri"])

    def test_special_day_selection_contract(self) -> None:
        for token in ("all", "daily", "everyday", "*"):
            self.assertEqual(_special_scheduled_day_selection([token]), tuple(range(7)))
        for token in ("weekdays", "weekday", "workdays", "workday", "mon-fri", "mo-fr"):
            self.assertEqual(_special_scheduled_day_selection([token]), (0, 1, 2, 3, 4))
        for token in ("weekend", "weekends", "sat-sun", "sa-su"):
            self.assertEqual(_special_scheduled_day_selection([token]), (5, 6))
        self.assertIsNone(_special_scheduled_day_selection([]))
        self.assertIsNone(_special_scheduled_day_selection(["mon", "tue"]))
        self.assertIsNone(_special_scheduled_day_selection(["unknown"]))

    def test_weekday_range_contract(self) -> None:
        self.assertEqual(_weekday_range_bounds("mon-fri"), (0, 4))
        self.assertEqual(_weekday_range_bounds(" fri - mon "), (4, 0))
        self.assertIsNone(_weekday_range_bounds("mon"))
        self.assertIsNone(_weekday_range_bounds("bad-fri"))
        self.assertIsNone(_weekday_range_bounds("mon-bad"))
        self.assertIsNone(_weekday_range_bounds("mon-tue-wed"))
        self.assertEqual(_weekday_range_values(0, 0), [0])
        self.assertEqual(_weekday_range_values(0, 4), [0, 1, 2, 3, 4])
        self.assertEqual(_weekday_range_values(4, 1), [4, 5, 6, 0, 1])

    def test_weekday_collection_contract(self) -> None:
        target = [1]
        _append_unique_weekday(target, 1)
        _append_unique_weekday(target, 2)
        self.assertEqual(target, [1, 2])
        _extend_weekday_range(target, "fri-mon")
        self.assertEqual(target, [1, 2, 4, 5, 6, 0])
        _extend_weekday_range(target, "invalid")
        self.assertEqual(target, [1, 2, 4, 5, 6, 0])
        self.assertEqual(_weekday_indices_for_token("wed"), [2])
        self.assertEqual(_weekday_indices_for_token("fri-mon"), [4, 5, 6, 0])
        self.assertEqual(_weekday_indices_for_token("invalid"), [])

    def test_enabled_day_normalization_contract(self) -> None:
        self.assertEqual(_normalized_weekday_selection(["mon", "wed", "mon"], (6,)), (0, 2))
        self.assertEqual(_normalized_weekday_selection(["bad"], (6,)), (6,))
        self.assertEqual(normalize_scheduled_enabled_days(None, (1, 2)), (1, 2))
        self.assertEqual(normalize_scheduled_enabled_days("weekend"), (5, 6))
        self.assertEqual(normalize_scheduled_enabled_days("fri-mon"), (4, 5, 6, 0))
        self.assertEqual(scheduled_enabled_days_text("fri-mon"), "Fri,Sat,Sun,Mon")
        self.assertEqual(scheduled_enabled_days_text(None, (1, 3)), "Tue,Thu")

    def test_parse_hhmm_contract(self) -> None:
        fallback = (6, 30)
        for value, expected in (
            ("00:00", (0, 0)),
            ("23:59", (23, 59)),
            (" 7:05 ", (7, 5)),
            ("24:00", fallback),
            ("23:60", fallback),
            ("-1:30", fallback),
            ("bad", fallback),
            ("1:2:3", fallback),
            (None, fallback),
        ):
            with self.subTest(value=value):
                self.assertEqual(parse_hhmm(value, fallback), expected)

        class InvalidTimeText:
            def __str__(self) -> str:
                raise ValueError("invalid time text")

        self.assertEqual(parse_hhmm(InvalidTimeText(), fallback), fallback)
        self.assertEqual(normalize_hhmm_text("7:05"), "07:05")
        self.assertEqual(normalize_hhmm_text("bad", "05:45"), "05:45")
        self.assertEqual(normalize_hhmm_text("bad", "bad"), "06:30")
        self.assertEqual(normalize_hhmm_text("bad"), "06:30")

    def test_scheduled_target_day_contract(self) -> None:
        self.assertEqual(_scheduled_target_day(datetime(2026, 4, 20, 18, 44), MONTH_WINDOWS), date(2026, 4, 20))
        self.assertEqual(_scheduled_target_day(datetime(2026, 4, 20, 18, 45), MONTH_WINDOWS), date(2026, 4, 21))
        overnight = {4: ((20, 0), (6, 0))}
        self.assertEqual(_scheduled_target_day(datetime(2026, 4, 20, 21, 0), overnight), date(2026, 4, 20))
        equal_window = {4: ((8, 0), (8, 0))}
        self.assertEqual(_scheduled_target_day(datetime(2026, 4, 20, 9, 0), equal_window), date(2026, 4, 20))

    def test_snapshot_context_contract(self) -> None:
        target = date(2026, 4, 20)
        calls: list[tuple[datetime, object]] = []

        def target_day(when: datetime, windows: object) -> date:
            calls.append((when, windows))
            return target

        request_time = datetime(2026, 4, 19, 20, 0)
        ctx = _scheduled_snapshot_context(
            request_time,
            MONTH_WINDOWS,
            "mon-fri",
            -5.0,
            "20:00",
            target_day_func=target_day,
        )
        self.assertEqual(calls, [(request_time, MONTH_WINDOWS)])
        self.assertEqual(ctx.target_date, target)
        self.assertEqual(ctx.target_day_index, 0)
        self.assertTrue(ctx.target_enabled)
        self.assertEqual((ctx.start_minutes, ctx.end_minutes), (495, 1125))
        self.assertEqual(ctx.fallback_start, datetime(2026, 4, 19, 18, 45))
        self.assertEqual(ctx.night_boost_end, datetime(2026, 4, 20, 8, 15))

        delayed = _scheduled_snapshot_context(
            datetime(2026, 4, 19, 20, 0),
            MONTH_WINDOWS,
            "weekend",
            3600.0,
            "06:30",
            target_day_func=lambda _when, _windows: target,
        )
        self.assertFalse(delayed.target_enabled)
        self.assertEqual(delayed.fallback_start, datetime(2026, 4, 19, 19, 45))
        self.assertEqual(delayed.night_boost_end, datetime(2026, 4, 20, 6, 30))
        invalid_end = _scheduled_snapshot_context(
            request_time,
            MONTH_WINDOWS,
            "mon-fri",
            0.0,
            "invalid",
            target_day_func=lambda _when, _windows: target,
        )
        self.assertEqual(invalid_end.night_boost_end, datetime(2026, 4, 20, 6, 30))

    def test_snapshot_render_contract(self) -> None:
        ctx = context()
        self.assertEqual(
            _scheduled_snapshot_text_fields(ctx),
            ("Mon", "2026-04-20", "2026-04-19 19:00"),
        )
        snapshot = _scheduled_snapshot(ctx, "night-boost", "night-boost-window", target_day_enabled=True)
        self.assertEqual(snapshot.state_code, 4)
        self.assertEqual(snapshot.reason_code, 4)
        self.assertTrue(snapshot.night_boost_active)
        self.assertEqual(snapshot.target_day_index, 0)
        self.assertEqual(snapshot.target_day_label, "Mon")
        self.assertEqual(snapshot.target_date_text, "2026-04-20")
        self.assertTrue(snapshot.target_day_enabled)
        self.assertEqual(snapshot.fallback_start_text, "2026-04-19 19:00")
        self.assertEqual(snapshot.boost_until_text, "2026-04-20 06:30")
        self.assertFalse(_scheduled_snapshot(ctx, "auto-window", "daytime-auto", target_day_enabled=False).night_boost_active)

    def test_daytime_window_boundaries(self) -> None:
        ctx = context()
        self.assertFalse(_scheduled_daytime_window_active(datetime(2026, 4, 20, 7, 59), ctx))
        self.assertTrue(_scheduled_daytime_window_active(datetime(2026, 4, 20, 8, 0), ctx))
        self.assertTrue(_scheduled_daytime_window_active(datetime(2026, 4, 20, 8, 15), ctx))
        self.assertTrue(_scheduled_daytime_window_active(datetime(2026, 4, 20, 17, 59), ctx))
        self.assertFalse(_scheduled_daytime_window_active(datetime(2026, 4, 20, 18, 0), ctx))
        self.assertFalse(_scheduled_daytime_window_active(datetime(2026, 4, 19, 12, 0), ctx))
        self.assertFalse(
            _scheduled_daytime_window_active(
                datetime(2026, 4, 20, 12, 0), context(start_minutes=1200, end_minutes=360)
            )
        )
        self.assertFalse(
            _scheduled_daytime_window_active(
                datetime(2026, 4, 20, 12, 0), context(start_minutes=480, end_minutes=480)
            )
        )

    def test_post_night_state_boundaries(self) -> None:
        ctx = context()
        self.assertEqual(
            _scheduled_post_night_state(datetime(2026, 4, 19, 18, 59), ctx),
            ("waiting-fallback", "waiting-fallback-delay"),
        )
        self.assertEqual(
            _scheduled_post_night_state(datetime(2026, 4, 19, 19, 0), ctx),
            ("night-boost", "night-boost-window"),
        )
        self.assertEqual(
            _scheduled_post_night_state(datetime(2026, 4, 20, 8, 0), ctx),
            ("auto-window", "daytime-auto"),
        )
        self.assertEqual(
            _scheduled_post_night_state(datetime(2026, 4, 20, 6, 30), ctx),
            ("after-latest-end", "latest-end-reached"),
        )
        minute_sensitive = context(start_minutes=500, night_boost_end=datetime(2026, 4, 20, 6, 30))
        self.assertEqual(
            _scheduled_post_night_state(datetime(2026, 4, 20, 8, 15), minute_sensitive),
            ("after-latest-end", "latest-end-reached"),
        )
        minute_sensitive = context(start_minutes=490, night_boost_end=datetime(2026, 4, 20, 6, 30))
        self.assertEqual(
            _scheduled_post_night_state(datetime(2026, 4, 20, 8, 15), minute_sensitive),
            ("auto-window", "daytime-auto"),
        )
        equal_window = context(start_minutes=480, end_minutes=480)
        self.assertEqual(
            _scheduled_post_night_state(datetime(2026, 4, 20, 12, 0), equal_window),
            ("after-latest-end", "latest-end-reached"),
        )

    def test_scheduled_mode_snapshot_states(self) -> None:
        windows = {4: ((8, 0), (18, 0))}
        daytime = scheduled_mode_snapshot(datetime(2026, 4, 20, 12, 0), windows, "mon-fri")
        self.assertEqual((daytime.state, daytime.reason), ("auto-window", "daytime-auto"))
        self.assertIs(daytime.target_day_enabled, True)
        inactive = scheduled_mode_snapshot(datetime(2026, 4, 19, 7, 0), windows, "mon-fri")
        self.assertEqual((inactive.state, inactive.reason), ("inactive-day", "target-day-disabled"))
        self.assertIs(inactive.target_day_enabled, False)
        waiting = scheduled_mode_snapshot(datetime(2026, 4, 19, 18, 30), windows, "all", delay_seconds=3600)
        self.assertEqual(waiting.state, "waiting-fallback")
        boost = scheduled_mode_snapshot(datetime(2026, 4, 19, 19, 0), windows, "all", delay_seconds=3600)
        self.assertEqual(boost.state, "night-boost")
        ended = scheduled_mode_snapshot(datetime(2026, 4, 20, 6, 30), windows, "all", delay_seconds=3600)
        self.assertEqual(ended.state, "after-latest-end")
        self.assertIs(ended.target_day_enabled, True)

    def test_scheduled_mode_snapshot_delegation_contract(self) -> None:
        when = datetime(2026, 4, 20, 7, 0)
        windows = {4: ((8, 0), (18, 0))}
        target_func = lambda _when, _windows: date(2026, 4, 20)
        ctx = context()
        with patch(
            "venus_evcharger.core.common_schedule._scheduled_snapshot_context",
            return_value=ctx,
        ) as build_context, patch(
            "venus_evcharger.core.common_schedule._scheduled_daytime_window_active",
            return_value=False,
        ), patch(
            "venus_evcharger.core.common_schedule._scheduled_post_night_state",
            return_value=("night-boost", "night-boost-window"),
        ):
            snapshot = scheduled_mode_snapshot(when, windows, "mon-fri", 123.0, "05:45", target_func)
        build_context.assert_called_once_with(when, windows, "mon-fri", 123.0, "05:45", target_func)
        self.assertEqual(snapshot.state, "night-boost")

        default_snapshot = scheduled_mode_snapshot(datetime(2026, 4, 19, 19, 0), windows, "all")
        self.assertEqual(default_snapshot.state, "night-boost")
        self.assertEqual(default_snapshot.fallback_start_text, "2026-04-19 19:00")
        self.assertEqual(default_snapshot.boost_until_text, "2026-04-20 06:30")

    def test_night_window_contract(self) -> None:
        windows = {4: ((8, 0), (18, 0))}
        self.assertFalse(scheduled_night_window_active(datetime(2026, 4, 19, 18, 30), windows, 3600))
        self.assertTrue(scheduled_night_window_active(datetime(2026, 4, 19, 19, 0), windows, 3600))
        self.assertFalse(scheduled_night_window_active(datetime(2026, 4, 20, 8, 0), windows, 3600))

    def test_night_window_delegation_contract(self) -> None:
        when = datetime(2026, 4, 19, 20, 0)
        windows = {4: ((8, 0), (18, 0))}
        expected = scheduled_mode_snapshot(when, windows, "all", 3600.0, "23:59")
        with patch(
            "venus_evcharger.core.common_schedule.scheduled_mode_snapshot",
            return_value=expected,
        ) as snapshot:
            self.assertEqual(scheduled_night_window_active(when, windows, 123.0), expected.night_boost_active)
        snapshot.assert_called_once_with(
            when,
            windows,
            (0, 1, 2, 3, 4),
            delay_seconds=123.0,
            latest_end_time="23:59",
        )
        default_delay = scheduled_night_window_active(datetime(2026, 4, 19, 19, 0), windows)
        self.assertTrue(default_delay)

    def test_month_range_contract(self) -> None:
        self.assertTrue(_month_in_range(3, 1, 4))
        self.assertTrue(_month_in_range(1, 1, 4))
        self.assertTrue(_month_in_range(4, 1, 4))
        self.assertFalse(_month_in_range(5, 1, 4))
        self.assertTrue(_month_in_range(12, 11, 2))
        self.assertTrue(_month_in_range(2, 11, 2))
        self.assertFalse(_month_in_range(5, 11, 2))
        self.assertTrue(_month_in_range(3, 3, 3))
        self.assertFalse(_month_in_range(5, 3, 3))
        self.assertTrue(_month_in_range(11, 11, 2))
        self.assertTrue(month_in_ranges(7, [(1, 2), (6, 8)]))
        self.assertFalse(month_in_ranges(5, [(1, 2), (6, 8)]))
        self.assertFalse(month_in_ranges(5, []))

    def test_month_window_contract(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_dict({"DEFAULT": {"AutoJanStart": "07:15", "AutoJanEnd": "19:45"}})
        self.assertEqual(month_window(parser, 1, "08:00", "18:00"), ((7, 15), (19, 45)))
        self.assertEqual(month_window(parser, 2, "08:30", "17:30"), ((8, 30), (17, 30)))
        parser["DEFAULT"]["AutoJanStart"] = "bad"
        parser["DEFAULT"]["AutoJanEnd"] = "25:00"
        self.assertEqual(month_window(parser, 1, "08:00", "18:00"), ((8, 0), (18, 0)))
        empty = configparser.ConfigParser()
        self.assertEqual(month_window(empty, 1, "bad", "bad"), ((8, 0), (18, 0)))


if __name__ == "__main__":
    unittest.main()
