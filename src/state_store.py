"""Firestore-backed state: Gmail's historyId cursor, and watch-renewal tracking.

This is small operational state only -- NOT the PHI content store deferred to
v2 (SPEC-email-triage-core.md "Out of Scope (v1)"). Every document here holds
only IDs, timestamps, or status fields. Nothing PHI-bearing ever belongs here.
"""

from functools import lru_cache

from google.cloud import firestore

_COLLECTION = "email_triage_state"

_HISTORY_DOCUMENT_ID = "gmail_history_cursor"
_HISTORY_FIELD = "history_id"

_WATCH_RENEWAL_DOCUMENT_ID = "watch_renewal"
_RENEWED_AT_FIELD = "renewed_at"
_EXPIRATION_FIELD = "expiration"


@lru_cache(maxsize=1)
def _get_client() -> firestore.Client:
    return firestore.Client()


def get_last_history_id() -> str | None:
    """Returns the last-processed historyId, or None if no cursor has been
    stored yet (first run, or after a reset). Callers should treat None as
    "start from now", not as an error -- this mirrors how
    gmail_client.HistoryIdExpiredError is handled by the push handler (Task 10).
    """
    doc = _get_document_ref(_HISTORY_DOCUMENT_ID).get()
    if not doc.exists:
        return None
    return doc.to_dict().get(_HISTORY_FIELD)


def set_last_history_id(history_id: str) -> None:
    _get_document_ref(_HISTORY_DOCUMENT_ID).set({_HISTORY_FIELD: history_id})


def get_last_watch_renewal() -> dict | None:
    """Returns {'renewed_at': ..., 'expiration': ...} for the last successful
    watch() renewal, or None if none has ever succeeded. Read on a renewal
    FAILURE (Task 20) to log an actionable error -- when it last actually
    worked, and when the (now-stale) watch was set to expire.
    """
    doc = _get_document_ref(_WATCH_RENEWAL_DOCUMENT_ID).get()
    if not doc.exists:
        return None
    return doc.to_dict()


def set_last_watch_renewal(renewed_at: str, expiration: str) -> None:
    _get_document_ref(_WATCH_RENEWAL_DOCUMENT_ID).set(
        {_RENEWED_AT_FIELD: renewed_at, _EXPIRATION_FIELD: expiration}
    )


def _get_document_ref(document_id: str):
    return _get_client().collection(_COLLECTION).document(document_id)
