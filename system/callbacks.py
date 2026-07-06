"""LangGraph Callback Handler — 替代原 MessageBus 的三段式 Hook

LangGraph 继承 LangChain 的 BaseCallbackHandler 机制，在节点执行的
开始/结束/出错时自动触发回调，无需经过 MessageBus。

对应关系:
  on_chain_start  ←  原 h_before (前置日志 + 拦截)
  on_chain_end    ←  原 h_after  (后置审计)
  on_chain_error  ←  原 h_error  (异常告警 + 指标上报)
"""

from langchain_core.callbacks import BaseCallbackHandler


class AgentCallbackHandler(BaseCallbackHandler):
    """LangGraph 工作流生命周期回调"""

    def __init__(self, logger=None, metrics=None):
        self.logger = logger
        self.metrics = metrics

    def on_chain_start(self, serialized, inputs, **kwargs):
        """节点开始执行 — 对应 before_hook"""
        name = serialized.get("name", "unknown") if serialized else "unknown"
        if self.logger:
            self.logger.info(f"[Callback:start] 节点 '{name}' 开始执行")

    def on_chain_end(self, outputs, **kwargs):
        """节点执行完成 — 对应 after_hook"""
        if self.logger:
            self.logger.info(f"[Callback:end] 节点执行完成")

    def on_chain_error(self, error, **kwargs):
        """节点执行出错 — 对应 error_hook"""
        if self.metrics:
            self.metrics.increment("agent_errors")
        if self.logger:
            self.logger.error(f"[Callback:error] 节点执行失败: {error}")
