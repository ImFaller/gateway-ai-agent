# 变更日志

本文记录当前仓库从初始化到最新版本的关键改动，方便后来查看 GitHub 或接手项目的人理解：哪些问题已经处理、为什么这样处理、涉及哪些文件。

## 2026-07-07

### 本次提交 - 修复 AI 对话删除策略的目标解析

提交信息：`fix: harden chat strategy delete target resolution`

本次提交处理两个 AI 对话删除策略相关问题。

#### 1. 真实存在的短策略 ID 被监视模型误判为占位符

原问题：

- 手动添加策略 `id=strategy`、`name=策略` 后，AI 对话输入“删除策略id为strategy的策略”会被监视模型拒绝。
- 拒绝理由认为 `strategy` 像占位符或示例文本，而不是真实策略 ID。

原因判断：

- 代码层格式校验允许 `strategy_id=strategy`。
- 运行时策略列表中也确实存在该 ID。
- 拒绝来自 LLM-as-a-Judge 监视模型语义审查。
- 监视模型之前只看到用户原话和动作 JSON，没有看到“该 ID 已由代码层确认存在”的确定性上下文。

改动方式：

- 在监视模型前增加删除目标解析逻辑。
- 对真实存在且用户明确提到的策略 ID，给动作补充 `_target_exists`、`_target_name`、`_target_source` 审查上下文。
- 调整监视模型提示词：已确认存在的单条策略 ID，不应仅因名称通用而被判定为占位符。

#### 2. 用户只输入“删除策略”时不应猜测并删除某条策略

原问题：

- AI 对话输入“删除策略”时，模型可能猜测一个已有策略 ID 并继续执行删除。
- 这会导致未指定 ID 或名称时误删策略。

原因判断：

- 子智能体 prompt 虽要求“不明确不要返回 action”，但没有代码层兜底。
- 高危删除动作不能只依赖 LLM 自律，必须在执行前做确定性目标校验。

改动方式：

- 增加裸删除请求识别，例如“删除策略”“删策略”“删除一个策略”等。
- 若用户未明确提供策略 ID 或策略名称，删除动作会被移除，并提示用户补充关键信息。
- 支持从用户原话解析“id 为 xxx”和“名称为 xxx”。
- 当名称唯一匹配时，自动解析为真实 `strategy_id`。

涉及文件：

- `frontend/routes/chat.py`
- `agents/router_agent.py`
- `tests/test_chat_strategy_delete_targets.py`

验证：

- `python -m unittest tests.test_chat_strategy_delete_targets`
- `python -m compileall frontend\routes\chat.py agents\router_agent.py tests\test_chat_strategy_delete_targets.py`

## 2026-07-06

### `1da927c` - 建立初始项目基线

提交信息：`chore: establish initial project baseline`

关键内容：

- 将当前项目初始化为 Git 仓库，并提交初始代码基线。
- 新增 `.gitignore`，避免把本地敏感配置和缓存文件提交到 GitHub。
- `.env` 被忽略，不会上传 API Key、密码等本机私有配置。
- `.env.example` 保留为示例配置文件，API Key 改为占位符。

说明：

- `.gitignore` 只影响“提交 Git 时忽略哪些本地文件”，不会影响别人通过 GitHub 下载项目。
- GitHub 下载包会包含所有已提交源码、`requirements.txt`、`.env.example`、README 和本文档。

### `3acd145` - 改进策略管理界面

提交信息：`feat: improve strategy management UI`

本次提交对应三个核心需求。

#### 1. 策略管理界面显示触发条件和执行步骤详情

原问题：

- 策略管理页只能看到策略名称、优先级、触发条件数量和步骤数量。
- 完整触发条件和步骤详情只能在 AI 聊天中输入类似“xx 策略详情”查看。

原因判断：

- 后端 `/api/v1/strategies` 已经返回完整数据。
- `Strategy.to_dict()` 中已经包含 `triggers` 和 `steps`。
- 问题主要在前端 `frontend/web/admin.html` 的 `loadStrategies()`，之前只渲染了数量，没有渲染详情。

改动方式：

- 在策略列表每条策略下方增加可展开的详情区域。
- 展示触发条件字段、操作符、值。
- 展示执行步骤 ID、Agent、动作、超时时间和 `params`。
- 增加 HTML 转义，避免策略名称、字段值中含特殊字符时破坏页面结构。

涉及文件：

- `frontend/web/admin.html`

#### 2. 策略管理界面支持手动删除策略

原问题：

- 页面不能直接删除策略。
- 只能通过 AI 聊天触发删除操作。

原因判断：

- 后端删除接口已经存在：`DELETE /api/v1/strategies/{strategy_id}`。
- 策略引擎中 `remove_strategy()` 已经实现软删除，删除后进入回收站。
- 策略管理页已有回收站、恢复、永久删除能力，但主策略列表缺少“删除”按钮。

改动方式：

- 在策略管理列表增加“操作”列。
- 增加“删除”按钮。
- 点击删除时弹出确认框。
- 调用 `DELETE /api/v1/strategies/{id}`。
- 删除成功后刷新策略列表。
- 删除行为仍然是软删除，策略进入回收站，保留 30 天，可恢复。

涉及文件：

- `frontend/web/admin.html`
- 复用既有后端接口：`frontend/routes/api.py`
- 复用既有策略引擎逻辑：`engine/strategy_engine.py`

#### 3. 手动添加策略时限制触发条件输入格式

原问题：

- 手动添加新策略时，触发条件最后一栏总是普通文本输入。
- 无论字段是 `port`、`protocol` 还是 IP，输入值都按字符串提交。
- 这会导致策略匹配时类型不一致，例如端口 `"22"` 和实际上下文端口 `22` 不相等。

原因判断：

- 前端 `doAddStrategy()` 之前直接读取 `.t-val` 字符串。
- 后端 `StrategyConfig.triggers` 之前是 `list[dict]`，缺少嵌套字段校验。
- 策略引擎在执行时依赖字段类型，例如：
  - `port` 应该是整数。
  - `in` 应该能接收列表。
  - `exists` 应该不要求用户输入值。
  - `gt/lt` 应该只用于数值字段。

前端改动：

- 根据字段和操作符动态调整输入提示和类型。
- `port`：
  - 普通比较必须是 `0-65535` 整数。
  - `in` 支持逗号分隔端口列表，并转换为数字数组。
  - 不允许 `contains` 和 `regex`。
- `protocol`：
  - 限制为 `tcp/udp/http/https/ftp/smtp/icmp`。
  - `in` 支持逗号分隔列表，并统一转小写。
- `source_ip` / `dest_ip`：
  - 普通比较校验 IP 格式。
  - `in` 支持 IP 列表。
  - `regex` 校验正则表达式是否可编译。
  - 不允许 `gt/lt`。
- `exists`：
  - 禁用输入框。
  - 提交值统一为 `true`。

后端改动：

- 新增 `TriggerConfig` Pydantic 模型。
- 将 `StrategyConfig.triggers` 从 `list[dict]` 改成 `list[TriggerConfig]`。
- 后端按字段和操作符再次校验并规范化值。
- API 创建策略时使用 `t.model_dump()` 转回策略引擎需要的字典结构。

这样即使绕过前端直接调用 API，也会被后端校验拦住。

涉及文件：

- `frontend/web/admin.html`
- `contracts/schemas.py`
- `frontend/routes/api.py`

验证：

- Python 编译检查通过。
- 前端脚本语法检查通过。
- 有效触发条件可以被规范化，例如端口字符串转整数、协议转小写、`exists` 转布尔值。
- 无效端口值会被 Pydantic 拒绝。

### `771b1a2` - 合并 GitHub 初始历史

提交信息：`merge: include github initial history`

背景：

- GitHub 仓库创建时已经有一个初始 README 提交。
- 本地项目也有独立 Git 历史。
- 第一次推送被 GitHub 拒绝，因为远端已有提交。

处理方式：

- 拉取远端 `origin/main`。
- 使用 `--allow-unrelated-histories` 合并两条历史。
- README 冲突时保留本地完整 README。
- 合并后再推送到 GitHub。

说明：

- 没有使用强推覆盖远端历史。
- 保留了 GitHub 初始提交记录。

### `2d426e3` - 更新本地运行说明和依赖

提交信息：`docs: update local setup instructions`

关键内容：

- 将 README 的运行方式修正为当前实际可用流程：

```bash
pip install -r requirements.txt
python -m uvicorn system.app:app --host 0.0.0.0 --port 8099
```

- 说明浏览器访问：

```text
http://localhost:8099
```

- 说明首次默认密码：

```text
123456
```

- 说明进入「模型配置」页面填写 API Key 后，对话功能可用。
- 在 `requirements.txt` 增加 `python-dotenv>=1.0.0`。

原因：

- `system/settings.py` 使用了 `from dotenv import load_dotenv`。
- 干净环境只安装原 `requirements.txt` 可能缺少 `python-dotenv`，导致启动失败。

验证：

- 用无 `.env` 的干净导出包模拟 GitHub 下载状态。
- 使用随机端口启动 uvicorn。
- `/api/v1/health` 返回正常。
- 首次运行 `/api/v1/auth/status` 显示默认密码状态。

### `f0c008e` - 恢复 README 项目概览

提交信息：`docs: restore project overview in readme`

背景：

- 前一版 README 过度聚焦本地运行说明，删掉了原来的项目结构介绍、API 表和多智能体流程。

处理方式：

- 从 Git 历史中取回原 README 的项目概览内容。
- 合并新的本地运行说明。
- README 现在同时包含：
  - 项目介绍。
  - 架构图。
  - 本地运行步骤。
  - `.gitignore` 和本地配置说明。
  - API 列表。
  - 多智能体编排流程。
  - 常用 Git 操作。

## 当前使用方式摘要

GitHub 用户下载项目后，通常只需要：

```bash
pip install -r requirements.txt
python -m uvicorn system.app:app --host 0.0.0.0 --port 8099
```

然后打开：

```text
http://localhost:8099
```

首次进入页面修改默认密码 `123456`，再到「模型配置」页面填写 API Key。

## 版本管理说明

- 已推送到 GitHub 的 commit 不建议随意改写消息，因为这需要强推并重写历史。
- 本文档采用新增变更日志文件的方式记录历史背景和改动原理。
- GitHub 会显示这个文件和对应提交，后来的人可以直接从 `CHANGELOG.md` 理解项目演进。
