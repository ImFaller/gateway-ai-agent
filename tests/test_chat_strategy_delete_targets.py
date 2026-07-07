import unittest
from types import SimpleNamespace

from frontend.routes.chat import (
    DELETE_TARGET_REQUIRED_MESSAGE,
    _resolve_strategy_action_targets,
)


class StrategyDeleteTargetResolutionTests(unittest.TestCase):
    def setUp(self):
        self.strategies = [
            SimpleNamespace(strategy_id="high_risk_traffic_block", name="高风险流量阻断策略"),
            SimpleNamespace(strategy_id="strategy", name="策略"),
        ]

    def test_rejects_bare_delete_even_if_model_guesses_an_id(self):
        actions = [{"action": "delete_strategy", "params": {"strategy_id": "strategy"}}]

        resolved, reply = _resolve_strategy_action_targets("删除策略", actions, self.strategies)

        self.assertEqual([], resolved)
        self.assertEqual(DELETE_TARGET_REQUIRED_MESSAGE, reply)

    def test_allows_existing_short_strategy_id_when_user_mentions_it(self):
        actions = [{"action": "delete_strategy", "params": {"strategy_id": "strategy"}}]

        resolved, reply = _resolve_strategy_action_targets("删除策略id为strategy的策略", actions, self.strategies)

        self.assertEqual("", reply)
        self.assertEqual("strategy", resolved[0]["params"]["strategy_id"])
        self.assertIs(True, resolved[0]["params"]["_target_exists"])
        self.assertEqual("策略", resolved[0]["params"]["_target_name"])

    def test_resolves_unique_strategy_name_to_id(self):
        actions = [{"action": "delete_strategy", "params": {"strategy_id": "high_risk_traffic_block"}}]

        resolved, reply = _resolve_strategy_action_targets("删除策略名称为策略的策略", actions, self.strategies)

        self.assertEqual("", reply)
        self.assertEqual("strategy", resolved[0]["params"]["strategy_id"])
        self.assertIs(True, resolved[0]["params"]["_target_exists"])


if __name__ == "__main__":
    unittest.main()
