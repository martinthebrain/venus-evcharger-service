# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from venus_evcharger.backend.models import (
    effective_supported_phase_selections,
    normalize_phase_selection,
    normalize_phase_selection_or_none,
    normalize_phase_selection_tuple,
    phase_selection_count,
    phase_switch_lockout_active,
    switch_feedback_mismatch,
)


class TestBackendModelHelpers(unittest.TestCase):
    def test_phase_selection_count_maps_supported_layouts(self) -> None:
        self.assertEqual(phase_selection_count("P1"), 1)
        self.assertEqual(phase_selection_count("P1_P2"), 2)
        self.assertEqual(phase_selection_count("P1_P2_P3"), 3)
        self.assertEqual(phase_selection_count("3P"), 3)
        self.assertEqual(normalize_phase_selection("unknown", "P1_P2"), "P1_P2")
        self.assertEqual(normalize_phase_selection_tuple("", ("P1_P2",)), ("P1_P2",))

    def test_phase_selection_or_none_requires_explicit_supported_layout(self) -> None:
        self.assertEqual(normalize_phase_selection_or_none("L1"), "P1")
        self.assertEqual(normalize_phase_selection_or_none("2p"), "P1_P2")
        self.assertEqual(normalize_phase_selection_or_none("P1+P2+P3"), "P1_P2_P3")
        self.assertIsNone(normalize_phase_selection_or_none(""))
        self.assertIsNone(normalize_phase_selection_or_none(None))
        self.assertIsNone(normalize_phase_selection_or_none("unknown"))

    def test_phase_switch_lockout_active_rejects_missing_or_expired_values(self) -> None:
        self.assertFalse(phase_switch_lockout_active(None, 200.0, now=100.0))
        self.assertFalse(phase_switch_lockout_active("P1_P2", None, now=100.0))
        self.assertFalse(phase_switch_lockout_active("", 200.0, now=100.0))
        self.assertFalse(phase_switch_lockout_active("P1_P2", object(), now=100.0))
        self.assertFalse(phase_switch_lockout_active("P1_P2", "bad", now=100.0))
        self.assertFalse(phase_switch_lockout_active("P1_P2", 90.0, now=100.0))
        self.assertTrue(phase_switch_lockout_active("P1_P2", 120.0, now=100.0))

    def test_effective_supported_phase_selections_cap_layouts_at_active_lockout_target(self) -> None:
        supported = ("P1", "P1_P2", "P1_P2_P3")

        self.assertEqual(
            effective_supported_phase_selections(
                supported,
                lockout_selection="P1_P2",
                lockout_until=120.0,
                now=100.0,
            ),
            ("P1",),
        )
        self.assertEqual(
            effective_supported_phase_selections(
                supported,
                lockout_selection="P1_P2_P3",
                lockout_until=120.0,
                now=100.0,
            ),
            ("P1", "P1_P2"),
        )
        self.assertEqual(
            effective_supported_phase_selections(
                supported,
                lockout_selection="P1_P2_P3",
                lockout_until=90.0,
                now=100.0,
            ),
            supported,
        )

    def test_switch_feedback_mismatch_detects_explicit_disagreement_only(self) -> None:
        self.assertFalse(switch_feedback_mismatch(True, None))
        self.assertFalse(switch_feedback_mismatch(True, True))
        self.assertFalse(switch_feedback_mismatch(False, False))
        self.assertTrue(switch_feedback_mismatch(True, False))
        self.assertTrue(switch_feedback_mismatch(False, True))
