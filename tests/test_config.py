import pytest

from job_agent.config import Config, load_config, load_secrets

MINIMAL_YAML = """
mode: wide
thresholds:
  wide:
    fit_score_min: 40
  selective:
    fit_score_min: 70
    seniority_strict: true
models:
  bulk: "some/cheap-model"
  quality: "some/strong-model"
  fallbacks:
    bulk: ["some/cheap-fallback"]
    quality: []
  eval_candidates: ["some/cheap-model", "some/strong-model"]
sources:
  djinni:
    enabled: true
    categories: ["QA", "Python"]
  linkedin:
    enabled: false
categories: ["QA", "Python"]
budget:
  monthly_usd: 10
"""


def test_load_config_parses_example_file(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(MINIMAL_YAML, encoding="utf-8")

    config = load_config(config_path)

    assert isinstance(config, Config)
    assert config.mode == "wide"
    assert config.active_threshold.fit_score_min == 40
    assert config.thresholds["selective"].seniority_strict is True
    assert config.models.bulk == "some/cheap-model"
    assert config.models.fallbacks.bulk == ["some/cheap-fallback"]
    assert config.sources.djinni.categories == ["QA", "Python"]
    assert config.sources.linkedin.enabled is False
    assert config.sources.remoteok.enabled is True  # default
    assert config.budget.monthly_usd == 10


def test_load_config_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "does-not-exist.yaml")


def test_real_example_config_file_parses():
    config = load_config("config.example.yaml")
    assert config.mode == "wide"
    assert set(config.categories) == {
        "QA",
        "QA Automation",
        "Support",
        "Sysadmin",
        "ML AI",
        "Python",
    }


def test_load_secrets_reads_env_file(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "OPENROUTER_API_KEY=sk-or-test\n"
        "TELEGRAM_BOT_TOKEN=123:abc\n"
        "TELEGRAM_CHAT_ID=42\n",
        encoding="utf-8",
    )

    secrets = load_secrets(env_path)

    assert secrets.openrouter_api_key == "sk-or-test"
    assert secrets.telegram_bot_token == "123:abc"
    assert secrets.telegram_chat_id == "42"
