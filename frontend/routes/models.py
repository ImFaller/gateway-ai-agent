from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional
import json
import os
import tempfile


async def _test_connection(config: dict) -> dict:
    """测试模型连接，返回 {status, message, latency}"""
    if not config.get("api_key"):
        return {"status": "error", "message": "API Key 不能为空"}
    try:
        import httpx
        import time
        start = time.time()
        async with httpx.AsyncClient(timeout=15) as client:
            # 优先使用 model 字段（实际模型名，如 deepseek-chat），回退到 id
            # id 可能被自动重命名为 deepseek-chat-2，不能用作 API 调用的 model 字段
            model_name = config.get("model") or config.get("id", "deepseek-chat")
            payload = {
                "model": model_name,
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 5,
            }
            headers = {
                "Authorization": "Bearer " + config["api_key"],
                "Content-Type": "application/json",
            }
            resp = await client.post(
                config.get("api_base", "https://api.deepseek.com/v1").rstrip("/") + "/chat/completions",
                json=payload,
                headers=headers,
            )
            elapsed = round(time.time() - start, 2)
            if resp.status_code == 200:
                return {"status": "ok", "message": "连接成功（" + str(elapsed) + "秒）", "latency": elapsed}
            else:
                return {"status": "error", "message": "HTTP " + str(resp.status_code)}
    except Exception as e:
        return {"status": "error", "message": str(e)[:200]}

router = APIRouter(prefix="/api/v1/models", tags=["模型配置"])

CONFIG_FILE = os.path.join(tempfile.gettempdir(), "gateway_ai_models.json")


class ModelConfig(BaseModel):
    id: str = Field(default="deepseek-chat", description="模型唯一标识（系统内部使用，可自动重命名）")
    name: str = Field(default="DeepSeek Chat", description="模型显示名称")
    provider: str = Field(default="deepseek", description="模型提供商")
    model: str = Field(default="deepseek-chat", description="实际模型名称（用于API调用，如 deepseek-chat / gpt-4o）")
    api_base: str = Field(default="https://api.deepseek.com/v1", description="API地址")
    api_key: str = Field(default="", description="API Key")
    max_tokens: int = Field(default=4096, description="最大Token数")
    temperature: float = Field(default=0.7, description="温度参数")
    enabled: bool = Field(default=True, description="是否启用")
    # 注：role 字段已移除 —— "哪个模型当主/监视"由 settings 记录（primary_model_id / verifier_model_id）
    # 业界做法：模型是"它是什么"（identity），role 是"它被怎么用"（usage），二者分离存储
    # role 由调度层（settings）持有，模型本身不带 role 属性


def _load_models():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return []


def _save_models(models):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(models, f, ensure_ascii=False, indent=2)


def _get_logger():
    """惰性获取 logger，避免循环导入"""
    try:
        from system.app import app_state
        return app_state.get("logger")
    except Exception:
        return None


@router.get("", summary="获取所有模型配置")
async def list_models():
    return _load_models()


@router.post("", summary="添加模型配置")
async def add_model(config: ModelConfig):
    logger = _get_logger()
    models = _load_models()
    # 检测显示名称是否重复（名称用于列表展示，重复会造成混淆）
    existing_names = {m.get("name", "") for m in models}
    if config.name in existing_names:
        if logger:
            logger.warning(f"[审计] 模型:添加 target={config.name} result=fail reason=名称重复")
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": f"显示名称「{config.name}」已存在，请更换一个名称"}
        )
    # 检测到重复 ID 时自动追加数字后缀（如 deepseek-chat → deepseek-chat-2 → deepseek-chat-3）
    # 允许用户为同一模型添加多份不同 API Key 的配置
    original_id = config.id
    new_id = original_id
    suffix = 2
    existing_ids = {m["id"] for m in models}
    while new_id in existing_ids:
        new_id = f"{original_id}-{suffix}"
        suffix += 1
    renamed = new_id != original_id
    config.id = new_id
    # 自动测试连通性
    if config.api_key:
        test_result = await _test_connection(config.model_dump())
        if test_result["status"] != "ok":
            if logger:
                logger.warning(f"[审计] 模型:添加 target={config.name} result=fail reason=连接测试失败")
            return JSONResponse(status_code=400, content={"status": "error", "message": "连接测试失败: " + test_result.get("message", "未知错误")})
    models.append(config.model_dump())
    _save_models(models)
    # 不记 API Key（敏感信息），只记名称和模型类型
    if logger:
        logger.info(f"[审计] 模型:添加 target={config.name} provider={config.provider} model={config.model} result=ok")
    msg = f"模型 {config.name} 已添加（已通过连接测试）"
    if renamed:
        msg = f"模型ID '{original_id}' 已存在，已自动重命名为 '{new_id}'。" + msg
    return {"status": "ok", "message": msg, "id": new_id, "renamed": renamed}


@router.put("/{model_id}", summary="更新模型配置")
async def update_model(model_id: str, config: ModelConfig):
    logger = _get_logger()
    models = _load_models()
    for i, m in enumerate(models):
        if m["id"] == model_id:
            # 自动测试连通性
            if config.api_key:
                test_result = await _test_connection(config.model_dump())
                if test_result["status"] != "ok":
                    if logger:
                        logger.warning(f"[审计] 模型:更新 target={config.name} result=fail reason=连接测试失败")
                    return JSONResponse(status_code=400, content={"status": "error", "message": "连接测试失败: " + test_result.get("message", "未知错误")})
            models[i] = config.model_dump()
            _save_models(models)
            if logger:
                logger.info(f"[审计] 模型:更新 target={config.name} result=ok")
            return {"status": "ok", "message": f"模型 {config.name} 已更新"}
    if logger:
        logger.warning(f"[审计] 模型:更新 target={model_id} result=fail reason=不存在")
    raise HTTPException(404, f"模型 {model_id} 不存在")


@router.delete("/{model_id}", summary="删除模型配置")
async def delete_model(model_id: str):
    logger = _get_logger()
    models = _load_models()
    before = len(models)
    models = [m for m in models if m["id"] != model_id]
    if len(models) == before:
        if logger:
            logger.warning(f"[审计] 模型:删除 target={model_id} result=fail reason=不存在")
        raise HTTPException(404, f"模型 {model_id} 不存在")
    _save_models(models)
    if logger:
        logger.info(f"[审计] 模型:删除 target={model_id} result=ok")
    return {"status": "ok", "message": f"模型 {model_id} 已删除"}


@router.patch("/{model_id}/toggle", summary="切换模型启用状态")
async def toggle_model(model_id: str):
    """启用/禁用已配置的模型，无需重新填写完整配置"""
    logger = _get_logger()
    models = _load_models()
    for m in models:
        if m["id"] == model_id:
            m["enabled"] = not m.get("enabled", True)
            _save_models(models)
            state = "已启用" if m["enabled"] else "已禁用"
            if logger:
                # 切换状态影响系统行为（被禁用的模型不会被选中为主/监视模型），必须审计
                logger.info(f"[审计] 模型:切换状态 target={m.get('name', model_id)} enabled={m['enabled']} result=ok")
            return {"status": "ok", "message": f"模型 {m.get('name', model_id)} {state}", "enabled": m["enabled"]}
    if logger:
        logger.warning(f"[审计] 模型:切换状态 target={model_id} result=fail reason=不存在")
    raise HTTPException(404, f"模型 {model_id} 不存在")


@router.post("/test/{model_id}", summary="测试已保存模型的连通性")
async def test_saved_model(model_id: str):
    """根据模型ID测试已保存配置的连通性"""
    models = _load_models()
    for m in models:
        if m["id"] == model_id:
            return await _test_connection(m)
    raise HTTPException(404, f"模型 {model_id} 不存在")


@router.post("/test-all", summary="检测所有已配置模型")
async def test_all_models():
    models = _load_models()
    results = []
    for m in models:
        name = m.get("name", m.get("id", "unknown"))
        result = await _test_connection(m)
        results.append({"id": m.get("id"), "name": name, "test": result})
    return results


@router.post("/test", summary="测试模型连接")
async def test_model(config: ModelConfig):
    """尝试调用模型API验证连接是否可用"""
    if not config.api_key:
        return {"status": "error", "message": "API Key 不能为空"}
    try:
        import httpx
        start = __import__("time").time()
        async with httpx.AsyncClient(timeout=15) as client:
            payload = {
                "model": config.model or config.id,
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 5,
            }
            headers = {
                "Authorization": "Bearer " + config.api_key,
                "Content-Type": "application/json",
            }
            resp = await client.post(
                config.api_base.rstrip("/") + "/chat/completions",
                json=payload,
                headers=headers,
            )
            elapsed = round(__import__("time").time() - start, 2)
            if resp.status_code == 200:
                return {"status": "ok", "message": "连接成功（" + str(elapsed) + "秒）", "latency": elapsed}
            else:
                body = resp.text[:200]
                return {"status": "error", "message": "HTTP " + str(resp.status_code) + ": " + body}
    except Exception as e:
        return {"status": "error", "message": str(e)[:200]}


@router.get("/active", summary="获取当前主模型/监视模型")
async def get_active_model():
    """返回当前 settings 指定的主模型和监视模型配置。

    业界模式（LiteLLM Router）：router 层按 alias 查找模型，模型本身无 role。
    这里前端读 settings + models 联合，按 id 匹配出当前用途。
    """
    try:
        from frontend.routes.settings import get_settings
        s = get_settings()
    except Exception:
        s = {}
    primary_id = s.get("primary_model_id")
    verifier_id = s.get("verifier_model_id")

    models = _load_models()
    primary = None
    verifier = None
    for m in models:
        if primary_id and m.get("id") == primary_id and m.get("enabled", True) and m.get("api_key"):
            primary = m
        if verifier_id and m.get("id") == verifier_id and m.get("enabled", True) and m.get("api_key"):
            verifier = m
    # 兜底：未指定主模型时取第一个启用且有 api_key 的模型
    if not primary:
        for m in models:
            if m.get("enabled", True) and m.get("api_key"):
                primary = m
                break
    return {"primary": primary, "verifier": verifier}
