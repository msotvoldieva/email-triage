"""Cloud Run entrypoint: Pub/Sub push handler.

Task 6 (tasks/todo.md): verifies the Pub/Sub OIDC bearer token, then parses the
Gmail watch envelope into {email_address, history_id}. Nothing else yet --
fetching, classifying, and labeling are wired in by later tasks (Phase 2-4).

There is no PHI in a Pub/Sub push envelope (Gmail's push payload only ever
carries emailAddress + historyId, never message content), but this function
must not become the place that assumption quietly stops being true later --
log only the two parsed metadata fields, never the raw request body.
"""

import base64
import json
import logging
from dataclasses import dataclass

from google.auth.exceptions import GoogleAuthError
from google.auth.transport import requests as google_auth_requests
from google.oauth2 import id_token
from werkzeug.exceptions import BadRequest

import config

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

    return "", 200


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
