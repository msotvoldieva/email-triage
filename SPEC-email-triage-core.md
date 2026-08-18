# Spec: `email-triage-core` (Gmail → Vertex AI Classification)

Module of: `CAPABILITY_MAP.md`. Depended on by: `SPEC-phi-dashboard.md`.

## Objective

A single-tenant pipeline that automatically labels incoming email in one HIPAA
practice's shared Gmail inbox by category, using Vertex AI for classification and
summarization, entirely within Google's BAA-covered service surface.

**Why:** Staff currently triage every inbound message by hand. Automated, consistent
labeling by category lets them scan and prioritize instead of reading everything
cold — without introducing any new party that touches PHI. **No third-party LLM
vendor, and no non-BAA-covered Google endpoint, is ever in the data path.**

**User:** Front-desk/admin staff at the practice, who see category labels applied to
messages in their existing Gmail interface — no new tool for them to learn.

**Success looks like:** every message that lands in the shared inbox gets exactly one
category label (or a "Needs Review" fallback) within a few minutes, with zero PHI
leaving Google's BAA-covered services and a queryable metadata-only audit trail of
every classification decision.

**This is the MVP.** This module ships and gets validated with the client on its own —
it is a complete, useful product without `phi-dashboard`, not a partial build waiting
on it. Summary generation and the Firestore content store described in
`CAPABILITY_MAP.md`'s interface boundary are **deliberately out of scope here**; they
get added to this module as a follow-on change only once `phi-dashboard` is greenlit.
Building them now would mean generating and storing PHI-derived summary text with no
consumer, and asking a compliance reviewer to clear a second data store before the
core pipeline has even proven itself. See "Out of Scope (v1)" below.

**Hosting model:** this runs in a GCP project dedicated to this one client, inside the
implementing partner company's GCP org (not the client's own org, and not shared with
any other client's project). See `CAPABILITY_MAP.md` for the cross-module hosting
rationale and the business/legal prerequisites (partner BAA with Google, partner–client
subcontractor BA agreement) that this depends on but doesn't resolve.

## Tech Stack

- **Language/runtime:** Python 3.12
- **Compute:** Cloud Run (2nd gen), container-based, private ingress (Pub/Sub push only)
- **Trigger:** Gmail API `users.watch()` → Cloud Pub/Sub topic → push subscription → Cloud Run
- **Classification:** Vertex AI Gemini (`aiplatform.googleapis.com` only — **never** the
  public Gemini Developer API), structured/JSON output mode
- **Gmail access:** Domain-wide-delegated GCP service account, **cross-org**: the
  client's Workspace admin authorizes this project's service account client ID (living
  in the partner's GCP org) for `gmail.readonly` + `gmail.modify` scopes on the one
  shared mailbox. Cloud Run's attached runtime service account is the credential — no
  stored keys or OAuth secrets.
- **Audit store:** BigQuery (metadata-only event log — message ID, category, confidence,
  model version, timestamp; never subject/body)
- **Scheduling:** Cloud Scheduler (daily Gmail `watch()` renewal — subscriptions expire
  every 7 days)
- **Secrets:** Secret Manager for any non-identity config (e.g. taxonomy version pin)
- **IaC:** Terraform — project, VPC-SC perimeter, VPC + Serverless VPC Access connector
  (network egress lockdown), Cloud Run, Pub/Sub, IAM bindings, BigQuery dataset,
  Scheduler job
- **Network isolation — two separate controls, not one:**
  - **VPC-SC service perimeter** over the six GCP services confirmed to support it:
    Vertex AI, Pub/Sub, BigQuery, Secret Manager, Cloud Logging, Firestore (Firestore
    used only for the `historyId` cursor state store — see "Out of Scope (v1)" for why
    this is not a PHI content store). **Gmail API is not a VPC-SC-supported service**,
    confirmed against Google's official supported-products documentation — it is
    governed by IAM/domain-wide delegation scope instead, not by the perimeter.
  - **Network egress lockdown:** Cloud Run's egress routed entirely through a VPC
    Serverless VPC Access connector, with no route to the public internet (no Cloud NAT
    to `0.0.0.0/0`) — Google API traffic only, via Private Google Access. This is the
    control that actually stops an arbitrary outbound call to a non-Google destination;
    VPC-SC's `restricted_services` alone governs access to specified Google API
    resources, not generic internet egress from the compute itself.

## Commands

```
Setup (local):       python -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt
Infra plan:           terraform -chdir=infra plan
Infra apply:          terraform -chdir=infra apply
Run tests:            pytest --cov=src --cov-report=term-missing
Lint:                 ruff check src tests
Format:                ruff format src tests
Local invoke (dev):    (from src/) functions-framework --target=handle_pubsub_push --debug
Deploy:                gcloud run deploy email-triage --source . --region <region> --no-allow-unauthenticated
```

## Project Structure

```
email-triage/
  CAPABILITY_MAP.md          → cross-module map (this module + phi-dashboard)
  SPEC-email-triage-core.md  → this document
  src/
    main.py                → Cloud Run entrypoint (Pub/Sub push handler)
    gmail_client.py         → Gmail API wrapper (watch, history.list, get, modify labels)
    classifier.py            → Vertex AI Gemini prompt construction + structured-output call
    taxonomy.py               → loads taxonomy config, maps category -> Gmail label ID
    audit.py                   → writes metadata-only classification events to BigQuery
    config.py                    → env/Secret Manager-backed settings
  taxonomy/
    taxonomy.yaml             → placeholder taxonomy (category, description, label) — TBD with client
  infra/
    main.tf, vpc_sc.tf, iam.tf, pubsub.tf, cloud_run.tf, bigquery.tf, scheduler.tf
  tests/
    unit/                      → mocked Gmail/Vertex AI clients, synthetic fixtures only
    integration/                → runs against a sandbox project + synthetic test mailbox only
  docs/
    SETUP.md                     → Workspace admin steps (cross-org domain-wide delegation, watch setup)
```

## Code Style

One example beats a description — this is the shape all handler code should follow:
structured logging, no PHI in log fields, explicit typed returns.

```python
@dataclass
class ClassificationResult:
    category: str
    confidence: float
    needs_review: bool

def classify(subject: str, body: str, taxonomy: Taxonomy) -> ClassificationResult:
    """Classify one email against the practice's taxonomy via Vertex AI.

    Never logs `subject` or `body`. Falls back to needs_review=True below
    settings.CONFIDENCE_THRESHOLD or when the model returns no valid category.
    """
    response = _vertex_client.generate(
        model=settings.CLASSIFIER_MODEL,
        contents=_build_prompt(subject, body, taxonomy),
        response_schema=CLASSIFICATION_SCHEMA,
    )
    result = _parse(response, taxonomy)
    logger.info("classification.completed", extra={
        "category": result.category,
        "confidence": result.confidence,
        # deliberately no subject/body/message content here
    })
    return result
```

- snake_case for functions/modules, PascalCase for classes/dataclasses
- Every function that touches email content takes typed `subject`/`body` params
  explicitly — never a raw Gmail API payload passed downstream — so it's obvious at a
  glance which functions can see PHI and which only see metadata
- No bare `except:` — catch specific exceptions, log without PHI, re-raise or route to
  a dead-letter path

## Testing Strategy

- **Framework:** pytest, `pytest-mock` for mocking Gmail/Vertex AI clients
- **Unit tests** (`tests/unit/`): prompt construction, response parsing, confidence
  threshold logic, category → label mapping, watch-renewal scheduling logic. All
  fixtures are synthetic, invented emails — **real client PHI must never appear in a
  test fixture, log, or commit.**
- **Integration tests** (`tests/integration/`): run against a separate sandbox GCP
  project and a synthetic test Gmail mailbox seeded with non-PHI sample messages;
  verifies the full watch → Pub/Sub → Cloud Run → label pipeline end to end.
- **Coverage expectation:** ≥85% on `src/`, with 100% on `classifier.py` and
  `gmail_client.py` branch logic (confidence threshold, label mapping, retry paths),
  since those are where a mistake is most consequential.
- A dedicated test asserts `audit.write_event()`'s signature and every call site cannot
  carry `subject` or `body` — the metadata-only boundary is a testable contract, not
  just a convention.
- No load/performance testing planned for v1 (single shared inbox, low volume expected).

## Boundaries

- **Always do:**
  - Call Vertex AI exclusively via the `aiplatform.googleapis.com` endpoint
  - Enforce both the VPC-SC service perimeter (Task 2a) and the network egress
    lockdown (Task 2b) — the perimeter alone does not stop arbitrary internet egress
  - Keep subject/body out of every log line, exception message, and BigQuery row
  - Use the Cloud Run service account's attached identity — never a downloaded key file
  - Run full test suite before any deploy
  - Write an audit event for every classification decision, including "needs review" ones
- **Ask first:**
  - Adding any new external dependency or library
  - Widening the VPC-SC perimeter's allowed services, or the delegated Gmail scopes
  - Adding a second mailbox or expanding beyond the single shared inbox
  - Changing the confidence threshold in production
  - Finalizing/changing the taxonomy once the client workshop concludes
  - Any change to BigQuery audit-log retention period
  - Adding summary generation or any new data store ahead of `phi-dashboard` being
    greenlit (see "Out of Scope (v1)")
- **Never do:**
  - Send email subject, body, attachments, or sender PHI to any non-Google-BAA-covered
    endpoint (no third-party LLM API, no SaaS error tracker, no analytics SDK)
  - Call the public Gemini Developer API as a fallback for any reason
  - Commit secrets, service account keys, or real client email content to the repo
  - Disable, narrow-scope-bypass, or punch a hole in the VPC-SC perimeter or the
    network egress lockdown without sign-off
  - Store PHI in Cloud Logging, BigQuery, or any location outside Gmail itself

## Out of Scope (v1)

Deferred to when `phi-dashboard` is greenlit, per `CAPABILITY_MAP.md`'s interface
boundary — not part of this MVP build:

- Summary generation (extending `classifier.py`'s Vertex AI call to also produce a
  short summary)
- The Firestore content store (`{message_id, category, confidence, summary,
  classified_at}`) that `phi-dashboard` would read
- Any IAM grant or infra resource that exists only to serve that future store

When `phi-dashboard` work begins, adding these is a follow-on change to *this* spec —
reviewed and approved the same way any other spec change is, not folded silently into
the dashboard module's build.

## Success Criteria

- New messages in the shared inbox receive exactly one category label (or the
  "Needs Review" fallback) within a few minutes of arrival
- Zero email content ever leaves Google's BAA-covered service surface — verifiable by
  inspecting VPC-SC perimeter violation logs, the VPC's route/firewall config (no
  public internet route), and the codebase for any non-Google network call
- 100% of classification decisions produce a BigQuery audit row (message ID, category,
  confidence, model version, timestamp); zero rows ever contain subject/body text
- `watch()` renews automatically with no coverage gap exceeding a few hours
- All infrastructure is defined in Terraform and reproducible from a clean project
- Swapping in the client's real, final taxonomy requires only a config change to
  `taxonomy/taxonomy.yaml`, not a code change

## Open Questions

- **Final taxonomy/categories** — pending workshop with the client; placeholder
  taxonomy ships in `taxonomy/taxonomy.yaml` in the meantime
- Confidence threshold value for triggering "Needs Review" (needs a pilot to tune)
- Who monitors the "Needs Review" label, and how often?
- BigQuery audit log retention period (HIPAA-driven — needs a number from the client/compliance)
- Confirm the partner company's own Google BAA covers this hosting project, and that the
  partner–client BA/subcontractor agreement is executed before real PHI flows (see
  `CAPABILITY_MAP.md`) — a business/legal prerequisite, not something this spec can verify
- Is a feedback/correction loop for mis-classified email in scope for v1, or later?
