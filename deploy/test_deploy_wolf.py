"""Offline tests for deploy screen shaping (no DB, no LLM)."""
from __future__ import annotations

import unittest

from deploy.deploy_wolf import build_deploy_screen_response


class TestDeployScreenResponse(unittest.TestCase):
    def test_build_screen_from_deploy_result(self):
        deploy_result = {
            "wolf": {"wolf_id": "W0007", "wolf_name": "Alpha"},
            "brain": {
                "birth_intent": "Value wolf born today.",
                "picks": [
                    {
                        "symbol": "INFY",
                        "quantity": 2,
                        "buy_price": 1800.0,
                        "target": 2000.0,
                        "stop_loss": 1500.0,
                        "conviction": 80,
                        "reasoning": "Strong moat.",
                    }
                ],
            },
            "executor": {
                "summary": "Bought INFY (2 @ ₹1,800.00 = ₹3,600.00). Cash: ₹10,000.00 → ₹6,400.00.",
                "actions_taken": [
                    {
                        "action": "BUY",
                        "symbol": "INFY",
                        "quantity": 2,
                        "price": 1800.0,
                        "value": 3600.0,
                    }
                ],
                "actions_rejected": [],
            },
            "shortlist": [{"symbol": "INFY"}, {"symbol": "TCS"}],
        }
        screen = build_deploy_screen_response(
            deploy_result, strategy="value", allocation=10_000
        )
        self.assertTrue(screen["supported"])
        self.assertEqual(screen["pipeline"], "wolf_brain")
        self.assertEqual(len(screen["candidates"]), 1)
        self.assertEqual(screen["botAction"]["action"], "executed")
        self.assertIn("INFY", screen["botAction"]["message"])


if __name__ == "__main__":
    unittest.main()
