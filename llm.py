"""LLM 客户端：调用 OpenAI 兼容接口，将 Markdown 笔记转换为结构化 JSON。"""

import json
import logging
import os
import re

import httpx

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

_OPTION_LETTERS = ["A", "B", "C", "D", "E", "F"]


def _normalize_base_url(base_url: str) -> str:
    """规范化 base_url，兼容是否带 /v1 的情况。"""
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url:
        return ""
    if base_url.endswith("/v1"):
        return base_url + "/chat/completions"
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
    # 退而求其次：截取第一个 { 到最后一个 }，并尝试修复尾部多余的逗号与注释
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        snippet = text[start : end + 1]
        snippet = re.sub(r",\s*([}\]])", r"\1", snippet)
        snippet = re.sub(r"//[^\n]*", "", snippet)
        try:
            return json.loads(snippet)
        except json.JSONDecodeError:
            return None
    return None


def _resolve_single_answer(answer: str, options: list) -> str:
    """尽力把答案对齐到 options 中的某一项。

    兼容 answer 为「A」/「A. 选项内容」/「选项内容」等写法。
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
    stripped = re.sub(r"^[A-Fa-f][\.、:：)\s]+", "", answer).strip()
    if stripped in options:
        return stripped
    # 单个字母映射到 options 下标
    upper = answer.upper()
    if upper in _OPTION_LETTERS:
        idx = _OPTION_LETTERS.index(upper)
        if idx < len(options):
            return options[idx]
    return answer


def _normalize(data: dict) -> dict:
    """对 LLM 返回值做字段级校验与补全，丢弃无效题目。"""
    outline = data.get("outline") or {}
    if not isinstance(outline, dict):
        outline = {}
    sections = outline.get("sections") or []
    if not isinstance(sections, list):
        sections = []

    questions_raw = data.get("questions") or []
    if not isinstance(questions_raw, list):
        questions_raw = []

    questions = []
    for q in questions_raw:
        if not isinstance(q, dict):
            continue
        q_type = q.get("type")
        question = (q.get("question") or "").strip()
        explanation = (q.get("explanation") or "").strip()
        if not question:
            continue

        if q_type == "single":
            options = q.get("options") or []
            if not isinstance(options, list):
                continue
            options = [str(o).strip() for o in options if o]
            if len(options) < 2:
                continue
            answer = _resolve_single_answer(q.get("answer"), options)
            questions.append(
                {
                    "type": "single",
                    "question": question,
                    "options": options,
                    "answer": answer,
                    "explanation": explanation,
                }
            )
        elif q_type == "qa":
            answer = (q.get("answer") or "").strip()
            questions.append(
                {
                    "type": "qa",
                    "question": question,
                    "answer": answer,
                    "explanation": explanation,
                }
            )
        # 其余未知 type 忽略

    return {
        "outline": {
            "title": outline.get("title") or "复习提纲",
            "sections": sections,
        },
        "questions": questions,
        "source": "llm",
    }


async def _post_completion(client: httpx.AsyncClient, endpoint: str, payload: dict, headers: dict):
    """发送请求，并对不支持 response_format 的接口做一次降级重试。"""
    response = await client.post(endpoint, json=payload, headers=headers)
    # 部分兼容接口不接受 response_format，去掉后重试一次
    if response.status_code == 400 and "response_format" in payload:
        retry_payload = {k: v for k, v in payload.items() if k != "response_format"}
        logger.warning("接口不支持 response_format，降级重试")
        response = await client.post(endpoint, json=retry_payload, headers=headers)
    return response


async def generate_with_llm(markdown: str):
    """调用 OpenAI 兼容接口生成复习内容。

    返回 (result, failure_reason)：
    - 成功时 result 为 dict，failure_reason 为 None
    - 失败时 result 为 None，failure_reason 为 not_configured / http_error / parse_error
    """
    base_url = os.getenv("LLM_BASE_URL", "")
    api_key = os.getenv("LLM_API_KEY", "")
    model = os.getenv("LLM_MODEL", "")

    if not api_key or not model:
        return None, "not_configured"

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
            response = await _post_completion(client, endpoint, payload, headers)
            response.raise_for_status()
            body = response.json()
    except Exception as exc:
        logger.warning("LLM 请求失败: %s", exc)
        return None, "http_error"

    # 优先取 content，兼容不同返回结构
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        logger.warning("LLM 返回结构异常: %s", exc)
        return None, "parse_error"

    parsed = _extract_json(content)
    if parsed is None:
        logger.warning("LLM 返回内容无法解析为 JSON")
        return None, "parse_error"

    result = _normalize(parsed)
    if not result["questions"]:
        logger.warning("LLM 返回的 questions 为空")
        return None, "parse_error"

    return result, None


async def generate_review_content(markdown: str) -> dict:
    """入口：优先调用真实模型，失败则返回内置示例数据并标注失败原因。"""
    result, reason = await generate_with_llm(markdown)
    if result is not None:
        return result

    fallback = {**SAMPLE_DATA, "fallback_reason": reason or "unknown"}
    return fallback