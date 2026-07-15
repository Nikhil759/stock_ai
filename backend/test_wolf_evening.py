"""Unit tests for wolf_evening exit logic."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from wolf_evening import check_exit_reason


class TestCheckExitReason(unittest.TestCase):
    def test_target_hit(self):
        self.assertEqual(check_exit_reason(105.0, 100.0, 90.0), "target")

    def test_stop_hit(self):
        self.assertEqual(check_exit_reason(85.0, 100.0, 90.0), "stop_loss")

    def test_target_takes_priority_over_stop_when_both_true(self):
        self.assertEqual(check_exit_reason(110.0, 100.0, 110.0), "target")

    def test_no_exit_in_range(self):
        self.assertIsNone(check_exit_reason(95.0, 100.0, 90.0))

    def test_zero_targets_ignored(self):
        self.assertIsNone(check_exit_reason(50.0, 0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
