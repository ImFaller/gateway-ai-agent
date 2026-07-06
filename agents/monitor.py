import json
import time
from agents.base import BaseAgent


class MonitorAgent(BaseAgent):
    def __init__(self, llm_client=None, message_bus=None):
        super().__init__("monitor_agent", "监控Agent", llm_client, message_bus)
        self._alerts = []
        self._health_status = {"status": "healthy", "last_check": time.time()}

    async def handle_task(self, task):
        action = task.get("action", "health_check")
        params = task.get("params", {})
        context = task.get("context", {})
        if action == "health_check":
            return self._health_check()
        elif action == "analyze_anomaly":
            return await self._analyze_anomaly(params, context)
        elif action == "report":
            return self._generate_report()
        return {"status": "unknown_action", "action": action}

    def _health_check(self):
        self._health_status["last_check"] = time.time()
        return {
            "agent": self.agent_id, "action": "health_check",
            "status": self._health_status["status"],
            "timestamp": time.time(),
            "metrics": self.get_metrics(),
        }

    async def _analyze_anomaly(self, params, context):
        messages = [
            {"role": "system", "content": "你是网闸AI设备的监控Agent。分析以下异常信息并评估严重程度。\n返回JSON：{\"severity\": \"high/medium/low\", \"summary\": \"...\", \"action_required\": \"...\"}"},
            {"role": "user", "content": json.dumps({"params": params}, ensure_ascii=False)}
        ]
        analysis = await self.llm_chat(messages)
        try:
            result = json.loads(analysis)
        except (json.JSONDecodeError, TypeError):
            result = {"severity": "medium", "summary": analysis[:200]}
        alert = {"time": time.time(), "severity": result.get("severity", "medium"),
                 "summary": result.get("summary", ""), "params": params}
        self._alerts.append(alert)
        return {"agent": self.agent_id, "action": "analyze_anomaly", "result": result, "alert": alert}

    def _generate_report(self):
        active_alerts = [a for a in self._alerts if a.get("severity") in ("high", "medium")]
        return {
            "agent": self.agent_id, "action": "report",
            "total_alerts": len(self._alerts),
            "active_alerts": len(active_alerts),
            "health_status": self._health_status,
            "metrics": self.get_metrics(),
        }

    def get_alerts(self, severity=None):
        if severity:
            return [a for a in self._alerts if a.get("severity") == severity]
        return list(self._alerts)
