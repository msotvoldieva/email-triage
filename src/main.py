"""Cloud Run entrypoint: Pub/Sub push handler.

Task 6: verifies the Pub/Sub OIDC bearer token, then parses the Gmail watch
envelope into {email_address, history_id}. Task 10: wires that envelope to
gmail_client + state_store to actually fetch new mail -- no classification or
labeling yet (Task 16 adds that); this slice only proves fetch works end to end.

There is no PHI in a Pub/Sub push envelope (Gmail's push payload only ever
carries emailAddress + historyId, never message content). Fetched messages DO
carry subject/body (gmail_client.get_message) -- never log those, only counts.
"""

import base64
import json
import logging
from dataclasses import dataclass

from google.auth.exceptions import GoogleAuthError
from google.auth.transport import requests as google_auth_requests
from google.oauth2 import id_token
from googleapiclient.errors import HttpError
from werkzeug.exceptions import BadRequest

import config
import gmail_client
import state_store

logger = logging.getLogger(__name__)

_google_auth_request = google_auth_requests.Request()


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

    fetched_count = 0
    for message_id in message_ids:
        try:
            gmail_client.get_message(envelope.email_address, message_id)
        except HttpError as exc:
            # A message can vanish between history.list and messages.get (e.g. the
            # user deleted it in the seconds between). One bad message shouldn't
            # abort the rest of the batch or trigger a Pub/Sub redelivery of
            # messages we already successfully fetched.
            logger.warning("message.fetch_failed", extra={"status": exc.resp.status})
            continue
        fetched_count += 1

    logger.info(
        "push.fetched",
        extra={"message_count": fetched_count, "history_id": envelope.history_id},
    )

    state_store.set_last_history_id(envelope.history_id)


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
