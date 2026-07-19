import unittest

from venus_evcharger.auto.logic_types import (
    RelayDecisionState,
    RelayDecisionTypeError,
    require_relay_bool,
    require_relay_decision,
)


class TestAutoLogicTypes(unittest.TestCase):
    def test_relay_decision_state_contracts_pending_and_resolved_values(self) -> None:
        pending = RelayDecisionState.pending()

        self.assertIsNone(pending.relay_on)
        self.assertTrue(pending.is_pending)
        with self.assertRaisesRegex(RelayDecisionTypeError, "pending relay decision has no resolved value"):
            pending.resolved_value()

        resolved_true = RelayDecisionState.resolved(True)
        resolved_false = RelayDecisionState.resolved(False)

        self.assertFalse(resolved_true.is_pending)
        self.assertFalse(resolved_false.is_pending)
        self.assertTrue(resolved_true.resolved_value())
        self.assertFalse(resolved_false.resolved_value())

    def test_require_relay_bool_accepts_plain_bool_values(self) -> None:
        self.assertIs(require_relay_bool(True), True)
        self.assertIs(require_relay_bool(False), False)

    def test_require_relay_bool_rejects_malformed_helper_output(self) -> None:
        with self.assertRaisesRegex(RelayDecisionTypeError, "relay decision must be bool, got str"):
            require_relay_bool("yes")
        with self.assertRaisesRegex(RelayDecisionTypeError, "test gate must be bool, got str"):
            require_relay_bool("yes", label="test gate")

    def test_require_relay_decision_accepts_plain_bool_values(self) -> None:
        self.assertIs(require_relay_decision(True), True)
        self.assertIs(require_relay_decision(False), False)

    def test_require_relay_decision_rejects_malformed_helper_output(self) -> None:
        with self.assertRaisesRegex(RelayDecisionTypeError, "relay decision must be bool or RelayDecisionState, got int"):
            require_relay_decision(1)
        with self.assertRaisesRegex(
            RelayDecisionTypeError,
            "test decision must be bool or RelayDecisionState, got int",
        ):
            require_relay_decision(1, label="test decision")

    def test_require_relay_decision_accepts_explicit_pending_state(self) -> None:
        pending = RelayDecisionState.pending()

        self.assertIs(require_relay_decision(pending), pending)


if __name__ == "__main__":
    unittest.main()
