"""API 层测试：路由、入参校验、限流与降级行为。"""

import json

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture
def client(monkeypatch):
    """未配置模型的客户端：请求会走内置示例数据降级路径。"""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    main.RATE_LIMITER.reset()
    with TestClient(main.app) as test_client:
        yield test_client
    main.RATE_LIMITER.reset()


def test_health_reports_status(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"


def test_health_does_not_leak_api_key(client, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-super-secret")
    body = client.get("/api/health").json()
    assert "sk-super-secret" not in json.dumps(body, ensure_ascii=False)


def test_index_serves_the_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "灵动笔卡" in response.text


def test_static_assets_are_served(client):
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_generate_falls_back_to_sample_when_not_configured(client):
    response = client.post("/api/generate", json={"markdown": "# 测试笔记"})
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "sample"
    assert body["fallback_reason"] == "not_configured"
    assert body["degraded"] is True
    assert body["questions"]
    assert body["outline"]["sections"]


def test_generate_rejects_blank_markdown(client):
    response = client.post("/api/generate", json={"markdown": "   \n  "})
    assert response.status_code == 400


def test_generate_rejects_oversized_markdown(client):
    response = client.post("/api/generate", json={"markdown": "x" * 50001})
    assert response.status_code == 422


def test_generate_accepts_markdown_at_the_limit(client):
    response = client.post("/api/generate", json={"markdown": "x" * 50000})
    assert response.status_code == 200


def test_rate_limit_returns_429_with_retry_after(client, monkeypatch):
    monkeypatch.setattr(main.RATE_LIMITER, "limit", 1)
    first = client.post("/api/generate", json={"markdown": "# 一"})
    second = client.post("/api/generate", json={"markdown": "# 二"})
    assert first.status_code == 200
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) >= 1


def test_rate_limit_disabled_when_zero(client, monkeypatch):
    monkeypatch.setattr(main.RATE_LIMITER, "limit", 0)
    for _ in range(5):
        assert client.post("/api/generate", json={"markdown": "# x"}).status_code == 200
