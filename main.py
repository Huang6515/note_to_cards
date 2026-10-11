"""灵动笔卡 (NoteMind AI) 后端服务。

将 Markdown 课程笔记转换为结构化复习提纲与自测卡片。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config import env_bool, env_float, env_int, env_str, public_config
from llm import generate_review_content
from ratelimit import SlidingWindowLimiter

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# 显式指定路径：否则 load_dotenv() 会去找「当前工作目录」，从别处启动 uvicorn
# （IDE、systemd、python -m）时就读不到 .env，然后静默降级成示例数据。
# config / llm 都在请求时读取环境变量，所以这里晚于 import 也没有问题。
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(
    level=env_str("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("note_to_cards")

# 每 IP 每分钟允许的生成次数；设 0 关闭限流
RATE_LIMITER = SlidingWindowLimiter(env_int("RATE_LIMIT_PER_MINUTE", 10), 60.0)
# 同时打往上游的请求数上限，避免瞬时打满模型配额
LLM_SEMAPHORE = asyncio.Semaphore(max(1, env_int("LLM_MAX_CONCURRENCY", 4)))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """用一个长生命周期的 httpx 客户端复用连接（省掉每次请求的 TLS 握手）。"""
    app.state.http = httpx.AsyncClient(
        timeout=httpx.Timeout(env_float("LLM_TIMEOUT", 60.0), connect=10.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )
    logger.info(
        "服务启动 | 模型=%s | 限流=%s/分钟 | 并发上限=%s",
        public_config().get("model") or "未配置",
        RATE_LIMITER.limit if RATE_LIMITER.enabled else "关闭",
        env_int("LLM_MAX_CONCURRENCY", 4),
    )
    try:
        yield
    finally:
        await app.state.http.aclose()
        logger.info("服务已停止")


app = FastAPI(
    title="灵动笔卡 (NoteMind AI)",
    description="Markdown 课程笔记转智能复习提纲与自测卡片",
    version="1.1.0",
    lifespan=lifespan,
)

# 前端与后端同源，默认不需要 CORS；只有把前端部署到别处时才用 ALLOW_ORIGINS 显式开放
_allow_origins = [o.strip() for o in env_str("ALLOW_ORIGINS").split(",") if o.strip()]
if _allow_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allow_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

app.add_middleware(GZipMiddleware, minimum_size=1024)


class GenerateRequest(BaseModel):
    markdown: str = Field(
        default="",
        description="用户粘贴的 Markdown 课程笔记",
        max_length=50000,
    )


def _client_key(request: Request) -> str:
    """取客户端标识用于限流；只有显式信任反向代理时才读 X-Forwarded-For。"""
    if env_bool("TRUST_PROXY", False):
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.get("/api/health")
async def health():
    """健康检查 + 配置摘要（不含密钥），前端可据此提前提示"未配置模型"。"""
    return {"status": "ok", **public_config()}


@app.post("/api/generate")
async def generate(request: Request, payload: GenerateRequest):
    """接收 Markdown 文本，返回提纲与题目。"""
    if not payload.markdown.strip():
        return JSONResponse(status_code=400, content={"detail": "笔记内容不能为空"})

    client_key = _client_key(request)
    if not RATE_LIMITER.allow(client_key):
        retry_after = int(RATE_LIMITER.retry_after(client_key)) + 1
        logger.warning("触发限流: %s，需等待 %s 秒", client_key, retry_after)
        return JSONResponse(
            status_code=429,
            content={"detail": f"请求过于频繁，请 {retry_after} 秒后重试"},
            headers={"Retry-After": str(retry_after)},
        )

    async with LLM_SEMAPHORE:
        return await generate_review_content(
            payload.markdown, client=request.app.state.http
        )


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
