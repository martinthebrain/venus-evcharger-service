import unittest

from venus_evcharger.auto.logic_types import (
    RelayDecisionState,
    RelayDecisionTypeError,
    require_relay_bool,
    require_relay_decision,
)


class TestAutoLogicTypes(unittest.TestCase):
    def test_require_relay_bool_rejects_malformed_helper_output(self) -> None:
        with self.assertRaisesRegex(RelayDecisionTypeError, "test gate must be bool, got str"):
            require_relay_bool("yes", label="test gate")

    def test_require_relay_decision_rejects_malformed_helper_output(self) -> None:
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
