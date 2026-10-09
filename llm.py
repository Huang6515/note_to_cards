"""LLM 客户端：调用 OpenAI 兼容接口，将 Markdown 笔记转换为结构化 JSON。"""

import json
import os
import re

import httpx

from sample_data import SAMPLE_DATA

SYSTEM_PROMPT = """你是一名专业的课程学习助手，擅长将课程笔记整理成复习材料与自测题目。

请将用户提供的 Markdown 课程笔记，转化为结构化的复习资料。

严格要求：
1. 只输出一个合法的 JSON 对象，不要输出任何解释性文字，不要用 Markdown 代码块包裹。
2. JSON 结构必须符合以下 schema：

{
  "outline": {
    "title": "笔记主题标题",
    "sections": [
      {
        "title": "章节标题",
        "points": ["提炼后的核心要点（简洁）"],
        "children": [  /* 子章节，结构与父节点相同，可递归 */ ]
      }
    ]
  },
  "questions": [
    {
      "type": "single",
      "question": "单选题题干",
      "options": ["选项A", "选项B", "选项C", "选项D"],
      "answer": "正确选项内容（必须与 options 中某一项完全一致）",
      "explanation": "答案解析"
    },
    {
      "type": "qa",
      "question": "问答题题干",
      "answer": "参考答案",
      "explanation": "知识要点解析"
    }
  ]
}

3. outline.sections 要根据笔记的标题层级（#、##、###）组织，points 是每个章节的核心要点。
4. 至少生成 3 道单选题（type=single）与 2 道问答题（type=qa），覆盖笔记重点。
5. 单选题 answer 的取值必须是 options 数组中的一项，不允许带选项字母前缀。
6. 语言与笔记保持一致（中文笔记用中文，英文笔记用英文）。
"""


def _normalize_base_url(base_url: str) -> str:
    """规范化 base_url，兼容是否带 /v1 的情况。"""
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url:
        return ""
    if base_url.endswith("/v1") or base_url.endswith("/v1/"):
        return base_url.rstrip("/") + "/chat/completions"
    return base_url + "/v1/chat/completions"


def _extract_json(text: str):
    """从 LLM 返回文本中稳健地解析出 JSON 对象。"""
    if not text:
        return None
    text = text.strip()
    # 去除可能的 markdown 代码块
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 退而求其次：截取第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _normalize(data: dict) -> dict:
    """对 LLM 返回值做一次轻量校验与兜底。"""
    outline = data.get("outline") or {}
    questions = data.get("questions") or []
    if not isinstance(questions, list):
        questions = []
    return {
        "outline": {
            "title": outline.get("title") or "复习提纲",
            "sections": outline.get("sections") or [],
        },
        "questions": questions,
        "source": "llm",
    }


async def generate_with_llm(markdown: str) -> dict:
    """调用 OpenAI 兼容接口生成复习内容。失败时返回 None，由调用方兜底。"""
    base_url = os.getenv("LLM_BASE_URL", "")
    api_key = os.getenv("LLM_API_KEY", "")
    model = os.getenv("LLM_MODEL", "")

    if not api_key or not model:
        return None

    endpoint = _normalize_base_url(base_url or "https://api.openai.com")
    timeout = float(os.getenv("LLM_TIMEOUT", "60"))

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": markdown.strip() or "(空笔记)"},
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response.raise_for_status()
            body = response.json()
    except Exception:
        return None

    # 优先取 content，兼容不同返回结构
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None

    parsed = _extract_json(content)
    if parsed is None:
        return None
    return _normalize(parsed)


async def generate_review_content(markdown: str) -> dict:
    """入口：优先调用真实模型，失败则返回内置示例数据。"""
    result = await generate_with_llm(markdown)
    if result is not None:
        return result
    return SAMPLE_DATA