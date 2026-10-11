"""LLM 客户端：调用 OpenAI 兼容接口，将 Markdown 笔记转换为结构化 JSON。

对外只暴露 :func:`generate_review_content`：成功返回模型结果，失败返回内置示例
数据并带上 ``fallback_reason``，保证前端永远有东西可渲染。
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import re
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from config import llm_settings
from sample_data import SAMPLE_DATA

logger = logging.getLogger("note_to_cards.llm")

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
        "children": []
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

3. 类型是硬性要求，不要改变：
   - outline.sections 必须是数组；每个元素的 points 必须是「字符串数组」，不要写成一句话；
   - children 必须是数组（没有子章节就给空数组 []），结构与本层相同，最多嵌套 3 层；
   - questions 必须是数组，每题只能属于 single 或 qa 两种 type。
4. outline.sections 要根据笔记的标题层级（#、##、###）组织，points 是每个章节的核心要点。
5. 至少生成 3 道单选题（type=single）与 2 道问答题（type=qa），覆盖笔记重点。
6. 单选题 answer 的取值必须是 options 数组中的一项，不允许带选项字母前缀。
7. 语言与笔记保持一致（中文笔记用中文，英文笔记用英文）。
"""

_OPTION_LETTERS = ["A", "B", "C", "D", "E", "F"]

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
_LETTER_PREFIX_RE = re.compile(r"^[A-Fa-f][.、:：)\s]+")

# 上游可重试的状态码（限流 / 网关抖动 / 5xx）
_RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})

# 结构清洗上限，防止模型返回畸形数据把前端撑爆
_MAX_SECTION_DEPTH = 4
_MAX_SECTIONS = 40
_MAX_POINTS_PER_SECTION = 20
_MAX_QUESTIONS = 40
_MAX_OPTIONS = 8

# 进程内结果缓存：同一篇笔记重复点"生成"时不再重复烧钱。
# 多副本部署时各副本各存一份，对演示场景足够。
_CACHE_TTL = 1800.0
_CACHE_MAX_ENTRIES = 128
_cache: dict[str, tuple[float, dict]] = {}


def _normalize_base_url(base_url: str) -> str:
    """把用户填的接口地址补全成 chat/completions 端点。

    兼容这些写法：

    - ``https://api.openai.com``                  → 补 ``/v1/chat/completions``
    - ``https://api.deepseek.com/v1``             → 补 ``/chat/completions``
    - ``https://open.bigmodel.cn/api/paas/v4``    → 补 ``/chat/completions``
    - ``.../v1/chat/completions``（已经是完整端点）→ 原样返回
    """
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url:
        return ""
    if base_url.endswith("/chat/completions"):
        return base_url
    # 地址里已经有业务路径（/v1、/v1beta、/api/paas/v4 …）时不再插入 /v1
    if urlsplit(base_url).path.strip("/"):
        return base_url + "/chat/completions"
    return base_url + "/v1/chat/completions"


def _extract_json(text: str) -> dict | None:
    """从 LLM 返回文本中稳健地解析出 JSON 对象。"""
    if not text:
        return None
    text = text.strip()
    # 去除可能的 markdown 代码块
    fence = _JSON_FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    # 退而求其次：截取第一个 { 到最后一个 }，并修复尾部多余的逗号。
    # 注意：这里只修尾逗号，绝不能再用正则去"清注释" —— 字符串里的
    # //（例如 https://…）会被误伤，导致整段 JSON 解析失败。
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        snippet = _TRAILING_COMMA_RE.sub(r"\1", text[start : end + 1])
        try:
            parsed = json.loads(snippet)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _resolve_single_answer(answer: Any, options: list) -> str:
    """尽力把答案对齐到 options 中的某一项。

    兼容 answer 为「A」/「A. 选项内容」/「选项内容」等写法。
    返回空串表示无法对齐，调用方会丢弃这道题。
    """
    if not isinstance(answer, str):
        return ""
    answer = answer.strip()
    if not answer:
        return ""
    # 直接命中
    if answer in options:
        return answer
    # 去掉「X. 」前缀后命中
    stripped = _LETTER_PREFIX_RE.sub("", answer).strip()
    if stripped in options:
        return stripped
    # 单个字母映射到 options 下标
    upper = answer.upper()
    if upper in _OPTION_LETTERS:
        idx = _OPTION_LETTERS.index(upper)
        if idx < len(options):
            return options[idx]
    return ""


def _question_id(q_type: Any, question: str) -> str:
    """由题干内容派生稳定 ID：前端用它做错题本与进度持久化的键。"""
    digest = hashlib.sha256(f"{q_type}\x00{question}".encode()).hexdigest()
    return digest[:12]


def _clean_points(raw: Any) -> list[str]:
    """把 points 统一成字符串数组：模型经常偷懒写成一句话或一个对象。"""
    if isinstance(raw, str):
        raw = [raw]
    elif isinstance(raw, dict):
        raw = list(raw.values())
    elif not isinstance(raw, list):
        return []
    points: list[str] = []
    for point in raw:
        if isinstance(point, (str, int, float)):
            text = str(point).strip()
            if text:
                points.append(text)
    return points[:_MAX_POINTS_PER_SECTION]


def _clean_sections(raw: Any, depth: int = 0) -> list[dict]:
    """递归清洗提纲树，保证前端可以无条件信任结构。

    前端的 ``buildOutlineNode`` 会对 points / children 直接调 ``forEach``，
    模型一旦把 points 返成字符串，整块渲染就会抛错失败 —— 所以这里必须兜住。
    """
    if depth > _MAX_SECTION_DEPTH or not isinstance(raw, list):
        return []
    cleaned: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        points = _clean_points(item.get("points"))
        children = _clean_sections(item.get("children"), depth + 1)
        if not title and not points and not children:
            continue
        cleaned.append(
            {
                "title": title or "未命名章节",
                "points": points,
                "children": children,
            }
        )
        if len(cleaned) >= _MAX_SECTIONS:
            logger.warning("提纲章节数超过上限 %s，已截断", _MAX_SECTIONS)
            break
    return cleaned


def _normalize(data: Any) -> dict:
    """对 LLM 返回值做字段级校验与补全，丢弃无效题目。"""
    if not isinstance(data, dict):
        data = {}

    outline = data.get("outline")
    if not isinstance(outline, dict):
        outline = {}

    questions_raw = data.get("questions")
    if not isinstance(questions_raw, list):
        questions_raw = []

    questions: list[dict] = []
    seen: set[str] = set()

    for item in questions_raw:
        if not isinstance(item, dict):
            continue
        q_type = item.get("type")
        question = str(item.get("question") or "").strip()
        explanation = str(item.get("explanation") or "").strip()
        if not question:
            continue

        qid = _question_id(q_type, question)
        if qid in seen:
            continue

        if q_type == "single":
            raw_options = item.get("options")
            if not isinstance(raw_options, list):
                continue
            options: list[str] = []
            for option in raw_options:
                if isinstance(option, (str, int, float)):
                    text = str(option).strip()
                    if text and text not in options:
                        options.append(text)
            if len(options) < 2:
                continue
            options = options[:_MAX_OPTIONS]
            answer = _resolve_single_answer(item.get("answer"), options)
            if answer not in options:
                # 给不出一道无法答对的题：宁可不给
                logger.warning("丢弃 answer 无法对齐到选项的单选题: %s", question[:40])
                continue
            seen.add(qid)
            questions.append(
                {
                    "id": qid,
                    "type": "single",
                    "question": question,
                    "options": options,
                    "answer": answer,
                    "explanation": explanation,
                }
            )
        elif q_type == "qa":
            answer = str(item.get("answer") or "").strip()
            if not answer:
                logger.warning("丢弃没有参考答案的问答题: %s", question[:40])
                continue
            seen.add(qid)
            questions.append(
                {
                    "id": qid,
                    "type": "qa",
                    "question": question,
                    "answer": answer,
                    "explanation": explanation,
                }
            )
        # 其余未知 type 忽略

        if len(questions) >= _MAX_QUESTIONS:
            logger.warning("题目数量超过上限 %s，已截断", _MAX_QUESTIONS)
            break

    title = str(outline.get("title") or "").strip()
    return {
        "outline": {
            "title": title or "复习提纲",
            "sections": _clean_sections(outline.get("sections")),
        },
        "questions": questions,
        "source": "llm",
    }


async def _post_completion(
    client: httpx.AsyncClient,
    endpoint: str,
    payload: dict,
    headers: dict,
    max_retries: int,
) -> httpx.Response:
    """发送请求；对不支持的 response_format 与可重试状态码做退避重试。"""
    payload = dict(payload)
    attempt = 0
    while True:
        response = await client.post(endpoint, json=payload, headers=headers)

        # 部分兼容接口不接受 response_format，去掉后重试一次
        if response.status_code == 400 and "response_format" in payload:
            logger.warning("接口不支持 response_format，去掉该参数后重试")
            payload = {k: v for k, v in payload.items() if k != "response_format"}
            response = await client.post(endpoint, json=payload, headers=headers)

        if response.status_code in _RETRYABLE_STATUS and attempt < max_retries:
            delay = min(8.0, 0.8 * (2**attempt))
            logger.warning(
                "上游返回 %s，%.1fs 后重试（%s/%s）",
                response.status_code,
                delay,
                attempt + 1,
                max_retries,
            )
            await asyncio.sleep(delay)
            attempt += 1
            continue
        return response


async def generate_with_llm(
    markdown: str, client: httpx.AsyncClient | None = None
) -> tuple[dict | None, str | None]:
    """调用 OpenAI 兼容接口生成复习内容。

    返回 ``(result, failure_reason)``：

    - 成功：``result`` 为 dict，``failure_reason`` 为 None
    - 失败：``result`` 为 None，``failure_reason`` 取 ``not_configured`` /
      ``timeout`` / ``http_error`` / ``truncated`` / ``parse_error`` /
      ``no_questions`` 之一
    """
    settings = llm_settings()
    if not settings["api_key"] or not settings["model"]:
        return None, "not_configured"

    endpoint = _normalize_base_url(settings["base_url"])
    if not endpoint:
        return None, "not_configured"

    payload = {
        "model": settings["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": markdown.strip() or "(空笔记)"},
        ],
        "temperature": 0.3,
        "max_tokens": settings["max_tokens"],
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {settings['api_key']}",
        "Content-Type": "application/json",
    }

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings["timeout"], connect=10.0)
        )
    try:
        response = await _post_completion(
            client, endpoint, payload, headers, settings["max_retries"]
        )
        response.raise_for_status()
        body = response.json()
    except httpx.TimeoutException:
        logger.warning("LLM 请求超时（LLM_TIMEOUT=%s 秒）", settings["timeout"])
        return None, "timeout"
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "LLM 返回错误状态 %s：%s",
            exc.response.status_code,
            exc.response.text[:200],
        )
        return None, "http_error"
    except Exception as exc:  # noqa: BLE001 - 任何网络异常都降级，不能让接口 500
        logger.warning("LLM 请求失败: %s: %s", type(exc).__name__, exc)
        return None, "http_error"
    finally:
        if own_client:
            await client.aclose()

    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or not choices:
        logger.warning("LLM 返回结构异常：没有 choices")
        return None, "parse_error"

    choice = choices[0] if isinstance(choices[0], dict) else {}
    if choice.get("finish_reason") == "length":
        logger.warning(
            "模型输出被 max_tokens=%s 截断，笔记可能过长", settings["max_tokens"]
        )
        return None, "truncated"

    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        logger.warning("LLM 返回内容为空")
        return None, "parse_error"

    usage = body.get("usage")
    if isinstance(usage, dict) and usage:
        logger.info(
            "LLM 用量 model=%s prompt=%s completion=%s total=%s",
            body.get("model"),
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            usage.get("total_tokens"),
        )

    parsed = _extract_json(content)
    if parsed is None:
        logger.warning("LLM 返回内容无法解析为 JSON")
        return None, "parse_error"

    result = _normalize(parsed)
    if not result["questions"]:
        logger.warning("LLM 返回的题目为空或全部无效")
        return None, "no_questions"

    return result, None


def _cache_key(markdown: str, model_identity: str) -> str:
    return hashlib.sha256(f"{model_identity}\x00{markdown}".encode()).hexdigest()


def _cache_get(key: str) -> dict | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    created_at, value = entry
    if time.monotonic() - created_at > _CACHE_TTL:
        _cache.pop(key, None)
        return None
    return copy.deepcopy(value)


def _cache_put(key: str, value: dict) -> None:
    if len(_cache) >= _CACHE_MAX_ENTRIES:
        oldest = min(_cache, key=lambda k: _cache[k][0])
        _cache.pop(oldest, None)
    _cache[key] = (time.monotonic(), copy.deepcopy(value))


def clear_cache() -> None:
    """清空结果缓存（测试与运维用）。"""
    _cache.clear()


async def generate_review_content(
    markdown: str, client: httpx.AsyncClient | None = None
) -> dict:
    """入口：优先命中缓存，其次调用真实模型，失败则返回内置示例数据。

    降级结果**不进缓存**，这样用户补好 Key 之后立刻就能生效。
    """
    settings = llm_settings()
    cache_key = _cache_key(
        markdown.strip(), f"{settings['base_url']}|{settings['model']}"
    )

    cached = _cache_get(cache_key)
    if cached is not None:
        logger.info("命中缓存，跳过模型调用")
        cached["cached"] = True
        return cached

    result, reason = await generate_with_llm(markdown, client=client)
    if result is not None:
        _cache_put(cache_key, result)
        return result

    logger.info("降级为内置示例数据，原因=%s", reason)
    fallback = copy.deepcopy(SAMPLE_DATA)
    fallback["fallback_reason"] = reason or "unknown"
    fallback["degraded"] = True
    return fallback
