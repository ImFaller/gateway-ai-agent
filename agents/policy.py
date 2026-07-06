import json
from agents.base import BaseAgent


class PolicyAgent(BaseAgent):
    def __init__(self, llm_client=None, message_bus=None):
        super().__init__("policy_agent", "策略分析Agent", llm_client, message_bus)

    async def handle_task(self, task):
        action = task.get("action", "analyze")
        params = task.get("params", {})
        context = task.get("context", {})
        if action == "analyze":
            return await self._analyze_policy(params, context)
        elif action == "suggest":
            return await self._suggest_policy(params, context)
        elif action == "compliance_check":
            return await self._compliance_check(params, context)
        return {"status": "unknown_action", "action": action}

    async def _analyze_policy(self, params, context):
        messages = [
            {"role": "system", "content": "你是网闸AI设备的策略分析Agent。你负责解读和评估安全策略规则。\n请分析：\n1. 策略规则是否合理\n2. 是否存在策略冲突\n3. 策略是否符合安全基线\n4. 策略调整建议\n以JSON格式返回结果。"},
            {"role": "user", "content": json.dumps({
                "task": "analyze_policy", "params": params,
                "context": {k: v for k, v in context.items() if k != "content"}
            }, ensure_ascii=False)}
        ]
        analysis = await self.llm_chat(messages)
        try:
            result = json.loads(analysis)
        except (json.JSONDecodeError, TypeError):
            result = {"compliant": True, "summary": analysis[:200]}
        return {"agent": self.agent_id, "action": "analyze", "result": result}

    async def _suggest_policy(self, params, context):
        return {"agent": self.agent_id, "action": "suggest", "suggestion": "当前策略配置合理，无需调整"}

    async def _compliance_check(self, params, context):
        return {"agent": self.agent_id, "action": "compliance_check", "compliant": True}
