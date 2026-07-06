from fastapi import APIRouter, HTTPException
from contracts.schemas import StrategyConfig, ExecutionRequest

router = APIRouter(prefix="/api/v1", tags=["网闸AI设备智能体"])


def _get_app():
    from system.app import app_state
    return app_state


@router.get("/health", summary="健康检查", description="检查服务是否正常运行")
async def health():
    return {"status": "ok", "service": "gateway-ai-agent"}


@router.get("/overview", summary="系统总览", description="获取系统总览数据，包括策略数量、执行次数、日志统计等")
async def overview():
    app = _get_app()
    return app["dashboard"].get_overview()


@router.get("/strategies", summary="策略列表", description="列出当前加载的所有安全策略")
async def list_strategies():
    app = _get_app()
    return app["dashboard"].get_strategies()


@router.post("/strategies", summary="添加策略", description="动态添加一条新策略，含重复检测（kubectl apply 语义）")
async def add_strategy(config: StrategyConfig):
    app = _get_app()
    from engine.strategy_engine import Strategy
    strategy = Strategy(
        strategy_id=config.id,
        name=config.name,
        description=config.description,
        enabled=config.enabled,
        priority=config.priority,
        triggers=config.triggers,
        steps=config.steps,
    )
    result_status, diff_reason = app["engine"].add_strategy(strategy)
    logger = app.get("logger")
    if result_status == "duplicate":
        if logger:
            logger.info(f"[审计] 策略:添加 target={config.id} result=duplicate reason=内容完全一致")
        return {"status": "ok", "strategy_id": config.id, "action": "duplicate",
                "message": f"策略 '{config.name}' 内容与已存在策略完全一致，未重复添加"}
    elif result_status == "duplicate_content":
        if logger:
            logger.info(f"[审计] 策略:添加 target={config.id} result=duplicate_content reason={diff_reason}")
        return {"status": "ok", "strategy_id": config.id, "action": "duplicate_content",
                "message": f"策略 '{config.name}' 内容与已存在的策略重复（{diff_reason}），未添加"}
    elif result_status == "updated":
        if logger:
            logger.info(f"[审计] 策略:更新 target={config.id} name={config.name} priority={config.priority} result=ok reason=覆盖同ID策略 diff={diff_reason}")
        return {"status": "ok", "strategy_id": config.id, "action": "updated",
                "message": f"策略 '{config.name}' 已存在但内容不同（{diff_reason}），已更新为新版本"}
    else:
        if logger:
            logger.info(f"[审计] 策略:添加 target={config.id} name={config.name} priority={config.priority} result=ok")
        return {"status": "ok", "strategy_id": config.id, "action": "added",
                "message": f"策略 '{config.name}' 已添加"}


@router.delete("/strategies/{strategy_id}", summary="删除策略", description="删除指定ID的策略（软删除，进入回收站，30 天内可恢复）")
async def delete_strategy(strategy_id: str):
    app = _get_app()
    logger = app.get("logger")
    deleted = app["engine"].remove_strategy(strategy_id)
    if deleted:
        if logger:
            logger.info(f"[审计] 策略:删除 target={strategy_id} result=ok reason=已进入回收站可恢复")
    else:
        # 找不到时也要记审计，可追溯"谁试图删除不存在的策略"（可能是误操作或攻击者探测）
        if logger:
            logger.warning(f"[审计] 策略:删除 target={strategy_id} result=fail reason=不存在")
    return {"status": "ok", "strategy_id": strategy_id, "deleted": deleted}


@router.get("/strategies/trash", summary="回收站列表", description="查看回收站中可恢复的策略列表")
async def list_trash():
    app = _get_app()
    return {"status": "ok", "trash": app["engine"].list_trash()}


@router.post("/strategies/{strategy_id}/restore", summary="恢复策略", description="从回收站恢复已删除的策略")
async def restore_strategy(strategy_id: str, new_id: str = None):
    """恢复回收站中的策略

    - 如果原 ID 已被新策略占用，可传 new_id 指定新 ID 恢复
    - 恢复后策略会重新出现在主列表，可正常使用
    """
    app = _get_app()
    logger = app.get("logger")
    status, reason = app["engine"].restore_strategy(strategy_id, new_id=new_id)
    if status == "restored":
        if logger:
            logger.info(f"[审计] 策略:恢复 target={strategy_id} result=ok reason={reason}")
        return {"status": "ok", "action": "restored", "message": reason}
    elif status == "id_conflict":
        if logger:
            logger.warning(f"[审计] 策略:恢复 target={strategy_id} result=fail reason=ID冲突")
        return {"status": "error", "action": "id_conflict", "message": reason}
    else:
        if logger:
            logger.warning(f"[审计] 策略:恢复 target={strategy_id} result=fail reason=不在回收站")
        return {"status": "error", "action": "not_in_trash", "message": reason}


@router.delete("/strategies/trash/{strategy_id}", summary="永久删除", description="从回收站永久删除策略（不可恢复）")
async def purge_strategy(strategy_id: str):
    app = _get_app()
    logger = app.get("logger")
    purged = app["engine"].purge_strategy(strategy_id)
    if purged:
        if logger:
            logger.warning(f"[审计] 策略:永久删除 target={strategy_id} result=ok reason=从回收站清除不可恢复")
        return {"status": "ok", "action": "purged", "message": f"策略 {strategy_id} 已永久删除"}
    if logger:
        logger.warning(f"[审计] 策略:永久删除 target={strategy_id} result=fail reason=不在回收站")
    return {"status": "error", "message": f"回收站中没有策略 {strategy_id}"}


@router.patch("/strategies/{strategy_id}", summary="更新策略", description="部分更新指定策略（PATCH 语义，只改传入字段，未传字段保持原值）")
async def update_strategy(strategy_id: str, fields: dict):
    """部分更新策略（PATCH 语义）

    请求体只传需要改的字段，例如只改优先级：
        {"priority": 80}
    或禁用某策略：
        {"enabled": false}

    支持字段：name / description / enabled / priority / triggers / steps
    """
    app = _get_app()
    logger = app.get("logger")
    # 剔除空值字段（PATCH 语义：未传 = 不改）
    update_fields = {k: v for k, v in fields.items() if v is not None}
    if not update_fields:
        return {"status": "error", "message": "未提供待更新字段"}
    status, reason = app["engine"].update_strategy(strategy_id, **update_fields)
    if status == "updated":
        if logger:
            logger.info(f"[审计] 策略:更新 target={strategy_id} result=ok reason={reason}")
        return {"status": "ok", "action": "updated", "strategy_id": strategy_id, "message": reason}
    elif status == "no_change":
        if logger:
            logger.info(f"[审计] 策略:更新 target={strategy_id} result=no_change reason={reason}")
        return {"status": "ok", "action": "no_change", "strategy_id": strategy_id, "message": reason}
    else:
        if logger:
            logger.warning(f"[审计] 策略:更新 target={strategy_id} result=fail reason=不存在")
        raise HTTPException(status_code=404, detail=f"未找到策略 ID '{strategy_id}'")


@router.post("/execute", summary="执行策略编排", description="提交请求上下文，LangGraph工作流: 策略匹配→条件路由→Agent并行执行→汇总")
async def execute_strategy(req: ExecutionRequest):
    app = _get_app()
    logger = app.get("logger")
    workflow = app["workflow"]
    callback = app.get("callback")
    # 构建 LangGraph 初始 State
    initial_state = req.to_graph_state()
    # 调用 LangGraph 工作流，传入 Callback
    config = {"callbacks": [callback]} if callback else None
    if logger:
        logger.info(f"[审计] 策略:执行 context={req.to_graph_state().get('context', {})}")
    try:
        result = await workflow.ainvoke(initial_state, config=config)
        app["metrics"].increment("api_executions")
        if logger:
            matched = result.get("matched_strategies", []) if isinstance(result, dict) else []
            logger.info(f"[审计] 策略:执行 result=ok matched_count={len(matched)}")
        return result
    except Exception as e:
        if logger:
            logger.error(f"[审计] 策略:执行 result=fail reason={str(e)[:200]}")
        raise


@router.get("/executions", summary="执行记录列表", description="查看历史执行记录")
async def list_executions(limit: int = 20):
    app = _get_app()
    return app["dashboard"].get_execution_history(limit)


@router.get("/executions/{execution_id}", summary="执行详情", description="查看单次执行的详细信息")
async def get_execution(execution_id: str):
    app = _get_app()
    for exec_data in app["engine"].execution_history:
        if exec_data["execution_id"] == execution_id:
            return exec_data
    raise HTTPException(status_code=404, detail="\u627e\u4e0d\u5230\u8be5\u6267\u884c\u8bb0\u5f55")


@router.get("/logs", summary="日志查询", description="查询系统运行日志，可按级别过滤")
async def get_logs(level: str = "", limit: int = 50):
    app = _get_app()
    if level:
        return app["logger"].query(level=level, limit=limit)
    return app["logger"].get_recent(limit)


@router.get("/metrics", summary="运行指标", description="获取系统运行指标，包括计数器和延迟统计")
async def get_metrics():
    app = _get_app()
    return app["metrics"].snapshot()


@router.get("/agents", summary="Agent状态", description="查看各Agent的运行状态和指标")
async def get_agents():
    app = _get_app()
    return app["dashboard"].get_agent_status()


@router.get("/alerts", summary="告警列表", description="查看监控Agent产生的告警，可按严重程度过滤")
async def get_alerts(severity: str = ""):
    app = _get_app()
    if severity:
        return app["monitor"].get_alerts(severity)
    return app["monitor"].get_alerts()
