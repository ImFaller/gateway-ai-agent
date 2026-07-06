from system.llm_client import LLMClient


class BaseAgent:
    """所有Agent的基类

    设计参考：
    - LangChain Tool 的 handle_tool_error 模式：LLM 调用失败自动重试，重试耗尽走降级
    - CrewAI Role-based Agent 模式：每个 Agent 携带 role/goal/backstory，
      让 LLM 的角色感更强、prompt 更精准、输出更稳定
      （CrewAI 文档：https://docs.crewai.com/concepts/agents）
    """

    # CrewAI Role-based 三要素：子类可覆盖
    # role: 角色身份（如"策略配置专家"）
    # goal: 这个角色要达成的目标
    # backstory: 背景故事，强化 LLM 对角色的理解
    role = "AI助手"
    goal = "协助用户完成任务"
    backstory = "你是一个通用的 AI 助手。"

    def __init__(self, agent_id, name, llm_client=None, message_bus=None):
        self.agent_id = agent_id
        self.name = name
        self.llm_client = llm_client
        self.message_bus = message_bus
        self._task_count = 0
        self._metrics = {"tasks_processed": 0, "errors": 0}
        self._max_retries = 2  # LangChain 默认重试次数

    async def handle_task(self, task):
        """处理任务（子类重写）"""
        raise NotImplementedError

    def get_system_prompt(self):
        """获取系统提示词（子类重写）

        默认实现注入 CrewAI 三要素，子类可在此基础上扩展。
        这样 prompt 自动包含角色信息，LLM 输出更聚焦。
        """
        return (
            f"你是{self.role}。\n"
            f"你的目标：{self.goal}\n"
            f"背景：{self.backstory}\n"
            "请用中文回复。"
        )

    def handle_error(self, error, messages):
        """LangChain handle_tool_error 模式: 异常降级处理（子类可重写）"""
        return None

    async def llm_chat(self, messages, temperature=0.7):
        """调用LLM并返回内容，失败时自动重试，重试耗尽走降级"""
        if not self.llm_client:
            raise RuntimeError("LLM客户端未配置")

        last_error = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = self.llm_client.chat(messages, temperature=temperature)
                content = self.llm_client.extract_content(resp)
                self._task_count += 1
                self._metrics["tasks_processed"] = self._task_count
                self._metrics["last_latency"] = resp.get("_elapsed_sec", 0)
                return content
            except Exception as e:
                last_error = e
                self._metrics["errors"] = self._metrics.get("errors", 0) + 1
                self._metrics["last_error"] = str(e)[:200]
                if attempt < self._max_retries:
                    continue  # 重试

        # 重试耗尽，走降级（LangChain handle_tool_error 模式）
        fallback = self.handle_error(last_error, messages)
        if fallback is not None:
            return fallback
        raise last_error

    def get_metrics(self):
        return dict(self._metrics)

    def reset_metrics(self):
        self._metrics = {"tasks_processed": 0, "errors": 0}
        self._task_count = 0
