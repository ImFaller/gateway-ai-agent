import asyncio
import unittest
from unittest.mock import patch

from agents.router_agent import RouterAgent, StrategyConfigAgent
from frontend.routes.chat import ADD_DETAILS_REQUIRED_MESSAGE, _call_langchain_agent


class ChatBareAddRequestTests(unittest.TestCase):
    def _chat_with_generated_action(self, message, generated_action):
        async def fake_route(self, message, history, model_config):
            return {"intent": RouterAgent.INTENT_CONFIG, "reply": "正在为您添加策略..."}

        async def fake_generate_action(self, message, history, model_config, strategy_list_text):
            return [generated_action], '{"action": "add_strategy"}'

        with patch.object(RouterAgent, "route", new=fake_route), patch.object(
            StrategyConfigAgent, "generate_action", new=fake_generate_action
        ):
            return asyncio.run(
                _call_langchain_agent(
                    message,
                    [],
                    {"api_key": "test-key", "model": "test-model", "api_base": "http://unused"},
                    trace_id="chat-test",
                )
            )

    def test_rejects_bare_add_even_if_model_generates_default_strategy(self):
        response = self._chat_with_generated_action(
            "添加策略",
            {
                "action": "add_strategy",
                "params": {
                    "id": "new_strategy",
                    "name": "新策略",
                    "priority": 50,
                    "triggers": [{"field": "port", "operator": "eq", "value": 80}],
                    "steps": [
                        {
                            "step_id": "audit_01",
                            "agent": "security_agent",
                            "action": "audit",
                            "params": {},
                            "timeout": 30,
                        }
                    ],
                },
            },
        )

        self.assertEqual([], response.actions)
        self.assertEqual(ADD_DETAILS_REQUIRED_MESSAGE, response.reply)

    def test_explains_missing_details_when_add_action_is_incomplete(self):
        response = self._chat_with_generated_action(
            "新增添加增加策略",
            {
                "action": "add_strategy",
                "params": {
                    "id": "new_strategy",
                    "name": "新策略",
                    "priority": 50,
                    "triggers": [],
                    "steps": [],
                },
            },
        )

        self.assertEqual([], response.actions)
        self.assertEqual(ADD_DETAILS_REQUIRED_MESSAGE, response.reply)


if __name__ == "__main__":
    unittest.main()
