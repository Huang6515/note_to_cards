"""集中读取环境变量。

设计原则：**配置写错不应该让服务 500**。任何解析失败都回退到默认值并留下日志，
这样用户把 ``LLM_TIMEOUT=60s``（带单位）写进 .env 时只会看到一条 warning，
而不是整个接口报错。
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlsplit

logger = logging.getLogger("note_to_cards.config")

# 一个模型都不配时使用的兜底地址（与 .env.example 保持一致）
DEFAULT_BASE_URL = "https://api.openai.com"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_TOKENS = 4000
DEFAULT_MAX_RETRIES = 2


def env_str(name: str, default: str = "") -> str:
    """读取字符串环境变量并去掉首尾空白。"""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip()


def env_int(name: str, default: int) -> int:
    raw = env_str(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("环境变量 %s=%r 不是合法整数，回退为 %s", name, raw, default)
        return default


def env_float(name: str, default: float) -> float:
    raw = env_str(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("环境变量 %s=%r 不是合法数字，回退为 %s", name, raw, default)
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = env_str(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def llm_settings() -> dict:
    """当前 LLM 配置快照（含密钥，只在服务端使用）。"""
    return {
        "base_url": env_str("LLM_BASE_URL") or DEFAULT_BASE_URL,
        "api_key": env_str("LLM_API_KEY"),
        "model": env_str("LLM_MODEL"),
        "timeout": env_float("LLM_TIMEOUT", DEFAULT_TIMEOUT),
        "max_tokens": env_int("LLM_MAX_TOKENS", DEFAULT_MAX_TOKENS),
        "max_retries": env_int("LLM_MAX_RETRIES", DEFAULT_MAX_RETRIES),
    }


def public_config() -> dict:
    """给 /api/health 用的配置摘要：能让前端提前发现"没配 Key"，但不泄露密钥。"""
    settings = llm_settings()
    host = urlsplit(settings["base_url"]).netloc or settings["base_url"]
    return {
        "llm_configured": bool(settings["api_key"] and settings["model"]),
        "model": settings["model"],
        "endpoint_host": host,
    }
