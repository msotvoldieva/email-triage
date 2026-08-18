"""Task 8 (tasks/todo.md): gmail_client.py -- watch/history.list/get.

All googleapiclient calls are mocked -- no real Gmail API or network access, and
no real email content (synthetic fixtures only, per SPEC-email-triage-core.md's
Testing Strategy).
"""

import base64

import pytest
from googleapiclient.errors import HttpError

import gmail_client
import taxonomy


@pytest.fixture(autouse=True)
def _clear_service_cache():
    gmail_client._get_gmail_service.cache_clear()
    gmail_client._LABEL_NAME_TO_ID_CACHE.clear()
    yield
    gmail_client._get_gmail_service.cache_clear()
    gmail_client._LABEL_NAME_TO_ID_CACHE.clear()


@pytest.fixture
def sample_taxonomy(tmp_path):
    path = tmp_path / "taxonomy.yaml"
    path.write_text(
        "categories:\n"
        "  - name: billing\n"
        "    description: Invoices.\n"
        "    label: Triage/Billing\n"
        "  - name: scheduling\n"
        "    description: Appointments.\n"
        "    label: Triage/Scheduling\n"
    )
    return taxonomy.load_taxonomy(path)


def _http_error(status: int) -> HttpError:
    resp = type("_Resp", (), {"status": status, "reason": "error"})()
    return HttpError(resp=resp, content=b'{"error": {"message": "error"}}')


# --- credential construction: regression coverage for the keyless domain-wide
# delegation fix -- Cloud Run's attached SA has no with_subject() method (that
# requires a local private key google.auth's compute credentials don't expose),
# so this must go through google.auth.iam.Signer + IAM Credentials API remote
# signing instead. This test exists so that fix can't silently regress. ---


def test_build_delegated_credentials_uses_iam_signer(mocker):
    bootstrap_creds = mocker.Mock(
        service_account_email="runtime@test-project.iam.gserviceaccount.com"
    )
    mocker.patch("gmail_client.google.auth.default", return_value=(bootstrap_creds, "test-project"))
    signer_cls = mocker.patch("gmail_client.iam.Signer")
    creds_cls = mocker.patch("gmail_client.service_account.Credentials")

    gmail_client._build_delegated_credentials("mailbox@example.com", ["scope-a"])

    bootstrap_creds.refresh.assert_called_once()
    signer_cls.assert_called_once_with(
        mocker.ANY, bootstrap_creds, "runtime@test-project.iam.gserviceaccount.com"
    )
    creds_cls.assert_called_once_with(
        signer_cls.return_value,
        "runtime@test-project.iam.gserviceaccount.com",
        gmail_client._TOKEN_URI,
        scopes=["scope-a"],
        subject="mailbox@example.com",
    )


def test_get_gmail_service_caches_per_subject(mocker):
    mocker.patch("gmail_client._build_delegated_credentials")
    build = mocker.patch("gmail_client.build")

    gmail_client._get_gmail_service("mailbox@example.com")
    gmail_client._get_gmail_service("mailbox@example.com")

    build.assert_called_once()


# --- start_watch ---


def test_start_watch_calls_users_watch_with_topic(mocker):
    fake_service = mocker.Mock()
    fake_service.users.return_value.watch.return_value.execute.return_value = {
        "historyId": "111",
        "expiration": "1234567890000",
    }
    mocker.patch("gmail_client._get_gmail_service", return_value=fake_service)

    result = gmail_client.start_watch("mailbox@example.com", "projects/p/topics/t")

    fake_service.users.return_value.watch.assert_called_once_with(
        userId="me", body={"topicName": "projects/p/topics/t", "labelFilterAction": "include"}
    )
    assert result == {"historyId": "111", "expiration": "1234567890000"}


# --- list_new_message_ids ---


def test_list_new_message_ids_returns_ids_across_pages(mocker):
    fake_service = mocker.Mock()
    fake_service.users.return_value.history.return_value.list.return_value.execute.side_effect = [
        {
            "history": [{"messagesAdded": [{"message": {"id": "m1"}}]}],
            "nextPageToken": "page2",
        },
        {"history": [{"messagesAdded": [{"message": {"id": "m2"}}]}]},
    ]
    mocker.patch("gmail_client._get_gmail_service", return_value=fake_service)

    result = gmail_client.list_new_message_ids("mailbox@example.com", "100")

    assert result == ["m1", "m2"]


def test_list_new_message_ids_empty_history_returns_empty_list(mocker):
    fake_service = mocker.Mock()
    fake_service.users.return_value.history.return_value.list.return_value.execute.return_value = {}
    mocker.patch("gmail_client._get_gmail_service", return_value=fake_service)

    result = gmail_client.list_new_message_ids("mailbox@example.com", "100")

    assert result == []


def test_list_new_message_ids_skips_entries_missing_message_id(mocker):
    fake_service = mocker.Mock()
    fake_service.users.return_value.history.return_value.list.return_value.execute.return_value = {
        "history": [{"messagesAdded": [{"message": {}}, {"message": {"id": "m1"}}]}],
    }
    mocker.patch("gmail_client._get_gmail_service", return_value=fake_service)

    result = gmail_client.list_new_message_ids("mailbox@example.com", "100")

    assert result == ["m1"]


def test_list_new_message_ids_raises_history_expired_on_404(mocker):
    fake_service = mocker.Mock()
    fake_service.users.return_value.history.return_value.list.return_value.execute.side_effect = (
        _http_error(404)
    )
    mocker.patch("gmail_client._get_gmail_service", return_value=fake_service)

    with pytest.raises(gmail_client.HistoryIdExpiredError):
        gmail_client.list_new_message_ids("mailbox@example.com", "stale-id")


def test_list_new_message_ids_reraises_other_http_errors(mocker):
    fake_service = mocker.Mock()
    fake_service.users.return_value.history.return_value.list.return_value.execute.side_effect = (
        _http_error(500)
    )
    mocker.patch("gmail_client._get_gmail_service", return_value=fake_service)

    with pytest.raises(HttpError):
        gmail_client.list_new_message_ids("mailbox@example.com", "100")


# --- get_message ---


def _b64_no_padding(text: str) -> str:
    encoded = base64.urlsafe_b64encode(text.encode("utf-8")).decode("utf-8")
    return encoded.rstrip("=")  # simulate Gmail's often-unpadded response


def test_get_message_returns_decoded_subject_body_labels(mocker):
    fake_service = mocker.Mock()
    fake_service.users.return_value.messages.return_value.get.return_value.execute.return_value = {
        "labelIds": ["INBOX", "UNREAD"],
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Test Subject"},
                {"name": "From", "value": "sender@example.com"},
            ],
            "mimeType": "text/plain",
            "body": {"data": _b64_no_padding("Hello, this is the body.")},
        },
    }
    mocker.patch("gmail_client._get_gmail_service", return_value=fake_service)

    subject, body, label_ids = gmail_client.get_message("mailbox@example.com", "msg-1")

    assert subject == "Test Subject"
    assert body == "Hello, this is the body."
    assert label_ids == ["INBOX", "UNREAD"]


def test_get_message_prefers_plain_text_part_in_multipart(mocker):
    fake_service = mocker.Mock()
    fake_service.users.return_value.messages.return_value.get.return_value.execute.return_value = {
        "labelIds": ["INBOX"],
        "payload": {
            "headers": [{"name": "Subject", "value": "Multipart"}],
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/html", "body": {"data": _b64_no_padding("<p>html</p>")}},
                {"mimeType": "text/plain", "body": {"data": _b64_no_padding("plain text body")}},
            ],
        },
    }
    mocker.patch("gmail_client._get_gmail_service", return_value=fake_service)

    _subject, body, _labels = gmail_client.get_message("mailbox@example.com", "msg-2")

    assert body == "plain text body"


def test_get_message_falls_back_to_html_when_no_plain_text(mocker):
    fake_service = mocker.Mock()
    fake_service.users.return_value.messages.return_value.get.return_value.execute.return_value = {
        "labelIds": [],
        "payload": {
            "headers": [{"name": "Subject", "value": "HTML only"}],
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/html", "body": {"data": _b64_no_padding("<p>only html</p>")}},
            ],
        },
    }
    mocker.patch("gmail_client._get_gmail_service", return_value=fake_service)

    _subject, body, _labels = gmail_client.get_message("mailbox@example.com", "msg-3")

    assert body == "<p>only html</p>"


def test_get_message_missing_subject_header_returns_empty_string(mocker):
    fake_service = mocker.Mock()
    fake_service.users.return_value.messages.return_value.get.return_value.execute.return_value = {
        "labelIds": [],
        "payload": {
            "headers": [],
            "mimeType": "text/plain",
            "body": {"data": _b64_no_padding("body only")},
        },
    }
    mocker.patch("gmail_client._get_gmail_service", return_value=fake_service)

    subject, _body, _labels = gmail_client.get_message("mailbox@example.com", "msg-4")

    assert subject == ""


def test_get_message_falls_through_when_plain_text_part_has_no_data(mocker):
    """A text/plain part with no body.data (e.g. it's an attachment reference,
    not inline content) shouldn't short-circuit -- extraction should keep
    looking rather than returning empty/crashing.
    """
    fake_service = mocker.Mock()
    fake_service.users.return_value.messages.return_value.get.return_value.execute.return_value = {
        "labelIds": [],
        "payload": {
            "headers": [{"name": "Subject", "value": "Empty plain part"}],
            "mimeType": "multipart/mixed",
            "parts": [
                {"mimeType": "text/plain", "body": {}},
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "body": {"data": _b64_no_padding("nested body")},
                        },
                    ],
                },
            ],
        },
    }
    mocker.patch("gmail_client._get_gmail_service", return_value=fake_service)

    _subject, body, _labels = gmail_client.get_message("mailbox@example.com", "msg-6")

    assert body == "nested body"


def test_get_message_returns_empty_body_when_no_part_has_data(mocker):
    fake_service = mocker.Mock()
    fake_service.users.return_value.messages.return_value.get.return_value.execute.return_value = {
        "labelIds": [],
        "payload": {
            "headers": [{"name": "Subject", "value": "Attachment only"}],
            "mimeType": "multipart/mixed",
            "parts": [
                {"mimeType": "application/pdf", "body": {"attachmentId": "att-1"}},
            ],
        },
    }
    mocker.patch("gmail_client._get_gmail_service", return_value=fake_service)

    _subject, body, _labels = gmail_client.get_message("mailbox@example.com", "msg-5")

    assert body == ""


def test_decode_body_handles_missing_padding():
    unpadded = _b64_no_padding("padding test")

    assert gmail_client._decode_body(unpadded) == "padding test"


# --- Task 15: ensure_label / apply_label / already_labeled ---


def _fake_service_with_labels(mocker, labels: list[dict]):
    fake_service = mocker.Mock()
    fake_service.users.return_value.labels.return_value.list.return_value.execute.return_value = {
        "labels": labels
    }
    mocker.patch("gmail_client._get_gmail_service", return_value=fake_service)
    return fake_service


def test_ensure_label_returns_existing_id_without_creating(mocker):
    fake_service = _fake_service_with_labels(mocker, [{"id": "Label_1", "name": "Triage/Billing"}])

    result = gmail_client.ensure_label("mailbox@example.com", "Triage/Billing")

    assert result == "Label_1"
    fake_service.users.return_value.labels.return_value.create.assert_not_called()


def test_ensure_label_creates_when_missing(mocker):
    fake_service = _fake_service_with_labels(mocker, [])
    fake_service.users.return_value.labels.return_value.create.return_value.execute.return_value = {
        "id": "Label_new",
        "name": "Triage/Billing",
    }

    result = gmail_client.ensure_label("mailbox@example.com", "Triage/Billing")

    assert result == "Label_new"
    fake_service.users.return_value.labels.return_value.create.assert_called_once_with(
        userId="me", body={"name": "Triage/Billing"}
    )


def test_ensure_label_caches_across_calls(mocker):
    fake_service = _fake_service_with_labels(mocker, [{"id": "Label_1", "name": "Triage/Billing"}])

    gmail_client.ensure_label("mailbox@example.com", "Triage/Billing")
    gmail_client.ensure_label("mailbox@example.com", "Triage/Billing")

    fake_service.users.return_value.labels.return_value.list.assert_called_once()


def test_ensure_label_handles_409_race_by_refetching(mocker):
    fake_service = _fake_service_with_labels(mocker, [])
    fake_service.users.return_value.labels.return_value.create.return_value.execute.side_effect = (
        _http_error(409)
    )
    # Second list() call (the refetch after 409) sees the label another
    # instance just created.
    fake_service.users.return_value.labels.return_value.list.return_value.execute.side_effect = [
        {"labels": []},
        {"labels": [{"id": "Label_raced", "name": "Triage/Billing"}]},
    ]

    result = gmail_client.ensure_label("mailbox@example.com", "Triage/Billing")

    assert result == "Label_raced"


def test_ensure_label_raises_if_409_but_still_not_found_after_refetch(mocker):
    fake_service = _fake_service_with_labels(mocker, [])
    fake_service.users.return_value.labels.return_value.create.return_value.execute.side_effect = (
        _http_error(409)
    )

    with pytest.raises(HttpError):
        gmail_client.ensure_label("mailbox@example.com", "Triage/Billing")


def test_ensure_label_reraises_non_409_create_errors(mocker):
    fake_service = _fake_service_with_labels(mocker, [])
    fake_service.users.return_value.labels.return_value.create.return_value.execute.side_effect = (
        _http_error(400)
    )

    with pytest.raises(HttpError):
        gmail_client.ensure_label("mailbox@example.com", "Reserved/Name")


def test_apply_label_calls_modify_with_add_label_ids(mocker):
    fake_service = mocker.Mock()
    mocker.patch("gmail_client._get_gmail_service", return_value=fake_service)

    gmail_client.apply_label("mailbox@example.com", "msg-1", "Label_1")

    fake_service.users.return_value.messages.return_value.modify.assert_called_once_with(
        userId="me", id="msg-1", body={"addLabelIds": ["Label_1"]}
    )


def test_already_labeled_true_when_taxonomy_label_present(mocker, sample_taxonomy):
    _fake_service_with_labels(mocker, [{"id": "Label_1", "name": "Triage/Billing"}])

    result = gmail_client.already_labeled(
        "mailbox@example.com", ["INBOX", "Label_1"], sample_taxonomy
    )

    assert result is True


def test_already_labeled_true_for_needs_review_label(mocker, sample_taxonomy):
    _fake_service_with_labels(mocker, [{"id": "Label_nr", "name": "Triage/Needs Review"}])

    result = gmail_client.already_labeled(
        "mailbox@example.com", ["INBOX", "Label_nr"], sample_taxonomy
    )

    assert result is True


def test_already_labeled_false_when_no_taxonomy_label_present(mocker, sample_taxonomy):
    _fake_service_with_labels(mocker, [{"id": "Label_1", "name": "Triage/Billing"}])

    result = gmail_client.already_labeled(
        "mailbox@example.com", ["INBOX", "UNREAD"], sample_taxonomy
    )

    assert result is False


def test_already_labeled_false_when_taxonomy_label_not_yet_created_in_gmail(
    mocker, sample_taxonomy
):
    """A message can't carry a label that was never created -- an empty
    Gmail label list (first message ever processed) is a legitimate state,
    not an error.
    """
    _fake_service_with_labels(mocker, [])

    result = gmail_client.already_labeled("mailbox@example.com", ["INBOX"], sample_taxonomy)

    assert result is False
