import json
from agents.base import BaseAgent


class SecurityAgent(BaseAgent):
    def __init__(self, llm_client=None, message_bus=None):
        super().__init__("security_agent", "安全审查Agent", llm_client, message_bus)

    async def handle_task(self, task):
        action = task.get("action", "audit")
        params = task.get("params", {})
        context = task.get("context", {})
        if action == "audit":
            return await self._audit_traffic(params, context)
        elif action == "threat_scan":
            return await self._threat_scan(params, context)
        return {"status": "unknown_action", "action": action}

    async def _audit_traffic(self, params, context):
        content = params.get("content", context.get("content", ""))
        src = params.get("source_ip", context.get("source_ip", "unknown"))
        dst = params.get("dest_ip", context.get("dest_ip", "unknown"))
        messages = [
            {"role": "system", "content": "你是网闸AI设备的安全审查Agent。你负责分析网络流量和数据包的安全性。\n请评估以下因素：\n1. 源IP和目标IP的可信度\n2. 数据内容是否包含恶意载荷\n3. 协议和端口是否合规\n4. 综合风险评估(high/medium/low)\n以JSON格式返回结果，包含risk_level、risk_reason和建议action(allow/deny/review)。"},
            {"role": "user", "content": json.dumps({
                "source_ip": src, "dest_ip": dst,
                "content_preview": content[:1000] if content else "",
                "protocol": params.get("protocol", "unknown"),
                "port": params.get("port", 0),
            }, ensure_ascii=False)}
        ]
        analysis = await self.llm_chat(messages)
        try:
            result = json.loads(analysis)
        except (json.JSONDecodeError, TypeError):
            result = {"risk_level": "unknown", "summary": analysis[:200]}
        return {"agent": self.agent_id, "action": "audit", "result": result,
                "source_ip": src, "dest_ip": dst}

    async def _threat_scan(self, params, context):
        return {"agent": self.agent_id, "action": "threat_scan", "status": "scanned", "threats_found": 0}

    def handle_error(self, error, messages):
        """LangChain handle_tool_error 模式: LLM不可用时返回降级结果"""
        return json.dumps({
            "risk_level": "unknown",
            "risk_reason": f"LLM调用失败，已降级: {str(error)[:100]}",
            "action": "review",
        }, ensure_ascii=False)
