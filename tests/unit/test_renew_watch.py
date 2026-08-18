"""Task 20/21 (tasks/todo.md): Cloud Scheduler-triggered watch renewal.

Dispatched from handle_pubsub_push by path (POST /renew-watch) rather than a
second Cloud Run service -- functions-framework serves one target function
per deployment. Reuses the same OIDC verification as the Pub/Sub push path,
since Scheduler's token is issued for the same invoker SA/audience.
"""

import pytest

import config
import main


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch):
    monkeypatch.setenv("CLOUD_RUN_AUDIENCE", "https://example.run.app")
    monkeypatch.setenv(
        "INVOKER_SERVICE_ACCOUNT_EMAIL", "invoker@test-project.iam.gserviceaccount.com"
    )
    monkeypatch.setenv("VERTEX_AI_LOCATION", "us-central1")
    monkeypatch.setenv("CLASSIFIER_MODEL", "gemini-test-model")
    monkeypatch.setenv("MAILBOX_ADDRESS", "mailbox@example.com")
    monkeypatch.setenv("GMAIL_WATCH_TOPIC", "projects/test-project/topics/gmail-watch")
    config.load_settings.cache_clear()
    yield
    config.load_settings.cache_clear()


def _valid_claims():
    return {
        "email": "invoker@test-project.iam.gserviceaccount.com",
        "aud": "https://example.run.app",
    }


class _FakeRequest:
    def __init__(self, headers=None, path="/renew-watch"):
        self.headers = headers or {}
        self.path = path


def test_renew_watch_route_dispatches_from_handle_pubsub_push(mocker):
    handle_renew = mocker.patch("main._handle_renew_watch", return_value=("", 200))
    request = _FakeRequest(path="/renew-watch")

    result = main.handle_pubsub_push(request)

    handle_renew.assert_called_once_with(request)
    assert result == ("", 200)


def test_renew_watch_missing_auth_returns_401(mocker):
    verify = mocker.patch("main.id_token.verify_oauth2_token")
    request = _FakeRequest(headers={})

    _body, status = main._handle_renew_watch(request)

    assert status == 401
    verify.assert_not_called()


def test_renew_watch_invalid_token_returns_401(mocker):
    mocker.patch("main.id_token.verify_oauth2_token", side_effect=ValueError("bad token"))
    request = _FakeRequest(headers={"Authorization": "Bearer garbage"})

    _body, status = main._handle_renew_watch(request)

    assert status == 401


def test_renew_watch_success_calls_start_watch_and_stores_renewal(mocker):
    mocker.patch("main.id_token.verify_oauth2_token", return_value=_valid_claims())
    start_watch = mocker.patch(
        "main.gmail_client.start_watch",
        return_value={"historyId": "111", "expiration": "1234567890000"},
    )
    set_renewal = mocker.patch("main.state_store.set_last_watch_renewal")
    request = _FakeRequest(headers={"Authorization": "Bearer fake-token"})

    _body, status = main._handle_renew_watch(request)

    assert status == 200
    start_watch.assert_called_once_with(
        "mailbox@example.com", "projects/test-project/topics/gmail-watch"
    )
    set_renewal.assert_called_once_with(renewed_at=mocker.ANY, expiration="1234567890000")


def test_renew_watch_failure_logs_actionable_error_with_last_renewal_context(mocker, caplog):
    mocker.patch("main.id_token.verify_oauth2_token", return_value=_valid_claims())
    mocker.patch("main.gmail_client.start_watch", side_effect=TimeoutError("upstream timed out"))
    mocker.patch(
        "main.state_store.get_last_watch_renewal",
        return_value={"renewed_at": "2026-08-11T00:00:00+00:00", "expiration": "9999999999000"},
    )
    request = _FakeRequest(headers={"Authorization": "Bearer fake-token"})

    with caplog.at_level("ERROR"):
        _body, status = main._handle_renew_watch(request)

    assert status == 500
    record = caplog.records[-1]
    assert record.last_renewed_at == "2026-08-11T00:00:00+00:00"
    assert record.last_known_expiration == "9999999999000"


def test_renew_watch_failure_with_no_prior_successful_renewal(mocker, caplog):
    """First-ever renewal attempt failing -- there's no prior successful
    renewal to report, and that itself should be visible in the log, not
    cause a crash while trying to format the error message.
    """
    mocker.patch("main.id_token.verify_oauth2_token", return_value=_valid_claims())
    mocker.patch("main.gmail_client.start_watch", side_effect=TimeoutError("upstream timed out"))
    mocker.patch("main.state_store.get_last_watch_renewal", return_value=None)
    request = _FakeRequest(headers={"Authorization": "Bearer fake-token"})

    with caplog.at_level("ERROR"):
        _body, status = main._handle_renew_watch(request)

    assert status == 500
