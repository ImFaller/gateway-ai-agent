"""主从智能体架构 —— 对话层编排

业界模式：
- LangChain RouterChain：主链先识别意图，再路由到专用子链
- AutoGen GroupChatManager：主智能体做语义识别，分发任务给专家智能体
- CrewAI Manager：1 个 Manager + N 个 Specialist，Manager 不干活只调度

架构（导师方向：1主智能体调度 → 策略配置/策略查询）：

    用户输入
       ↓
    ┌─────────────────────────┐
    │  RouterAgent（主智能体）   │  第 1 次 LLM 调用：只识别意图
    │  - 语义识别               │  返回 {"intent": "...", "reply": "..."}
    │  - 任务路由               │
    └──────────┬──────────────┘
               ↓
    ┌──────────┴──────────────────────────────┐
    │           路由分发（无 LLM）              │
    │  intent → 对应子智能体                  │
    └──┬─────────────┬──────────────┬────────┘
       ↓             ↓              ↓
  StrategyConfig  StrategyQuery  StrategyExecute
  （第 2 次 LLM）  （直接执行）   （第 2 次 LLM）
  生成策略参数    查询系统状态    生成执行上下文
"""
import json
import httpx
from agents.base import BaseAgent
from system.prompt_guard import wrap_user_input, sanitize_user_input, TRUST_BOUNDARY_DECLARATION


# ============================================================
# 主智能体：RouterAgent —— 只做意图识别和任务路由
# ============================================================
# 参考 LangChain RouterChain：主链只负责判断"用户想干啥"，不生成具体参数
# 这样 prompt 短、专注、易维护，加新功能不用动主智能体

class RouterAgent(BaseAgent):
    """主智能体 —— 语义识别 + 任务路由

    职责：
    1. 接收用户消息
    2. 调用 LLM 识别意图（intent）
    3. 返回意图 + 简短回复，由调用方决定路由到哪个子智能体

    与原 _call_langchain_agent 的区别：
    - 原：1 次 LLM 调用同时做意图识别 + 参数生成（prompt 臃肿）
    - 现：主智能体只识别意图，参数生成交给子智能体（prompt 专注）
    """

    # CrewAI Role-based 三要素
    role = "主调度智能体"
    goal = "准确识别用户意图，把任务路由给最合适的子智能体，自己不生成任何参数"
    backstory = (
        "你是网闸AI设备的大脑，负责理解用户说什么、想干什么。"
        "你不亲自执行操作，只判断意图：是配置策略、查询状态、执行编排还是闲聊。"
        "你的判断准确性直接决定后续流程是否正确。"
    )

    # 意图分类（对应导师说的"策略配置/策略查询"）
    INTENT_CONFIG = "strategy_config"     # 添加/删除策略
    INTENT_QUERY = "strategy_query"       # 查询系统状态
    INTENT_EXECUTE = "strategy_execute"   # 执行策略编排
    INTENT_RESTORE = "strategy_restore"   # 恢复已删除的策略
    INTENT_CHAT = "chat"                  # 普通对话（无需操作）

    def __init__(self, llm_client=None, message_bus=None):
        super().__init__("router", "主调度智能体", llm_client, message_bus)

    def get_system_prompt(self):
        # 主智能体 prompt 很短：只问意图，不问参数
        # 这是"主从架构"的关键 —— 主智能体"轻"，子智能体"重"
        # CrewAI 三要素已通过 BaseAgent 注入，这里补充具体业务指令
        return (
            f"你是{self.role}。背景：{self.backstory}\n"
            f"你的目标：{self.goal}\n"
            "\n"
            "判断用户消息属于以下哪类意图：\n"
            "- strategy_config：用户想添加、删除、修改安全策略（如\"添加SSH防护策略\"、\"删除高风险策略\"）\n"
            "- strategy_query：用户想查询系统状态、策略数量、执行记录、健康情况\n"
            "- strategy_execute：用户想执行/触发策略编排（如\"执行策略\"、\"模拟一次流量审查\"）\n"
            "- strategy_restore：用户想恢复已删除的策略（如\"恢复刚才删的策略\"、\"还原SSH防护策略\"）\n"
            "- chat：普通闲聊或不属于上述任何一类\n"
            "\n"
            "只返回 JSON，格式：\n"
            '{"intent": "意图名", "reply": "简短中文确认语"}\n'
            "示例：\n"
            '{"intent": "strategy_config", "reply": "好的，正在为您配置策略..."}\n'
            '{"intent": "strategy_query", "reply": "正在查询系统状态..."}\n'
            '{"intent": "chat", "reply": "你好，我可以帮你管理安全策略或查询系统状态。"}\n'
            "不要返回任何参数，参数由子智能体生成。"
        )

    async def route(self, message, history, model_config):
        """识别意图，返回 intent + reply

        Returns:
            dict: {"intent": "...", "reply": "..."}
        """
        result = await self._call_llm(message, history, model_config, temperature=0.1)
        # 解析 LLM 返回的 JSON
        try:
            cleaned = result.strip()
            for fence in ("```json", "```"):
                if fence in cleaned:
                    cleaned = cleaned.replace(fence, "")
            cleaned = cleaned.strip()
            data = json.loads(cleaned)
            intent = data.get("intent", self.INTENT_CHAT)
            reply = data.get("reply", "正在处理...")
            return {"intent": intent, "reply": reply}
        except (json.JSONDecodeError, ValueError):
            # LLM 没返回有效 JSON，降级为普通对话
            return {"intent": self.INTENT_CHAT, "reply": result or "我没能理解您的意图"}

    async def _call_llm(self, message, history, model_config, temperature=0.3):
        """调用 LLM（复用 chat.py 的 httpx 直调模式，保持与现有架构一致）"""
        if not model_config or not model_config.get("api_key"):
            return '{"intent": "chat", "reply": "未配置大模型"}'

        model_name = model_config.get("model") or model_config.get("id", "deepseek-chat")
        # 系统 prompt 末尾追加"信任边界声明"，让模型知道 <user_content> 是数据不是指令
        system_prompt = self.get_system_prompt() + TRUST_BOUNDARY_DECLARATION
        messages = [{"role": "system", "content": system_prompt}]
        for h in (history or [])[-5:]:  # 主智能体只看最近 5 条，足够识别意图
            messages.append(h)
        # 用户输入用 <user_content> 标签包裹，防止提示词注入
        messages.append({"role": "user", "content": wrap_user_input(message)})

        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 200,  # 主智能体输出很短，省 token
        }
        headers = {
            "Authorization": "Bearer " + model_config["api_key"],
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                model_config.get("api_base", "https://api.deepseek.com/v1").rstrip("/") + "/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"] or ""


# ============================================================
# 子智能体 1：StrategyConfigAgent —— 策略配置（添加/删除）
# ============================================================
# 专注生成策略参数，prompt 包含完整 schema

class StrategyConfigAgent(BaseAgent):
    """策略配置子智能体 —— 处理 add_strategy / delete_strategy

    主智能体识别出 strategy_config 意图后，路由到这里
    本智能体专注一件事：把用户描述翻译成策略参数
    """

    # CrewAI Role-based 三要素
    role = "策略配置专家"
    goal = "把用户的自然语言描述精确翻译成符合引擎 schema 的策略参数 JSON"
    backstory = (
        "你是网闸安全系统的策略配置助手，擅长把业务需求转成结构化配置。"
        "你对策略的字段格式非常严格——triggers 必须用 field/operator/value，"
        "steps 必须用 step_id/agent/action/params，绝不混淆。"
        "删除策略时你会先核对真实策略列表，绝不编造不存在的 ID。"
    )

    def __init__(self, llm_client=None, message_bus=None):
        super().__init__("strategy_config", "策略配置智能体", llm_client, message_bus)

    def get_system_prompt(self, strategy_list_text="（无策略）"):
        return (
            f"你是{self.role}。背景：{self.backstory}\n"
            f"你的目标：{self.goal}\n"
            "\n"
            "用户会用自然语言描述想添加、删除或修改的策略，你生成对应的动作 JSON。\n"
            "\n"
            "===== 当前系统已有策略列表 =====\n"
            f"{strategy_list_text}\n"
            "================================\n"
            "\n"
            "返回 JSON（三选一）：\n"
            '{"action": "add_strategy", "params": {"id": "策略ID", "name": "策略名称", "priority": 50, "triggers": [...], "steps": [...]}}\n'
            '{"action": "delete_strategy", "params": {"strategy_id": "要删除的真实策略ID"}}\n'
            '{"action": "update_strategy", "params": {"strategy_id": "要改的真实策略ID", "priority": 80, ...}}\n'
            "\n"
            "add_strategy 参数说明（字段名必须严格匹配）：\n"
            "- id: 策略唯一标识（字符串，如 ssh_protection）\n"
            "- name: 策略名称\n"
            "- priority: 优先级（整数 1-999）\n"
            "- triggers: 触发条件列表，每个元素必须是 {\"field\": 字段, \"operator\": 运算符, \"value\": 值}\n"
            "    field: source_ip, port, protocol, destination 等\n"
            "    operator: eq, ne, gt, lt, contains, in, regex, exists\n"
            "- steps: 执行步骤列表，每个元素必须是 {\"step_id\": \"唯一ID\", \"agent\": 执行者, \"action\": 动作, \"params\": {}}\n"
            "    agent: security_agent, policy_agent, monitor_agent\n"
            "    action: audit, compliance_check, analyze_anomaly, health_check\n"
            "\n"
            "update_strategy 参数说明（PATCH 语义，只改传入字段，未传字段保持原值）：\n"
            "- strategy_id: 必填，要更新的策略 ID（必须从上方列表选真实存在的 ID）\n"
            "- name / description / enabled / priority / triggers / steps: 全部可选，只传需要改的字段\n"
            "  例：只改优先级 → {\"action\": \"update_strategy\", \"params\": {\"strategy_id\": \"ssh_protection\", \"priority\": 80}}\n"
            "  例：禁用某策略 → {\"action\": \"update_strategy\", \"params\": {\"strategy_id\": \"ssh_protection\", \"enabled\": false}}\n"
            "  注意：triggers/steps 是整体替换，不是追加。要改触发条件需传完整的新 triggers 列表\n"
            "\n"
            "重要规则：\n"
            "1. 删除/更新策略时 strategy_id 必须从上方列表选真实存在的 ID，禁止编造\n"
            "2. 禁止批量删除：如果用户说\"删除全部/所有\"策略，不要返回任何 action JSON\n"
            "   只返回纯文本 reply：\"检测到批量删除意图。为安全考虑，智能体对话场景不支持批量删除策略，\"\n"
            "   \"请到「策略管理」页面逐条删除（删除后会进入回收站，可恢复）。\"\n"
            "3. 删除单条时 strategy_id 必须是具体的真实 ID，不能是 all/__all__/* 等通配符\n"
            "4. update_strategy 至少要传一个待更新字段（除 strategy_id 外），如果用户只想查不改，返回 reply 而不是 action\n"
            "5. 如果用户意图不明确或无法匹配到具体策略，也不要返回 action，只在 reply 中说明"
        )

    async def generate_action(self, message, history, model_config, strategy_list_text):
        """生成策略配置动作"""
        result = await self._call_llm(message, history, model_config,
                                     self.get_system_prompt(strategy_list_text))
        # 复用 chat.py 的 _parse_actions 解析逻辑
        from frontend.routes.chat import _parse_actions
        actions = _parse_actions(result)
        return actions, result

    async def _call_llm(self, message, history, model_config, system_prompt):
        if not model_config or not model_config.get("api_key"):
            return ""

        model_name = model_config.get("model") or model_config.get("id", "deepseek-chat")
        # 系统 prompt 末尾追加信任边界声明，用户输入用 <user_content> 标签包裹
        messages = [{"role": "system", "content": system_prompt + TRUST_BOUNDARY_DECLARATION}]
        for h in (history or [])[-5:]:
            messages.append(h)
        messages.append({"role": "user", "content": wrap_user_input(message)})

        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 1500,
        }
        headers = {
            "Authorization": "Bearer " + model_config["api_key"],
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                model_config.get("api_base", "https://api.deepseek.com/v1").rstrip("/") + "/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"] or ""


# ============================================================
# 子智能体 2：StrategyQueryAgent —— 策略查询（查询系统状态）
# ============================================================
# 查询类不需要 LLM 生成参数，直接返回动作即可

class StrategyQueryAgent(BaseAgent):
    """策略查询子智能体 —— 处理 get_system_status

    主智能体识别出 strategy_query 意图后，路由到这里
    查询类操作不需要 LLM 生成参数（系统状态查询是固定的），直接返回动作
    """

    # CrewAI Role-based 三要素
    role = "策略查询智能体"
    goal = "快速响应用户的状态查询请求，不调 LLM，直接返回查询动作"
    backstory = (
        "你负责处理所有查询类请求。这类请求的参数固定，"
        "不需要 LLM 生成，直接返回 get_system_status 动作让后端查询实时数据。"
        "你的存在是为了节省 token 和加快响应速度。"
    )

    def __init__(self, llm_client=None, message_bus=None):
        super().__init__("strategy_query", "策略查询智能体", llm_client, message_bus)

    async def generate_action(self, message, history, model_config, strategy_list_text):
        """查询类直接返回固定动作，无需 LLM 调用

        这是主从架构的优势：简单意图不浪费 LLM 调用
        把用户原始消息也带上，便于后端判断是要看概览还是某条策略的详情
        """
        actions = [{
            "action": "get_system_status",
            "params": {"__user_message": message}  # 带上原始消息用于详情判断
        }]
        reply = "正在查询系统实时状态..."
        return actions, reply


# ============================================================
# 子智能体 3：StrategyExecuteAgent —— 策略执行编排
# ============================================================

class StrategyExecuteAgent(BaseAgent):
    """策略执行子智能体 —— 处理 execute_strategy

    主智能体识别出 strategy_execute 意图后，路由到这里
    本智能体专注生成执行上下文（context）
    """

    # CrewAI Role-based 三要素
    role = "策略执行编排专家"
    goal = "从用户描述中提取关键信息，生成触发策略匹配的执行上下文 context"
    backstory = (
        "你是网闸AI系统的执行调度助手。用户想触发一次策略编排时，"
        "你负责把用户描述的场景（如\"模拟一次 SSH 访问\"）"
        "翻译成策略引擎能理解的 context（包含 port/protocol/source_ip 等字段）。"
        "你对网络协议和端口号很熟悉，能从模糊描述中提取准确字段。"
    )

    def __init__(self, llm_client=None, message_bus=None):
        super().__init__("strategy_execute", "策略执行智能体", llm_client, message_bus)

    def get_system_prompt(self, strategy_list_text="（无策略）"):
        return (
            f"你是{self.role}。背景：{self.backstory}\n"
            f"你的目标：{self.goal}\n"
            "\n"
            "用户想触发策略编排，你需要生成执行上下文 context。\n"
            "\n"
            "===== 当前系统已有策略列表 =====\n"
            f"{strategy_list_text}\n"
            "================================\n"
            "\n"
            "返回 JSON：\n"
            '{"action": "execute_strategy", "params": {"context": {"port": 22, "protocol": "ssh", "source_ip": "..."}}}\n'
            "\n"
            "context 是触发策略匹配的上下文，包含字段如：\n"
            "- port: 端口号\n"
            "- protocol: 协议（ssh, http, https, ftp 等）\n"
            "- source_ip: 源 IP\n"
            "- destination: 目标地址\n"
            "\n"
            "根据用户描述提取这些字段，生成 context。"
        )

    async def generate_action(self, message, history, model_config, strategy_list_text):
        result = await self._call_llm(message, history, model_config,
                                     self.get_system_prompt(strategy_list_text))
        from frontend.routes.chat import _parse_actions
        actions = _parse_actions(result)
        return actions, result

    async def _call_llm(self, message, history, model_config, system_prompt):
        if not model_config or not model_config.get("api_key"):
            return ""

        model_name = model_config.get("model") or model_config.get("id", "deepseek-chat")
        # 系统 prompt 末尾追加信任边界声明，用户输入用 <user_content> 标签包裹
        messages = [{"role": "system", "content": system_prompt + TRUST_BOUNDARY_DECLARATION}]
        for h in (history or [])[-5:]:
            messages.append(h)
        messages.append({"role": "user", "content": wrap_user_input(message)})

        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 800,
        }
        headers = {
            "Authorization": "Bearer " + model_config["api_key"],
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                model_config.get("api_base", "https://api.deepseek.com/v1").rstrip("/") + "/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"] or ""


# ============================================================
# 子智能体 4：RestoreStrategyAgent —— 策略恢复
# ============================================================
# 用户说"恢复刚才删的策略"或"还原 SSH 防护"时，主智能体识别为 strategy_restore 意图
# 本智能体从回收站列表中帮用户挑出对应的 strategy_id

class RestoreStrategyAgent(BaseAgent):
    """策略恢复子智能体 —— 处理 restore_strategy

    主智能体识别出 strategy_restore 意图后，路由到这里
    本智能体专注一件事：从回收站列表中选出用户想恢复的策略 ID

    与 StrategyQueryAgent 类似（不需要复杂参数生成），但需要 LLM 选 ID
    因为用户可能说"恢复刚才删的"或"恢复 SSH 防护"，需要语义匹配
    """

    # CrewAI Role-based 三要素
    role = "策略恢复助手"
    goal = "从回收站列表中准确选出用户想恢复的策略 ID，避免误恢复"
    backstory = (
        "你是网闸安全系统的策略恢复助手。用户误删策略后想恢复时，"
        "你负责从回收站列表里挑出对应的策略 ID。"
        "你严格依据回收站列表选 ID，绝不编造不存在的 ID。"
    )

    def __init__(self, llm_client=None, message_bus=None):
        super().__init__("strategy_restore", "策略恢复智能体", llm_client, message_bus)

    def get_system_prompt(self, trash_list_text="（回收站为空）"):
        return (
            f"你是{self.role}。背景：{self.backstory}\n"
            f"你的目标：{self.goal}\n"
            "\n"
            "用户想恢复已删除的策略。你从回收站列表中选出对应的策略 ID。\n"
            "\n"
            "===== 当前回收站列表 =====\n"
            f"{trash_list_text}\n"
            "==========================\n"
            "\n"
            "返回 JSON：\n"
            '{"action": "restore_strategy", "params": {"strategy_id": "回收站中的策略ID"}}\n'
            "\n"
            "选 ID 的规则：\n"
            "- 必须从上方回收站列表中选真实存在的 ID\n"
            "- 用户说\"恢复刚才删的\" → 选 deleted_at 最大的（列表第一条）\n"
            "- 用户说\"恢复 SSH 防护\" → 选 ID 或 name 包含 SSH 的\n"
            "- 用户说\"恢复所有\" → 返回 params: {\"strategy_id\": \"__all__\"}\n"
            "- 如果回收站为空或无法匹配，返回 params: {\"strategy_id\": \"\"}\n"
            "\n"
            "重要：strategy_id 必须从上方列表选真实存在的 ID，禁止编造。"
        )

    async def generate_action(self, message, history, model_config, trash_list_text):
        """从回收站列表中选 ID 生成恢复动作"""
        result = await self._call_llm(message, history, model_config,
                                     self.get_system_prompt(trash_list_text))
        from frontend.routes.chat import _parse_actions
        actions = _parse_actions(result)
        return actions, result

    async def _call_llm(self, message, history, model_config, system_prompt):
        if not model_config or not model_config.get("api_key"):
            return ""

        model_name = model_config.get("model") or model_config.get("id", "deepseek-chat")
        messages = [{"role": "system", "content": system_prompt + TRUST_BOUNDARY_DECLARATION}]
        for h in (history or [])[-5:]:
            messages.append(h)
        messages.append({"role": "user", "content": wrap_user_input(message)})

        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": 0.1,  # 低温度，选 ID 要稳定
            "max_tokens": 300,
        }
        headers = {
            "Authorization": "Bearer " + model_config["api_key"],
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                model_config.get("api_base", "https://api.deepseek.com/v1").rstrip("/") + "/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"] or ""


# ============================================================
# 监视智能体：VerifierAgent —— LLM-as-a-Judge 双模型交叉验证
# ============================================================
# 业界模式：
# - OpenAI Evals 指南推荐的 "LLM-as-a-Judge"：用第二个模型审查第一个模型的输出
# - Hermes MoA（Mixture of Agents）：多模型独立判断后聚合
# - LangChain CriteriaEvalChain：基于准则的 LLM 评估链
#
# 与 Hermes Verifier（格式校验）的区别：
# - Hermes Verifier 是"规则校验"（字段是否齐全、类型是否正确）
# - VerifierAgent 是"语义审查"（动作是否符合用户意图、是否有危险操作）
# 两者互补：先格式校验，再语义审查


class VerifierAgent(BaseAgent):
    """监视智能体 —— 用第二个模型审查主模型生成的动作

    职责：
    1. 接收用户原始请求 + 主模型生成的动作
    2. 用独立的监视模型（verifier_model_config）审查动作合理性
    3. 返回 approve / reject + 拒绝原因

    与 Hermes Verifier 的关系：
    - Hermes Verifier（_verify_action_format）：规则校验，看字段格式
    - VerifierAgent（本类）：语义审查，看动作意图
    - 流程顺序：Hermes Verifier → VerifierAgent
    """

    # CrewAI Role-based 三要素
    role = "动作审查员"
    goal = "审查主模型生成的动作是否合理、安全、符合用户意图，拦截危险或错误操作"
    backstory = (
        "你是独立审查员，使用与主模型不同的模型，不盲信主模型的输出。"
        "你的职责是发现主模型的疏漏：意图识别错误、参数不合理、越权操作、"
        "危险操作（如删除所有策略、修改系统配置等）。"
        "审查时保持谨慎，宁可错杀不可放过。"
    )

    def __init__(self, llm_client=None, message_bus=None):
        super().__init__("verifier", "监视智能体", llm_client, message_bus)

    def get_system_prompt(self):
        return (
            f"你是{self.role}。背景：{self.backstory}\n"
            f"你的目标：{self.goal}\n"
            "\n"
            "你会收到用户原始请求和主模型生成的动作。请审查：\n"
            "1. 动作类型是否匹配用户意图（如用户说\"添加\"不应是\"删除\"）\n"
            "2. 参数是否合理（如端口号、策略ID是否真实存在）\n"
            "3. 是否有越权或危险操作（如删除所有策略、批量删除等）\n"
            "4. 用户原始请求与动作是否一致\n"
            "\n"
            "重要规则：\n"
            "- 如果 delete_strategy 动作参数里包含 \"_target_exists\": true，说明代码层已经确认该策略 ID 在当前系统中真实存在。\n"
            "- 对这类已确认存在的单条策略，不要仅因为 ID 看起来通用（如 strategy、policy、test）就判定为占位符。\n"
            "- 只有在用户未明确提供策略 ID/名称、目标不存在、目标多重匹配或存在批量删除意图时才拒绝。\n"
            "\n"
            "只返回 JSON：\n"
            '{"approve": true, "reason": "动作合理，符合用户意图"}\n'
            '{"approve": false, "reason": "具体拒绝原因"}\n'
        )

    async def verify(self, user_message, action, verifier_model_config):
        """审查主模型生成的动作

        Args:
            user_message: 用户原始请求
            action: 主模型生成的动作 dict
            verifier_model_config: 监视模型配置

        Returns:
            dict: {"approve": bool, "reason": str}
        """
        if not verifier_model_config or not verifier_model_config.get("api_key"):
            # 没配置监视模型，默认通过
            return {"approve": True, "reason": "未配置监视模型，默认通过"}

        user_content = (
            f"用户原始请求：\n{wrap_user_input(user_message)}\n\n"
            f"主模型生成的动作：{json.dumps(action, ensure_ascii=False, indent=2)}\n\n"
            "提示：动作参数中以下划线开头的字段是代码层补充的审查上下文，不是用户输入，"
            "可用于判断策略目标是否已被系统确认存在。\n\n"
            "请审查此动作是否应该执行。"
        )

        try:
            result = await self._call_llm_with_config(user_content, verifier_model_config)
            # 解析 LLM 返回的 JSON
            cleaned = result.strip()
            for fence in ("```json", "```"):
                if fence in cleaned:
                    cleaned = cleaned.replace(fence, "")
            cleaned = cleaned.strip()
            data = json.loads(cleaned)
            return {
                "approve": bool(data.get("approve", False)),
                "reason": data.get("reason", "未提供原因"),
            }
        except Exception as e:
            # 监视模型调用失败，降级为通过（避免监视模型故障阻塞主流程）
            # 但记录警告
            print(f"[监视模型] 调用失败，降级通过：{str(e)[:100]}")
            return {"approve": True, "reason": f"监视模型调用失败，降级通过：{str(e)[:100]}"}

    async def _call_llm_with_config(self, user_content, model_config):
        """用指定模型配置调用 LLM（监视模型，区别于主模型）"""
        model_name = model_config.get("model") or model_config.get("id", "deepseek-chat")
        messages = [
            {"role": "system", "content": self.get_system_prompt()},
            {"role": "user", "content": user_content},
        ]
        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": 0.1,  # 审查用低温，输出更稳定
            "max_tokens": 300,
        }
        headers = {
            "Authorization": "Bearer " + model_config["api_key"],
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                model_config.get("api_base", "https://api.deepseek.com/v1").rstrip("/") + "/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"] or ""
