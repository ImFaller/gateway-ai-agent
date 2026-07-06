import json
import time


class DashboardDataProvider:
    """Dashboard数据提供商 —— 为Web界面提供聚合后的可观测性数据"""

    def __init__(self, logger=None, metrics=None, strategy_engine=None,
                 orchestrator=None, monitor_agent=None):
        self.logger = logger
        self.metrics = metrics
        self.strategy_engine = strategy_engine
        self.orchestrator = orchestrator
        self.monitor_agent = monitor_agent

    def get_overview(self):
        """获取总览数据"""
        overview = {
            "status": "running",
            "uptime": 0,
            "timestamp": time.time(),
        }
        if self.orchestrator:
            stats = self.orchestrator.get_statistics()
            overview["total_executions"] = stats.get("total_executions", 0)
        if self.strategy_engine:
            estats = self.strategy_engine.get_execution_stats()
            overview.update({
                "strategies_count": estats.get("strategies_count", 0),
                "total_evaluations": estats.get("total_executions", 0),
                "succeeded": estats.get("succeeded", 0),
                "failed": estats.get("failed", 0),
            })
        if self.metrics:
            snapshot = self.metrics.snapshot()
            overview["metrics"] = snapshot
        if self.logger:
            log_stats = self.logger.get_statistics()
            overview["log_stats"] = log_stats
            overview["recent_logs"] = self.logger.get_recent(10)
        return overview

    def get_strategies(self):
        if not self.strategy_engine:
            return []
        return [s.to_dict() for s in self.strategy_engine.strategies]

    def get_execution_history(self, limit=50):
        if not self.strategy_engine:
            return []
        return self.strategy_engine.execution_history[-limit:]

    def get_agent_status(self):
        agents = []
        if self.orchestrator:
            agents.append({
                "id": "orchestrator",
                "name": "策略编排器",
                "status": "active",
                "metrics": self.orchestrator.get_metrics(),
            })
        # Other agents are tracked via orchestrator
        return agents

    def get_alerts(self):
        if not self.monitor_agent:
            return []
        return self.monitor_agent.get_alerts()

    def get_full_data(self):
        return {
            "overview": self.get_overview(),
            "strategies": self.get_strategies(),
            "executions": self.get_execution_history(20),
            "agents": self.get_agent_status(),
            "alerts": self.get_alerts(),
        }
