"""Task 6 (tasks/todo.md): push envelope parsing/auth.

Every test here mocks the OIDC verification call -- no real Google credentials
or network access, and no real PHI (there is none in a Pub/Sub push envelope
regardless, but these fixtures are synthetic on principle, matching
SPEC-email-triage-core.md's Testing Strategy).
"""

import base64
import json

import pytest
from googleapiclient.errors import HttpError

import classifier
import config
import gmail_client
import main
import taxonomy


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch):
    monkeypatch.setenv("CLOUD_RUN_AUDIENCE", "https://email-triage-test.example.run.app")
    monkeypatch.setenv(
        "INVOKER_SERVICE_ACCOUNT_EMAIL", "invoker@test-project.iam.gserviceaccount.com"
    )
    monkeypatch.setenv("VERTEX_AI_LOCATION", "us-central1")
    monkeypatch.setenv("CLASSIFIER_MODEL", "gemini-test-model")
    config.load_settings.cache_clear()
    yield
    config.load_settings.cache_clear()


def _valid_claims():
    return {
        "email": "invoker@test-project.iam.gserviceaccount.com",
        "aud": "https://email-triage-test.example.run.app",
    }


def _push_envelope(email_address="mailbox@example.com", history_id="123456"):
    payload = json.dumps({"emailAddress": email_address, "historyId": history_id}).encode("utf-8")
    return {
        "message": {
            "data": base64.b64encode(payload).decode("utf-8"),
            "messageId": "abc123",
            "publishTime": "2026-08-18T00:00:00Z",
        },
        "subscription": "projects/test-project/subscriptions/gmail-watch-push",
    }


class _FakeRequest:
    """Minimal stand-in for a Flask Request -- just what handle_pubsub_push reads."""

    def __init__(self, json_body=None, headers=None, raise_on_json=False):
        self._json_body = json_body
        self.headers = headers or {}
        self._raise_on_json = raise_on_json

    def get_json(self, force=True, silent=False):
        if self._raise_on_json:
            raise ValueError("not valid JSON")
        return self._json_body


def test_valid_push_returns_200(mocker):
    mocker.patch("main.id_token.verify_oauth2_token", return_value=_valid_claims())
    mocker.patch("main._process_envelope")
    request = _FakeRequest(
        json_body=_push_envelope(),
        headers={"Authorization": "Bearer fake-token"},
    )

    _body, status = main.handle_pubsub_push(request)

    assert status == 200


def test_missing_authorization_header_returns_401(mocker):
    verify = mocker.patch("main.id_token.verify_oauth2_token")
    request = _FakeRequest(json_body=_push_envelope(), headers={})

    _body, status = main.handle_pubsub_push(request)

    assert status == 401
    verify.assert_not_called()


def test_invalid_oidc_token_returns_401(mocker):
    mocker.patch("main.id_token.verify_oauth2_token", side_effect=ValueError("bad token"))
    request = _FakeRequest(
        json_body=_push_envelope(),
        headers={"Authorization": "Bearer garbage"},
    )

    _body, status = main.handle_pubsub_push(request)

    assert status == 401


def test_token_from_wrong_service_account_returns_401(mocker):
    mocker.patch(
        "main.id_token.verify_oauth2_token",
        return_value={
            "email": "someone-else@evil.example.com",
            "aud": "https://email-triage-test.example.run.app",
        },
    )
    request = _FakeRequest(
        json_body=_push_envelope(),
        headers={"Authorization": "Bearer fake-token"},
    )

    _body, status = main.handle_pubsub_push(request)

    assert status == 401


def test_malformed_json_body_returns_400(mocker):
    mocker.patch("main.id_token.verify_oauth2_token", return_value=_valid_claims())
    request = _FakeRequest(
        json_body=None,
        headers={"Authorization": "Bearer fake-token"},
        raise_on_json=True,
    )

    _body, status = main.handle_pubsub_push(request)

    assert status == 400


def test_envelope_missing_message_key_returns_400(mocker):
    mocker.patch("main.id_token.verify_oauth2_token", return_value=_valid_claims())
    request = _FakeRequest(
        json_body={"subscription": "projects/test-project/subscriptions/gmail-watch-push"},
        headers={"Authorization": "Bearer fake-token"},
    )

    _body, status = main.handle_pubsub_push(request)

    assert status == 400


def test_envelope_missing_data_field_returns_400(mocker):
    mocker.patch("main.id_token.verify_oauth2_token", return_value=_valid_claims())
    request = _FakeRequest(
        json_body={"message": {"messageId": "abc123"}},
        headers={"Authorization": "Bearer fake-token"},
    )

    _body, status = main.handle_pubsub_push(request)

    assert status == 400


def test_envelope_missing_history_id_returns_400(mocker):
    mocker.patch("main.id_token.verify_oauth2_token", return_value=_valid_claims())
    payload = json.dumps({"emailAddress": "mailbox@example.com"}).encode("utf-8")
    request = _FakeRequest(
        json_body={"message": {"data": base64.b64encode(payload).decode("utf-8")}},
        headers={"Authorization": "Bearer fake-token"},
    )

    _body, status = main.handle_pubsub_push(request)

    assert status == 400


def test_json_body_not_a_dict_returns_400(mocker):
    mocker.patch("main.id_token.verify_oauth2_token", return_value=_valid_claims())
    request = _FakeRequest(
        json_body=["not", "a", "dict"],
        headers={"Authorization": "Bearer fake-token"},
    )

    _body, status = main.handle_pubsub_push(request)

    assert status == 400


def test_decoded_payload_not_a_dict_returns_400(mocker):
    mocker.patch("main.id_token.verify_oauth2_token", return_value=_valid_claims())
    payload = json.dumps(["not", "a", "dict"]).encode("utf-8")
    request = _FakeRequest(
        json_body={"message": {"data": base64.b64encode(payload).decode("utf-8")}},
        headers={"Authorization": "Bearer fake-token"},
    )

    _body, status = main.handle_pubsub_push(request)

    assert status == 400


def test_envelope_data_not_valid_base64_json_returns_400(mocker):
    mocker.patch("main.id_token.verify_oauth2_token", return_value=_valid_claims())
    request = _FakeRequest(
        json_body={"message": {"data": "not-valid-base64!!!"}},
        headers={"Authorization": "Bearer fake-token"},
    )

    _body, status = main.handle_pubsub_push(request)

    assert status == 400


def test_get_taxonomy_loads_the_real_bundled_file():
    """Unlike gmail_client/classifier/state_store's external-service calls,
    loading the taxonomy is just a local file read -- worth exercising for
    real rather than always mocking it out, same reasoning as
    test_taxonomy.py's test_real_taxonomy_yaml_file_loads_successfully.
    """
    main._get_taxonomy.cache_clear()

    result = main._get_taxonomy()

    assert taxonomy.NEEDS_REVIEW_CATEGORY in result.category_names()
    main._get_taxonomy.cache_clear()


def test_valid_push_calls_process_envelope_with_parsed_envelope(mocker):
    mocker.patch("main.id_token.verify_oauth2_token", return_value=_valid_claims())
    process = mocker.patch("main._process_envelope")
    request = _FakeRequest(
        json_body=_push_envelope(email_address="mailbox@example.com", history_id="999"),
        headers={"Authorization": "Bearer fake-token"},
    )

    main.handle_pubsub_push(request)

    process.assert_called_once_with(
        main.PushEnvelope(email_address="mailbox@example.com", history_id="999")
    )


# --- _process_envelope (Task 10 fetch, Task 16 classify+label): wires the push
# handler to gmail_client + state_store + classifier ---


def _envelope(email_address="mailbox@example.com", history_id="999"):
    return main.PushEnvelope(email_address=email_address, history_id=history_id)


@pytest.fixture
def sample_taxonomy(tmp_path, mocker):
    path = tmp_path / "taxonomy.yaml"
    path.write_text(
        "categories:\n  - name: billing\n    description: Invoices.\n    label: Triage/Billing\n"
    )
    result = taxonomy.load_taxonomy(path)
    mocker.patch("main._get_taxonomy", return_value=result)
    # Mocked here, not just per-test: any test reaching the labeling success
    # path calls audit.write_event, which would otherwise hit real BigQuery ->
    # _get_client() -> a network call this sandbox can't make. Same failure
    # mode as the apply_label incident (Task 16) -- baking the mock into the
    # shared fixture means a future test can't reintroduce it by omission.
    mocker.patch("main.audit.write_event")
    return result


def test_process_envelope_bootstraps_cursor_when_none_stored(mocker):
    mocker.patch("main.state_store.get_last_history_id", return_value=None)
    set_cursor = mocker.patch("main.state_store.set_last_history_id")
    list_ids = mocker.patch("main.gmail_client.list_new_message_ids")
    get_message = mocker.patch("main.gmail_client.get_message")

    main._process_envelope(_envelope(history_id="999"))

    list_ids.assert_not_called()
    get_message.assert_not_called()
    set_cursor.assert_called_once_with("999")


def test_process_envelope_resyncs_on_history_id_expired(mocker):
    mocker.patch("main.state_store.get_last_history_id", return_value="stale")
    set_cursor = mocker.patch("main.state_store.set_last_history_id")
    mocker.patch(
        "main.gmail_client.list_new_message_ids",
        side_effect=gmail_client.HistoryIdExpiredError("expired"),
    )
    get_message = mocker.patch("main.gmail_client.get_message")

    main._process_envelope(_envelope(history_id="999"))

    get_message.assert_not_called()
    set_cursor.assert_called_once_with("999")


def test_process_envelope_isolates_per_message_fetch_failure(mocker, sample_taxonomy):
    mocker.patch("main.state_store.get_last_history_id", return_value="500")
    set_cursor = mocker.patch("main.state_store.set_last_history_id")
    mocker.patch("main.gmail_client.list_new_message_ids", return_value=["m1", "m2", "m3"])
    mocker.patch("main.gmail_client.already_labeled", return_value=False)
    mocker.patch(
        "main.classifier.classify",
        return_value=classifier.ClassificationResult(
            category="billing", confidence=0.9, needs_review=False
        ),
    )
    mocker.patch("main.gmail_client.ensure_label", return_value="Label_1")
    mocker.patch("main.gmail_client.apply_label")

    def _fake_get_message(_email, message_id):
        if message_id == "m2":
            resp = type("_Resp", (), {"status": 404, "reason": "not found"})()
            raise HttpError(resp=resp, content=b'{"error": {"message": "gone"}}')
        return ("subject", "body", ["INBOX"])

    get_message = mocker.patch("main.gmail_client.get_message", side_effect=_fake_get_message)

    main._process_envelope(_envelope(history_id="999"))

    assert get_message.call_count == 3
    set_cursor.assert_called_once_with("999")


def test_process_envelope_skips_classification_when_already_labeled(mocker, sample_taxonomy):
    mocker.patch("main.state_store.get_last_history_id", return_value="500")
    mocker.patch("main.state_store.set_last_history_id")
    mocker.patch("main.gmail_client.list_new_message_ids", return_value=["m1"])
    mocker.patch(
        "main.gmail_client.get_message",
        return_value=("subject", "body", ["INBOX", "Label_1"]),
    )
    mocker.patch("main.gmail_client.already_labeled", return_value=True)
    classify = mocker.patch("main.classifier.classify")
    ensure_label = mocker.patch("main.gmail_client.ensure_label")

    main._process_envelope(_envelope(history_id="999"))

    classify.assert_not_called()
    ensure_label.assert_not_called()


def test_process_envelope_classifies_and_labels_new_message(mocker, sample_taxonomy):
    mocker.patch("main.state_store.get_last_history_id", return_value="500")
    mocker.patch("main.state_store.set_last_history_id")
    mocker.patch("main.gmail_client.list_new_message_ids", return_value=["m1"])
    mocker.patch("main.gmail_client.get_message", return_value=("subject", "body", ["INBOX"]))
    mocker.patch("main.gmail_client.already_labeled", return_value=False)
    mocker.patch(
        "main.classifier.classify",
        return_value=classifier.ClassificationResult(
            category="billing", confidence=0.9, needs_review=False
        ),
    )
    ensure_label = mocker.patch("main.gmail_client.ensure_label", return_value="Label_1")
    apply_label = mocker.patch("main.gmail_client.apply_label")

    main._process_envelope(_envelope(history_id="999"))

    ensure_label.assert_called_once_with("mailbox@example.com", "Triage/Billing")
    apply_label.assert_called_once_with("mailbox@example.com", "m1", "Label_1")


def test_process_envelope_low_confidence_applies_needs_review_label(mocker, sample_taxonomy):
    mocker.patch("main.state_store.get_last_history_id", return_value="500")
    mocker.patch("main.state_store.set_last_history_id")
    mocker.patch("main.gmail_client.list_new_message_ids", return_value=["m1"])
    mocker.patch("main.gmail_client.get_message", return_value=("subject", "body", ["INBOX"]))
    mocker.patch("main.gmail_client.already_labeled", return_value=False)
    mocker.patch(
        "main.classifier.classify",
        return_value=classifier.ClassificationResult(
            category=taxonomy.NEEDS_REVIEW_CATEGORY, confidence=0.3, needs_review=True
        ),
    )
    ensure_label = mocker.patch("main.gmail_client.ensure_label", return_value="Label_nr")
    apply_label = mocker.patch("main.gmail_client.apply_label")

    main._process_envelope(_envelope(history_id="999"))

    ensure_label.assert_called_once_with("mailbox@example.com", "Triage/Needs Review")
    apply_label.assert_called_once_with("mailbox@example.com", "m1", "Label_nr")


# --- Task 19: audit write, after successful labeling ---


def test_process_envelope_writes_audit_event_after_labeling(mocker, sample_taxonomy):
    mocker.patch("main.state_store.get_last_history_id", return_value="500")
    mocker.patch("main.state_store.set_last_history_id")
    mocker.patch("main.gmail_client.list_new_message_ids", return_value=["m1"])
    mocker.patch("main.gmail_client.get_message", return_value=("subject", "body", ["INBOX"]))
    mocker.patch("main.gmail_client.already_labeled", return_value=False)
    mocker.patch(
        "main.classifier.classify",
        return_value=classifier.ClassificationResult(
            category="billing", confidence=0.9, needs_review=False
        ),
    )
    mocker.patch("main.gmail_client.ensure_label", return_value="Label_1")
    mocker.patch("main.gmail_client.apply_label")
    write_event = mocker.patch("main.audit.write_event")

    main._process_envelope(_envelope(history_id="999"))

    write_event.assert_called_once_with(
        message_id="m1",
        category="billing",
        confidence=0.9,
        needs_review=False,
        model_version="gemini-test-model",
        classified_at=mocker.ANY,
    )


def test_process_envelope_writes_audit_event_for_needs_review_too(mocker, sample_taxonomy):
    mocker.patch("main.state_store.get_last_history_id", return_value="500")
    mocker.patch("main.state_store.set_last_history_id")
    mocker.patch("main.gmail_client.list_new_message_ids", return_value=["m1"])
    mocker.patch("main.gmail_client.get_message", return_value=("subject", "body", ["INBOX"]))
    mocker.patch("main.gmail_client.already_labeled", return_value=False)
    mocker.patch(
        "main.classifier.classify",
        return_value=classifier.ClassificationResult(
            category=taxonomy.NEEDS_REVIEW_CATEGORY, confidence=0.3, needs_review=True
        ),
    )
    mocker.patch("main.gmail_client.ensure_label", return_value="Label_nr")
    mocker.patch("main.gmail_client.apply_label")
    write_event = mocker.patch("main.audit.write_event")

    main._process_envelope(_envelope(history_id="999"))

    write_event.assert_called_once_with(
        message_id="m1",
        category=taxonomy.NEEDS_REVIEW_CATEGORY,
        confidence=0.3,
        needs_review=True,
        model_version="gemini-test-model",
        classified_at=mocker.ANY,
    )


def test_process_envelope_no_audit_write_on_classify_failure(mocker, sample_taxonomy):
    mocker.patch("main.state_store.get_last_history_id", return_value="500")
    mocker.patch("main.state_store.set_last_history_id")
    mocker.patch("main.gmail_client.list_new_message_ids", return_value=["m1"])
    mocker.patch("main.gmail_client.get_message", return_value=("subject", "body", ["INBOX"]))
    mocker.patch("main.gmail_client.already_labeled", return_value=False)
    mocker.patch("main.classifier.classify", side_effect=TimeoutError("timed out"))
    write_event = mocker.patch("main.audit.write_event")

    main._process_envelope(_envelope(history_id="999"))

    # No successful classification -> nothing valid to audit for this message.
    write_event.assert_not_called()


def test_process_envelope_no_audit_write_when_skipped_as_already_labeled(mocker, sample_taxonomy):
    mocker.patch("main.state_store.get_last_history_id", return_value="500")
    mocker.patch("main.state_store.set_last_history_id")
    mocker.patch("main.gmail_client.list_new_message_ids", return_value=["m1"])
    mocker.patch(
        "main.gmail_client.get_message",
        return_value=("subject", "body", ["INBOX", "Label_1"]),
    )
    mocker.patch("main.gmail_client.already_labeled", return_value=True)
    write_event = mocker.patch("main.audit.write_event")

    main._process_envelope(_envelope(history_id="999"))

    write_event.assert_not_called()


def test_process_envelope_audit_call_never_receives_subject_or_body(mocker, sample_taxonomy):
    """Task 18 already enforces this structurally (write_event's signature has
    no subject/body parameter at all), but this confirms the call SITE here
    doesn't somehow smuggle message content through one of the six legitimate
    parameters either (e.g. passing body as model_version by mistake).
    """
    secret_subject = "SECRET_SUBJECT_MARKER"
    secret_body = "SECRET_BODY_MARKER"

    mocker.patch("main.state_store.get_last_history_id", return_value="500")
    mocker.patch("main.state_store.set_last_history_id")
    mocker.patch("main.gmail_client.list_new_message_ids", return_value=["m1"])
    mocker.patch(
        "main.gmail_client.get_message",
        return_value=(secret_subject, secret_body, ["INBOX"]),
    )
    mocker.patch("main.gmail_client.already_labeled", return_value=False)
    mocker.patch(
        "main.classifier.classify",
        return_value=classifier.ClassificationResult(
            category="billing", confidence=0.9, needs_review=False
        ),
    )
    mocker.patch("main.gmail_client.ensure_label", return_value="Label_1")
    mocker.patch("main.gmail_client.apply_label")
    write_event = mocker.patch("main.audit.write_event")

    main._process_envelope(_envelope(history_id="999"))

    call_values = list(write_event.call_args.kwargs.values())
    assert secret_subject not in call_values
    assert secret_body not in call_values


def test_process_envelope_isolates_classify_failure_per_message(mocker, sample_taxonomy):
    mocker.patch("main.state_store.get_last_history_id", return_value="500")
    set_cursor = mocker.patch("main.state_store.set_last_history_id")
    mocker.patch("main.gmail_client.list_new_message_ids", return_value=["m1", "m2"])
    mocker.patch("main.gmail_client.get_message", return_value=("subject", "body", ["INBOX"]))
    mocker.patch("main.gmail_client.already_labeled", return_value=False)
    classify = mocker.patch(
        "main.classifier.classify",
        side_effect=[
            TimeoutError("upstream timed out"),
            classifier.ClassificationResult(category="billing", confidence=0.9, needs_review=False),
        ],
    )
    apply_label = mocker.patch("main.gmail_client.apply_label")
    mocker.patch("main.gmail_client.ensure_label", return_value="Label_1")

    main._process_envelope(_envelope(history_id="999"))

    assert classify.call_count == 2
    # m1 failed and was skipped -- only m2 got labeled, but the batch didn't abort
    apply_label.assert_called_once_with("mailbox@example.com", "m2", "Label_1")
    set_cursor.assert_called_once_with("999")


def test_process_envelope_isolates_label_failure_per_message(mocker, sample_taxonomy):
    mocker.patch("main.state_store.get_last_history_id", return_value="500")
    set_cursor = mocker.patch("main.state_store.set_last_history_id")
    mocker.patch("main.gmail_client.list_new_message_ids", return_value=["m1", "m2"])
    mocker.patch("main.gmail_client.get_message", return_value=("subject", "body", ["INBOX"]))
    mocker.patch("main.gmail_client.already_labeled", return_value=False)
    mocker.patch(
        "main.classifier.classify",
        return_value=classifier.ClassificationResult(
            category="billing", confidence=0.9, needs_review=False
        ),
    )
    resp = type("_Resp", (), {"status": 500, "reason": "error"})()
    ensure_label = mocker.patch(
        "main.gmail_client.ensure_label",
        side_effect=[HttpError(resp=resp, content=b"{}"), "Label_1"],
    )
    apply_label = mocker.patch("main.gmail_client.apply_label")

    main._process_envelope(_envelope(history_id="999"))

    assert ensure_label.call_count == 2
    apply_label.assert_called_once_with("mailbox@example.com", "m2", "Label_1")
    set_cursor.assert_called_once_with("999")
