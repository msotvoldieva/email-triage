"""Gmail API wrapper: watch, history.list, get, and (Task 15) label operations.

Every function here is scoped to a single shared mailbox, identified by
`subject_email` -- this project never touches more than one mailbox
(SPEC-email-triage-core.md, "Ask first: adding a second mailbox").
"""

import base64
import logging
from functools import lru_cache

import google.auth
from google.auth import iam
from google.auth.transport import requests as google_auth_transport_requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from taxonomy import Taxonomy

logger = logging.getLogger(__name__)

# Gmail label name -> id, cached per mailbox. A plain dict (not lru_cache) so
# ensure_label can update a single entry in place after creating a label,
# rather than invalidating the whole cache.
_LABEL_NAME_TO_ID_CACHE: dict[str, dict[str, str]] = {}

# Both scopes are authorized in the client's Workspace Admin console for this
# project's runtime SA (docs/SETUP.md, Task 24) -- gmail.modify is technically a
# superset of gmail.readonly, but both are requested to match what's documented
# there; no behavior depends on which one a given call actually needed.
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

_TOKEN_URI = "https://oauth2.googleapis.com/token"


class HistoryIdExpiredError(Exception):
    """Gmail rejected our historyId as too old (a 404-class error) -- history is
    retained for a limited window, and a long gap (e.g. a cold start after
    downtime) can exceed it. Callers should treat this as 'resync needed': reset
    the cursor to 'start from now' rather than crash or silently miss mail.
    """


def _build_delegated_credentials(
    subject_email: str, scopes: list[str]
) -> service_account.Credentials:
    """Builds domain-wide-delegated credentials, subject-impersonating the shared
    mailbox, WITHOUT a downloaded private key file.

    Cloud Run's attached SA identity can't use the standard `.with_subject()` flow
    -- that requires local JWT signing with a private key, which google.auth's
    compute/metadata-derived credentials never expose (Google holds the key).
    Instead this uses `google.auth.iam.Signer`, which signs the JWT assertion
    remotely via the IAM Credentials API. Verified against Google's own
    gce-to-adminsdk reference pattern before writing this, not assumed.

    Requires the runtime SA to hold roles/iam.serviceAccountTokenCreator on
    ITSELF (infra/iam.tf) -- without that self-grant, the remote signing call
    fails with a permission error.
    """
    bootstrap_credentials, _ = google.auth.default()
    request = google_auth_transport_requests.Request()
    bootstrap_credentials.refresh(request)

    signer = iam.Signer(request, bootstrap_credentials, bootstrap_credentials.service_account_email)

    return service_account.Credentials(
        signer,
        bootstrap_credentials.service_account_email,
        _TOKEN_URI,
        scopes=scopes,
        subject=subject_email,
    )


@lru_cache(maxsize=4)
def _get_gmail_service(subject_email: str):
    credentials = _build_delegated_credentials(subject_email, GMAIL_SCOPES)
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def start_watch(subject_email: str, topic_name: str) -> dict:
    """Starts (or renews) a Gmail watch on the mailbox, publishing history
    notifications to `topic_name` (full resource name, e.g.
    'projects/{project}/topics/{topic}'). Returns the API response, containing
    historyId and expiration (ms since epoch, ~7 days out -- Task 20 renews
    before this expires).
    """
    service = _get_gmail_service(subject_email)
    return (
        service.users()
        .watch(userId="me", body={"topicName": topic_name, "labelFilterAction": "include"})
        .execute()
    )


def list_new_message_ids(subject_email: str, start_history_id: str) -> list[str]:
    """Returns the IDs of messages added since start_history_id, handling
    pagination. Raises HistoryIdExpiredError if Gmail reports the historyId is
    no longer valid.
    """
    service = _get_gmail_service(subject_email)
    message_ids: list[str] = []
    page_token = None

    while True:
        try:
            response = (
                service.users()
                .history()
                .list(
                    userId="me",
                    startHistoryId=start_history_id,
                    historyTypes=["messageAdded"],
                    pageToken=page_token,
                )
                .execute()
            )
        except HttpError as exc:
            if exc.resp.status == 404:
                raise HistoryIdExpiredError(str(exc)) from exc
            raise

        for record in response.get("history", []):
            for added in record.get("messagesAdded", []):
                message_id = added.get("message", {}).get("id")
                if message_id:
                    message_ids.append(message_id)

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return message_ids


def get_message(subject_email: str, message_id: str) -> tuple[str, str, list[str]]:
    """Fetches one message and returns (subject, body, label_ids) -- decoded
    plain text, never the raw base64/MIME payload.
    """
    service = _get_gmail_service(subject_email)
    raw = service.users().messages().get(userId="me", id=message_id, format="full").execute()

    payload = raw.get("payload", {})
    headers = payload.get("headers", [])
    subject = next(
        (h.get("value", "") for h in headers if h.get("name", "").lower() == "subject"), ""
    )
    body = _extract_body(payload)
    label_ids = raw.get("labelIds", [])

    return subject, body, label_ids


def _extract_body(payload: dict) -> str:
    """Walks a Gmail message payload's MIME tree, preferring the first
    text/plain part and falling back to whatever else has body data (typically
    text/html) if no plain-text part exists.
    """
    body_data = payload.get("body", {}).get("data")
    parts = payload.get("parts", [])

    if payload.get("mimeType") == "text/plain" and body_data:
        return _decode_body(body_data)

    if not parts:
        return _decode_body(body_data) if body_data else ""

    for part in parts:
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data")
            if data:
                return _decode_body(data)

    for part in parts:
        nested = _extract_body(part)
        if nested:
            return nested

    return ""


def _decode_body(data: str) -> str:
    """Gmail's body data is URL-safe base64, but often arrives without padding
    -- decoding it directly can raise 'Incorrect padding'. Pad to a multiple of
    4 before decoding; a well-documented, easy-to-hit Gmail API gotcha, verified
    rather than assumed.
    """
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def _get_label_map(subject_email: str, *, force_refresh: bool = False) -> dict[str, str]:
    if not force_refresh and subject_email in _LABEL_NAME_TO_ID_CACHE:
        return _LABEL_NAME_TO_ID_CACHE[subject_email]

    service = _get_gmail_service(subject_email)
    response = service.users().labels().list(userId="me").execute()
    label_map = {
        label["name"]: label["id"]
        for label in response.get("labels", [])
        if "name" in label and "id" in label
    }
    _LABEL_NAME_TO_ID_CACHE[subject_email] = label_map
    return label_map


def ensure_label(subject_email: str, label_name: str) -> str:
    """Returns the Gmail label ID for label_name, creating it if it doesn't
    exist yet. The label list is cached per mailbox and refreshed only on a
    cache miss or a create-time 409, not on every call.

    Handles the race where another instance creates the same label between
    our lookup and our create call: Gmail's labels.create returns 409 for a
    genuine name conflict (verified against Google's documented behavior --
    distinct from the 400 it returns for a reserved-name conflict, which is
    a real error and should propagate) -- on 409, re-fetch the label list to
    pick up what the other instance just created, rather than treating it as
    a failure.
    """
    label_map = _get_label_map(subject_email)
    if label_name in label_map:
        return label_map[label_name]

    service = _get_gmail_service(subject_email)
    try:
        created = service.users().labels().create(userId="me", body={"name": label_name}).execute()
    except HttpError as exc:
        if exc.resp.status != 409:
            raise
        label_map = _get_label_map(subject_email, force_refresh=True)
        if label_name not in label_map:
            raise
        return label_map[label_name]

    label_id = created["id"]
    _LABEL_NAME_TO_ID_CACHE.setdefault(subject_email, {})[label_name] = label_id
    return label_id


def apply_label(subject_email: str, message_id: str, label_id: str) -> None:
    service = _get_gmail_service(subject_email)
    service.users().messages().modify(
        userId="me", id=message_id, body={"addLabelIds": [label_id]}
    ).execute()


def already_labeled(subject_email: str, existing_label_ids: list[str], taxonomy: Taxonomy) -> bool:
    """True if the message already carries any of the taxonomy's labels
    (including needs-review) -- used to make Pub/Sub redelivery a no-op
    rather than a duplicate classification/audit write. A taxonomy label
    that hasn't been created in Gmail yet (e.g. the very first message ever
    processed) simply can't be on any message -- that's a normal state, not
    an error.
    """
    label_map = _get_label_map(subject_email)
    taxonomy_label_ids = {
        label_map[category.label] for category in taxonomy.categories if category.label in label_map
    }
    return bool(taxonomy_label_ids.intersection(existing_label_ids))
