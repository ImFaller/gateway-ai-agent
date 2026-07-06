from pydantic import BaseModel, Field, field_validator
from typing import TypedDict, Any, Annotated
from operator import add


# ===== LangGraph State 定义 =====
# LangGraph 核心概念: State 是节点间共享的全局状态对象
# 使用 TypedDict + Annotated[list, add] 实现累加更新模式


class GraphState(TypedDict, total=False):
    """LangGraph 全局状态 — 贯穿整个策略编排流程

    节点返回的 dict 会合并到此 State:
    - 标量字段: 覆盖更新
    - Annotated[list, add]: 累加追加（用于 steps / agent_results）
    """
    # 输入: 请求上下文
    context: dict
    # 执行元信息
    execution_id: str
    # 匹配的策略ID列表
    matched_strategies: list[str]
    # 分发的步骤（累加）
    steps: Annotated[list[dict], add]
    # 各Agent返回的结果（累加）
    agent_results: Annotated[list[dict], add]
    # 最终状态: completed | no_match | error
    status: str
    # 路由目标列表（条件边用）
    route_to: list[str]
    # 错误信息
    error: str


# ===== Pydantic 请求模型（API入口校验） =====


class StrategyConfig(BaseModel):
    """策略配置模型"""
    id: str = Field(description="策略唯一标识")
    name: str = Field(description="策略名称")
    description: str = Field(default="", description="策略描述")
    enabled: bool = Field(default=True, description="是否启用")
    priority: int = Field(default=100, ge=1, le=999, description="优先级，数值越小优先级越高")
    triggers: list[dict] = Field(default_factory=list, description="触发条件列表")
    steps: list[dict] = Field(default_factory=list, description="执行步骤列表")


class ExecutionRequest(BaseModel):
    """执行请求模型"""
    context: dict = Field(default_factory=dict, description="自定义上下文（设置此字段后，下方各字段将被忽略）")
    source_ip: str = Field(default="", description="源IP地址")
    dest_ip: str = Field(default="", description="目标IP地址")
    content: str = Field(default="", description="传输数据内容")
    protocol: str = Field(default="tcp", description="网络协议")
    port: int = Field(default=0, ge=0, le=65535, description="目标端口号")

    @field_validator("protocol")
    @classmethod
    def validate_protocol(cls, v):
        """协议枚举校验"""
        allowed = {"tcp", "udp", "http", "https", "ftp", "smtp", "icmp"}
        if v and v.lower() not in allowed:
            raise ValueError(f"协议必须是 {allowed} 之一")
        return v.lower() if v else v

    def to_graph_state(self) -> GraphState:
        """转换为 LangGraph State"""
        ctx = self.context or {
            "source_ip": self.source_ip,
            "dest_ip": self.dest_ip,
            "content": self.content,
            "protocol": self.protocol,
            "port": self.port,
        }
        return GraphState(
            context=ctx,
            execution_id="",
            matched_strategies=[],
            steps=[],
            agent_results=[],
            status="",
            route_to=[],
            error="",
        )
