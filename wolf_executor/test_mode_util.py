"""Tests for paper/live → executor mode mapping."""
from __future__ import annotations

import unittest

from wolf_executor.mode_util import executor_mode_from_wolf, normalize_wolf_mode


class TestModeUtil(unittest.TestCase):
    def test_normalize_wolf_mode(self):
        self.assertEqual(normalize_wolf_mode("paper"), "paper")
        self.assertEqual(normalize_wolf_mode("live"), "live")
        self.assertEqual(normalize_wolf_mode("LIVE"), "live")
        self.assertEqual(normalize_wolf_mode(None), "paper")
        self.assertEqual(normalize_wolf_mode("bogus"), "paper")

    def test_executor_mode_from_wolf(self):
        self.assertEqual(executor_mode_from_wolf("paper"), "paper")
        self.assertEqual(executor_mode_from_wolf("live"), "real")
        self.assertEqual(executor_mode_from_wolf(None), "paper")


if __name__ == "__main__":
    unittest.main()
