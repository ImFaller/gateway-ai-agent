from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Union
import json, os
import re
import traceback
import tempfile

router = APIRouter(prefix="/api/v1/chat", tags=["智能体对话"])

CONFIG_FILE = os.path.join(tempfile.gettempdir(), "gateway_ai_models.json")


class ChatRequest(BaseModel):
    message: str = Field(description="用户消息")
    history: list[dict] = Field(default_factory=list, description="对话历史")


class ChatResponse(BaseModel):
    reply: str
    actions: list[dict] = []


DELETE_TARGET_REQUIRED_MESSAGE = (
    "缺少要删除的具体策略。请提供策略 ID 或策略名称，例如："
    "「删除策略 id 为 strategy 的策略」或「删除策略名称为 策略 的策略」。"
)


def _get_active_model():
    """读取当前主模型。

    优先级：
    1. settings.primary_model_id 指定的模型（若存在且启用、有 api_key）
    2. 第一个 enabled 且有 api_key 的模型（兜底）
    """
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                models = json.load(f)
        else:
            return None
        # 1) 优先用设置中指定的主模型
        try:
            from frontend.routes.settings import get_settings
            primary_id = get_settings().get("primary_model_id")
        except Exception:
            primary_id = None
        if primary_id:
            for m in models:
                if m.get("id") == primary_id and m.get("enabled", True) and m.get("api_key"):
                    return m
        # 2) 兜底：第一个启用且有 api_key 的模型
        for m in models:
            if m.get("enabled", True) and m.get("api_key"):
                return m
    except Exception:
        pass
    return None


@router.post("", summary="与智能体对话")
async def chat_with_agent(req: ChatRequest):
    active_model = _get_active_model()
    result = await _call_langchain_agent(req.message, req.history, active_model)
    return result


# ===== 技能注册表 =====
# 参考 LangChain @tool 的 args_schema 模式 + CrewAI/OpenCode 的 allow/ask/deny 权限模型
# LangChain: 每个 Tool 用 Pydantic BaseModel 定义 args_schema 做参数校验
# CrewAI:   permission = allow(自动执行) / ask(需确认) / deny(禁止)


# ===== 嵌套字段的 Pydantic Schema =====
# 参照 LangChain args_schema 模式：不仅顶层字段有校验，嵌套字段也要有 schema
# 这样 LLM 返回的 triggers/steps/context 即使字段名对、值类型错也能在入口拦下
# 比直接用 list[dict] / dict 强 —— 裸 dict 等于没校验


class TriggerCondition(BaseModel):
    """触发条件嵌套 schema —— 对应 engine.StrategyCondition"""
    field: str = Field(description="字段名，如 source_ip/port/protocol 等")
    operator: str = Field(description="运算符：eq/ne/gt/lt/contains/in/regex/exists")
    # value 支持字符串或列表：eq/ne/gt/lt/contains/regex/exists 用 str，in 用 list
    # 例如 in 操作符：{"operator":"in","value":["http","https"]}
    value: Union[str, list[str]] = Field(default="", description="比较值（in 操作符时传列表）")

    @field_validator("operator")
    @classmethod
    def _validate_operator(cls, v):
        allowed = {"eq", "ne", "gt", "lt", "contains", "in", "regex", "exists"}
        if v not in allowed:
            raise ValueError(f"operator 必须是 {allowed} 之一，收到 '{v}'")
        return v


class StrategyStep(BaseModel):
    """执行步骤嵌套 schema —— 对应 engine.StrategyStep"""
    step_id: str = Field(description="步骤唯一ID")
    agent: str = Field(description="执行者：security/policy/monitor")
    action: str = Field(description="动作名")
    params: dict = Field(default_factory=dict, description="动作参数")
    timeout: int = Field(default=30, ge=1, le=600, description="超时秒数")


class ExecutionContext(BaseModel):
    """执行上下文嵌套 schema —— 对应 contracts.ExecutionRequest 的 context 部分"""
    source_ip: str = Field(default="", description="源IP")
    dest_ip: str = Field(default="", description="目标IP")
    protocol: str = Field(default="tcp", description="协议")
    port: int = Field(default=0, ge=0, le=65535, description="端口")
    content: str = Field(default="", description="传输内容")

    @field_validator("protocol")
    @classmethod
    def _validate_protocol(cls, v):
        allowed = {"tcp", "udp", "http", "https", "ftp", "smtp", "icmp"}
        if v not in allowed:
            raise ValueError(f"protocol 必须是 {allowed} 之一，收到 '{v}'")
        return v


class AddStrategyArgs(BaseModel):
    """add_strategy 的参数 schema（LangChain args_schema 模式）
    升级：triggers/steps 从 list[dict] 改为 list[TriggerCondition]/list[StrategyStep]
    嵌套字段类型错误现在能在入口拦下，而不是等到引擎层才崩"""
    id: str = Field(description="策略唯一标识")
    name: str = Field(description="策略名称")
    enabled: bool = Field(default=True, description="是否启用")
    priority: int = Field(default=50, ge=1, le=999, description="优先级")
    triggers: list[TriggerCondition] = Field(default_factory=list, description="触发条件")
    steps: list[StrategyStep] = Field(default_factory=list, description="执行步骤")


class DeleteStrategyArgs(BaseModel):
    """delete_strategy 的参数 schema"""
    strategy_id: str = Field(description="要删除的策略ID")


class ExecuteStrategyArgs(BaseModel):
    """execute_strategy 的参数 schema
    升级：context 从裸 dict 改为 ExecutionContext 嵌套模型
    LLM 返回 port="abc" 或 protocol="xyz" 现在能在入口拦下"""
    context: ExecutionContext = Field(default_factory=ExecutionContext, description="执行上下文")


class RestoreStrategyArgs(BaseModel):
    """restore_strategy 的参数 schema
    strategy_id 可为：
    - 普通策略 ID（恢复单条）
    - "__all__"（恢复所有回收站策略）
    """
    strategy_id: str = Field(description="要恢复的策略 ID（来自回收站），或 '__all__' 恢复全部")
    new_id: Optional[str] = Field(default=None, description="可选，原 ID 冲突时指定新 ID 恢复")


class UpdateStrategyArgs(BaseModel):
    """update_strategy 的参数 schema（PATCH 语义，部分字段更新）

    与 AddStrategyArgs 的区别：
    - AddStrategyArgs 要求全量字段（id/name/...）—— kubectl apply 语义
    - UpdateStrategyArgs 除 strategy_id 必填外，其余字段都 Optional —— PATCH 语义
      未传的字段保持原值，只改传入的字段

    适用场景：用户只想改某个字段（如优先级/启用状态），不想重写整个策略
    """
    strategy_id: str = Field(description="要更新的策略 ID（必须已存在）")
    name: Optional[str] = Field(default=None, description="可选，新策略名称")
    description: Optional[str] = Field(default=None, description="可选，新描述")
    enabled: Optional[bool] = Field(default=None, description="可选，启用/禁用")
    priority: Optional[int] = Field(default=None, ge=1, le=999, description="可选，新优先级")
    triggers: Optional[list[TriggerCondition]] = Field(default=None, description="可选，新触发条件列表（整体替换）")
    steps: Optional[list[StrategyStep]] = Field(default=None, description="可选，新执行步骤列表（整体替换）")


# 技能注册表：name → {permission, args_schema}
# permission: allow=自动执行, ask=需确认, deny=禁止（CrewAI 模式）
SKILL_REGISTRY = {
    "get_system_status": {
        "permission": "allow",
        "args_schema": None,
    },
    "add_strategy": {
        "permission": "ask",
        "args_schema": AddStrategyArgs,
    },
    "execute_strategy": {
        "permission": "ask",
        "args_schema": ExecuteStrategyArgs,
    },
    "delete_strategy": {
        "permission": "ask",
        "args_schema": DeleteStrategyArgs,
    },
    "restore_strategy": {
        "permission": "ask",
        "args_schema": RestoreStrategyArgs,
    },
    "update_strategy": {
        "permission": "ask",
        "args_schema": UpdateStrategyArgs,
    },
}


@router.post("/execute-action", summary="执行智能体指定的操作")
async def execute_action(data: dict):
    action = data.get("action", "")
    params = data.get("params", {})
    confirmed = data.get("confirmed", False)
    password = data.get("password", "")

    def get_app():
        from system.app import app_state
        return app_state

    app = get_app()
    engine = app.get("engine")
    logger = app.get("logger")

    # 1. 白名单校验（LangChain: 未注册的 tool 不会被调用）
    skill = SKILL_REGISTRY.get(action)
    if not skill:
        logger.warning(f"[审计] 策略:拒绝未注册 action={action} result=fail")
        return {"status": "error", "message": f"未注册的操作: {action}"}

    # 2. 权限校验（CrewAI: allow/ask/deny 模型）
    permission = skill["permission"]
    if permission == "deny":
        logger.warning(f"[审计] 策略:禁止操作 action={action} result=fail reason=deny")
        return {"status": "error", "message": f"操作 '{action}' 已被禁止"}
    if permission == "ask" and not confirmed:
        return {
            "status": "confirmation_required",
            "message": f"操作 '{action}' 需要确认后执行",
            "permission": "ask",
        }

    # 2.5 密码二次校验 —— 受保护动作（add/delete/execute_strategy）需要密码确认
    # 业界模式：金融/运维系统对高危操作的二次认证（2FA-like）
    # 密码每次都经 bcrypt 验证，前端 sessionStorage 缓存避免用户重复输入
    PROTECTED_ACTIONS = {"add_strategy", "delete_strategy", "execute_strategy", "restore_strategy", "update_strategy"}
    if action in PROTECTED_ACTIONS:
        if not password:
            return {
                "status": "password_required",
                "message": f"操作 '{action}' 需要密码二次确认",
            }
        from frontend.routes.auth import PasswordStore
        if not PasswordStore.verify(password):
            logger.warning(f"[审计] 认证:二次确认 action={action} result=fail reason=密码错误")
            return {"status": "password_wrong", "message": "密码错误"}

    # 3. 参数校验（LangChain: args_schema Pydantic 校验）
    args_schema = skill["args_schema"]
    if args_schema:
        try:
            validated = args_schema.model_validate(params)
            params = validated.model_dump()
        except Exception as e:
            if logger:
                logger.warning(f"[审计] 策略:参数校验 action={action} result=fail reason={str(e)[:120]}")
            return {"status": "error", "message": f"参数校验失败: {str(e)[:200]}"}

    if action == "add_strategy":
        try:
            from engine.strategy_engine import Strategy
            s = Strategy(
                strategy_id=params.get("id","custom"),
                name=params.get("name","自定义策略"),
                enabled=params.get("enabled",True),
                priority=params.get("priority",50),
                triggers=params.get("triggers",[]),
                steps=params.get("steps",[]),
            )
            # 检查重复：add_strategy 已含双重查重（同 ID/同内容）
            result_status, diff_reason = engine.add_strategy(s)
            if result_status == "duplicate":
                logger.info(f"[审计] 策略:添加 target={params.get('id','custom')} result=duplicate reason=内容完全一致")
                return {"status":"ok","message":f"策略 '{params.get('name')}' 内容与已存在策略完全一致，未重复添加"}
            elif result_status == "duplicate_content":
                logger.info(f"[审计] 策略:添加 target={params.get('id','custom')} result=duplicate_content reason={diff_reason}")
                return {"status":"ok","message":f"策略 '{params.get('name')}' 内容与已存在的策略重复（{diff_reason}），未添加。如需修改请直接编辑原策略"}
            elif result_status == "updated":
                logger.info(f"[审计] 策略:更新 target={params.get('id','custom')} name={params.get('name','自定义策略')} priority={params.get('priority',50)} result=ok reason=覆盖同ID策略 diff={diff_reason}")
                return {"status":"ok","message":f"策略 '{params.get('name')}' 已存在但内容不同（{diff_reason}），已更新为新版本"}
            else:
                logger.info(f"[审计] 策略:添加 target={params.get('id','custom')} name={params.get('name','自定义策略')} priority={params.get('priority',50)} result=ok")
                return {"status":"ok","message":f"策略 '{params.get('name')}' 已添加"}
        except TypeError as e:
            # 触发器/步骤字段名不匹配（LLM 返回的格式与引擎不符）
            logger.warning(f"[审计] 策略:添加 result=fail reason=参数格式错误 detail={str(e)[:120]}")
            return {"status":"error","message":f"策略字段格式不匹配，请检查 triggers/steps 结构: {str(e)[:150]}"}
        except Exception as e:
            logger.warning(f"[审计] 策略:添加 result=fail reason={str(e)[:120]}")
            return {"status":"error","message":f"添加策略失败: {str(e)[:150]}"}

    elif action == "execute_strategy":
        # 调用 LangGraph 工作流
        workflow = app.get("workflow")
        callback = app.get("callback")
        if not workflow:
            return {"status": "error", "message": "工作流未初始化"}
        initial_state = {
            "context": params.get("context", {}),
            "execution_id": "",
            "matched_strategies": [],
            "steps": [],
            "agent_results": [],
            "status": "",
            "route_to": [],
            "error": "",
        }
        config = {"callbacks": [callback]} if callback else None
        if logger:
            logger.info(f"[审计] 策略:执行 context={params.get('context', {})}")
        try:
            result = await workflow.ainvoke(initial_state, config=config)
            matched = result.get("matched_strategies", [])
            steps = result.get("steps", [])
            if logger:
                logger.info(f"[审计] 策略:执行 result=ok matched_count={len(matched)}")
            return {"status": "ok", "message": f"已执行策略编排，匹配 {len(matched)} 条策略，分发 {len(steps)} 个步骤", "result": result}
        except Exception as e:
            if logger:
                logger.error(f"[审计] 策略:执行 result=fail reason={str(e)[:200]}")
            return {"status": "error", "message": f"执行失败: {str(e)[:150]}"}

    elif action == "delete_strategy":
        strategy_id = params.get("strategy_id","")
        if not strategy_id:
            return {"status": "error", "message": "strategy_id 不能为空"}
        # 内部审查元数据不参与业务执行，删除前仅保留真正的策略 ID。
        strategy_id = str(strategy_id).strip()
        # 拒绝批量删除（业界共识：聊天场景批量删除太危险）
        # 业界参考：Linux rm -rf 要 -f / kubectl delete all 要 --all / 数据库 DROP TABLE 需高权限
        # 通配符检测：__all__/all/* 等都视为批量删除意图
        if strategy_id.lower() in {"__all__", "all", "*", "全部", "所有"}:
            logger.warning(f"[审计] 策略:删除 target={strategy_id} result=fail reason=拒绝批量删除")
            return {
                "status": "error",
                "message": (
                    "检测到批量删除意图，已拒绝执行。"
                    "智能体对话场景不支持批量删除策略，"
                    "请到「策略管理」页面逐条删除（删除后会进入回收站，可恢复）。"
                )
            }
        deleted = engine.remove_strategy(strategy_id)
        if not deleted:
            # 未找到该 ID，附上现有策略列表帮助用户/LLM 定位
            existing = [f"{s.strategy_id}（{s.name}）" for s in engine.strategies]
            hint = "、".join(existing) if existing else "（当前无策略）"
            logger.warning(f"[审计] 策略:删除 target={strategy_id} result=fail reason=不存在")
            return {"status":"error","message":f"未找到策略 ID '{strategy_id}'，现有策略: {hint}"}
        logger.info(f"[审计] 策略:删除 target={strategy_id} result=ok reason=已进入回收站可恢复")
        return {"status":"ok","message":f"策略 '{strategy_id}' 已删除（已进入回收站，30 天内可恢复）"}

    elif action == "restore_strategy":
        strategy_id = params.get("strategy_id", "")
        new_id = params.get("new_id")
        if not strategy_id:
            # 回收站为空或 LLM 无法匹配
            trash = engine.list_trash()
            if not trash:
                return {"status":"error","message":"回收站为空，无可恢复的策略"}
            hint = "、".join(f"{t['strategy_id']}（{t['name']}）" for t in trash)
            return {"status":"error","message":f"未指定要恢复的策略 ID。回收站现有: {hint}"}
        # __all__ 恢复全部
        if strategy_id == "__all__":
            trash = engine.list_trash()
            if not trash:
                return {"status":"ok","message":"回收站为空，无需恢复"}
            success, fail = [], []
            for t in trash:
                sid = t["strategy_id"]
                status, reason = engine.restore_strategy(sid, new_id=None)
                if status == "restored":
                    success.append(sid)
                else:
                    fail.append(f"{sid}({reason})")
            logger.info(f"[审计] 策略:恢复 all result=ok success={len(success)} fail={len(fail)}")
            msg = f"批量恢复完成：成功 {len(success)} 条"
            if fail:
                msg += f"，失败 {len(fail)} 条（{'; '.join(fail)}）"
            return {"status":"ok","message":msg}
        # 恢复单条
        status, reason = engine.restore_strategy(strategy_id, new_id=new_id)
        if status == "restored":
            logger.info(f"[审计] 策略:恢复 target={strategy_id} result=ok reason={reason}")
            return {"status":"ok","message":reason}
        elif status == "id_conflict":
            logger.warning(f"[审计] 策略:恢复 target={strategy_id} result=fail reason=ID冲突")
            return {"status":"error","message":f"恢复失败：{reason}。请换一个 ID 重试，或先删除当前占用该 ID 的策略"}
        else:
            logger.warning(f"[审计] 策略:恢复 target={strategy_id} result=fail reason=不在回收站")
            trash = engine.list_trash()
            hint = "、".join(f"{t['strategy_id']}（{t['name']}）" for t in trash) if trash else "（回收站为空）"
            return {"status":"error","message":f"回收站中没有策略 ID '{strategy_id}'，回收站现有: {hint}"}

    elif action == "update_strategy":
        strategy_id = params.get("strategy_id","")
        if not strategy_id:
            # 没传 ID，附上现有策略列表帮助 LLM/用户定位
            existing = [f"{s.strategy_id}（{s.name}）" for s in engine.strategies]
            hint = "、".join(existing) if existing else "（当前无策略）"
            logger.warning(f"[审计] 策略:更新 result=fail reason=缺少strategy_id")
            return {"status":"error","message":f"未指定要更新的策略 ID，现有策略: {hint}"}
        # 收集实际要更新的字段（剔除 strategy_id 和值为 None 的字段）
        update_fields = {
            k: v for k, v in params.items()
            if k != "strategy_id" and v is not None
        }
        if not update_fields:
            return {"status":"error","message":f"未指定要更新的字段（除 strategy_id 外至少传一个字段）"}
        status, reason = engine.update_strategy(strategy_id, **update_fields)
        if status == "updated":
            logger.info(f"[审计] 策略:更新 target={strategy_id} result=ok reason={reason}")
            return {"status":"ok","message":f"策略 '{strategy_id}' 已更新（{reason}）"}
        elif status == "no_change":
            logger.info(f"[审计] 策略:更新 target={strategy_id} result=no_change reason={reason}")
            return {"status":"ok","message":f"策略 '{strategy_id}' 未改动（{reason}）"}
        else:
            # not_found —— 附上现有策略列表帮助 LLM/用户定位
            existing = [f"{s.strategy_id}（{s.name}）" for s in engine.strategies]
            hint = "、".join(existing) if existing else "（当前无策略）"
            logger.warning(f"[审计] 策略:更新 target={strategy_id} result=fail reason=不存在")
            return {"status":"error","message":f"未找到策略 ID '{strategy_id}'，现有策略: {hint}"}

    elif action == "get_system_status":
        try:
            app = get_app()
            engine = app.get("engine")
            logger = app.get("logger")
            metrics = app.get("metrics")

            strategies_list = engine.strategies if engine else []
            stats = engine.get_execution_stats() if engine else {}
            metric_snap = metrics.snapshot() if metrics else {}
            log_stats = logger.get_statistics() if logger else {}

            # 判断用户是想看"概览"还是"某个策略详情"
            # 用户输入里如果带了策略 ID 或名称关键字，就返回详情；否则返回概览
            # 例如："查看 ssh_protection 详情" / "查看SSH防护策略" → 详情
            #       "查看系统状态" / "健康检查" → 概览
            # user_message 从 params.__user_message 取（由 StrategyQueryAgent 注入）
            user_message = params.get("__user_message", "") if isinstance(params, dict) else ""
            detail_target = _extract_strategy_target_from_message(user_message, strategies_list)

            if detail_target:
                # 返回单个策略的完整详情（triggers + steps）
                msg = _format_strategy_detail(detail_target)
                return {"status": "ok", "message": msg}
            else:
                # 返回系统概览
                msg = (
                    f"**系统状态**: 运行中\n"
                    f"- 策略数量: {len(strategies_list)} 条\n"
                    f"- 执行总次数: {stats.get('total_executions', 0)}\n"
                    f"- 成功: {stats.get('succeeded', 0)} / 失败: {stats.get('failed', 0)}\n"
                    f"- 日志总量: {log_stats.get('total', 0)}\n"
                )
                if strategies_list:
                    names = [s.name for s in strategies_list[:10]]
                    msg += f"- 已加载策略: {', '.join(names)}\n"
                    msg += "\n💡 如需查看某条策略的详细配置，可输入「查看 ssh_protection 详情」或「SSH防护策略详情」"
                return {"status": "ok", "message": msg}
        except Exception as e:
            return {"status": "ok", "message": "系统运行中（查询详情时遇到问题: " + str(e)[:60] + "）"}

    return {"status":"error","message":f"未知操作: {action}"}


async def _call_langchain_agent(message, history, model_config=None):
    """主从智能体架构 —— 1 主调度 + N 子智能体 + 输出校验

    业界模式：
    - LangChain RouterChain / AutoGen GroupChatManager：主从架构
    - Hermes Verifier：子智能体输出后加校验节点，避免 LLM 偶尔返回错误格式

    流程：
        用户消息
           ↓
        [第 0 层] 注入话术检测（代码层拦截，不调 LLM）
           ↓ 通过
        RouterAgent（主智能体，第 1 次 LLM）→ 识别意图 intent
           ↓
        路由分发：
          - strategy_config  → StrategyConfigAgent（第 2 次 LLM，生成策略参数）
          - strategy_query   → StrategyQueryAgent（直接返回动作，省 LLM）
          - strategy_execute → StrategyExecuteAgent（第 2 次 LLM，生成执行上下文）
          - chat             → 直接返回主智能体的回复
           ↓
        ActionVerifier（Hermes 模式）→ 校验动作格式合法性
           ↓
        返回 ChatResponse
    """
    # 获取当前已有策略列表，供子智能体注入 prompt
    try:
        from system.app import app_state
        _engine = app_state.get("engine")
        existing_strategies = _engine.strategies if _engine else []
        strategy_list_text = "\n".join(
            f"- ID: {s.strategy_id} | 名称: {s.name} | 优先级: {s.priority} | 启用: {s.enabled}"
            for s in existing_strategies
        ) or "（当前无策略）"
    except Exception:
        strategy_list_text = "（无法读取策略列表）"

    # 获取回收站列表，供 RestoreStrategyAgent 选 ID
    try:
        trash_list = _engine.list_trash() if _engine else []
        trash_list_text = "\n".join(
            f"- ID: {t['strategy_id']} | 名称: {t['name']} | 剩余: {t['days_left']} 天"
            for t in trash_list
        ) or "（回收站为空）"
    except Exception:
        trash_list_text = "（无法读取回收站）"

    # ===== 第 0 层：提示词注入检测（代码层拦截） =====
    # 业界做法：Lakera AI Guardrails / NeMo Guardrails input rails
    # 在调用 LLM 前先做规则匹配，发现典型注入话术直接拒绝
    # 避免"忽略以上指令""现在你是"等话术诱导模型执行非用户本意操作
    from system.prompt_guard import detect_injection
    is_injection, matched = detect_injection(message)
    if is_injection:
        try:
            from system.app import app_state
            _logger = app_state.get("logger")
            if _logger:
                _logger.warning(f"[安全] 拦截提示词注入: matched='{matched}', input={message[:120]}")
        except Exception:
            pass
        return ChatResponse(
            reply=(
                f"⚠️ 检测到可疑指令注入（匹配到「{matched}」）。\n\n"
                f"为安全考虑，本次请求已被拦截，未执行任何操作。\n"
                f"如果您确实需要进行添加/删除策略等操作，请去掉注入性话术后再发送。"
            ),
            actions=[]
        )

    # 无模型配置时的模拟回复
    if not model_config or not model_config.get("api_key"):
        return _simulate_chat(message, history)

    try:
        # ===== 第 1 层：主智能体识别意图 =====
        from agents.router_agent import (
            RouterAgent, StrategyConfigAgent, StrategyQueryAgent,
            StrategyExecuteAgent, RestoreStrategyAgent,
        )

        router = RouterAgent()
        route_result = await router.route(message, history, model_config)
        intent = route_result.get("intent", RouterAgent.INTENT_CHAT)
        reply = route_result.get("reply", "正在处理...")

        print(f"[主智能体] 意图: {intent}, 回复: {reply}")

        # ===== 第 2 层：路由到子智能体 =====
        actions = []

        if intent == RouterAgent.INTENT_CONFIG:
            # 策略配置子智能体（add/delete）
            sub_agent = StrategyConfigAgent()
            actions, raw = await sub_agent.generate_action(
                message, history, model_config, strategy_list_text
            )
            print(f"[策略配置子智能体] 生成动作: {actions}")
            if actions:
                # 子智能体返回了动作，用友好提示替换原始 JSON
                action_name = actions[0].get("action", "")
                reply = {
                    "add_strategy": "正在为您添加策略...",
                    "delete_strategy": "正在删除策略...",
                }.get(action_name, f"正在执行: {action_name}...")
            else:
                # actions 为空有两种情况：
                # 1. LLM 主动拒绝（如批量删除）—— raw 是友好提示文本，直接用
                # 2. LLM 真的没生成有效内容 —— 用默认提示
                if raw and len(raw.strip()) > 10:
                    reply = raw.strip()
                else:
                    reply = reply or "未能识别具体的策略操作"

        elif intent == RouterAgent.INTENT_QUERY:
            # 策略查询子智能体（不需要 LLM，直接返回动作）
            sub_agent = StrategyQueryAgent()
            actions, raw = await sub_agent.generate_action(
                message, history, model_config, strategy_list_text
            )
            print(f"[策略查询子智能体] 生成动作: {actions}")

        elif intent == RouterAgent.INTENT_EXECUTE:
            # 策略执行子智能体
            sub_agent = StrategyExecuteAgent()
            actions, raw = await sub_agent.generate_action(
                message, history, model_config, strategy_list_text
            )
            print(f"[策略执行子智能体] 生成动作: {actions}")
            if actions:
                reply = "正在执行策略编排..."

        elif intent == RouterAgent.INTENT_RESTORE:
            # 策略恢复子智能体（从回收站恢复）
            sub_agent = RestoreStrategyAgent()
            actions, raw = await sub_agent.generate_action(
                message, history, model_config, trash_list_text
            )
            print(f"[策略恢复子智能体] 生成动作: {actions}")
            if actions:
                reply = "正在恢复策略..."

        else:
            # INTENT_CHAT：普通对话，直接用主智能体的回复
            pass

        # ===== 第 3 层：Hermes Verifier —— 校验子智能体输出格式 =====
        # 业界模式：Hermes Agent v0.18 的 "自证清白" —— LLM 输出后加 Verifier 节点
        # 避免偶发的格式错误（缺字段、类型不对、action 不在白名单等）
        # 校验失败的动作被剔除，不让下游 execute-action 处理坏数据
        if actions:
            verified_actions = []
            for act in actions:
                ok, err = _verify_action_format(act)
                if ok:
                    verified_actions.append(act)
                else:
                    print(f"[Verifier] 剔除格式不合法的动作: {act}，原因: {err}")
            actions = verified_actions
            # 如果校验后动作全被剔除，提示用户
            if not actions and intent != RouterAgent.INTENT_CHAT:
                reply = "⚠️ 智能体生成的动作格式有误，请重试或换种描述方式。"

        if actions:
            actions, target_reply = _resolve_strategy_action_targets(message, actions, existing_strategies)
            if target_reply:
                reply = target_reply

        # ===== 第 4 层：LLM-as-a-Judge —— 监视模型语义审查 =====
        # 业界模式：OpenAI Evals "LLM-as-a-Judge" / Hermes MoA / LangChain CriteriaEvalChain
        # 用第二个独立模型审查主模型生成的动作是否符合用户意图、是否安全
        # 与第 3 层 Hermes Verifier 的区别：第 3 层看"格式"，第 4 层看"语义"
        if actions:
            actions, judge_reply = await _run_llm_judge(message, actions, intent)
            if judge_reply:
                # 监视模型有意见要反馈给用户
                reply = judge_reply if not actions else reply + "\n\n" + judge_reply

        return ChatResponse(reply=reply, actions=actions)

    except Exception as e:
        error_detail = traceback.format_exc()
        print(f"[主从智能体] 异常: {error_detail}")
        return ChatResponse(
            reply=f"调用大模型失败: {str(e)}\n\n请检查模型配置是否正确。",
            actions=[]
        )


# ===== 策略详情查询辅助函数 =====
# 业界做法：Kubernetes kubectl get 支持 "get all"（概览）和 "get <name> -o yaml"（详情）两种模式
# 这里借鉴同样设计：用户消息里如果带策略 ID 或名称关键字，返回详情；否则返回概览


def _extract_strategy_target_from_message(message: str, strategies_list) -> object:
    """从用户消息里识别要查看详情的策略

    匹配规则（按优先级）：
    1. 精确匹配策略 ID
    2. 包含策略 ID（用户输入"ssh_protection 详情"）
    3. 包含策略名称（用户输入"SSH防护策略详情"）
    4. 用户输入"详情/详细/配置/内容"等关键词但未指定具体策略 → 返回 None，让用户明确指定

    Returns:
        Strategy 对象或 None（None 表示用户想看概览）
    """
    if not message or not strategies_list:
        return None

    msg_lower = message.lower()

    # 用户必须明确表达"想看详情"的意图，否则返回概览
    # 这是防止把"查看 ssh 状态"误判成"查看 ssh 策略详情"
    detail_keywords = ["详情", "详细", "配置", "内容", "触发条件", "执行步骤", "detail", "config"]
    wants_detail = any(kw in msg_lower for kw in detail_keywords)
    if not wants_detail:
        return None

    # 匹配策略 ID 或名称
    for s in strategies_list:
        if s.strategy_id and s.strategy_id.lower() in msg_lower:
            return s
        if s.name and s.name.lower() in msg_lower:
            return s

    # 用户想看详情但没匹配到任何策略
    return None


def _format_strategy_detail(strategy) -> str:
    """格式化单条策略的完整详情（分组卡片风格，无 emoji）

    输出格式参考 Kubernetes kubectl describe，分组呈现：
    - 基本信息（ID/名称/优先级/启用状态）
    - 触发条件列表
    - 执行步骤列表

    设计依据：
    - 业界共识：交互式场景（聊天/命令行）用分组卡片，不用表格
    - 参考 kubectl describe / aws describe-instances / Slack bot 回执
    """
    if not strategy:
        return "未找到对应策略"

    # 字段名中英映射（找不到映射时降级显示原文，不影响功能）
    FIELD_CN = {
        "source_ip": "源IP", "dest_ip": "目标IP",
        "protocol": "协议", "port": "端口",
    }
    OPERATOR_CN = {
        "eq": "等于", "ne": "不等于",
        "gt": "大于", "lt": "小于",
        "in": "属于", "contains": "包含",
        "regex": "匹配正则", "exists": "存在",
    }
    # agent 名称中英映射（去掉 _agent 后缀，加中文标签）
    AGENT_CN = {
        "security_agent": "安全审计", "security": "安全审计",
        "policy_agent": "合规检查", "policy": "合规检查",
        "monitor_agent": "异常告警", "monitor": "异常告警",
        "orchestrator": "编排",
    }
    # 常见参数 key 中英映射
    PARAM_KEY_CN = {
        "deep_scan": "深度扫描",
        "check_level": "检查级别",
        "alert_level": "告警级别",
        "strict": "严格", "high": "高", "low": "低", "medium": "中",
    }

    def _map_field(f: str) -> str:
        return FIELD_CN.get(f, f)

    def _map_op(op: str) -> str:
        return OPERATOR_CN.get(op, op)

    def _map_agent(a: str) -> str:
        return AGENT_CN.get(a, a)

    def _map_param(k, v) -> str:
        key_cn = PARAM_KEY_CN.get(k, k)
        if isinstance(v, bool):
            val_cn = "是" if v else "否"
        elif isinstance(v, str) and v in PARAM_KEY_CN:
            val_cn = PARAM_KEY_CN[v]
        else:
            val_cn = str(v)
        return f"{key_cn}={val_cn}"

    lines = []
    # ===== 基本信息区 =====
    lines.append(f"**{strategy.name}** 策略详情")
    lines.append("-" * 30)
    lines.append(f"ID        {strategy.strategy_id}")
    lines.append(f"优先级    {strategy.priority}")
    lines.append(f"状态      {'启用' if strategy.enabled else '禁用'}")
    lines.append("")

    # ===== 触发条件区 =====
    lines.append("触发条件（AND 关系）")
    lines.append("-" * 30)
    if strategy.triggers:
        for i, t in enumerate(strategy.triggers, 1):
            val = t.value
            # 列表值友好显示：[22, 23] → [22, 23]
            if isinstance(val, list):
                val = "[" + ", ".join(str(v) for v in val) + "]"
            # 正则用代码块标记，明显区分"数据"和"标签"
            if t.operator == "regex" and isinstance(val, str):
                lines.append(f"  {i}. {_map_field(t.field)} {_map_op(t.operator)} `{val}`")
            else:
                lines.append(f"  {i}. {_map_field(t.field)} {_map_op(t.operator)} {val}")
    else:
        lines.append("  （无）")
    lines.append("")

    # ===== 执行步骤区 =====
    lines.append("执行步骤（按顺序）")
    lines.append("-" * 30)
    if strategy.steps:
        for i, s in enumerate(strategy.steps, 1):
            agent_cn = _map_agent(s.agent)
            lines.append(f"  {i}. {agent_cn}（{s.agent}）")
            lines.append(f"     - 步骤ID: {s.step_id}")
            lines.append(f"     - 动作: {s.action}")
            if s.params:
                lines.append(f"     - 参数:")
                for k, v in s.params.items():
                    lines.append(f"       · {_map_param(k, v)}")
            if hasattr(s, 'timeout') and s.timeout:
                lines.append(f"     - 超时: {s.timeout}s")
    else:
        lines.append("  （无）")

    return "\n".join(lines)


def _message_mentions_strategy_target(message: str, strategy) -> bool:
    """判断用户原话是否明确提到了某条策略的 ID 或名称。"""
    if not message or not strategy:
        return False
    msg_lower = message.lower()
    sid = (getattr(strategy, "strategy_id", "") or "").lower()
    name = (getattr(strategy, "name", "") or "").lower()
    return bool((sid and sid in msg_lower) or (name and name in msg_lower))


def _is_bare_delete_request(message: str) -> bool:
    """识别缺少具体目标的删除请求，如「删除策略」。"""
    compact = re.sub(r"\s+", "", message or "").lower()
    bare_patterns = {
        "删除策略", "删策略", "删除一条策略", "删一条策略",
        "删除一个策略", "删一个策略", "删除该策略", "删该策略",
    }
    return compact in bare_patterns


def _find_strategy_by_id(strategy_id: str, strategies_list):
    if not strategy_id:
        return None
    sid = str(strategy_id).strip().lower()
    for strategy in strategies_list or []:
        if (strategy.strategy_id or "").lower() == sid:
            return strategy
    return None


def _find_unique_strategy_by_name_in_message(message: str, strategies_list):
    if not message:
        return None
    msg_lower = message.lower()
    matches = [
        strategy for strategy in (strategies_list or [])
        if strategy.name and strategy.name.lower() in msg_lower
    ]
    return matches[0] if len(matches) == 1 else None


def _extract_explicit_delete_target(message: str) -> tuple[str, str]:
    """从用户原话里提取明确的删除目标类型和值。"""
    if not message:
        return "", ""

    patterns = [
        ("id", r"(?:策略\s*)?id\s*(?:为|是|=|:|：)\s*([A-Za-z0-9_.-]+)"),
        ("name", r"(?:策略)?(?:名称|名字|名)\s*(?:为|是|=|:|：)\s*([^\s，。,.！？!]+)"),
    ]
    for target_type, pattern in patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            if target_type == "name":
                value = re.sub(r"(?:的)?策略$", "", value).strip()
            return target_type, value
    return "", ""


def _resolve_strategy_action_targets(message: str, actions: list[dict], strategies_list) -> tuple[list[dict], str]:
    """对高危策略动作做确定性目标解析，避免 LLM 猜 ID 或误判真实短 ID。

    返回值：
    - actions: 解析后的动作；目标不明确的删除动作会被移除
    - reply: 需要直接反馈给用户的提示，空字符串表示无需覆盖回复
    """
    resolved_actions = []
    reply = ""

    for action in actions:
        if action.get("action") != "delete_strategy":
            resolved_actions.append(action)
            continue

        params = action.setdefault("params", {})
        strategy_id = str(params.get("strategy_id", "") or "").strip()
        explicit_type, explicit_value = _extract_explicit_delete_target(message)
        matched = None

        if not explicit_type and _is_bare_delete_request(message):
            reply = DELETE_TARGET_REQUIRED_MESSAGE
            continue

        if explicit_type == "id":
            matched = _find_strategy_by_id(explicit_value, strategies_list)
            strategy_id = explicit_value
        elif explicit_type == "name":
            name_matches = [
                strategy for strategy in (strategies_list or [])
                if (strategy.name or "").lower() == explicit_value.lower()
            ]
            matched = name_matches[0] if len(name_matches) == 1 else None
            if matched:
                params["strategy_id"] = matched.strategy_id
                strategy_id = matched.strategy_id

        if not matched:
            matched = _find_strategy_by_id(strategy_id, strategies_list)

        if not matched:
            matched = _find_unique_strategy_by_name_in_message(message, strategies_list)
            if matched:
                params["strategy_id"] = matched.strategy_id
                strategy_id = matched.strategy_id

        if not matched or not _message_mentions_strategy_target(message, matched):
            reply = DELETE_TARGET_REQUIRED_MESSAGE
            continue

        params["_target_exists"] = True
        params["_target_name"] = matched.name
        params["_target_source"] = "id" if (matched.strategy_id or "").lower() == strategy_id.lower() else "name"
        resolved_actions.append(action)

    return resolved_actions, reply


def _verify_action_format(action: dict) -> tuple[bool, str]:
    """Hermes Verifier：校验子智能体生成的动作格式

    校验规则：
    1. 必须有 action 字段
    2. action 必须在白名单内
    3. 各 action 的 params 必填字段是否齐全
    4. 字段类型正确（list/int/str）

    Returns:
        (is_valid, error_message)
    """
    if not isinstance(action, dict):
        return False, "动作不是 dict"

    action_name = action.get("action")
    if not action_name:
        return False, "缺少 action 字段"

    # action 白名单
    ALLOWED_ACTIONS = {"add_strategy", "delete_strategy", "execute_strategy", "get_system_status", "restore_strategy", "update_strategy"}
    if action_name not in ALLOWED_ACTIONS:
        return False, f"未知动作: {action_name}"

    params = action.get("params", {})

    # 各动作的字段校验
    if action_name == "add_strategy":
        if not params.get("id"):
            return False, "add_strategy 缺少 id 字段"
        if not params.get("name"):
            return False, "add_strategy 缺少 name 字段"
        priority = params.get("priority", 50)
        if not isinstance(priority, int) or priority < 1 or priority > 999:
            return False, f"priority 必须是 1-999 的整数，得到: {priority}"
        if not isinstance(params.get("triggers", []), list):
            return False, "triggers 必须是列表"
        if not isinstance(params.get("steps", []), list):
            return False, "steps 必须是列表"

    elif action_name == "delete_strategy":
        sid = params.get("strategy_id", "")
        if not sid:
            return False, "delete_strategy 缺少 strategy_id 字段"
        # 拒绝批量删除（业界共识：聊天场景批量删除太危险）
        if isinstance(sid, str) and sid.lower() in {"__all__", "all", "*", "全部", "所有"}:
            return False, f"拒绝批量删除（{sid}），请到策略管理页逐条删除"

    elif action_name == "execute_strategy":
        if not isinstance(params.get("context", {}), dict):
            return False, "context 必须是 dict"

    elif action_name == "restore_strategy":
        if not params.get("strategy_id"):
            return False, "restore_strategy 缺少 strategy_id 字段"

    elif action_name == "update_strategy":
        # PATCH 语义：strategy_id 必填，其余字段可选但至少要有一个
        if not params.get("strategy_id"):
            return False, "update_strategy 缺少 strategy_id 字段"
        # 收集可选字段（除 strategy_id 外）
        optional_fields = {"name", "description", "enabled", "priority", "triggers", "steps"}
        provided = {k for k in optional_fields if params.get(k) is not None}
        if not provided:
            return False, "update_strategy 至少要提供一个待更新字段（name/description/enabled/priority/triggers/steps）"
        # 类型校验
        if "priority" in provided:
            p = params.get("priority")
            if not isinstance(p, int) or p < 1 or p > 999:
                return False, f"priority 必须是 1-999 的整数，得到: {p}"
        if "enabled" in provided:
            if not isinstance(params.get("enabled"), bool):
                return False, "enabled 必须是布尔值"
        if "triggers" in provided and not isinstance(params.get("triggers"), list):
            return False, "triggers 必须是列表"
        if "steps" in provided and not isinstance(params.get("steps"), list):
            return False, "steps 必须是列表"

    # get_system_status 无需 params
    return True, ""


def _get_verifier_model():
    """获取监视模型配置（按 role=verifier/both 选取）

    返回 None 表示未配置监视模型
    """
    try:
        from frontend.routes.settings import get_settings
        from frontend.routes.models import _load_models

        settings = get_settings()
        strength = settings.get("verify_strength", "off")
        if strength == "off":
            return None, strength

        verifier_id = settings.get("verifier_model_id")
        if not verifier_id:
            return None, strength

        models = _load_models()
        for m in models:
            if m.get("id") == verifier_id and m.get("enabled", True) and m.get("api_key"):
                return m, strength
    except Exception as e:
        print(f"[监视模型] 加载失败: {str(e)[:100]}")
    return None, "off"


async def _run_llm_judge(user_message, actions, intent):
    """执行 LLM-as-a-Judge 监视（按强度策略）

    注意：本函数在 _call_langchain_agent（async）中被调用，
    内部直接 await verifier.verify，所以调用方需 await 本函数。
    为简化调用，本函数被改为 async。

    Args:
        user_message: 用户原始消息
        actions: 主模型生成的动作列表
        intent: 主智能体识别的意图

    Returns:
        tuple: (过滤后的 actions, 给用户的提示文案)
            - 过滤后的 actions：被监视模型拒绝的动作已剔除（strong/high_risk_only 模式）
            - 提示文案：监视模型的意见（拒绝原因、警告等），无意见时为空字符串
    """
    verifier_config, strength = _get_verifier_model()
    if strength == "off" or not verifier_config:
        return actions, ""

    # high_risk_only 模式：只审查高危动作（add/delete_strategy）
    HIGH_RISK_ACTIONS = {"add_strategy", "delete_strategy"}
    if strength == "high_risk_only":
        actions_to_judge = [a for a in actions if a.get("action") in HIGH_RISK_ACTIONS]
        if not actions_to_judge:
            return actions, ""  # 没有高危动作，跳过监视
    else:
        # strong / warn_only 模式：审查所有动作
        actions_to_judge = actions

    from agents.router_agent import VerifierAgent

    verifier = VerifierAgent()
    approved_actions = []
    warnings = []

    for act in actions:
        # 非高危动作在 high_risk_only 模式下直接通过
        if strength == "high_risk_only" and act.get("action") not in HIGH_RISK_ACTIONS:
            approved_actions.append(act)
            continue

        result = None
        try:
            # _run_llm_judge 在 async 上下文中调用，直接 await
            result = await verifier.verify(user_message, act, verifier_config)
        except Exception as e:
            print(f"[监视模型] 审查异常: {str(e)[:100]}")
            # 审查异常降级为通过
            approved_actions.append(act)
            continue

        approved = result.get("approve", False)
        reason = result.get("reason", "")

        if approved:
            approved_actions.append(act)
            print(f"[监视模型] 通过: {act.get('action')} - {reason}")
        else:
            print(f"[监视模型] 拒绝: {act.get('action')} - {reason}")
            if strength == "warn_only":
                # warn_only：不拦截，只警告
                approved_actions.append(act)
                warnings.append(f"⚠️ 监视模型警告（{act.get('action')}）：{reason}")
            else:
                # strong / high_risk_only：拦截该动作
                warnings.append(f"❌ 监视模型拒绝执行（{act.get('action')}）：{reason}")

    reply_msg = "\n".join(warnings) if warnings else ""
    return approved_actions, reply_msg


def _extract_json_blocks(text):
    """用栈匹配提取所有顶层 JSON 对象块（参考业界做法，支持嵌套、markdown 代码块包裹）

    逐行扫描对 markdown 代码块（```json ... ```）和嵌套 JSON 处理不稳，
    改用栈匹配花括号深度，能正确处理：
    - 单行 JSON: {"action": "x"}
    - 多行 JSON
    - 嵌套 JSON: {"params": {"triggers": [...]}}
    - markdown 代码块包裹: ```json\\n{...}\\n```
    """
    blocks = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == '{':
            depth = 0
            start = i
            in_string = False
            escape = False
            while i < n:
                c = text[i]
                if escape:
                    escape = False
                elif c == '\\':
                    escape = True
                elif c == '"':
                    in_string = not in_string
                elif not in_string:
                    if c == '{':
                        depth += 1
                    elif c == '}':
                        depth -= 1
                        if depth == 0:
                            blocks.append(text[start:i + 1])
                            break
                i += 1
        i += 1
    return blocks


def _parse_actions(reply):
    """从大模型回复中解析动作 JSON

    参考：网上 LLM action 提取常用 regex/栈匹配方式，
    用 _extract_json_blocks 提取候选块再逐个 json.loads
    """
    actions = []
    # 先去除 markdown 代码块标记（```json ... ```），避免干扰花括号匹配
    cleaned = reply
    for fence in ("```json", "```"):
        if fence in cleaned:
            cleaned = cleaned.replace(fence, "")
    # 提取所有 JSON 块
    for block in _extract_json_blocks(cleaned):
        try:
            data = json.loads(block)
            if isinstance(data, dict) and "action" in data:
                actions.append(data)
        except (json.JSONDecodeError, ValueError):
            continue
    return actions


def _simulate_chat(message, history):
    # 未配置大模型时的回复
    # 仅“查看状态”类查询不依赖大模型，可直接返回动作让系统查询实时数据
    if any(k in message for k in ["状态", "健康", "情况"]):
        return ChatResponse(
            reply="正在查询系统实时状态...",
            actions=[{"action": "get_system_status"}]
        )

    # 其余所有输入（添加策略、执行编排、删除策略等）均依赖大模型
    # 统一提示用户先配置大模型
    return ChatResponse(
        reply="⚠️ 该功能需要大模型支持，当前未配置大模型，无法处理您的请求。\n\n"
              "请先在左侧导航的「模型配置」页面中填入有效的模型信息"
              "（模型ID、API Key、Base URL）并启用，配置完成后即可正常使用。",
        actions=[]
    )
