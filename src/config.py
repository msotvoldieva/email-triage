"""Environment-backed settings. No secrets or PHI live here -- just config values
injected as Cloud Run environment variables by infra/cloud_run.tf.

load_settings() is cached (not a module-level singleton evaluated at import time)
so tests can set env vars via monkeypatch and call load_settings.cache_clear()
between cases, rather than needing env vars present before this module is even
imported.
"""

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    cloud_run_audience: str
    invoker_service_account_email: str
    confidence_threshold: float
    vertex_ai_location: str
    classifier_model: str
    mailbox_address: str
    gmail_watch_topic: str


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    return Settings(
        cloud_run_audience=_require_env("CLOUD_RUN_AUDIENCE"),
        invoker_service_account_email=_require_env("INVOKER_SERVICE_ACCOUNT_EMAIL"),
        confidence_threshold=float(os.environ.get("CONFIDENCE_THRESHOLD", "0.75")),
        vertex_ai_location=_require_env("VERTEX_AI_LOCATION"),
        # No default: an exact, currently-available Vertex AI model ID is something
        # to verify against the live Model Garden at deploy time, not guess now.
        classifier_model=_require_env("CLASSIFIER_MODEL"),
        # The shared mailbox's address -- the Pub/Sub push path gets this from the
        # push envelope itself (Gmail tells us), but the Scheduler-triggered watch
        # renewal (Task 20) has no incoming envelope to read it from.
        mailbox_address=_require_env("MAILBOX_ADDRESS"),
        gmail_watch_topic=_require_env("GMAIL_WATCH_TOPIC"),
    )


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value
