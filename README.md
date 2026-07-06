# 网闸AI设备智能体 (Gateway AI Agent)

模拟 AI 网关配置网页，嵌入大模型对话能力。项目基于多智能体框架，面向网闸 AI 设备管理场景，支持策略管理、策略编排、模型配置、日志记录、指标监控和可观测性看板。

## 架构

```text
┌─────────────┐     ┌──────────────────┐     ┌──────────────┐
│   API路由    │────▶│   策略编排器      │────▶│  大模型 API   │
│  (FastAPI)  │     │  Orchestrator    │     │  客户端       │
└─────────────┘     └───────┬──────────┘     └──────────────┘
                            │
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼
   ┌────────────┐   ┌────────────┐   ┌────────────┐
   │ 安全审查Agent│   │ 策略分析Agent│   │ 监控Agent   │
   └────────────┘   └────────────┘   └────────────┘
          │                 │                 │
          └─────────────────┼─────────────────┘
                            ▼
                   ┌────────────────┐
                   │   策略引擎      │
                   │  + 消息总线     │
                   └────────────────┘
                            │
                   ┌────────────────┐
                   │  可观测性层     │
                   │ 日志/指标/看板  │
                   └────────────────┘
```

## 本地运行

### 1. 下载项目

方式一：在 GitHub 页面点击 `Code` -> `Download ZIP`，解压后进入项目目录。

方式二：使用 Git 克隆：

```bash
git clone https://github.com/ImFaller/gateway-ai-agent.git
cd gateway-ai-agent
```

### 2. 安装依赖

建议使用 Python 3.10 或更高版本。

```bash
pip install -r requirements.txt
```

### 3. 启动服务

```bash
python -m uvicorn system.app:app --host 0.0.0.0 --port 8099
```

### 4. 打开网页

浏览器访问：

```text
http://localhost:8099
```

### 5. 首次使用

首次进入页面会提示修改默认密码。

默认密码：

```text
123456
```

请按页面提示设置新密码。

### 6. 配置模型

进入「模型配置」页面，填写模型服务信息和 API Key。

DeepSeek 示例：

```text
提供商：DeepSeek
模型：deepseek-chat
API 地址：https://api.deepseek.com/v1
API Key：你的 API Key
```

保存并测试连接成功后，对话功能即可使用。

## 重要说明

- `.gitignore` 已经提交到仓库，用来避免把本地 `.env`、缓存文件、虚拟环境、日志等内容上传到 GitHub。
- `.gitignore` 不会影响别人下载和运行项目。GitHub 下载包会包含所有已提交的源码文件、`requirements.txt`、`.env.example` 和 README。
- 如果通过网页「模型配置」填写 API Key，通常不需要手动创建 `.env`。
- 模型配置、密码、运行时新增策略等数据会保存在本机临时目录中，不会提交到 GitHub。
- 如果需要使用 `.env` 配置默认模型，可以复制 `.env.example` 为 `.env`，再填写自己的 API Key。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/health` | 健康检查 |
| GET | `/api/v1/overview` | 系统总览 |
| GET | `/api/v1/strategies` | 策略列表 |
| POST | `/api/v1/strategies` | 添加策略 |
| DELETE | `/api/v1/strategies/{id}` | 删除策略，进入回收站 |
| PATCH | `/api/v1/strategies/{id}` | 更新策略 |
| GET | `/api/v1/strategies/trash` | 策略回收站列表 |
| POST | `/api/v1/strategies/{id}/restore` | 从回收站恢复策略 |
| DELETE | `/api/v1/strategies/trash/{id}` | 从回收站永久删除策略 |
| POST | `/api/v1/execute` | 执行策略编排 |
| GET | `/api/v1/executions` | 执行历史 |
| GET | `/api/v1/logs` | 日志查询 |
| GET | `/api/v1/metrics` | 指标数据 |
| GET | `/api/v1/agents` | Agent 状态 |
| GET | `/api/v1/alerts` | 告警列表 |
| GET | `/api/v1/models` | 模型配置列表 |
| POST | `/api/v1/models` | 添加模型配置 |
| POST | `/api/v1/chat` | 智能体对话 |

## 多智能体编排流程

1. **接收请求**：策略编排器接收外部请求，例如数据流、网络包或用户提交的执行上下文。
2. **策略匹配**：策略引擎评估上下文，匹配符合触发条件的策略。
3. **任务分派**：通过消息总线将任务分派给对应 Agent：
   - 安全审查 Agent：数据内容安全分析、威胁检测。
   - 策略分析 Agent：规则解读、合规检查。
   - 监控 Agent：系统健康监测、异常告警。
4. **结果汇总**：编排器收集各 Agent 结果，做出最终决策。
5. **可观测性**：执行过程会记录日志、指标和状态，可在管理页面查看。

## 常用入口

| 功能 | 地址 |
| --- | --- |
| 管理页面 | `http://localhost:8099` |
| 健康检查 | `http://localhost:8099/api/v1/health` |
| 策略列表 | `http://localhost:8099/api/v1/strategies` |

## 常用 Git 操作

查看当前修改：

```bash
git status
```

提交本地修改：

```bash
git add .
git commit -m "说明这次改了什么"
```

上传到 GitHub：

```bash
git push
```

从 GitHub 同步最新代码：

```bash
git pull
```
