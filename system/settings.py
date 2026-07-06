import os
from pathlib import Path

from dotenv import load_dotenv


class Settings:
    """全局配置管理"""

    def __init__(self):
        self.BASE_DIR = Path(os.path.dirname(os.path.dirname(__file__)))
        # 加载 .env 文件到环境变量
        env_path = self.BASE_DIR / ".env"
        if env_path.exists():
            load_dotenv(str(env_path), override=True)
        self.load_from_env()

    def load_from_env(self):
        self.DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
        self.DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
        self.DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        self.LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "60"))
        self.HOST = os.getenv("HOST", "0.0.0.0")
        self.PORT = int(os.getenv("PORT", "8099"))
        self.LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
        self.STRATEGIES_PATH = os.getenv(
            "STRATEGIES_PATH",
            str(self.BASE_DIR / "engine" / "strategies.yaml")
        )

    @property
    def deepseek_configured(self):
        return bool(self.DEEPSEEK_API_KEY)


settings = Settings()
