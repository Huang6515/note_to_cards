"""灵动笔卡 (NoteMind AI) 后端服务。

将 Markdown 课程笔记转换为结构化复习提纲与自测卡片。
"""

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from llm import generate_review_content

# 加载项目根目录下的 .env 文件（用于配置 LLM 密钥等）
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(
    title="灵动笔卡 (NoteMind AI)",
    description="Markdown 课程笔记转智能复习提纲与自测卡片",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class GenerateRequest(BaseModel):
    markdown: str = Field(
        default="",
        description="用户粘贴的 Markdown 课程笔记",
        max_length=50000,
    )


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/generate")
async def generate(request: GenerateRequest):
    """接收 Markdown 文本，返回提纲与题目。"""
    data = await generate_review_content(request.markdown)
    return data


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")