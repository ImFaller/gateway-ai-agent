"""系统设置存储 —— 监视强度、监视模型选择等

借鉴业界 SaaS 应用的 settings store 模式：简单 JSON 持久化，键值对存储。
"""
import os
import json
import tempfile
from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import Optional

router = APIRouter(prefix="/api/v1/settings", tags=["系统设置"])

SETTINGS_FILE = os.path.join(tempfile.gettempdir(), "gateway_ai_settings.json")

# 监视强度可选值
# strong: 强一致（双签）—— 所有动作都需主+监视模型一致同意
# high_risk_only: 仅高危审查（推荐）—— 仅 add/delete_strategy 调监视模型
# warn_only: 仅警告 —— 监视不通过时只提示，仍执行
# off: 关闭 —— 不调监视模型
VERIFY_STRENGTH_VALUES = {"strong", "high_risk_only", "warn_only", "off"}


class SystemSettings(BaseModel):
    verify_strength: str = Field(default="off", description="监视强度：strong/high_risk_only/warn_only/off")
    verifier_model_id: Optional[str] = Field(default=None, description="监视模型ID（对应 models 配置的 id）")
    primary_model_id: Optional[str] = Field(default=None, description="主模型ID（对应 models 配置的 id，未设置时取第一个启用模型）")


def _load_settings() -> dict:
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 兼容旧数据：补默认字段
            data.setdefault("verify_strength", "off")
            data.setdefault("verifier_model_id", None)
            data.setdefault("primary_model_id", None)
            return data
    except Exception:
        pass
    return {"verify_strength": "off", "verifier_model_id": None, "primary_model_id": None}


def _save_settings(settings: dict):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def get_settings() -> dict:
    """供其他模块调用（如 chat.py 读取监视强度）"""
    return _load_settings()


@router.get("", summary="获取系统设置")
async def get_settings_api():
    return _load_settings()


def _get_logger():
    """惰性获取 logger，避免循环导入"""
    try:
        from system.app import app_state
        return app_state.get("logger")
    except Exception:
        return None


@router.put("", summary="更新系统设置")
async def update_settings_api(settings: SystemSettings):
    logger = _get_logger()
    if settings.verify_strength not in VERIFY_STRENGTH_VALUES:
        if logger:
            logger.warning(f"[审计] 设置:更新 result=fail reason=非法监视强度 verify_strength={settings.verify_strength}")
        return {"status": "error", "message": f"非法的监视强度，可选值：{VERIFY_STRENGTH_VALUES}"}
    _save_settings(settings.model_dump())
    # 设置变更影响系统行为（如监视强度从 off 改为 strong 会让所有高危操作都要双模型验证），必须审计
    if logger:
        logger.info(
            f"[审计] 设置:更新 verify_strength={settings.verify_strength} "
            f"primary_model_id={settings.primary_model_id} verifier_model_id={settings.verifier_model_id} result=ok"
        )
    return {"status": "ok", "message": "设置已保存"}
