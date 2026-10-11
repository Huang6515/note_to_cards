"""llm 模块测试：JSON 解析、URL 规范化、结构清洗与上游交互。"""

import asyncio
import json

import httpx
import pytest

import llm
from llm import (
    _clean_sections,
    _extract_json,
    _normalize,
    _normalize_base_url,
    _resolve_single_answer,
    generate_review_content,
    generate_with_llm,
)

# --------------------------------------------------------------------------
# 纯函数：base_url 规范化
# --------------------------------------------------------------------------


def test_normalize_base_url_without_path():
    assert (
        _normalize_base_url("https://api.openai.com")
        == "https://api.openai.com/v1/chat/completions"
    )


def test_normalize_base_url_with_v1():
    assert (
        _normalize_base_url("https://api.deepseek.com/v1")
        == "https://api.deepseek.com/v1/chat/completions"
    )


def test_normalize_base_url_trailing_slash():
    assert (
        _normalize_base_url("https://api.example.com/v1/")
        == "https://api.example.com/v1/chat/completions"
    )


def test_normalize_base_url_empty():
    assert _normalize_base_url("") == ""


def test_normalize_base_url_full_endpoint_is_idempotent():
    """用户直接粘贴控制台给的完整端点时，不能再拼一次路径。"""
    url = "https://api.example.com/v1/chat/completions"
    assert _normalize_base_url(url) == url


def test_normalize_base_url_with_versioned_path():
    """智谱的兼容地址带 /api/paas/v4，不能补成 /v4/v1/chat/completions。"""
    assert (
        _normalize_base_url("https://open.bigmodel.cn/api/paas/v4")
        == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    )


# --------------------------------------------------------------------------
# 纯函数：JSON 提取
# --------------------------------------------------------------------------


def test_extract_json_plain():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_with_fence():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_with_surrounding_text():
    assert _extract_json('这里是内容 {"a": 1} 后面还有') == {"a": 1}


def test_extract_json_invalid():
    assert _extract_json("not json at all") is None


def test_extract_json_trailing_comma():
    assert _extract_json('{"a": [1, 2, ],}') == {"a": [1, 2]}


def test_extract_json_keeps_urls_in_fallback_path():
    """回归：兜底修复尾逗号时，绝不能用正则清 "//" 注释，否则 URL 会被截断。"""
    data = _extract_json('{"a": "https://api.example.com/v1", "b": 1,}')
    assert data == {"a": "https://api.example.com/v1", "b": 1}


def test_extract_json_rejects_non_object():
    assert _extract_json("[1, 2, 3]") is None


# --------------------------------------------------------------------------
# 纯函数：答案对齐
# --------------------------------------------------------------------------


def test_resolve_single_answer_direct():
    assert _resolve_single_answer("香蕉", ["苹果", "香蕉", "橙子"]) == "香蕉"


def test_resolve_single_answer_letter():
    assert _resolve_single_answer("B", ["苹果", "香蕉", "橙子"]) == "香蕉"


def test_resolve_single_answer_letter_with_dot():
    assert _resolve_single_answer("B. 香蕉", ["苹果", "香蕉", "橙子"]) == "香蕉"


def test_resolve_single_answer_returns_empty_when_unaligned():
    """对齐失败必须返回空串，让调用方丢弃这道题。"""
    assert _resolve_single_answer("乙项", ["甲", "乙"]) == ""
    assert _resolve_single_answer("E", ["甲", "乙"]) == ""
    assert _resolve_single_answer(None, ["甲", "乙"]) == ""


# --------------------------------------------------------------------------
# 纯函数：提纲清洗
# --------------------------------------------------------------------------


def test_clean_sections_only_accepts_lists():
    assert _clean_sections(None) == []
    assert _clean_sections("not a list") == []
    assert _clean_sections([{"title": "t", "points": ["p"]}]) == [
        {"title": "t", "points": ["p"], "children": []}
    ]


def test_clean_sections_coerces_string_points():
    """模型把 points 写成一句话时，必须转成数组，否则前端 forEach 会崩。"""
    result = _clean_sections([{"title": "章", "points": "要点一；要点二"}])
    assert result[0]["points"] == ["要点一；要点二"]


def test_clean_sections_accepts_dict_points():
    result = _clean_sections([{"title": "章", "points": {"a": "要点一", "b": "要点二"}}])
    assert result[0]["points"] == ["要点一", "要点二"]


def test_clean_sections_drops_non_dict_children():
    result = _clean_sections([{"title": "章", "children": [{"title": "子"}, "oops", 42]}])
    assert len(result[0]["children"]) == 1


def test_clean_sections_limits_depth():
    node = {"title": "leaf", "points": ["p"]}
    for _ in range(10):
        node = {"title": "wrap", "points": [], "children": [node]}
    result = _clean_sections([node])
    depth = 0
    cursor = result[0]
    while cursor["children"]:
        cursor = cursor["children"][0]
        depth += 1
    assert depth <= llm._MAX_SECTION_DEPTH


def test_clean_sections_skips_empty_nodes():
    assert _clean_sections([{"title": "", "points": [], "children": []}]) == []


# --------------------------------------------------------------------------
# 纯函数：整体校验
# --------------------------------------------------------------------------


def test_normalize_drops_invalid_questions():
    data = {
        "outline": {"title": "主题", "sections": []},
        "questions": [
            {
                "type": "single",
                "question": "单选",
                "options": ["A", "B"],
                "answer": "A",
                "explanation": "x",
            },
            {"type": "qa", "question": "", "answer": "空题干应被丢弃", "explanation": ""},
            {"type": "unknown", "question": "未知类型", "answer": "x"},
            "not a dict",
        ],
    }
    result = _normalize(data)
    assert result["source"] == "llm"
    assert result["outline"]["title"] == "主题"
    assert len(result["questions"]) == 1
    assert result["questions"][0]["type"] == "single"


def test_normalize_missing_outline():
    result = _normalize({"questions": []})
    assert result["outline"]["title"] == "复习提纲"
    assert result["outline"]["sections"] == []


def test_normalize_drops_single_with_unaligned_answer():
    """回归：answer 不在 options 里时，前端永远判错且不高亮正确答案。"""
    data = {
        "questions": [
            {
                "type": "single",
                "question": "q",
                "options": ["甲", "乙", "丙", "丁"],
                "answer": "乙项",
                "explanation": "",
            }
        ]
    }
    assert _normalize(data)["questions"] == []


def test_normalize_drops_single_with_out_of_range_letter():
    data = {
        "questions": [
            {"type": "single", "question": "q", "options": ["甲", "乙"], "answer": "E"}
        ]
    }
    assert _normalize(data)["questions"] == []


def test_normalize_deduplicates_options():
    data = {
        "questions": [
            {
                "type": "single",
                "question": "q",
                "options": ["甲", "甲", "乙"],
                "answer": "甲",
            }
        ]
    }
    options = _normalize(data)["questions"][0]["options"]
    assert options == ["甲", "乙"]


def test_normalize_drops_qa_without_answer():
    data = {"questions": [{"type": "qa", "question": "q", "answer": "   "}]}
    assert _normalize(data)["questions"] == []


def test_normalize_deduplicates_questions():
    data = {
        "questions": [
            {"type": "qa", "question": "同一题", "answer": "a"},
            {"type": "qa", "question": "同一题", "answer": "a"},
        ]
    }
    assert len(_normalize(data)["questions"]) == 1


def test_normalize_caps_question_count():
    data = {
        "questions": [
            {"type": "qa", "question": f"第{i}题", "answer": "a"} for i in range(200)
        ]
    }
    assert len(_normalize(data)["questions"]) == llm._MAX_QUESTIONS


def test_normalize_assigns_stable_ids():
    data = {"questions": [{"type": "qa", "question": "q", "answer": "a"}]}
    first = _normalize(data)["questions"][0]["id"]
    second = _normalize(data)["questions"][0]["id"]
    assert first and first == second


def test_normalize_handles_garbage_input():
    assert _normalize(None)["questions"] == []
    assert _normalize("boom")["outline"]["sections"] == []


# --------------------------------------------------------------------------
# 上游交互（httpx.MockTransport，不产生真实网络请求）
# --------------------------------------------------------------------------


def _set_llm_env(monkeypatch, **overrides):
    env = {
        "LLM_API_KEY": "test-key",
        "LLM_MODEL": "test-model",
        "LLM_BASE_URL": "https://api.test",
        "LLM_MAX_RETRIES": "0",
    }
    env.update(overrides)
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _completion(content: str, finish_reason: str = "stop"):
    return httpx.Response(
        200,
        json={
            "model": "test-model",
            "choices": [
                {"finish_reason": finish_reason, "message": {"content": content}}
            ],
        },
    )


def _valid_payload():
    return {
        "outline": {
            "title": "主题",
            "sections": [{"title": "章", "points": ["要点"], "children": []}],
        },
        "questions": [
            {
                "type": "single",
                "question": "单选",
                "options": ["甲", "乙"],
                "answer": "甲",
                "explanation": "因为",
            },
            {"type": "qa", "question": "问答", "answer": "答案", "explanation": "解析"},
        ],
    }


def _run(coro):
    return asyncio.run(coro)


def test_generate_with_llm_success_payload(monkeypatch):
    _set_llm_env(monkeypatch)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return _completion(json.dumps(_valid_payload(), ensure_ascii=False))

    async def run():
        async with _client(handler) as client:
            return await generate_with_llm("笔记", client=client)

    result, reason = _run(run())
    assert reason is None
    assert [q["type"] for q in result["questions"]] == ["single", "qa"]
    assert seen["url"] == "https://api.test/v1/chat/completions"
    # 必须显式限制输出长度，否则长笔记会被上游默认上限截断
    assert seen["body"]["max_tokens"] == 4000
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["body"]["messages"][0]["role"] == "system"


def test_generate_with_llm_reports_truncation(monkeypatch):
    _set_llm_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return _completion('{"questions": [', finish_reason="length")

    async def run():
        async with _client(handler) as client:
            return await generate_with_llm("很长的笔记", client=client)

    result, reason = _run(run())
    assert result is None
    assert reason == "truncated"


def test_generate_with_llm_drops_response_format_on_400(monkeypatch):
    _set_llm_env(monkeypatch)
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": {"message": "unsupported"}})
        return _completion(json.dumps(_valid_payload(), ensure_ascii=False))

    async def run():
        async with _client(handler) as client:
            return await generate_with_llm("笔记", client=client)

    result, reason = _run(run())
    assert reason is None
    assert result["questions"]
    assert len(bodies) == 2
    assert "response_format" not in bodies[1]


async def _no_sleep(_seconds):
    return None


def test_generate_with_llm_retries_on_429(monkeypatch):
    _set_llm_env(monkeypatch, LLM_MAX_RETRIES="1")
    monkeypatch.setattr(llm.asyncio, "sleep", _no_sleep)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, json={"error": {"message": "rate limited"}})
        return _completion(json.dumps(_valid_payload(), ensure_ascii=False))

    async def run():
        async with _client(handler) as client:
            return await generate_with_llm("笔记", client=client)

    result, reason = _run(run())
    assert reason is None
    assert calls["count"] == 2


def test_generate_with_llm_reports_http_error(monkeypatch):
    _set_llm_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    async def run():
        async with _client(handler) as client:
            return await generate_with_llm("笔记", client=client)

    result, reason = _run(run())
    assert result is None
    assert reason == "http_error"


def test_generate_with_llm_reports_parse_error(monkeypatch):
    _set_llm_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return _completion("这不是 JSON")

    async def run():
        async with _client(handler) as client:
            return await generate_with_llm("笔记", client=client)

    assert _run(run())[1] == "parse_error"


def test_generate_with_llm_reports_empty_questions(monkeypatch):
    _set_llm_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return _completion('{"questions": []}')

    async def run():
        async with _client(handler) as client:
            return await generate_with_llm("笔记", client=client)

    assert _run(run())[1] == "no_questions"


def test_generate_with_llm_not_configured(monkeypatch):
    _set_llm_env(monkeypatch, LLM_API_KEY=None)

    async def run():
        return await generate_with_llm("笔记")

    result, reason = _run(run())
    assert result is None
    assert reason == "not_configured"


# --------------------------------------------------------------------------
# 入口：缓存与降级
# --------------------------------------------------------------------------


def test_generate_review_content_caches_result(monkeypatch):
    _set_llm_env(monkeypatch)
    llm.clear_cache()
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return _completion(json.dumps(_valid_payload(), ensure_ascii=False))

    async def run():
        async with _client(handler) as client:
            first = await generate_review_content("同一篇笔记", client=client)
            second = await generate_review_content("同一篇笔记", client=client)
            return first, second

    first, second = _run(run())
    assert calls["count"] == 1
    assert first["source"] == "llm"
    assert "cached" not in first
    assert second["cached"] is True
    llm.clear_cache()


def test_fallback_is_returned_when_not_configured(monkeypatch):
    _set_llm_env(monkeypatch, LLM_API_KEY=None)
    llm.clear_cache()

    async def run():
        return await generate_review_content("笔记")

    result = _run(run())
    assert result["source"] == "sample"
    assert result["fallback_reason"] == "not_configured"
    assert result["degraded"] is True
    assert result["questions"]


def test_fallback_is_not_cached(monkeypatch):
    """降级结果不能进缓存，否则用户补好 Key 之后仍然看到示例数据。"""
    _set_llm_env(monkeypatch, LLM_API_KEY=None)
    llm.clear_cache()

    async def run():
        return await generate_review_content("笔记")

    _run(run())
    assert llm._cache == {}


def test_fallback_is_an_isolated_copy(monkeypatch):
    _set_llm_env(monkeypatch, LLM_API_KEY=None)

    async def run():
        return await generate_review_content("笔记")

    first = _run(run())
    first["outline"]["title"] = "被改坏了"
    second = _run(run())
    assert second["outline"]["title"] != "被改坏了"


def test_fallback_reason_marks_http_error(monkeypatch):
    _set_llm_env(monkeypatch)
    llm.clear_cache()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "boom"}})

    async def run():
        async with _client(handler) as client:
            return await generate_review_content("笔记", client=client)

    result = _run(run())
    assert result["source"] == "sample"
    assert result["fallback_reason"] == "http_error"


@pytest.mark.parametrize("bad_value", ["60s", "", "abc"])
def test_llm_timeout_with_bad_value_falls_back(monkeypatch, bad_value):
    """回归：环境变量写错不能让接口 500。"""
    _set_llm_env(monkeypatch, LLM_TIMEOUT=bad_value)

    def handler(request: httpx.Request) -> httpx.Response:
        return _completion(json.dumps(_valid_payload(), ensure_ascii=False))

    async def run():
        async with _client(handler) as client:
            return await generate_with_llm("笔记", client=client)

    assert _run(run())[1] is None
