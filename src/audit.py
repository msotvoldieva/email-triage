"""Writes metadata-only classification events to BigQuery.

write_event()'s signature carries no parameter through which subject/body
could pass -- enforced structurally, not just by convention
(SPEC-email-triage-core.md "Never do": store PHI in BigQuery). Every
parameter maps 1:1 to a column in infra/bigquery.tf's classification_events
table, nothing more.

Writes via a MERGE upsert, not a streaming insert: BigQuery's
streaming-insert insertId deduplication is documented by Google as
best-effort, not guaranteed, and current guidance is to move away from
relying on it for exactly that reason (verified before writing this, not
assumed). A MERGE gives a genuine idempotent write on message_id instead --
calling write_event() twice for the same message_id (e.g. Pub/Sub
redelivery slipping past the upstream already_labeled dedupe check in
main.py) is a no-op the second time, not a duplicate row. The higher
per-write latency of a query job vs. a streaming insert is an acceptable
tradeoff given this project's low expected volume (SPEC-email-triage-core.md:
single shared inbox).
"""

import datetime
import logging
from functools import lru_cache

from google.cloud import bigquery

logger = logging.getLogger(__name__)

_DATASET = "email_triage_audit"
_TABLE = "classification_events"

_MERGE_SQL_TEMPLATE = """
MERGE `{project}.{dataset}.{table}` AS target
USING (SELECT @message_id AS message_id) AS source
ON target.message_id = source.message_id
WHEN NOT MATCHED THEN
  INSERT (message_id, category, confidence, needs_review, model_version, classified_at)
  VALUES (@message_id, @category, @confidence, @needs_review, @model_version, @classified_at)
"""


@lru_cache(maxsize=1)
def _get_client() -> bigquery.Client:
    return bigquery.Client()


def write_event(
    message_id: str,
    category: str,
    confidence: float,
    needs_review: bool,
    model_version: str,
    classified_at: datetime.datetime,
) -> None:
    client = _get_client()
    query = _MERGE_SQL_TEMPLATE.format(project=client.project, dataset=_DATASET, table=_TABLE)

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("message_id", "STRING", message_id),
            bigquery.ScalarQueryParameter("category", "STRING", category),
            bigquery.ScalarQueryParameter("confidence", "FLOAT64", confidence),
            bigquery.ScalarQueryParameter("needs_review", "BOOL", needs_review),
            bigquery.ScalarQueryParameter("model_version", "STRING", model_version),
            bigquery.ScalarQueryParameter("classified_at", "TIMESTAMP", classified_at),
        ]
    )

    # .result() blocks until the query job completes -- a compliance audit
    # trail shouldn't report "done" before the write has actually landed.
    client.query(query, job_config=job_config).result()

    logger.info(
        "audit.write_event",
        extra={
            "message_id": message_id,
            "category": category,
            "needs_review": needs_review,
            # deliberately nothing else -- this function's signature has no
            # parameter for subject/body to leak through in the first place
        },
    )
