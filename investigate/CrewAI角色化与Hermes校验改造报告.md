# 智能体可靠性改造报告 —— CrewAI Role-based + Hermes Verifier

> 生成时间：2026-07-03
> 改造范围：对话层主从架构增强
> 改造目标：借鉴业界两大成熟模式（CrewAI 角色化智能体 + Hermes 输出校验），
> 提升主从架构中子智能体的输出稳定性和可维护性。

---

## 一、改造背景

前期已实现"1 主调度 + N 子执行"的对话层主从架构：
- 主智能体 RouterAgent 识别意图
- 子智能体（Config/Query/Execute）生成具体动作

改造前发现两个问题：
1. **子智能体缺乏角色定义**：每个子智能体只有 agent_id/name，LLM 不知道"自己是谁"，prompt 散乱、输出风格不稳定
2. **子智能体输出无校验**：LLM 偶尔返回缺字段、字段类型错的 JSON，会被下游 execute-action 直接处理，导致策略添加失败或 500 错误

本次改造借鉴业界两个成熟模式解决上述问题。

---

## 二、业界方案调研

### 业界 AI Agent 生态分层

| 层级 | 代表项目 | 与本项目关系 |
|---|---|---|
| ③ 产品层 | OpenCode / OpenClaw / Hermes / OpenHuman | 借鉴产品设计理念 |
| ② 框架层 | LangGraph / LangChain / CrewAI / AutoGen | 直接借鉴设计模式 |
| ① 模型层 | DeepSeek / GPT-4 / Claude / Hermes-3 | 提供基础能力 |

本项目属于 ② 框架层，使用 LangGraph 自建编排图，因此重点借鉴同层框架的设计模式。

### 借鉴对象与价值

| 借鉴对象 | 层级 | 借鉴点 | 价值 |
|---|---|---|---|
| **CrewAI** | ② 框架 | Role-based Agent（role/goal/backstory 三要素） | 让子智能体有明确身份，prompt 自动注入角色信息，输出更稳定 |
| **Hermes Agent** | ③ 产品 | Verifier 节点（v0.18 "自证清白"机制） | 子智能体输出后加校验，剔除格式不合法的动作，避免坏数据流到下游 |

---

## 三、改造内容

### 改造 1：CrewAI Role-based Agent 模式

**核心思想**：每个 Agent 携带三个角色字段，让 LLM 输出更聚焦。

| 字段 | 含义 | CrewAI 原文 |
|---|---|---|
| `role` | 角色身份 | "你是策略配置专家" |
| `goal` | 这个角色要达成的目标 | "把用户描述翻译成策略参数 JSON" |
| `backstory` | 背景故事，强化角色理解 | "你对字段格式非常严格，绝不混淆 triggers/steps" |

**代码改造**：

1. `agents/base.py` 的 `BaseAgent` 新增三个类属性：
   ```python
   role = "AI助手"
   goal = "协助用户完成任务"
   backstory = "你是一个通用的 AI 助手。"
   ```
   并在 `get_system_prompt()` 默认实现中自动注入这三要素，子类只需覆盖即可获得角色化 prompt。

2. `agents/router_agent.py` 为 4 个智能体分别定义角色：

| 智能体 | role | goal（节选） |
|---|---|---|
| RouterAgent | 主调度智能体 | 准确识别意图，把任务路由给最合适的子智能体 |
| StrategyConfigAgent | 策略配置专家 | 把自然语言描述精确翻译成符合引擎 schema 的策略参数 |
| StrategyQueryAgent | 策略查询智能体 | 快速响应查询请求，不调 LLM，直接返回动作 |
| StrategyExecuteAgent | 策略执行编排专家 | 从描述中提取关键信息，生成执行上下文 context |

每个智能体的 `get_system_prompt()` 自动注入 `f"你是{self.role}。背景：{self.backstory}\n你的目标：{self.goal}\n..."`，LLM 角色感更强。

### 改造 2：Hermes Verifier 节点

**核心思想**：子智能体输出后加一个校验节点，剔除格式不合法的动作。

参考 Hermes Agent v0.18 引入的 "自证清白"机制——LLM 输出不直接信任，必须通过格式校验才能进入下游。

**代码改造**：

`frontend/routes/chat.py` 新增 `_verify_action_format()` 函数，在主从路由后、返回 ChatResponse 前调用。

**校验规则**：

| 动作 | 校验项 |
|---|---|
| 所有动作 | 必须是 dict；必须有 action 字段；action 必须在白名单内 |
| `add_strategy` | 必须有 id、name；priority 必须是 1-999 整数；triggers/steps 必须是 list |
| `delete_strategy` | 必须有 strategy_id |
| `execute_strategy` | context 必须是 dict |
| `get_system_status` | 无需 params |

**校验失败处理**：
- 不合法的动作被剔除，日志打印 `[Verifier] 剔除格式不合法的动作: ... 原因: ...`
- 全部动作都被剔除时，返回 `⚠️ 智能体生成的动作格式有误，请重试或换种描述方式。`
- 不让坏数据流到下游 execute-action，避免 500 错误

---

## 四、改造前后对比

### 流程对比

**改造前**（2 层）：
```
用户消息
   ↓
RouterAgent（识别意图）
   ↓
子智能体（生成动作）
   ↓
直接返回 ChatResponse
```

**改造后**（3 层）：
```
用户消息
   ↓
RouterAgent（识别意图，prompt 含角色定义）
   ↓
子智能体（生成动作，prompt 含角色定义）
   ↓
ActionVerifier（Hermes 模式，校验格式合法性）  ← 新增
   ↓
返回 ChatResponse
```

### 代码量变化

| 文件 | 改造前 | 改造后 | 变化 |
|---|---|---|---|
| `agents/base.py` | 65 行 | 78 行 | +13 行（新增三要素 + 默认注入） |
| `agents/router_agent.py` | 320 行 | 360 行 | +40 行（4 个智能体的角色定义） |
| `frontend/routes/chat.py` | 470 行 | 540 行 | +70 行（新增 Verifier 函数 + 调用） |

---

## 五、改造收益

### 1. 输出稳定性提升

**改造前**：LLM 不知道自己的角色，prompt 风格散乱，偶尔会输出错误格式（如把 triggers 写成对象而不是列表）。

**改造后**：
- 角色定义让 LLM 输出风格更一致
- Verifier 兜底，即使偶发错误也不会流到下游

### 2. 可调试性提升

**改造前**：LLM 输出错误后，错误只在 execute-action 时才暴露，难以定位是哪一层的问题。

**改造后**：
- 后端日志分层打印：`[主智能体]` / `[策略配置子智能体]` / `[Verifier]`
- Verifier 失败时打印具体原因（如"add_strategy 缺少 id 字段"），快速定位问题

### 3. 可维护性提升

**改造前**：加新功能要改一个大 prompt，可能影响现有识别。

**改造后**：
- 加新子智能体只需新建一个类，继承 BaseAgent 自动获得角色注入
- 加新校验规则只需在 `_verify_action_format()` 加 elif 分支
- 主智能体 prompt 不变，不影响现有功能

---

## 六、验证方式

### 测试用例

| 输入 | 预期意图 | 预期校验结果 |
|---|---|---|
| "你好" | chat | 无动作，直接回复 |
| "添加SSH防护策略..." | strategy_config | 通过校验，进入密码确认 |
| "查看系统状态" | strategy_query | 通过校验（固定动作） |
| "执行一次流量审查" | strategy_execute | 通过校验，生成 context |
| 模拟 LLM 返回缺字段的 JSON | — | Verifier 剔除，提示"格式有误" |

### 验证步骤

1. 重启后端：`python -m uvicorn system.app:app --host 0.0.0.0 --port 8099`
2. 浏览器硬刷新：Ctrl+F5
3. 输入上述测试用例
4. 观察后端日志，应看到分层输出：
   ```
   [主智能体] 意图: strategy_config, 回复: 好的，正在为您配置策略...
   [策略配置子智能体] 生成动作: [{'action': 'add_strategy', ...}]
   ```
5. 策略配置类操作应弹出密码框，输入密码后执行成功

---

## 七、后续可借鉴方向

本次只落地了最高优先级的两项。后续可考虑：

| 优先级 | 借鉴对象 | 借鉴点 | 工作量 |
|---|---|---|---|
| ★★ | OpenClaw | 技能链（多技能串联执行） | 中 |
| ★★ | OpenCode | LLM Provider 抽象层（支持多模型切换） | 中 |
| ★ | Hermes | MoA 多模型投票（高危操作双模型确认） | 大 |
| ★ | OpenHuman | 历史决策记忆（类似请求复用） | 大 |

---

## 八、总结

本次改造借鉴 CrewAI 和 Hermes 两个业界成熟模式，在不引入新依赖、不增加模型配置成本的前提下，显著提升了主从架构的稳定性和可维护性：

1. **CrewAI Role-based**：让每个子智能体有明确身份，LLM 输出更聚焦、更稳定
2. **Hermes Verifier**：加一道格式校验关卡，避免 LLM 偶发错误流到下游

两项改造都遵循"小步快跑"原则：改动小、风险低、收益明显，适合在演示前快速落地。
