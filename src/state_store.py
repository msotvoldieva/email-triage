"""Firestore-backed cursor for Gmail's historyId.

This is the cursor store only -- NOT the PHI content store deferred to v2
(SPEC-email-triage-core.md "Out of Scope (v1)"). It holds exactly one document
with exactly one field: a message/history ID cursor. Nothing PHI-bearing ever
belongs here.
"""

from functools import lru_cache

from google.cloud import firestore

_COLLECTION = "email_triage_state"
_DOCUMENT_ID = "gmail_history_cursor"
_FIELD = "history_id"


@lru_cache(maxsize=1)
def _get_client() -> firestore.Client:
    return firestore.Client()


def get_last_history_id() -> str | None:
    """Returns the last-processed historyId, or None if no cursor has been
    stored yet (first run, or after a reset). Callers should treat None as
    "start from now", not as an error -- this mirrors how
    gmail_client.HistoryIdExpiredError is handled by the push handler (Task 10).
    """
    doc = _get_document_ref().get()
    if not doc.exists:
        return None
    return doc.to_dict().get(_FIELD)


def set_last_history_id(history_id: str) -> None:
    _get_document_ref().set({_FIELD: history_id})


def _get_document_ref():
    return _get_client().collection(_COLLECTION).document(_DOCUMENT_ID)
