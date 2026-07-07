# AI 智能体可靠性学习实验路线

> 分支：`codex/agent-reliability-learning-roadmap`  
> 基线：`main`，旧修复分支 `fix/verifier-delete-existing-strategy-id` 已合入 `main`  
> 目标：把调研报告中的 Agent Runtime、策略 IR、Harness、HITL、可观测性和多智能体协作，拆成可持续开发、可验证、可学习的小实验。

## 1. 当前项目进度判断

项目已经不是“单层聊天机器人”阶段，而是进入了可靠性工程的早期形态：

| 方向 | 当前已有 | 下一步缺口 |
|---|---|---|
| 对话入口 | RouterAgent 主调度 + 子智能体，动作白名单，二次密码确认 | 还缺“自然语言 -> intent -> IR -> diff/plan”的显式链路 |
| 动作可靠性 | Pydantic 参数校验、Verifier 格式校验、删除目标解析保护 | 校验对象仍偏 action params，未形成独立策略 IR |
| 策略引擎 | StrategyEngine、运行时策略持久化、回收站、重复策略检测 | 缺策略变更计划、dry-run、precheck/postcheck、rollback 证据 |
| 多智能体编排 | LangGraph match -> security/policy/monitor -> aggregate | 状态机较短，未覆盖审批、提交、观测、回滚 |
| 可观测性 | 日志、metrics、dashboard、execution_history | 缺 trace_id 贯穿模型、Agent、工具、策略命中和审计证据 |
| 模型可靠性 | 主模型/监视模型角色、verify_strength 思路 | 缺模型能力画像、路由策略、输出归一化记录 |

因此后续学习路径不建议继续堆聊天功能，而应围绕“高风险网络控制面如何让 Agent 可控、可审计、可恢复”推进。

## 2. 主路线

主路线采用报告推荐的保守产品架构：

```text
自然语言
  -> Intent
  -> Policy IR
  -> IR Validator
  -> Change Plan / Diff
  -> Harness Gate Runner
  -> Human Approval
  -> Dry-run / Precheck
  -> Controlled Apply
  -> Postcheck / Evidence
  -> Audit / Rollback
```

学习重点是：LLM 只负责理解、编译和解释；写操作必须经过结构化对象、规则校验、审批和证据闭环。

## 3. 推荐开发实验路径

### 实验 1：策略 IR v1

学习目标：理解为什么网络配置不能从自然语言直接变成设备命令。

实现建议：
- 新增 `contracts/policy_ir.py`，定义 `PolicyIR`、`PolicyIntent`、`PolicySubject`、`PolicyAction`、`PolicyExpiry`、`RiskLevel`。
- 让策略配置子智能体先输出 IR，而不是直接输出 `add_strategy` 参数。
- 写测试覆盖：合法 IR、缺少对象、非法端口、过期时间格式、危险 action。

验收标准：
- 任意写配置类请求必须先生成可校验 IR。
- IR 校验失败时，不进入 `execute-action`。

### 实验 2：IR 到 StrategyConfig 的编译器

学习目标：区分“意图表示”和“本项目策略引擎可执行配置”。

实现建议：
- 新增 `engine/policy_compiler.py`。
- 输入 `PolicyIR`，输出当前 `Strategy` / `StrategyConfig` 所需的 triggers 和 steps。
- 编译器保持纯函数，方便测试和对比。

验收标准：
- 同一个 IR 多次编译结果稳定。
- 编译失败必须给出结构化错误，而不是让 LLM 临场解释。

### 实验 3：Harness Task Contract

学习目标：让 Agent 任务从“说完成”变成“有契约、有证据、有判定”。

实现建议：
- 新增 `system/harness.py` 或 `engine/harness.py`。
- 定义 `TaskContract`：intent、policy_ir、risk_level、expected_result、exit_conditions、approval_required。
- 定义 `RunRecord`：trace_id、state、events、evidence、verdict。

验收标准：
- 每次策略模拟或配置都产生一条 run record。
- 记录中能看到 IR、门禁结果、审批状态和最终判定。

### 实验 4：Gate Runner 和权限隔离

学习目标：学习 Agent 工具权限、最小权限和高危操作门禁。

实现建议：
- 把当前 `SKILL_REGISTRY` 升级为能力注册表：`read/write`、risk、requires_approval、allowed_params_schema。
- 所有写操作必须由 Gate Runner 检查：白名单、schema、风险级别、确认状态、密码状态。
- 删除、恢复、更新、执行策略全部走同一个 gate 入口。

验收标准：
- 未审批的高危写操作返回 `approval_required`。
- 未注册能力、自由文本命令、越权参数全部被拒绝。

### 实验 5：策略变更状态机

学习目标：学习 LangGraph/状态机在长流程 Agent 中的价值。

实现建议：
- 将策略配置流程扩展为：
  `IntentCaptured -> PolicyIRGenerated -> PolicyIRValidated -> PlanGenerated -> DiffReviewed -> HumanApproved -> PrecheckPassed -> Applied -> PostcheckObserved -> Completed`
- 异常路径覆盖：
  `ValidationFailed`、`ApprovalRejected`、`PrecheckFailed`、`PostcheckFailed -> Rollback`。

验收标准：
- 每个状态都有事件记录。
- 可以查询某次 run 卡在哪一步。

### 实验 6：证据包和完成判定

学习目标：理解可靠智能体的完成条件不应由模型自述决定。

实现建议：
- 定义 `EvidenceBundle`：strategy_before、strategy_after、diff、precheck_result、postcheck_result、logs_ref、metrics_ref。
- 定义四态结果：`completed`、`partial`、`failed`、`rolled_back`。
- Postcheck 至少验证策略是否存在、是否启用、是否可被测试 context 命中。

验收标准：
- `completed` 必须有证据支持。
- 没有 postcheck 证据时只能是 `partial` 或 `failed`。

### 实验 7：模型适配层和能力画像

学习目标：学习多模型不是“随便切模型”，而是按能力和风险路由。

实现建议：
- 给模型配置补充 capability profile：`json_mode`、`tool_calling`、`structured_output`、`max_context_tokens`、`cost_level`、`latency_level`。
- 新增 `model_router`：intent、IR 生成、风险评估、摘要分别选择模型。
- 记录每次模型调用的 task_type、model_id、latency、parse_result。

验收标准：
- 没有稳定 JSON 能力的模型不能进入写配置关键路径。
- 模型失败时可降级为人工确认或只读解释。

### 实验 8：可观测性闭环

学习目标：学习 trace_id 如何贯穿 Agent 系统。

实现建议：
- 统一生成 `trace_id`，贯穿 chat、router、子智能体、verifier、harness、strategy_engine。
- dashboard 增加 run 视图：状态、风险、耗时、模型、门禁、证据。
- 先用本地 JSONL/内存记录，后续再接 OpenTelemetry。

验收标准：
- 任意一次用户操作都能从 trace_id 追到模型输出、动作、门禁和策略结果。

## 4. 建议的迭代顺序

第一阶段先做“结构化可靠性”：
1. 策略 IR v1
2. IR 编译器
3. Harness Task Contract
4. Gate Runner

第二阶段做“有状态闭环”：
1. 状态机扩展
2. 证据包
3. 完成判定
4. 回滚路径

第三阶段做“生产化学习”：
1. 模型能力画像
2. 模型路由和 fallback
3. trace_id 全链路
4. dashboard run 视图

## 5. 下一步推荐落地项

建议下一次开发直接从“策略 IR v1 + 编译器测试”开始。

原因：
- 它是报告中最关键的安全缓冲层。
- 现有项目已经有 Pydantic schema 和 StrategyEngine，落地成本低。
- 做完后，后续 Harness、审批、diff、证据包都有统一对象可依附。

最小切片：
- `contracts/policy_ir.py`
- `engine/policy_compiler.py`
- `tests/test_policy_ir.py`
- `tests/test_policy_compiler.py`

完成这个切片后，系统会从“LLM 生成动作”前进一步，变成“LLM 生成可审查的策略意图，再由确定性代码编译为策略配置”。
