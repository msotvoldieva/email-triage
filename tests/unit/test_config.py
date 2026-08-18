import pytest

import config


@pytest.fixture(autouse=True)
def _clear_cache():
    config.load_settings.cache_clear()
    yield
    config.load_settings.cache_clear()


def test_missing_required_env_var_raises(monkeypatch):
    monkeypatch.delenv("CLOUD_RUN_AUDIENCE", raising=False)
    monkeypatch.delenv("INVOKER_SERVICE_ACCOUNT_EMAIL", raising=False)

    with pytest.raises(RuntimeError, match="CLOUD_RUN_AUDIENCE"):
        config.load_settings()


def test_confidence_threshold_defaults_when_unset(monkeypatch):
    monkeypatch.setenv("CLOUD_RUN_AUDIENCE", "https://example.run.app")
    monkeypatch.setenv("INVOKER_SERVICE_ACCOUNT_EMAIL", "invoker@example.iam.gserviceaccount.com")
    monkeypatch.setenv("VERTEX_AI_LOCATION", "us-central1")
    monkeypatch.setenv("CLASSIFIER_MODEL", "gemini-test-model")
    monkeypatch.delenv("CONFIDENCE_THRESHOLD", raising=False)

    settings = config.load_settings()

    assert settings.confidence_threshold == 0.75


def test_missing_classifier_model_raises(monkeypatch):
    monkeypatch.setenv("CLOUD_RUN_AUDIENCE", "https://example.run.app")
    monkeypatch.setenv("INVOKER_SERVICE_ACCOUNT_EMAIL", "invoker@example.iam.gserviceaccount.com")
    monkeypatch.setenv("VERTEX_AI_LOCATION", "us-central1")
    monkeypatch.delenv("CLASSIFIER_MODEL", raising=False)

    with pytest.raises(RuntimeError, match="CLASSIFIER_MODEL"):
        config.load_settings()
