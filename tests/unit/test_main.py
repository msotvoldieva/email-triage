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

import config
import gmail_client
import main


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


# --- _process_envelope (Task 10): wires the push handler to gmail_client + state_store ---


def _envelope(email_address="mailbox@example.com", history_id="999"):
    return main.PushEnvelope(email_address=email_address, history_id=history_id)


def test_process_envelope_bootstraps_cursor_when_none_stored(mocker):
    mocker.patch("main.state_store.get_last_history_id", return_value=None)
    set_cursor = mocker.patch("main.state_store.set_last_history_id")
    list_ids = mocker.patch("main.gmail_client.list_new_message_ids")
    get_message = mocker.patch("main.gmail_client.get_message")

    main._process_envelope(_envelope(history_id="999"))

    list_ids.assert_not_called()
    get_message.assert_not_called()
    set_cursor.assert_called_once_with("999")


def test_process_envelope_fetches_new_messages_and_updates_cursor(mocker):
    mocker.patch("main.state_store.get_last_history_id", return_value="500")
    set_cursor = mocker.patch("main.state_store.set_last_history_id")
    mocker.patch("main.gmail_client.list_new_message_ids", return_value=["m1", "m2"])
    get_message = mocker.patch(
        "main.gmail_client.get_message", return_value=("subject", "body", ["INBOX"])
    )

    main._process_envelope(_envelope(history_id="999"))

    assert get_message.call_count == 2
    get_message.assert_any_call("mailbox@example.com", "m1")
    get_message.assert_any_call("mailbox@example.com", "m2")
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


def test_process_envelope_isolates_per_message_fetch_failure(mocker):
    mocker.patch("main.state_store.get_last_history_id", return_value="500")
    set_cursor = mocker.patch("main.state_store.set_last_history_id")
    mocker.patch("main.gmail_client.list_new_message_ids", return_value=["m1", "m2", "m3"])

    def _fake_get_message(_email, message_id):
        if message_id == "m2":
            resp = type("_Resp", (), {"status": 404, "reason": "not found"})()
            raise HttpError(resp=resp, content=b'{"error": {"message": "gone"}}')
        return ("subject", "body", ["INBOX"])

    get_message = mocker.patch("main.gmail_client.get_message", side_effect=_fake_get_message)

    main._process_envelope(_envelope(history_id="999"))

    assert get_message.call_count == 3
    set_cursor.assert_called_once_with("999")
