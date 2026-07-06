import json
import time
from agents.base import BaseAgent


class OrchestratorAgent(BaseAgent):
    """编排器Agent — LangGraph 重构后的兼容层

    原来的编排逻辑（策略匹配+步骤分发）已移至 engine/graph.py 的 LangGraph StateGraph。
    此类保留用于:
    1. dashboard 统计数据（get_statistics）
    2. chat 接口的 analyze 动作（LLM 上下文分析）
    """

    def __init__(self, llm_client=None, message_bus=None, strategy_engine=None):
        super().__init__("orchestrator", "策略编排器", llm_client, message_bus)
        self.strategy_engine = strategy_engine
        self.execution_records = []

    async def handle_task(self, task):
        action = task.get("action", "orchestrate")
        context = task.get("context", {})
        if action == "analyze":
            return await self._analyze_context(context)
        elif action == "fallback":
            return {"status": "fallback", "message": "未匹配到任何策略", "context": context}
        # orchestrate 动作由 LangGraph 图处理，不再走这里
        return {"status": "unknown_action", "action": action}

    async def _analyze_context(self, context):
        prompt = self.get_system_prompt()
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "请分析以下请求上下文，提取关键安全要素:\n" + json.dumps(context, ensure_ascii=False, indent=2)}
        ]
        analysis = await self.llm_chat(messages)
        return {"analysis": analysis, "context": context}

    def get_system_prompt(self):
        return (
            "你是网闸AI设备的主编排器。你的职责是：\n"
            "1. 分析传入的请求和数据流\n"
            "2. 匹配安全策略规则\n"
            "3. 分派任务给安全Agent、策略Agent和监控Agent\n"
            "4. 汇总各Agent的结果并做出最终决策\n"
            "请保持高效、安全的编排决策。"
        )

    def get_statistics(self):
        return {
            "total_executions": len(self.strategy_engine.execution_history) if self.strategy_engine else 0,
            "metrics": self.get_metrics(),
        }
