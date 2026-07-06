import ipaddress
import re

from pydantic import BaseModel, Field, field_validator, model_validator
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


class TriggerConfig(BaseModel):
    """策略触发条件模型，负责按字段和操作符校验 value 类型。"""
    field: str = Field(description="字段名，如 source_ip/dest_ip/protocol/port")
    operator: str = Field(description="操作符：eq/ne/gt/lt/contains/in/regex/exists")
    value: Any = Field(default="", description="比较值")

    @field_validator("field")
    @classmethod
    def validate_field(cls, v):
        allowed = {"source_ip", "dest_ip", "protocol", "port"}
        if v not in allowed:
            raise ValueError(f"field 必须是 {allowed} 之一")
        return v

    @field_validator("operator")
    @classmethod
    def validate_operator(cls, v):
        allowed = {"eq", "ne", "gt", "lt", "contains", "in", "regex", "exists"}
        if v not in allowed:
            raise ValueError(f"operator 必须是 {allowed} 之一")
        return v

    @model_validator(mode="after")
    def validate_value_by_type(self):
        if self.operator == "exists":
            self.value = True
            return self

        if self.field == "port":
            if self.operator in {"contains", "regex"}:
                raise ValueError("port 不支持 contains/regex 操作符")
            if self.operator == "in":
                values = self.value if isinstance(self.value, list) else str(self.value).split(",")
                self.value = [self._parse_port(v) for v in values]
            else:
                self.value = self._parse_port(self.value)
            return self

        if self.field == "protocol":
            if self.operator in {"gt", "lt", "regex"}:
                raise ValueError("protocol 不支持 gt/lt/regex 操作符")
            allowed_protocols = {"tcp", "udp", "http", "https", "ftp", "smtp", "icmp"}
            values = self.value if isinstance(self.value, list) else (
                str(self.value).split(",") if self.operator == "in" else [self.value]
            )
            normalized = [str(v).strip().lower() for v in values if str(v).strip()]
            if not normalized or any(v not in allowed_protocols for v in normalized):
                raise ValueError(f"protocol 必须是 {allowed_protocols} 之一")
            self.value = normalized if self.operator == "in" else normalized[0]
            return self

        if self.field in {"source_ip", "dest_ip"}:
            if self.operator in {"gt", "lt"}:
                raise ValueError("IP 字段不支持 gt/lt 操作符")
            if self.operator == "regex":
                re.compile(str(self.value))
                return self
            if self.operator == "in":
                values = self.value if isinstance(self.value, list) else str(self.value).split(",")
                self.value = [self._parse_ip(v) for v in values]
            else:
                self.value = self._parse_ip(self.value)
            return self

        return self

    @staticmethod
    def _parse_port(v):
        if isinstance(v, bool):
            raise ValueError("port 必须是 0-65535 的整数")
        try:
            port = int(str(v).strip())
        except (TypeError, ValueError):
            raise ValueError("port 必须是 0-65535 的整数")
        if port < 0 or port > 65535:
            raise ValueError("port 必须是 0-65535 的整数")
        return port

    @staticmethod
    def _parse_ip(v):
        value = str(v).strip()
        try:
            ipaddress.ip_address(value)
        except ValueError:
            raise ValueError("IP 地址格式不正确")
        return value


class StrategyConfig(BaseModel):
    """策略配置模型"""
    id: str = Field(description="策略唯一标识")
    name: str = Field(description="策略名称")
    description: str = Field(default="", description="策略描述")
    enabled: bool = Field(default=True, description="是否启用")
    priority: int = Field(default=100, ge=1, le=999, description="优先级，数值越小优先级越高")
    triggers: list[TriggerConfig] = Field(default_factory=list, description="触发条件列表")
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
