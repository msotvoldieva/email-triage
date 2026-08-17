# Spec: `phi-dashboard` (Staff Triage View)

Module of: `CAPABILITY_MAP.md`. Depends on: `SPEC-email-triage-core.md`.

**Status: v2 — not started.** `email-triage-core` is the MVP and ships/gets validated
with the client on its own first. This spec exists so the interface boundary (the
content-store document shape) is decided and reviewed ahead of time, not designed
under pressure later. Before this module's Plan/Tasks phase begins, `email-triage-core`
needs its own follow-on spec change adding summary generation and the content store
(see that spec's "Out of Scope (v1)" section) — that's a prerequisite, not part of this
module's task list.

## Objective

A staff-facing web app showing recently triaged emails — category, AI-generated
summary, timestamp, and a link back to the message in Gmail — so staff can scan
triage status at a glance instead of opening each message individually. This module
introduces the only human login surface in the system, and the only place PHI-derived
content (the summary) is displayed outside Gmail itself.

**Why:** `email-triage-core` already classifies and summarizes every message and
stores the result; this module is the read-side view of that data for staff who want
an overview rather than working purely from Gmail labels.

**User:** All staff with access to the shared inbox — the same population that already
sees labeled mail in Gmail. No broader audience.

**Success looks like:** any of those staff can open the dashboard, authenticate with
their existing Workspace identity (no separate login), and see an accurate,
near-real-time list of triaged emails with summaries — while every view they make is
itself logged, since HIPAA requires knowing who looked at what, not just what happened
to the message.

## Tech Stack

- **Language/runtime:** Python 3.12 (matches `email-triage-core`)
- **Compute:** Cloud Run (2nd gen), in the **same dedicated GCP project** as
  `email-triage-core` (one project per client — see `CAPABILITY_MAP.md`)
- **Web framework:** Flask + server-rendered Jinja templates — simplest viable UI for a
  list/detail triage view; no SPA framework or separate frontend build needed for v1
- **Auth:** Identity-Aware Proxy (IAP), bound to the client's Workspace domain (or a
  Google Group the client manages for shared-inbox staff). No separate login system, no
  third-party auth — IAP injects a verified identity header the app trusts, and rejects
  unauthenticated requests before they ever reach application code.
- **Data source:** Firestore content store owned and written by `email-triage-core`
  (`{message_id, category, confidence, summary, classified_at}`) — this module has
  **read-only** IAM on that store, no write access, no schema ownership
- **Access audit store:** BigQuery — a `dashboard_access_events` table, separate from
  `email-triage-core`'s classification audit table, logging `{viewer_identity,
  message_id, viewed_at}` for every message a user opens
- **IaC:** Terraform, in the same `infra/` root as `email-triage-core` (shared project,
  shared VPC-SC perimeter) — additive resources only: this Cloud Run service, IAP
  config, the access-audit BigQuery table, read-only IAM binding on the content store
- **Network isolation:** inside the same VPC-SC perimeter as `email-triage-core`; no
  new egress destinations required beyond what's already allowed there plus IAP itself

## Commands

```
Setup (local):   python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
Run tests:        pytest --cov=src --cov-report=term-missing
Lint:              ruff check src tests
Format:             ruff format src tests
Local invoke (dev): flask --app src.app run --debug   # IAP identity mocked via env var locally
Deploy:              gcloud run deploy phi-dashboard --source . --region <region> --no-allow-unauthenticated
```

## Project Structure

```
email-triage/
  SPEC-phi-dashboard.md    → this document
  dashboard/
    src/
      app.py                → Flask app: routes, IAP identity extraction
      content_reader.py       → read-only Firestore client (list recent, get by message_id)
      access_audit.py           → writes {viewer_identity, message_id, viewed_at} to BigQuery
      templates/
        list.html               → recent triaged emails, filterable by category
        detail.html               → single message: category, summary, link to Gmail thread
      config.py                    → env/Secret Manager-backed settings
    infra/
      cloud_run.tf, iap.tf, bigquery.tf, iam.tf   → additive to email-triage-core's infra/
    tests/
      unit/                        → mocked Firestore/BigQuery clients, mocked IAP identity header
```

## Code Style

```python
def view_message(message_id: str, viewer_identity: str) -> RenderedDetail:
    """Render one triaged message's detail view and log the access.

    Every successful read through this function produces exactly one
    access_audit row. `viewer_identity` comes only from the IAP-verified
    header — never from a request body or query param.
    """
    record = content_reader.get(message_id)
    if record is None:
        abort(404)
    access_audit.log_view(viewer_identity=viewer_identity, message_id=message_id)
    return RenderedDetail.from_content_record(record)
```

- snake_case for functions/modules, PascalCase for classes
- Every route handler resolves `viewer_identity` from the IAP header exactly once, at
  the top of the request — never re-derived or trusted from elsewhere
- No route may call `content_reader` without a matching `access_audit.log_view()` call
  on the same code path — reviewed as a pairing, not left to convention

## Testing Strategy

- **Framework:** pytest, `pytest-mock` for Firestore/BigQuery clients, Flask test client
  for route tests
- **Unit tests:** IAP header present/absent/malformed (unauthenticated request never
  reaches a route handler), list view pagination, detail view for a valid and an
  unknown `message_id`, access-audit log write on every successful detail view
- **Coverage expectation:** ≥85% on `dashboard/src/`, 100% branch coverage on the IAP
  identity extraction path and `access_audit.py` — these are the two places a bug means
  either an access-control failure or a missing compliance record
- No integration test against a live IAP-fronted deployment planned for v1 beyond a
  manual sandbox check (Task-level detail comes in the Plan phase for this module)

## Boundaries

- **Always do:**
  - Require IAP on every route — no route may opt out of authentication
  - Log an access-audit row for every successful view of a message's summary
  - Treat the content store as read-only — no write path exists in this module's code
    or IAM at all
  - Keep the access-audit table to identity/message_id/timestamp only — never duplicate
    the summary text into it
- **Ask first:**
  - Adding any identity source beyond IAP/Workspace (e.g. a separate login, an API key)
  - Restricting the dashboard to a subset of staff rather than all shared-inbox staff
  - Adding search, export, or reporting features beyond the list/detail view
  - Any change to the access-audit retention period
- **Never do:**
  - Grant this module write access to the Firestore content store
  - Expose any route without IAP enforcement, including health checks that return
    message data
  - Send summary text, or any content read from the content store, to any
    non-Google-BAA-covered endpoint
  - Cache or persist summary text anywhere outside the request/response cycle (no
    client-side storage, no server-side cache layer holding PHI-derived text)

## Success Criteria

- An authenticated staff member sees an accurate, near-real-time list of triaged
  emails with category and summary
- An unauthenticated request is rejected by IAP before reaching application code —
  verifiable by inspecting IAP logs, not just app-level tests
- Every successful message view produces exactly one `dashboard_access_events` row
- The content store remains read-only from this module's IAM perspective, verifiable
  in the Terraform plan/state
- No summary text appears in any log, cache, or audit table other than the content
  store itself

## Open Questions

- Does the dashboard need search or filtering beyond category (e.g. by date range,
  by sender)?
- Should "Needs Review" be a distinct workflow (mark-as-resolved, assign to a staff
  member) or purely a read-only filter for v1?
- Access-audit retention period — likely the same number as the content-store
  retention question in `SPEC-email-triage-core.md`, but confirm they're meant to match
- Any export/reporting capability, or is this strictly a live view with no download path?
  (A download path would be a new PHI-egress surface and should be scoped deliberately,
  not added incidentally.)
