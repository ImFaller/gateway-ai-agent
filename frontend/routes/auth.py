"""
认证路由 —— 密码管理（业界标准：bcrypt 哈希存储）

参考：
- OWASP Password Storage Cheat Sheet：bcrypt/argon2 为推荐算法
- FastAPI 官方教程：passlib + bcrypt 是单用户/多用户场景的事实标准
- 不存明文：使用 bcrypt 自适应哈希 + 内置盐值

存储位置：与 gateway_ai_models.json 同目录，文件名 gateway_ai_password.json
结构：{"hash": "$2b$...", "is_default": true/false, "updated_at": "ISO时间"}

接口：
- POST /api/v1/auth/login             验证密码
- POST /api/v1/auth/change-password   修改密码（旧密码+新密码+确认新密码）
- GET  /api/v1/auth/status            查询密码状态（是否默认密码）
- POST /api/v1/auth/verify             通用密码校验（用于关键操作前二次确认）
"""
import json
import os
import tempfile
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1/auth", tags=["认证与密码"])

PASSWORD_FILE = os.path.join(tempfile.gettempdir(), "gateway_ai_password.json")
DEFAULT_PASSWORD = "123456"


class PasswordStore:
    """密码存储管理：bcrypt 哈希 + is_default 标记"""

    @staticmethod
    def _hash_password(plain: str) -> str:
        import bcrypt
        return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    @staticmethod
    def _verify_password(plain: str, hashed: str) -> bool:
        import bcrypt
        try:
            return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
        except Exception:
            return False

    @staticmethod
    def load() -> dict:
        if os.path.exists(PASSWORD_FILE):
            try:
                with open(PASSWORD_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    @staticmethod
    def save(data: dict) -> None:
        with open(PASSWORD_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @classmethod
    def init_default_if_missing(cls) -> bool:
        """启动时调用：如果密码文件不存在，写入默认密码。返回是否新建。"""
        if not cls.load():
            cls.save({
                "hash": cls._hash_password(DEFAULT_PASSWORD),
                "is_default": True,
                "updated_at": datetime.now().isoformat(),
            })
            return True
        return False

    @classmethod
    def verify(cls, plain: str) -> bool:
        data = cls.load()
        if not data:
            # 容错：密码文件丢了，临时用默认密码
            return plain == DEFAULT_PASSWORD
        return cls._verify_password(plain, data["hash"])

    @classmethod
    def is_default(cls) -> bool:
        data = cls.load()
        return bool(data.get("is_default", True)) if data else True

    @classmethod
    def change(cls, old_password: str, new_password: str) -> tuple[bool, str]:
        if not cls.verify(old_password):
            return False, "旧密码错误"
        if len(new_password) < 6:
            return False, "新密码长度至少 6 位"
        if new_password == DEFAULT_PASSWORD:
            return False, "新密码不能与默认密码相同"
        cls.save({
            "hash": cls._hash_password(new_password),
            "is_default": False,
            "updated_at": datetime.now().isoformat(),
        })
        return True, "密码修改成功"


# ===== 请求模型 =====

class LoginRequest(BaseModel):
    password: str = Field(description="待验证的明文密码")


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(description="旧密码")
    new_password: str = Field(description="新密码（至少 6 位）")
    confirm_password: str = Field(description="确认新密码")


class VerifyRequest(BaseModel):
    password: str = Field(description="用于二次确认的密码")
    action: str = Field(default="", description="要执行的动作，用于审计日志")


# ===== 接口 =====

def _get_logger():
    """惰性获取 logger，避免循环导入"""
    try:
        from system.app import app_state
        return app_state.get("logger")
    except Exception:
        return None


@router.post("/login", summary="验证密码")
def login(req: LoginRequest):
    logger = _get_logger()
    ok = PasswordStore.verify(req.password)
    # 安全审计：登录尝试不论成功失败都要记
    # 业界做法：Linux/var/log/secure、SSH auth.log 都记失败登录用于事后追查
    if logger:
        if ok:
            logger.info("[审计] 认证:登录 result=ok")
        else:
            logger.warning("[审计] 认证:登录 result=fail reason=密码错误")
    return {"status": "ok" if ok else "error",
            "message": "密码正确" if ok else "密码错误"}


@router.post("/change-password", summary="修改密码（需旧密码验证）")
def change_password(req: ChangePasswordRequest):
    logger = _get_logger()
    if req.new_password != req.confirm_password:
        if logger:
            logger.warning("[审计] 认证:改密 result=fail reason=两次新密码不一致")
        return {"status": "error", "message": "两次输入的新密码不一致"}
    ok, msg = PasswordStore.change(req.old_password, req.new_password)
    # 改密属于高危操作，结果必须审计
    if logger:
        if ok:
            logger.info("[审计] 认证:改密 result=ok")
        else:
            logger.warning(f"[审计] 认证:改密 result=fail reason={msg}")
    return {"status": "ok" if ok else "error", "message": msg}


@router.get("/status", summary="查询密码状态（是否仍为默认密码）")
def get_status():
    # 读操作不需要审计（业界共识：查询免审计，写操作审计）
    return {"is_default": PasswordStore.is_default(),
            "has_password": bool(PasswordStore.load())}


@router.post("/verify", summary="通用密码校验（用于关键操作前二次确认）")
def verify(req: VerifyRequest):
    logger = _get_logger()
    ok = PasswordStore.verify(req.password)
    # 二次确认的密码校验也要审计：可追溯"哪次高危操作前确认过密码"
    if logger:
        action = req.action or "(未指定)"
        if ok:
            logger.info(f"[审计] 认证:二次确认 action={action} result=ok")
        else:
            logger.warning(f"[审计] 认证:二次确认 action={action} result=fail reason=密码错误")
    return {"status": "ok" if ok else "error",
            "message": "验证通过" if ok else "密码错误",
            "action": req.action}
