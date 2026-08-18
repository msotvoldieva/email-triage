"""Cloud Run entrypoint: Pub/Sub push handler.

Task 6: verifies the Pub/Sub OIDC bearer token, then parses the Gmail watch
envelope into {email_address, history_id}. Task 10: wires that envelope to
gmail_client + state_store to fetch new mail. Task 16: classifies and labels
each fetched message that isn't already labeled.

There is no PHI in a Pub/Sub push envelope (Gmail's push payload only ever
carries emailAddress + historyId, never message content). Fetched messages DO
carry subject/body (gmail_client.get_message) -- never log those, only counts
and opaque message IDs.
"""

import base64
import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from google.auth.exceptions import GoogleAuthError
from google.auth.transport import requests as google_auth_requests
from google.oauth2 import id_token
from googleapiclient.errors import HttpError
from werkzeug.exceptions import BadRequest

import classifier
import config
import gmail_client
import state_store
from taxonomy import Taxonomy, load_taxonomy

logger = logging.getLogger(__name__)

_google_auth_request = google_auth_requests.Request()

# Resolved relative to this file's own location, not the process's cwd -- local
# dev runs functions-framework from within src/, the container runs it from
# /app, and this must work correctly under both.
_TAXONOMY_PATH = Path(__file__).resolve().parent.parent / "taxonomy" / "taxonomy.yaml"


@lru_cache(maxsize=1)
def _get_taxonomy() -> Taxonomy:
    """Loaded once per warm instance -- the taxonomy is baked into the
    container image (not fetched dynamically), so a new deploy is already
    required to pick up any change; no benefit to re-reading the file on
    every push. A malformed taxonomy raises here and fails the whole
    request loudly, which is correct: that's a deployment-wide
    misconfiguration, not a single bad message.
    """
    return load_taxonomy(_TAXONOMY_PATH)


@dataclass(frozen=True)
class PushEnvelope:
    email_address: str
    history_id: str


def handle_pubsub_push(request):
    """functions-framework HTTP entrypoint. Returns (body, status) per Flask's
    tuple response convention -- functions-framework serves this over HTTP.
    """
    if not _verify_push_request(request):
        return "", 401

    envelope = _parse_envelope(request)
    if envelope is None:
        return "", 400

    logger.info(
        "push.received",
        extra={"email_address": envelope.email_address, "history_id": envelope.history_id},
    )

    _process_envelope(envelope)

    return "", 200


def _process_envelope(envelope: PushEnvelope) -> None:
    """Fetches whatever's new since the last stored cursor, then advances the
    cursor to this push's historyId.

    Using the push's own historyId as the next cursor (rather than whatever
    history.list's response reports) is a deliberate simplification: it's the
    standard pattern for Gmail push notifications, and any slight staleness is
    harmless here -- Task 15/16's label-based dedupe makes a message showing up
    in two consecutive fetches a no-op, not a bug.

    No stored cursor (first run, or after a HistoryIdExpiredError) means there's
    no safe reference point to diff against -- bootstrap by adopting this push's
    historyId as the baseline without fetching a backlog, rather than guessing.
    """
    last_history_id = state_store.get_last_history_id()

    if last_history_id is None:
        logger.info("history.bootstrap", extra={"history_id": envelope.history_id})
        state_store.set_last_history_id(envelope.history_id)
        return

    try:
        message_ids = gmail_client.list_new_message_ids(envelope.email_address, last_history_id)
    except gmail_client.HistoryIdExpiredError:
        logger.warning("history.expired_resync", extra={"history_id": envelope.history_id})
        state_store.set_last_history_id(envelope.history_id)
        return

    taxonomy = _get_taxonomy()
    fetched_count = 0
    skipped_count = 0
    labeled_count = 0
    failed_count = 0

    for message_id in message_ids:
        try:
            subject, body, label_ids = gmail_client.get_message(envelope.email_address, message_id)
        except HttpError as exc:
            # A message can vanish between history.list and messages.get (e.g. the
            # user deleted it in the seconds between). One bad message shouldn't
            # abort the rest of the batch or trigger a Pub/Sub redelivery of
            # messages we already successfully fetched.
            logger.warning("message.fetch_failed", extra={"status": exc.resp.status})
            continue
        fetched_count += 1

        if gmail_client.already_labeled(envelope.email_address, label_ids, taxonomy):
            # Pub/Sub redelivery, or this message showed up in two consecutive
            # fetches (see the historyId cursor note above) -- already handled,
            # not an error, and specifically skips the Vertex AI call too.
            skipped_count += 1
            continue

        try:
            _classify_and_label(envelope.email_address, message_id, subject, body, taxonomy)
        except Exception:  # Deliberate per-message fault isolation, not a bare except.
            # classify()/ensure_label()/apply_label() can each fail in ways that
            # aren't fully enumerable in advance (Vertex AI timeouts/quota, Gmail
            # API errors). A failure here means THIS message doesn't get its
            # label -- and isn't automatically retried later, since the cursor
            # still advances past it below. That's a known, deliberate MVP
            # tradeoff (logged clearly, not silently dropped) rather than
            # building partial-cursor-advancement logic for v1.
            failed_count += 1
            logger.exception("message.classify_or_label_failed", extra={"message_id": message_id})
            continue
        labeled_count += 1

    logger.info(
        "push.fetched",
        extra={
            "message_count": fetched_count,
            "skipped_count": skipped_count,
            "labeled_count": labeled_count,
            "failed_count": failed_count,
            "history_id": envelope.history_id,
        },
    )

    state_store.set_last_history_id(envelope.history_id)


def _classify_and_label(
    subject_email: str, message_id: str, subject: str, body: str, taxonomy: Taxonomy
) -> None:
    result = classifier.classify(subject, body, taxonomy)
    label_name = taxonomy.label_for(result.category)
    label_id = gmail_client.ensure_label(subject_email, label_name)
    gmail_client.apply_label(subject_email, message_id, label_id)


def _verify_push_request(request) -> bool:
    settings = config.load_settings()

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return False
    token = auth_header[len("Bearer ") :]

    try:
        claims = id_token.verify_oauth2_token(
            token, _google_auth_request, audience=settings.cloud_run_audience
        )
    except (ValueError, GoogleAuthError):
        logger.warning("push.auth_failed")
        return False

    return claims.get("email") == settings.invoker_service_account_email


def _parse_envelope(request) -> PushEnvelope | None:
    try:
        body = request.get_json(force=True, silent=False)
    except (ValueError, TypeError, BadRequest):
        return None

    if not isinstance(body, dict):
        return None

    message = body.get("message")
    if not isinstance(message, dict):
        return None

    data_b64 = message.get("data")
    if not data_b64:
        return None

    try:
        decoded = base64.b64decode(data_b64).decode("utf-8")
        payload = json.loads(decoded)
    except (ValueError, TypeError):
        return None

    if not isinstance(payload, dict):
        return None

    email_address = payload.get("emailAddress")
    history_id = payload.get("historyId")
    if not email_address or not history_id:
        return None

    return PushEnvelope(email_address=email_address, history_id=str(history_id))
