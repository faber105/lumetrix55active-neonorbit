import unittest

from backend.services.session_transitions import loss_transition


def _session(max_martingale=3, max_failed_series=1, mode="count"):
    return {
        "mode": mode,
        "max_martingale": max_martingale,
        "max_failed_series": max_failed_series,
    }


class LossTransitionTests(unittest.TestCase):
    def test_loss_advances_each_cover_without_stopping_early(self):
        session = _session()
        series_loss = 0

        for current_level, amount in enumerate((1.0, 2.09, 4.36)):
            state = loss_transition(
                session,
                amount=amount,
                failed=0,
                level=current_level,
                series_loss=series_loss,
            )
            self.assertEqual(state["status"], "ACTIVE")
            self.assertEqual(state["stage"], "SCANNING")
            self.assertEqual(state["level"], current_level + 1)
            series_loss = state["series_loss"]

    def test_count_mode_continues_after_full_lost_chain(self):
        state = loss_transition(
            _session(),
            amount=9.10,
            failed=0,
            level=3,
            series_loss=7.45,
        )

        self.assertEqual(state["status"], "ACTIVE")
        self.assertEqual(state["stage"], "SCANNING")
        self.assertIsNone(state["reason"])
        self.assertEqual(state["level"], 0)
        self.assertEqual(state["series_loss"], 0)
        self.assertEqual(state["failed"], 1)

    def test_zero_cover_count_session_continues_after_loss(self):
        state = loss_transition(
            _session(max_martingale=0),
            amount=1.0,
            failed=0,
            level=0,
            series_loss=0,
        )

        self.assertEqual(state["status"], "ACTIVE")
        self.assertEqual(state["stage"], "SCANNING")
        self.assertEqual(state["level"], 0)

    def test_profit_mode_stops_at_failed_series_limit(self):
        state = loss_transition(
            _session(mode="profit"),
            amount=9.10,
            failed=0,
            level=3,
            series_loss=7.45,
        )

        self.assertEqual(state["status"], "STOPPED")
        self.assertEqual(state["stage"], "STOPPED")
        self.assertEqual(state["reason"], "MAX_FAILED_SERIES")

    def test_profit_mode_can_continue_until_failed_series_limit(self):
        session = _session(mode="profit", max_failed_series=2)
        state = loss_transition(
            session,
            amount=9.10,
            failed=0,
            level=3,
            series_loss=7.45,
        )

        self.assertEqual(state["status"], "ACTIVE")
        self.assertEqual(state["stage"], "SCANNING")
        self.assertEqual(state["failed"], 1)


if __name__ == "__main__":
    unittest.main()
