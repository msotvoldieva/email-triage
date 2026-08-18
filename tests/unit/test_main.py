"""Task 6 (tasks/todo.md): push envelope parsing/auth.

Every test here mocks the OIDC verification call -- no real Google credentials
or network access, and no real PHI (there is none in a Pub/Sub push envelope
regardless, but these fixtures are synthetic on principle, matching
SPEC-email-triage-core.md's Testing Strategy).
"""

import base64
import json

import pytest

import config
import main


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch):
    monkeypatch.setenv("CLOUD_RUN_AUDIENCE", "https://email-triage-test.example.run.app")
    monkeypatch.setenv(
        "INVOKER_SERVICE_ACCOUNT_EMAIL", "invoker@test-project.iam.gserviceaccount.com"
    )
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
