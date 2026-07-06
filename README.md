# 网闸AI设备智能体 (Gateway AI Agent)

基于多智能体框架的网闸AI设备管理系统，支持策略编排、DeepSeek API集成和可观测性监测。

## 架构

```
┌─────────────┐     ┌──────────────────┐     ┌──────────────┐
│   API路由    │────▶│   策略编排器      │────▶│  DeepSeek    │
│  (FastAPI)  │     │  Orchestrator    │     │  API 客户端  │
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

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并填入 DeepSeek API Key:

```bash
cp .env.example .env
# 编辑 .env 填入 DEEPSEEK_API_KEY
```

### 3. 启动服务

```bash
python main.py
```

服务将在 http://localhost:8099 启动，打开浏览器即可看到监控Dashboard。

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/v1/health | 健康检查 |
| GET | /api/v1/overview | 系统总览 |
| GET | /api/v1/strategies | 策略列表 |
| POST | /api/v1/strategies | 添加策略 |
| DELETE | /api/v1/strategies/{id} | 删除策略 |
| PATCH | /api/v1/strategies/{id} | 更新策略 |
| POST | /api/v1/execute | 执行策略编排 |
| GET | /api/v1/executions | 执行历史 |
| GET | /api/v1/logs | 日志查询 |
| GET | /api/v1/metrics | 指标数据 |
| GET | /api/v1/agents | Agent状态 |
| GET | /api/v1/alerts | 告警列表 |

## 多智能体编排流程

1. **接收请求**: 策略编排器接收外部请求（数据流、网络包等）
2. **策略匹配**: 策略引擎评估上下文，匹配符合条件的策略
3. **任务分派**: 通过消息总线将任务分派给对应Agent：
   - 安全审查Agent —— 数据内容安全分析、威胁检测
   - 策略分析Agent —— 规则解读、合规检查
   - 监控Agent —— 系统健康监测、异常告警
4. **结果汇总**: 编排器收集各Agent结果，做出最终决策
5. **可观测性**: 所有执行过程记录日志、指标，可在Dashboard实时查看
