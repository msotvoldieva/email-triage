"""Task 23 (tasks/todo.md): integration tests against a real sandbox mailbox.

NOT part of the default `pytest` run -- excluded via pyproject.toml's
`testpaths = ["tests/unit"]`. Requires a live, deployed Cloud Run service and
real sandbox credentials; run explicitly:

    pytest tests/integration/test_end_to_end.py -v

Gated on environment variables (SANDBOX_MAILBOX_ADDRESS, SANDBOX_PROJECT_ID,
SANDBOX_CLOUD_RUN_SERVICE_NAME) so it skips cleanly rather than failing
confusingly when sandbox infra doesn't exist yet -- e.g. every unit-test run,
or a machine without sandbox access. Every email sent here is synthetic and
invented, never real PHI, matching SPEC-email-triage-core.md's Testing
Strategy even though this suite talks to real GCP services.

UNVERIFIED: unlike everything under tests/unit/, this file has not been run
against a live sandbox -- there is no sandbox in this build environment to
run it against. Treat it as a reasoned first draft to validate for real
during Task 22/23, not as proven-correct code.
"""

import base64
import os
import time
import uuid
from datetime import UTC, datetime
from email.mime.text import MIMEText

import pytest
from google.cloud import bigquery
from google.cloud import logging as cloud_logging

import gmail_client

_MAILBOX = os.environ.get("SANDBOX_MAILBOX_ADDRESS", "")
_PROJECT_ID = os.environ.get("SANDBOX_PROJECT_ID", "")
_SERVICE_NAME = os.environ.get("SANDBOX_CLOUD_RUN_SERVICE_NAME", "")

pytestmark = pytest.mark.skipif(
    not (_MAILBOX and _PROJECT_ID and _SERVICE_NAME),
    reason=(
        "Integration tests require SANDBOX_MAILBOX_ADDRESS, SANDBOX_PROJECT_ID, "
        "and SANDBOX_CLOUD_RUN_SERVICE_NAME -- see docs/SETUP.md"
    ),
)

_POLL_TIMEOUT_SECONDS = 180
_POLL_INTERVAL_SECONDS = 5


def _send_test_email(subject: str, body: str) -> None:
    """Sends a synthetic test email to the sandbox mailbox, from itself.
    Reuses gmail_client's own domain-wide-delegated auth path -- no separate
    sender credentials needed, since the mailbox can send to itself.
    """
    service = gmail_client._get_gmail_service(_MAILBOX)
    message = MIMEText(body)
    message["to"] = _MAILBOX
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    service.users().messages().send(userId="me", body={"raw": raw}).execute()


def _wait_for_label(subject: str, expected_label: str) -> dict:
    """Polls the mailbox for a message with this exact subject to receive
    expected_label, up to _POLL_TIMEOUT_SECONDS. Fails the test (not a silent
    timeout) if the label never appears -- that failure IS the test signal.
    """
    service = gmail_client._get_gmail_service(_MAILBOX)

    deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        label_map = gmail_client._get_label_map(_MAILBOX, force_refresh=True)
        label_id = label_map.get(expected_label)

        if label_id is not None:
            response = (
                service.users().messages().list(userId="me", q=f'subject:"{subject}"').execute()
            )
            for msg_ref in response.get("messages", []):
                msg = service.users().messages().get(userId="me", id=msg_ref["id"]).execute()
                if label_id in msg.get("labelIds", []):
                    return msg

        time.sleep(_POLL_INTERVAL_SECONDS)

    pytest.fail(
        f"No message with subject {subject!r} received label {expected_label!r} "
        f"within {_POLL_TIMEOUT_SECONDS}s"
    )


def _audit_row_for(message_id: str) -> dict | None:
    client = bigquery.Client(project=_PROJECT_ID)
    query = f"""
        SELECT category, confidence, needs_review, model_version, classified_at
        FROM `{_PROJECT_ID}.email_triage_audit.classification_events`
        WHERE message_id = @message_id
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("message_id", "STRING", message_id)]
    )
    rows = list(client.query(query, job_config=job_config).result())
    return dict(rows[0]) if rows else None


def _recent_logs_contain(marker: str, since: datetime) -> bool:
    """Greps this Cloud Run service's Cloud Logging entries since `since`
    for `marker`. Checked as both the raw text payload and the JSON payload
    serialized to a string, since structured `extra` log fields (e.g. this
    project's own logger.info(..., extra={...}) calls) land in the JSON
    payload, not necessarily the text payload.
    """
    client = cloud_logging.Client(project=_PROJECT_ID)
    filter_str = (
        f'resource.type="cloud_run_revision" '
        f'AND resource.labels.service_name="{_SERVICE_NAME}" '
        f'AND timestamp>="{since.isoformat()}"'
    )
    for entry in client.list_entries(filter_=filter_str, page_size=1000):
        if marker in str(entry.payload):
            return True
    return False


@pytest.mark.parametrize(
    "category,label,subject,body",
    [
        (
            "billing",
            "Triage/Billing",
            "Question about my last invoice",
            "I was charged twice for my last visit, can you check the billing?",
        ),
        (
            "clinical",
            "Triage/Clinical",
            "Prescription refill request",
            "Could I get a refill on my current prescription please?",
        ),
        (
            "scheduling",
            "Triage/Scheduling",
            "Need to reschedule my appointment",
            "I need to move my Thursday appointment to sometime next week.",
        ),
        (
            "general-inquiry",
            "Triage/General",
            "Question about your services",
            "Do you offer telehealth appointments for existing patients?",
        ),
        (
            "personal",
            "Triage/Personal",
            "Dinner this weekend?",
            "Hey, are we still on for dinner Saturday? Let me know what time works.",
        ),
    ],
)
def test_email_classified_and_labeled_correctly(category, label, subject, body):
    unique_subject = f"{subject} [{uuid.uuid4().hex[:8]}]"

    _send_test_email(unique_subject, body)
    message = _wait_for_label(unique_subject, label)

    audit_row = _audit_row_for(message["id"])
    assert audit_row is not None, "Expected a matching BigQuery audit row"
    assert audit_row["category"] == category


def test_ambiguous_email_lands_in_needs_review():
    unique_subject = f"Hi [{uuid.uuid4().hex[:8]}]"

    _send_test_email(unique_subject, "Hi, just checking in.")
    message = _wait_for_label(unique_subject, "Triage/Needs Review")

    audit_row = _audit_row_for(message["id"])
    assert audit_row is not None
    assert audit_row["needs_review"] is True


def test_no_subject_or_body_in_cloud_logging_output():
    """The real end-to-end proof that SPEC-email-triage-core.md's 'never log
    subject/body' boundary holds in an actual deployment, not just against
    unit-test mocks: sends one email with a distinctive marker in both
    subject and body, waits for it to be fully processed, then asserts
    neither marker ever appears in this run's Cloud Logging output.
    """
    marker = uuid.uuid4().hex
    subject = f"LOGGING_TEST_SUBJECT_{marker}"
    body = f"LOGGING_TEST_BODY_{marker}"
    test_started_at = datetime.now(UTC)

    _send_test_email(subject, body)
    _wait_for_label(subject, "Triage/General")

    assert not _recent_logs_contain(subject, since=test_started_at)
    assert not _recent_logs_contain(body, since=test_started_at)
    assert not _recent_logs_contain(marker, since=test_started_at)
