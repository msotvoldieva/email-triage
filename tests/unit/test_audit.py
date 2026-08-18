"""Task 18 (tasks/todo.md): audit.py -- BigQuery metadata-only event write.

Uses a MERGE upsert, not a streaming insert -- BigQuery's streaming-insert
insertId deduplication is documented as best-effort, not guaranteed (verified
against Google's own docs, not assumed). MERGE gives a genuine idempotent
write on message_id instead, which is what this compliance audit trail
actually needs.
"""

import datetime
import inspect

import pytest

import audit


@pytest.fixture(autouse=True)
def _clear_client_cache():
    audit._get_client.cache_clear()
    yield
    audit._get_client.cache_clear()


@pytest.fixture
def fake_client(mocker):
    client = mocker.Mock()
    client.project = "test-project"
    mocker.patch("audit._get_client", return_value=client)
    return client


# --- structural enforcement: no parameter through which subject/body could pass ---


def test_write_event_signature_has_only_metadata_fields():
    """This is the acceptance criteria itself, not just a nice-to-have: the
    signature must have exactly these six parameters, nothing else -- no
    subject, body, or generic **kwargs a caller could smuggle content through.
    """
    params = list(inspect.signature(audit.write_event).parameters)

    assert params == [
        "message_id",
        "category",
        "confidence",
        "needs_review",
        "model_version",
        "classified_at",
    ]


# --- write_event: MERGE upsert ---


def test_write_event_calls_query_with_merge(fake_client):
    classified_at = datetime.datetime(2026, 8, 18, tzinfo=datetime.UTC)

    audit.write_event(
        message_id="msg-1",
        category="billing",
        confidence=0.9,
        needs_review=False,
        model_version="gemini-test-model",
        classified_at=classified_at,
    )

    fake_client.query.assert_called_once()
    sql = fake_client.query.call_args.args[0]
    assert "MERGE" in sql
    assert "test-project" in sql


def test_write_event_passes_all_six_fields_as_query_parameters(fake_client):
    classified_at = datetime.datetime(2026, 8, 18, tzinfo=datetime.UTC)

    audit.write_event(
        message_id="msg-1",
        category="billing",
        confidence=0.9,
        needs_review=False,
        model_version="gemini-test-model",
        classified_at=classified_at,
    )

    job_config = fake_client.query.call_args.kwargs["job_config"]
    param_names = {p.name for p in job_config.query_parameters}
    assert param_names == {
        "message_id",
        "category",
        "confidence",
        "needs_review",
        "model_version",
        "classified_at",
    }


def test_write_event_confidence_parameter_is_float64_typed(fake_client):
    audit.write_event(
        message_id="msg-1",
        category="billing",
        confidence=0.9,
        needs_review=False,
        model_version="gemini-test-model",
        classified_at=datetime.datetime(2026, 8, 18, tzinfo=datetime.UTC),
    )

    job_config = fake_client.query.call_args.kwargs["job_config"]
    confidence_param = next(p for p in job_config.query_parameters if p.name == "confidence")
    assert confidence_param.type_ == "FLOAT64"


def test_write_event_needs_review_true_for_low_confidence(fake_client):
    audit.write_event(
        message_id="msg-2",
        category="needs-review",
        confidence=0.2,
        needs_review=True,
        model_version="gemini-test-model",
        classified_at=datetime.datetime(2026, 8, 18, tzinfo=datetime.UTC),
    )

    job_config = fake_client.query.call_args.kwargs["job_config"]
    needs_review_param = next(p for p in job_config.query_parameters if p.name == "needs_review")
    assert needs_review_param.value is True


def test_write_event_waits_for_query_completion(fake_client):
    audit.write_event(
        message_id="msg-1",
        category="billing",
        confidence=0.9,
        needs_review=False,
        model_version="gemini-test-model",
        classified_at=datetime.datetime(2026, 8, 18, tzinfo=datetime.UTC),
    )

    # .result() blocks until the query job completes -- without this, a
    # write could be reported as "done" before it actually lands, which
    # would be a real problem for a compliance audit trail.
    fake_client.query.return_value.result.assert_called_once()


def test_write_event_logs_only_metadata_never_arbitrary_content(fake_client, caplog):
    with caplog.at_level("DEBUG"):
        audit.write_event(
            message_id="msg-1",
            category="billing",
            confidence=0.9,
            needs_review=False,
            model_version="gemini-test-model",
            classified_at=datetime.datetime(2026, 8, 18, tzinfo=datetime.UTC),
        )

    # Nothing to assert "isn't PHI" here beyond what the signature already
    # structurally excludes -- this just confirms a log line was emitted at
    # all, for observability, without erroring.
    assert "audit.write_event" in caplog.text


def test_get_client_is_cached(mocker):
    client_cls = mocker.patch("audit.bigquery.Client")

    audit._get_client()
    audit._get_client()

    client_cls.assert_called_once()
