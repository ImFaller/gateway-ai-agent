"""LangGraph 工作流图定义

将原来的 OrchestratorAgent + StrategyEngine.execute 编排逻辑
重构为 LangGraph 的 StateGraph + 条件边模式。

图结构:
  START → match_strategies → ──(条件边)──→ security_agent ─┐
                              ├─(条件边)──→ policy_agent   ├─→ aggregate → END
                              ├─(条件边)──→ monitor_agent ─┤
                              └─(无匹配)──→ fallback ──────┘
"""

import time
from langgraph.graph import StateGraph, START, END

from contracts.schemas import GraphState


def build_graph(agents: dict, strategy_engine, message_bus=None):
    """构建 LangGraph 工作流

    Args:
        agents: {"security_agent": SecurityAgent, "policy_agent": PolicyAgent, ...}
        strategy_engine: StrategyEngine 实例（策略匹配用）
        message_bus: MessageBus 实例（保留兼容，Hook 仍可用）
    Returns:
        编译后的 LangGraph 可执行图
    """

    # ===== 节点函数 =====

    async def match_node(state: GraphState) -> dict:
        """策略匹配节点 — 替代原 StrategyEngine.execute 的匹配逻辑"""
        context = state.get("context", {})
        matched = strategy_engine.evaluate(context)
        execution_id = f"exec-{int(time.time() * 1000)}"

        if not matched:
            return {
                "execution_id": execution_id,
                "matched_strategies": [],
                "status": "no_match",
                "route_to": [],
            }

        # 收集所有需要执行的 agent
        route_to = []
        steps = []
        for strategy in matched:
            for step in strategy.steps:
                if step.agent not in route_to:
                    route_to.append(step.agent)
                steps.append({
                    "step_id": step.step_id,
                    "agent": step.agent,
                    "action": step.action,
                    "params": step.params,
                    "strategy_id": strategy.strategy_id,
                    "status": "pending",
                })

        return {
            "execution_id": execution_id,
            "matched_strategies": [s.strategy_id for s in matched],
            "steps": steps,
            "route_to": route_to,
            "status": "dispatching",
        }

    def make_agent_node(agent_key: str):
        """为每个 agent 生成 LangGraph 节点函数"""
        agent = agents[agent_key]

        async def node_fn(state: GraphState) -> dict:
            # 找到属于此 agent 的步骤
            agent_steps = [s for s in state.get("steps", []) if s["agent"] == agent_key]
            results = []
            for step in agent_steps:
                task = {
                    "action": step["action"],
                    "params": step.get("params", {}),
                    "context": state.get("context", {}),
                    "execution_id": state.get("execution_id", ""),
                    "strategy_id": step.get("strategy_id", ""),
                }
                try:
                    result = await agent.handle_task(task)
                    results.append({"agent": agent_key, "step_id": step["step_id"], "result": result})
                except Exception as e:
                    results.append({"agent": agent_key, "step_id": step["step_id"], "error": str(e)[:200]})

            return {"agent_results": results}

        return node_fn

    async def fallback_node(state: GraphState) -> dict:
        """无匹配策略时的兜底节点"""
        return {
            "status": "no_match",
            "agent_results": [{"agent": "fallback", "message": "未匹配到任何策略"}],
        }

    async def aggregate_node(state: GraphState) -> dict:
        """汇总节点 — 收集所有 agent 结果，生成最终执行记录"""
        execution_id = state.get("execution_id", "")
        result = {
            "execution_id": execution_id,
            "context": state.get("context", {}),
            "matched_strategies": state.get("matched_strategies", []),
            "steps": state.get("steps", []),
            "agent_results": state.get("agent_results", []),
            "status": "completed",
            "error": None,
        }
        # 保留到 strategy_engine.execution_history（兼容 dashboard）
        strategy_engine.execution_history.append(result)
        return {"status": "completed"}

    # ===== 条件路由函数 =====

    def route_after_match(state: GraphState) -> list[str]:
        """条件边: 根据匹配结果决定路由到哪些 agent 节点

        LangGraph add_conditional_edges 的路由函数:
        - 返回节点名列表，图会并行执行这些节点
        - 空列表时走 fallback
        """
        if state.get("status") == "no_match":
            return ["fallback"]
        return state.get("route_to", [])

    # ===== 构建图 =====

    graph = StateGraph(GraphState)

    # 添加节点
    graph.add_node("match", match_node)
    graph.add_node("fallback", fallback_node)
    graph.add_node("aggregate", aggregate_node)

    agent_keys = list(agents.keys())
    for key in agent_keys:
        graph.add_node(key, make_agent_node(key))

    # 入口
    graph.add_edge(START, "match")

    # 条件边: match → 各 agent 节点 / fallback
    path_map = {key: key for key in agent_keys}
    path_map["fallback"] = "fallback"
    graph.add_conditional_edges("match", route_after_match, path_map)

    # 所有 agent 节点 → aggregate
    for key in agent_keys:
        graph.add_edge(key, "aggregate")
    graph.add_edge("fallback", "aggregate")

    # aggregate → END
    graph.add_edge("aggregate", END)

    return graph.compile()
