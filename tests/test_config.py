"""config 模块测试：环境变量解析失败必须回退，而不是抛异常。"""

from config import env_bool, env_float, env_int, env_str, llm_settings, public_config


def test_env_str_strips_whitespace(monkeypatch):
    monkeypatch.setenv("SOME_STR", "  hi  ")
    assert env_str("SOME_STR") == "hi"


def test_env_int_falls_back_on_garbage(monkeypatch):
    monkeypatch.setenv("SOME_INT", "60s")
    assert env_int("SOME_INT", 42) == 42


def test_env_int_accepts_valid(monkeypatch):
    monkeypatch.setenv("SOME_INT", "7")
    assert env_int("SOME_INT", 42) == 7


def test_env_float_accepts_valid(monkeypatch):
    monkeypatch.setenv("SOME_FLOAT", "30.5")
    assert env_float("SOME_FLOAT", 1.0) == 30.5


def test_env_float_falls_back_on_garbage(monkeypatch):
    monkeypatch.setenv("SOME_FLOAT", "abc")
    assert env_float("SOME_FLOAT", 1.5) == 1.5


def test_env_bool_variants(monkeypatch):
    for raw in ("1", "true", "YES", "on"):
        monkeypatch.setenv("SOME_BOOL", raw)
        assert env_bool("SOME_BOOL") is True
    for raw in ("0", "false", "no"):
        monkeypatch.setenv("SOME_BOOL", raw)
        assert env_bool("SOME_BOOL", True) is False
    # 空值视为未配置，走默认
    monkeypatch.setenv("SOME_BOOL", "")
    assert env_bool("SOME_BOOL", True) is True


def _clear_llm_env(monkeypatch):
    for name in (
        "LLM_API_KEY",
        "LLM_MODEL",
        "LLM_BASE_URL",
        "LLM_TIMEOUT",
        "LLM_MAX_TOKENS",
        "LLM_MAX_RETRIES",
    ):
        monkeypatch.delenv(name, raising=False)


def test_llm_settings_defaults(monkeypatch):
    _clear_llm_env(monkeypatch)
    settings = llm_settings()
    assert settings["base_url"] == "https://api.openai.com"
    assert settings["api_key"] == ""
    assert settings["model"] == ""
    assert settings["timeout"] == 60.0
    assert settings["max_tokens"] == 4000


def test_llm_settings_timeout_with_unit_falls_back(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_TIMEOUT", "60s")
    assert llm_settings()["timeout"] == 60.0


def test_llm_settings_accepts_float_timeout(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_TIMEOUT", "12.5")
    assert llm_settings()["timeout"] == 12.5


def test_public_config_never_leaks_the_key(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-super-secret")
    monkeypatch.setenv("LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    config = public_config()
    assert config["llm_configured"] is True
    assert config["model"] == "deepseek-chat"
    assert config["endpoint_host"] == "api.deepseek.com"
    assert "sk-super-secret" not in str(config)


def test_public_config_reports_unconfigured(monkeypatch):
    _clear_llm_env(monkeypatch)
    assert public_config()["llm_configured"] is False
