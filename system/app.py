import json
import logging
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from system.settings import settings
from system.llm_client import LLMClient
from engine.message_bus import MessageBus
from engine.strategy_engine import StrategyEngine
from engine.graph import build_graph
from agents.orchestrator import OrchestratorAgent
from agents.security import SecurityAgent
from agents.policy import PolicyAgent
from agents.monitor import MonitorAgent
from system.logger import AgentLogger
from system.metrics import MetricsCollector
from system.dashboard import DashboardDataProvider
from system.callbacks import AgentCallbackHandler
from frontend.routes.api import router as api_router
from frontend.routes.chat import router as chat_router
from frontend.routes.models import router as model_router
from frontend.routes.auth import router as auth_router, PasswordStore
from frontend.routes.settings import router as settings_router

app_state = {}


def create_app():
    app = FastAPI(title="网闸AI设备智能体", description="LangGraph重构版", version="3.0.0")

    bus = MessageBus()
    logger = AgentLogger(level=getattr(logging, settings.LOG_LEVEL, logging.INFO))
    metrics = MetricsCollector()

    llm_client = None
    _model_config_path = os.path.join(tempfile.gettempdir(), "gateway_ai_models.json")
    if os.path.exists(_model_config_path):
        try:
            with open(_model_config_path, "r", encoding="utf-8") as f:
                _models = json.load(f)
            for _m in _models:
                if _m.get("api_key"):
                    llm_client = LLMClient(
                        api_key=_m["api_key"],
                        base_url=_m.get("api_base", "https://api.deepseek.com/v1"),
                        # 优先使用 model 字段（实际模型名），id 可能被重命名为 deepseek-chat-2
                        model=_m.get("model") or _m.get("id", "deepseek-chat"),
                        timeout=settings.LLM_TIMEOUT,
                    )
                    logger.info("Model from web config: " + _m.get("name", _m["id"]))
                    break
        except Exception:
            pass
    if not llm_client and settings.deepseek_configured:
        llm_client = LLMClient(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
            model=settings.DEEPSEEK_MODEL,
        )
    if not llm_client:
        logger.warning("No model configured")

    engine = StrategyEngine(message_bus=bus)
    strategies_path = Path(settings.STRATEGIES_PATH)
    if strategies_path.exists():
        engine.load_from_yaml(str(strategies_path))
        logger.info("Loaded " + str(len(engine.strategies)) + " base strategies")
    else:
        logger.warning("Strategies file not found: " + str(strategies_path))
    # 加载运行时持久化的策略（用户上次会话通过智能体添加的）
    # 业界做法：K8s 启动时先加载默认 ConfigMap，再加载用户自定义资源
    runtime_before = len(engine.strategies)
    engine.load_runtime_strategies()
    runtime_count = len(engine.strategies) - runtime_before
    if runtime_count > 0:
        logger.info(f"Loaded {runtime_count} runtime strategies")

    # 加载回收站（含自动清理过期项，业界做法：Slack/Google Drive 启动清理 30 天前删除项）
    engine._load_trash()
    if engine._trash:
        logger.info(f"Loaded {len(engine._trash)} trashed strategies (recoverable)")

    # 创建各 Agent
    monitor_agent = MonitorAgent(llm_client=llm_client, message_bus=bus)
    security_agent = SecurityAgent(llm_client=llm_client, message_bus=bus)
    policy_agent = PolicyAgent(llm_client=llm_client, message_bus=bus)
    orchestrator = OrchestratorAgent(
        llm_client=llm_client, message_bus=bus, strategy_engine=engine,
    )

    # 构建 LangGraph 工作流图
    agents_map = {
        "security_agent": security_agent,
        "policy_agent": policy_agent,
        "monitor_agent": monitor_agent,
    }
    # LangGraph Callback — 替代原 MessageBus 的 before/after/error 三段式 Hook
    callback_handler = AgentCallbackHandler(logger=logger, metrics=metrics)
    workflow = build_graph(agents_map, engine, bus)
    logger.info("LangGraph workflow compiled")

    # 保留 MessageBus 订阅（Agent 内部仍可用 bus 通信）
    async def h_security(msg):
        metrics.increment("security_tasks")
        logger.info("Security agent received: " + str(msg.payload.get("action")))
    async def h_policy(msg):
        metrics.increment("policy_tasks")
        logger.info("Policy agent received: " + str(msg.payload.get("action")))
    async def h_monitor(msg):
        metrics.increment("monitor_tasks")
        logger.info("Monitor agent received: " + str(msg.payload.get("action")))

    bus.subscribe("task:audit", h_security)
    bus.subscribe("task:compliance_check", h_policy)
    bus.subscribe("task:analyze", h_policy)
    bus.subscribe("task:health_check", h_monitor)
    bus.subscribe("task:analyze_anomaly", h_monitor)

    dashboard = DashboardDataProvider(
        logger=logger, metrics=metrics, strategy_engine=engine,
        orchestrator=orchestrator, monitor_agent=monitor_agent,
    )

    global app_state
    app_state.update({
        "app": app, "bus": bus, "logger": logger, "metrics": metrics,
        "llm_client": llm_client, "engine": engine,
        "workflow": workflow,  # LangGraph 编译图
        "callback": callback_handler,  # LangGraph Callback
        "orchestrator": orchestrator, "security_agent": security_agent,
        "policy_agent": policy_agent, "monitor": monitor_agent,
        "dashboard": dashboard,
    })

    app.include_router(api_router)
    app.include_router(chat_router)
    app.include_router(model_router)
    app.include_router(auth_router)
    app.include_router(settings_router)

    # 启动时初始化默认密码（123456）—— bcrypt 哈希存储，不存明文
    if PasswordStore.init_default_if_missing():
        logger.warning("Default password initialized (123456). Please change it via Settings.")
    elif PasswordStore.is_default():
        logger.warning("Password is still default (123456). Please change it via Settings.")

    # Admin page（每次请求重新读取文件，并禁用浏览器缓存，确保代码修改立即生效）
    admin_html = Path(__file__).parent.parent / "frontend" / "web" / "admin.html"
    if admin_html.exists():
        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        async def admin_page():
            content = admin_html.read_text(encoding="utf-8")
            return HTMLResponse(content, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    # Dashboard page
    dash_html = Path(__file__).parent.parent / "frontend" / "web" / "dashboard.html"
    if dash_html.exists():
        @app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
        async def dash_page():
            return HTMLResponse(dash_html.read_text(encoding="utf-8"))

    @app.on_event("shutdown")
    async def shutdown():
        if llm_client:
            llm_client.close()
        logger.info("Shutdown")

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run("system.app:app", host=settings.HOST, port=settings.PORT,
                log_level=settings.LOG_LEVEL.lower())
