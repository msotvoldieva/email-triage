"""Task 13/14 (tasks/todo.md): classifier.py -- Vertex AI Gemini structured
output + confidence threshold logic.

Every test mocks the genai client -- no real Vertex AI calls, no real email
content (synthetic fixtures only). test_get_client_uses_vertex_ai is a
regression test protecting the single most load-bearing constraint in the
project (SPEC-email-triage-core.md): Vertex AI only, never the public Gemini
Developer API.
"""

import json

import pytest

import classifier
import config
import taxonomy

_TAXONOMY_YAML = """
categories:
  - name: billing
    description: Invoices and payments.
    label: Triage/Billing
  - name: scheduling
    description: Appointment requests.
    label: Triage/Scheduling
"""


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch):
    monkeypatch.setenv("CLOUD_RUN_AUDIENCE", "https://example.run.app")
    monkeypatch.setenv("INVOKER_SERVICE_ACCOUNT_EMAIL", "invoker@test.iam.gserviceaccount.com")
    monkeypatch.setenv("VERTEX_AI_LOCATION", "us-central1")
    monkeypatch.setenv("CLASSIFIER_MODEL", "gemini-test-model")
    monkeypatch.setenv("CONFIDENCE_THRESHOLD", "0.75")
    config.load_settings.cache_clear()
    classifier._get_client.cache_clear()
    yield
    config.load_settings.cache_clear()
    classifier._get_client.cache_clear()


@pytest.fixture
def sample_taxonomy(tmp_path):
    path = tmp_path / "taxonomy.yaml"
    path.write_text(_TAXONOMY_YAML)
    return taxonomy.load_taxonomy(path)


def _fake_response(text: str):
    return type("_FakeResponse", (), {"text": text})()


def _mock_generate_content(mocker, response_text: str | None = None, side_effect=None):
    fake_client = mocker.Mock()
    if side_effect is not None:
        fake_client.models.generate_content.side_effect = side_effect
    else:
        fake_client.models.generate_content.return_value = _fake_response(response_text)
    mocker.patch("classifier._get_client", return_value=fake_client)
    return fake_client


# --- regression coverage: Vertex AI only, never the public Gemini API ---


def test_get_client_uses_vertex_ai(mocker):
    mocker.patch("classifier.google.auth.default", return_value=(mocker.Mock(), "test-project"))
    client_cls = mocker.patch("classifier.genai.Client")
    settings = config.load_settings()

    classifier._get_client()

    client_cls.assert_called_once_with(
        vertexai=True,
        project="test-project",
        location=settings.vertex_ai_location,
    )
    # vertexai=True is a keyword argument, not inferred -- assert it explicitly
    # rather than just checking the call happened, so a future refactor that
    # drops it can't slip past this test silently.
    _, kwargs = client_cls.call_args
    assert kwargs["vertexai"] is True


def test_classifier_module_never_reads_an_api_key(monkeypatch):
    """No line of actual code in this module should construct a client with an
    API key -- that would open a door back to the public Gemini Developer API.
    Checks real code, not prose (this module's own docstrings mention the term
    while explaining why it's avoided, which isn't the same thing).
    """
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    import inspect

    code_lines = [
        line
        for line in inspect.getsource(classifier).splitlines()
        if not line.strip().startswith(("#", '"""', "'''"))
    ]
    assert not any("api_key=" in line for line in code_lines)


# --- classify(): high/low confidence, malformed output ---


def test_classify_high_confidence_returns_model_category(mocker, sample_taxonomy):
    _mock_generate_content(
        mocker, response_text=json.dumps({"category": "billing", "confidence": 0.92})
    )

    result = classifier.classify("Invoice question", "About my last invoice", sample_taxonomy)

    assert result.category == "billing"
    assert result.confidence == 0.92
    assert result.needs_review is False


def test_classify_low_confidence_falls_back_to_needs_review(mocker, sample_taxonomy):
    _mock_generate_content(
        mocker, response_text=json.dumps({"category": "billing", "confidence": 0.4})
    )

    result = classifier.classify("Vague email", "Not sure what this is about", sample_taxonomy)

    assert result.category == taxonomy.NEEDS_REVIEW_CATEGORY
    assert result.confidence == 0.4
    assert result.needs_review is True


def test_classify_confidence_at_exact_threshold_is_accepted(mocker, sample_taxonomy):
    _mock_generate_content(
        mocker, response_text=json.dumps({"category": "billing", "confidence": 0.75})
    )

    result = classifier.classify("Subject", "Body", sample_taxonomy)

    assert result.needs_review is False


def test_classify_malformed_json_response_returns_needs_review(mocker, sample_taxonomy):
    _mock_generate_content(mocker, response_text="not valid json{{{")

    result = classifier.classify("Subject", "Body", sample_taxonomy)

    assert result.category == taxonomy.NEEDS_REVIEW_CATEGORY
    assert result.confidence == 0.0
    assert result.needs_review is True


def test_classify_response_not_a_json_object_returns_needs_review(mocker, sample_taxonomy):
    _mock_generate_content(mocker, response_text=json.dumps(["billing", 0.9]))

    result = classifier.classify("Subject", "Body", sample_taxonomy)

    assert result.needs_review is True


def test_classify_category_outside_taxonomy_enum_returns_needs_review(mocker, sample_taxonomy):
    _mock_generate_content(
        mocker,
        response_text=json.dumps({"category": "not-a-real-category", "confidence": 0.99}),
    )

    result = classifier.classify("Subject", "Body", sample_taxonomy)

    assert result.needs_review is True


def test_classify_model_cannot_choose_needs_review_directly(mocker, sample_taxonomy):
    """needs-review is threshold-derived, not a model-selectable category --
    even if the model somehow outputs it, that's an invalid enum value, same
    as any other category outside the real taxonomy.
    """
    _mock_generate_content(
        mocker,
        response_text=json.dumps({"category": taxonomy.NEEDS_REVIEW_CATEGORY, "confidence": 0.99}),
    )

    result = classifier.classify("Subject", "Body", sample_taxonomy)

    assert result.needs_review is True
    assert result.confidence == 0.0


def test_classify_confidence_not_a_number_returns_needs_review(mocker, sample_taxonomy):
    _mock_generate_content(
        mocker, response_text=json.dumps({"category": "billing", "confidence": "high"})
    )

    result = classifier.classify("Subject", "Body", sample_taxonomy)

    assert result.needs_review is True


def test_classify_confidence_boolean_returns_needs_review(mocker, sample_taxonomy):
    """bool is a subclass of int in Python -- guard against True/False silently
    passing an isinstance(x, (int, float)) check.
    """
    _mock_generate_content(
        mocker, response_text=json.dumps({"category": "billing", "confidence": True})
    )

    result = classifier.classify("Subject", "Body", sample_taxonomy)

    assert result.needs_review is True


def test_classify_confidence_out_of_range_returns_needs_review(mocker, sample_taxonomy):
    _mock_generate_content(
        mocker, response_text=json.dumps({"category": "billing", "confidence": 1.5})
    )

    result = classifier.classify("Subject", "Body", sample_taxonomy)

    assert result.needs_review is True


def test_classify_missing_confidence_field_returns_needs_review(mocker, sample_taxonomy):
    _mock_generate_content(mocker, response_text=json.dumps({"category": "billing"}))

    result = classifier.classify("Subject", "Body", sample_taxonomy)

    assert result.needs_review is True


def test_classify_empty_subject_and_body_still_calls_model(mocker, sample_taxonomy):
    fake_client = _mock_generate_content(
        mocker, response_text=json.dumps({"category": "billing", "confidence": 0.9})
    )

    result = classifier.classify("", "", sample_taxonomy)

    fake_client.models.generate_content.assert_called_once()
    assert result.needs_review is False


def test_classify_vertex_ai_exception_propagates_not_swallowed(mocker, sample_taxonomy):
    """An API-level failure (timeout, 5xx, quota) is an infrastructure problem,
    not an ambiguous email -- it must not be silently disguised as a
    needs_review classification. The caller (main.py, Task 16) is responsible
    for catching this and deciding whether to isolate or let Pub/Sub retry.
    """
    _mock_generate_content(mocker, side_effect=TimeoutError("upstream timed out"))

    with pytest.raises(TimeoutError):
        classifier.classify("Subject", "Body", sample_taxonomy)


# --- prompt/schema construction ---


def test_classify_uses_configured_model(mocker, sample_taxonomy):
    fake_client = _mock_generate_content(
        mocker, response_text=json.dumps({"category": "billing", "confidence": 0.9})
    )

    classifier.classify("Subject", "Body", sample_taxonomy)

    _args, kwargs = fake_client.models.generate_content.call_args
    assert kwargs["model"] == "gemini-test-model"


def test_response_schema_enum_excludes_needs_review(sample_taxonomy):
    real_categories = [
        c for c in sample_taxonomy.categories if c.name != taxonomy.NEEDS_REVIEW_CATEGORY
    ]
    schema = classifier._build_response_schema(real_categories)

    assert taxonomy.NEEDS_REVIEW_CATEGORY not in schema["properties"]["category"]["enum"]
    assert set(schema["properties"]["category"]["enum"]) == {"billing", "scheduling"}


# --- logging never contains PHI ---


def test_classify_never_logs_subject_or_body(mocker, sample_taxonomy, caplog):
    _mock_generate_content(
        mocker, response_text=json.dumps({"category": "billing", "confidence": 0.9})
    )

    secret_subject = "SECRET_SUBJECT_MARKER"
    secret_body = "SECRET_BODY_MARKER"

    with caplog.at_level("DEBUG"):
        classifier.classify(secret_subject, secret_body, sample_taxonomy)

    log_text = caplog.text
    assert secret_subject not in log_text
    assert secret_body not in log_text
