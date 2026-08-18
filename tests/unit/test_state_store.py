"""Task 9/20 (tasks/todo.md): Firestore-backed historyId cursor + watch-renewal tracking.

This is small operational state only -- not the PHI content store deferred to
v2 (SPEC-email-triage-core.md "Out of Scope (v1)"). Nothing PHI-bearing here.
"""

import pytest

import state_store


@pytest.fixture(autouse=True)
def _clear_client_cache():
    state_store._get_client.cache_clear()
    yield
    state_store._get_client.cache_clear()


class _FakeDocSnapshot:
    def __init__(self, data):
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return self._data


class _FakeDocumentRef:
    def __init__(self):
        self._data = None

    def get(self):
        return _FakeDocSnapshot(self._data)

    def set(self, data):
        self._data = data


class _FakeCollectionRef:
    def __init__(self):
        self.document_calls = []
        self._doc = _FakeDocumentRef()

    def document(self, doc_id):
        self.document_calls.append(doc_id)
        return self._doc


class _FakeFirestoreClient:
    def __init__(self):
        self.collection_calls = []
        self._collection = _FakeCollectionRef()

    def collection(self, name):
        self.collection_calls.append(name)
        return self._collection


@pytest.fixture
def fake_client(mocker):
    client = _FakeFirestoreClient()
    mocker.patch("state_store._get_client", return_value=client)
    return client


def test_get_last_history_id_returns_none_when_document_missing(fake_client):
    result = state_store.get_last_history_id()

    assert result is None


def test_set_then_get_round_trips(fake_client):
    state_store.set_last_history_id("123456")

    result = state_store.get_last_history_id()

    assert result == "123456"


def test_set_last_history_id_overwrites_previous_value(fake_client):
    state_store.set_last_history_id("100")
    state_store.set_last_history_id("200")

    result = state_store.get_last_history_id()

    assert result == "200"


def test_get_uses_expected_collection_and_document(fake_client):
    state_store.get_last_history_id()

    assert fake_client.collection_calls == [state_store._COLLECTION]
    assert fake_client._collection.document_calls == [state_store._HISTORY_DOCUMENT_ID]


def test_get_last_history_id_missing_field_returns_none(fake_client):
    fake_client._collection._doc.set({"some_other_field": "value"})

    result = state_store.get_last_history_id()

    assert result is None


# --- Task 20: watch renewal tracking ---


def test_get_last_watch_renewal_returns_none_when_document_missing(fake_client):
    result = state_store.get_last_watch_renewal()

    assert result is None


def test_watch_renewal_set_then_get_round_trips(fake_client):
    state_store.set_last_watch_renewal(renewed_at="2026-08-18T00:00:00+00:00", expiration="123456")

    result = state_store.get_last_watch_renewal()

    assert result == {"renewed_at": "2026-08-18T00:00:00+00:00", "expiration": "123456"}


def test_watch_renewal_uses_expected_document(fake_client):
    state_store.get_last_watch_renewal()

    assert fake_client._collection.document_calls == [state_store._WATCH_RENEWAL_DOCUMENT_ID]
