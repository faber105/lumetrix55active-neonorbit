import unittest

from backend.services.session_transitions import loss_transition


def _session(max_martingale=3, max_failed_series=1):
    return {
        "mode": "count",
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
            self.assertEqual(state["stage"], "MARTINGALE")
            self.assertEqual(state["level"], current_level + 1)
            series_loss = state["series_loss"]

    def test_loss_on_last_cover_stops_count_session(self):
        state = loss_transition(
            _session(),
            amount=9.10,
            failed=0,
            level=3,
            series_loss=7.45,
        )

        self.assertEqual(state["status"], "STOPPED")
        self.assertEqual(state["stage"], "STOPPED")
        self.assertEqual(state["reason"], "FAILED_SERIES_LIMIT")
        self.assertEqual(state["level"], 0)
        self.assertEqual(state["series_loss"], 0)
        self.assertIn("3/3", state["message"])

    def test_zero_cover_session_stops_after_first_loss(self):
        state = loss_transition(
            _session(max_martingale=0),
            amount=1.0,
            failed=0,
            level=0,
            series_loss=0,
        )

        self.assertEqual(state["status"], "STOPPED")
        self.assertEqual(state["reason"], "FAILED_SERIES_LIMIT")

    def test_profit_mode_can_continue_until_failed_series_limit(self):
        session = {"mode": "profit", "max_martingale": 3, "max_failed_series": 2}
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
