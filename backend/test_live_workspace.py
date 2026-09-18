"""Tests for live workspace allowlist."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _ROOT / "backend"
for p in (_ROOT, _BACKEND):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from live_workspace import (
    effective_execution_workspace,
    live_workspace_allowed,
    live_workspace_email,
)


class TestLiveWorkspace(unittest.TestCase):
    def test_default_allowlist_email(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(live_workspace_email(), "bn5799@gmail.com")
            self.assertTrue(live_workspace_allowed("bn5799@gmail.com"))
            self.assertTrue(live_workspace_allowed("BN5799@Gmail.com"))
            self.assertFalse(live_workspace_allowed("other@gmail.com"))
            self.assertFalse(live_workspace_allowed(None))

    def test_env_override(self):
        with patch.dict(os.environ, {"LIVE_WORKSPACE_EMAIL": "beta@example.com"}):
            self.assertTrue(live_workspace_allowed("beta@example.com"))
            self.assertFalse(live_workspace_allowed("bn5799@gmail.com"))

    def test_effective_workspace_clamps_live(self):
        self.assertEqual(
            effective_execution_workspace("live", "other@gmail.com"),
            "paper",
        )
        self.assertEqual(
            effective_execution_workspace("live", "bn5799@gmail.com"),
            "live",
        )
        self.assertEqual(effective_execution_workspace("paper", None), "paper")


if __name__ == "__main__":
    unittest.main()
