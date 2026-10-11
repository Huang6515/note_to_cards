"""llm 模块单元测试：覆盖 JSON 解析、URL 规范化与字段校验等纯函数。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm import _extract_json, _normalize, _normalize_base_url, _resolve_single_answer


def test_normalize_base_url_without_v1():
    assert _normalize_base_url("https://api.openai.com") == "https://api.openai.com/v1/chat/completions"


def test_normalize_base_url_with_v1():
    assert _normalize_base_url("https://api.deepseek.com/v1") == "https://api.deepseek.com/v1/chat/completions"


def test_normalize_base_url_trailing_slash():
    assert _normalize_base_url("https://api.example.com/v1/") == "https://api.example.com/v1/chat/completions"


def test_normalize_base_url_empty():
    assert _normalize_base_url("") == ""


def test_extract_json_plain():
    data = _extract_json('{"a": 1}')
    assert data == {"a": 1}


def test_extract_json_with_fence():
    data = _extract_json('```json\n{"a": 1}\n```')
    assert data == {"a": 1}


def test_extract_json_with_surrounding_text():
    data = _extract_json('这里是内容 {"a": 1} 后面还有')
    assert data == {"a": 1}


def test_extract_json_invalid():
    assert _extract_json("not json at all") is None


def test_extract_json_trailing_comma():
    data = _extract_json('{"a": [1, 2, ],}')
    assert data == {"a": [1, 2]}


def test_resolve_single_answer_direct():
    options = ["苹果", "香蕉", "橙子"]
    assert _resolve_single_answer("香蕉", options) == "香蕉"


def test_resolve_single_answer_letter():
    options = ["苹果", "香蕉", "橙子"]
    assert _resolve_single_answer("B", options) == "香蕉"


def test_resolve_single_answer_letter_with_dot():
    options = ["苹果", "香蕉", "橙子"]
    assert _resolve_single_answer("B. 香蕉", options) == "香蕉"


def test_normalize_drops_invalid_questions():
    data = {
        "outline": {"title": "主题", "sections": []},
        "questions": [
            {"type": "single", "question": "单选", "options": ["A", "B"], "answer": "A", "explanation": "x"},
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
    data = {"questions": []}
    result = _normalize(data)
    assert result["outline"]["title"] == "复习提纲"
    assert result["outline"]["sections"] == []