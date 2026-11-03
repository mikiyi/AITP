"""应用配置：路径相对项目根目录解析，环境变量优先于 .env。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 只允许监听的本机回环地址
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(slots=True)
class AppConfig:
    host: str = "127.0.0.1"
    port: int = 8760
    debug: bool = False
    db_path: Path = Path("data/aitp.db")
    log_path: Path = Path("data/aitp.log")
    deploy_log_path: Path = Path("logs/deploy.log")
    secret_key_path: Path = Path("data/.secret_key")
    prompts_dir: Path = Path("backend/prompts")
    static_dir: Path = Path("backend/aitp/static")

    @property
    def root(self) -> Path:
        return PROJECT_ROOT

    @property
    def loopback_only(self) -> bool:
        """是否只监听本机回环地址（不对局域网/公网暴露）。"""
        return self.host in LOOPBACK_HOSTS

    def resolve(self, p: Path) -> Path:
        return p if p.is_absolute() else (PROJECT_ROOT / p)


def load_config(root: Path | None = None) -> AppConfig:
    base = Path(root) if root else PROJECT_ROOT
    load_dotenv(base / ".env", override=False)

    cfg = AppConfig(
        host=os.environ.get("AITP_HOST", "127.0.0.1"),
        port=_env_int("AITP_PORT", 8760),
        debug=_env_bool("AITP_DEBUG", False),
        db_path=Path(os.environ.get("AITP_DB_PATH", "data/aitp.db")),
        log_path=Path(os.environ.get("AITP_LOG_PATH", "data/aitp.log")),
        deploy_log_path=Path(os.environ.get("AITP_DEPLOY_LOG_PATH", "logs/deploy.log")),
        secret_key_path=Path(os.environ.get("AITP_SECRET_KEY_PATH", "data/.secret_key")),
        prompts_dir=Path(os.environ.get("AITP_PROMPTS_DIR", "backend/prompts")),
        static_dir=Path(os.environ.get("AITP_STATIC_DIR", "backend/aitp/static")),
    )
    cfg.db_path = cfg.resolve(cfg.db_path)
    cfg.log_path = cfg.resolve(cfg.log_path)
    cfg.deploy_log_path = cfg.resolve(cfg.deploy_log_path)
    cfg.secret_key_path = cfg.resolve(cfg.secret_key_path)
    cfg.prompts_dir = cfg.resolve(cfg.prompts_dir)
    cfg.static_dir = cfg.resolve(cfg.static_dir)
    return cfg
