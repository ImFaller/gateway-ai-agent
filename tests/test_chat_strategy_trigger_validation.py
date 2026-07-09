import unittest

from pydantic import ValidationError

from frontend.routes.chat import AddStrategyArgs


class ChatStrategyTriggerValidationTests(unittest.TestCase):
    def _payload(self, trigger):
        return {
            "id": "bug",
            "name": "协议触发策略",
            "priority": 10,
            "triggers": [trigger],
            "steps": [
                {
                    "step_id": "audit_01",
                    "agent": "security_agent",
                    "action": "audit",
                }
            ],
        }

    def test_rejects_invalid_protocol_trigger_from_chat_action(self):
        payload = self._payload({
            "field": "protocol",
            "operator": "in",
            "value": ["test", "false"],
        })

        with self.assertRaises(ValidationError):
            AddStrategyArgs.model_validate(payload)

    def test_normalizes_valid_protocol_trigger_from_chat_action(self):
        payload = self._payload({
            "field": "protocol",
            "operator": "eq",
            "value": "HTTP",
        })

        validated = AddStrategyArgs.model_validate(payload)

        self.assertEqual("http", validated.triggers[0].value)

    def test_rejects_add_strategy_without_triggers_or_steps(self):
        payload = {
            "id": "new_strategy",
            "name": "新策略",
            "priority": 50,
            "triggers": [],
            "steps": [],
        }

        with self.assertRaises(ValidationError):
            AddStrategyArgs.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
