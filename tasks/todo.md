# Task List: `email-triage-core` (MVP)

Plan: `tasks/plan.md` · Spec: `SPEC-email-triage-core.md` · Map: `CAPABILITY_MAP.md`

## Phase 0: Foundation Infra

- [x] Task 1: Terraform project bootstrap
  - **Description:** Create the Terraform root module that provisions a new GCP project in the partner company's org, dedicated to this one client, and enables required APIs: Gmail, Vertex AI, Pub/Sub, Cloud Run, BigQuery, Secret Manager, Cloud Scheduler, Cloud Build, IAM, Access Context Manager.
  - **Acceptance criteria:**
    - [ ] `terraform init` succeeds
    - [ ] `terraform plan` lists all required APIs as `google_project_service` resources
    - [ ] Project is scoped to exactly one client — no shared/multi-tenant project reused across clients
    - [ ] No hardcoded project IDs/secrets — all via `variables.tf` + `.tfvars` (gitignored)
  - **Verification:** `terraform -chdir=infra validate` and `terraform -chdir=infra plan`
  - **Dependencies:** None
  - **Files:** `infra/main.tf`, `infra/versions.tf`, `infra/variables.tf`
  - **Estimated scope:** S

- [x] Task 2a: VPC-SC service perimeter (corrected scope)
  - **Description:** Define a service perimeter, under the partner org's existing (singleton, shared-across-clients) Access Context Manager policy, restricting this project's protected-service access to exactly the six GCP services confirmed to support VPC-SC: `aiplatform.googleapis.com`, `pubsub.googleapis.com`, `bigquery.googleapis.com`, `secretmanager.googleapis.com`, `firestore.googleapis.com`, `logging.googleapis.com`. **Gmail API is not a VPC-SC-supported service** (confirmed against Google's official supported-products list) — it's excluded here and governed instead by IAM/domain-wide delegation scopes (Task 3).
  - **Acceptance criteria:**
    - [ ] `restricted_services` lists exactly the six confirmed-supported services — no Gmail, no broader set
    - [ ] The org's Access Context Manager policy is referenced via a variable (`access_policy_id`), not created by this module — a policy is a singleton per org and must not be re-created per client
    - [ ] Perimeter starts in dry-run mode (`spec` block) so the Phase 0 checkpoint can review violations before enforcing
  - **Verification:** `terraform validate`; `terraform plan` diff reviewed line-by-line against the spec's allowed-service list
  - **Dependencies:** Task 1
  - **Files:** `infra/vpc_sc.tf`
  - **Estimated scope:** S

- [x] Task 2b: Network egress lockdown
  - **Description:** VPC-SC's `restricted_services` alone doesn't stop Cloud Run from making an arbitrary outbound call to a non-Google destination — it governs access to specified Google API resources, not generic internet egress. This task closes that gap: a VPC network + Serverless VPC Access connector, Cloud Run's egress setting routing *all* traffic through it, and no route to the public internet (no Cloud NAT to `0.0.0.0/0`) — Google API traffic only, via Private Google Access / the restricted VIP. This is what actually delivers "nothing egresses outside Google's BAA-covered services."
  - **Acceptance criteria:**
    - [ ] Cloud Run service (once deployed in Task 6) has `vpc_access.egress = ALL_TRAFFIC` through the connector
    - [ ] The VPC has no default route to the public internet — verified by inspecting route/firewall config, not just by absence of a NAT resource
    - [ ] Google API traffic resolves via the restricted VIP (`199.36.153.4/30`) over Private Google Access
  - **Verification:** `terraform validate`; manual review of route table once applied in Task 22's sandbox
  - **Dependencies:** Task 1
  - **Files:** `infra/network.tf`
  - **Estimated scope:** S

- [x] Task 3: IAM — service accounts + cross-org domain-wide delegation prep
  - **Description:** Create the Cloud Run runtime service account and Scheduler invoker service account with least-privilege IAM bindings (Vertex AI user, BigQuery data editor on the audit dataset only, Pub/Sub subscriber). Output the runtime SA's client ID — this is what the *client's* Workspace admin will authorize for domain-wide delegation (`gmail.readonly`, `gmail.modify`) in *their* Admin console, even though the SA itself lives in the partner's project. That cross-org authorization step is manual and documented in Task 24, not something Terraform can perform.
  - **Acceptance criteria:**
    - [ ] Runtime SA has no roles beyond what `src/` code actually calls (no `roles/editor` or similar broad grants)
    - [ ] `terraform output` exposes the runtime SA's unique client ID in a form that can be handed directly to the client's Workspace admin
  - **Verification:** `terraform plan`; manual review of granted roles against the code's actual API calls
  - **Dependencies:** Task 1
  - **Files:** `infra/iam.tf`
  - **Estimated scope:** S

- [x] Task 4: Pub/Sub topic, dead-letter topic, required service-agent IAM grants
  - **Description:** Create the Gmail-watch topic and a dead-letter topic with a bounded max-delivery-attempts, plus the two Google-managed-service-agent IAM grants both require to function at all. **The push subscription resource itself moves to Task 6** — a push subscription requires a live endpoint URL at creation time, which only exists once Cloud Run is deployed; creating it here would be a forward reference to a resource that doesn't exist yet.
  - **Acceptance criteria:**
    - [ ] `gmail-api-push@system.gserviceaccount.com` granted `roles/pubsub.publisher` on the Gmail-watch topic — without this, `watch()` succeeds but nothing is ever delivered, a well-documented easy-to-miss step
    - [ ] The Pub/Sub service agent (`service-{project_number}@gcp-sa-pubsub.iam.gserviceaccount.com`) granted `roles/pubsub.publisher` on the dead-letter topic (the matching `roles/pubsub.subscriber` grant on the source subscription is added in Task 6, alongside the subscription it scopes to)
    - [ ] Dead-letter topic created with the pipeline's `max_delivery_attempts` bound documented (actual `dead_letter_policy` block lives on the subscription, added in Task 6)
  - **Verification:** `terraform validate`; `terraform plan`
  - **Dependencies:** Task 1
  - **Files:** `infra/pubsub.tf`
  - **Estimated scope:** S

- [x] Task 5: BigQuery audit dataset + table
  - **Description:** Create the audit dataset and a `classification_events` table with schema: `message_id STRING, category STRING, confidence FLOAT64, needs_review BOOL, model_version STRING, classified_at TIMESTAMP`. No column capable of holding subject/body text.
  - **Acceptance criteria:**
    - [ ] Table schema matches exactly — reviewed to confirm no free-text/PHI-capable column exists
  - **Verification:** `terraform plan`
  - **Dependencies:** Task 1
  - **Files:** `infra/bigquery.tf`
  - **Estimated scope:** XS

### Checkpoint: Foundation Infra
- [ ] `terraform -chdir=infra plan` succeeds with no errors
- [ ] Human reviews IAM scopes and VPC-SC perimeter's allowed-service list against SPEC-email-triage-core.md
- [ ] Do not `terraform apply` yet — apply happens in Phase 7 against the sandbox

## Phase 1: App Skeleton + Push Ingestion

- [x] Task 6: Cloud Run service scaffold + Pub/Sub push endpoint + push subscription
  - **Description:** Minimal Python service (Flask or FastAPI) exposing a push endpoint that verifies the Pub/Sub OIDC bearer token, parses the envelope into `{email_address, history_id}`, and logs receipt with structured logging (no PHI — there is none in the envelope anyway, but the log statement itself must not later be copy-pasted somewhere PHI gets added). Also creates, in `infra/`: the `google_cloud_run_v2_service` resource itself, the `invoker` SA's `roles/run.invoker` binding on it (deferred from Task 3), and the Pub/Sub push subscription (deferred from Task 4) — `push_config.push_endpoint` pointing at the now-known Cloud Run URL, `oidc_token` against the `invoker` SA, and the `dead_letter_policy` block referencing Task 4's dead-letter topic. Also grants the Pub/Sub service agent `roles/pubsub.subscriber` on this subscription (the matching half of Task 4's dead-letter publisher grant) and the Pub/Sub service agent `roles/iam.serviceAccountTokenCreator` on the `invoker` SA (required for Pub/Sub to mint OIDC tokens as that SA).
  - **Acceptance criteria:**
    - [ ] Endpoint returns 401 on missing/invalid OIDC token, 200 on valid push
    - [ ] Envelope parsing handles malformed/missing fields without crashing (400, not 500)
    - [ ] Push subscription's `oidc_token` references the `invoker` SA; `dead_letter_policy.max_delivery_attempts` set (e.g. 5)
    - [ ] `invoker` SA has `roles/run.invoker` scoped to this specific Cloud Run service only, not project-wide
  - **Verification:** `pytest tests/unit/test_main.py`; `terraform validate`; `functions-framework --target=handle_pubsub_push --debug` with a sample curl payload
  - **Dependencies:** Task 3, Task 4
  - **Files:** `src/main.py`, `src/config.py`, `requirements.txt`, `Dockerfile`, `infra/cloud_run.tf`, `infra/pubsub.tf`
  - **Estimated scope:** M

- [x] Task 7: Unit tests for push envelope parsing/auth (satisfied by Task 6's TDD flow — see tests/unit/test_main.py)
  - **Description:** Cover valid push, invalid/missing OIDC token, malformed envelope, empty body.
  - **Acceptance criteria:**
    - [ ] All four cases above have a dedicated test
  - **Verification:** `pytest --cov=src.main`
  - **Dependencies:** Task 6
  - **Files:** `tests/unit/test_main.py`
  - **Estimated scope:** S

### Checkpoint: App Skeleton
- [ ] `pytest` passes
- [ ] Local invocation with a sample Pub/Sub envelope returns 200; log output inspected for PHI (should be none)

## Phase 2: Gmail Integration (Fetch)

- [x] Task 8: `gmail_client.py` — watch/history.list/get
  - **Description:** Wrapper functions: `start_watch(topic_name)`, `list_new_message_ids(start_history_id)`, `get_message(message_id) -> (subject, body, existing_label_ids)`. Uses the Cloud Run attached SA credentials via cross-org domain-wide delegation (subject impersonation of the shared mailbox address).
  - **Acceptance criteria:**
    - [ ] `list_new_message_ids` handles a `404`/expired-history-id response by signaling "resync needed" rather than crashing
    - [ ] `get_message` returns decoded subject/body (not raw base64/MIME)
  - **Verification:** `pytest tests/unit/test_gmail_client.py` with mocked `googleapiclient` responses
  - **Dependencies:** Task 6
  - **Files:** `src/gmail_client.py`
  - **Estimated scope:** M

- [x] Task 9: Firestore-backed `historyId` cursor state store
  - **Description:** `get_last_history_id()` / `set_last_history_id(value)` against a single Firestore document. On missing document, returns `None` (caller treats this as "start from now"). This is a cursor only — not the PHI content store deferred to v2, holds no email content.
  - **Acceptance criteria:**
    - [ ] Read/write round-trips correctly
    - [ ] Missing-document case returns `None`, doesn't raise
  - **Verification:** `pytest tests/unit/test_state_store.py` with mocked Firestore client
  - **Dependencies:** Task 1
  - **Files:** `src/state_store.py`
  - **Estimated scope:** S

- [x] Task 10: Wire push handler to fetch
  - **Description:** On a valid push, read last historyId from the state store, call `list_new_message_ids`, fetch each new message, log the count fetched (metadata only), update state store with new historyId. No classification/labeling yet — this slice proves fetch works end to end.
  - **Acceptance criteria:**
    - [ ] Handler processes a push and updates state store historyId
    - [ ] Handles the "resync needed" signal from Task 8 by falling back to "start from now"
  - **Verification:** `pytest tests/unit/test_main.py`
  - **Dependencies:** Task 8, Task 9
  - **Files:** `src/main.py`
  - **Estimated scope:** S

- [x] Task 11: Unit tests for gmail_client + state_store (pagination + empty-history already covered by Task 8's tests)
  - **Description:** Round out coverage beyond the happy paths already covered in Tasks 8–9 — pagination in `history.list`, empty history, malformed message payload.
  - **Acceptance criteria:**
    - [ ] Pagination case covered
    - [ ] Empty-history case covered
  - **Verification:** `pytest --cov=src.gmail_client --cov=src.state_store`
  - **Dependencies:** Task 8, Task 9
  - **Files:** `tests/unit/test_gmail_client.py`, `tests/unit/test_state_store.py`
  - **Estimated scope:** S

### Checkpoint: Gmail Fetch
- [ ] Unit tests pass
- [ ] Manual sandbox check (once Phase 7 infra exists, or a scratch project): historyId advances correctly across two consecutive pushes with no reprocessing

## Phase 3: Taxonomy + Classification

- [x] Task 12: `taxonomy.py` + placeholder `taxonomy.yaml`
  - **Description:** Load and validate `taxonomy/taxonomy.yaml` (category name, description, Gmail label name) into a typed `Taxonomy` object. Ship a placeholder taxonomy (e.g. `billing`, `clinical`, `scheduling`, `general-inquiry`, `needs-review`) clearly marked as provisional pending the client workshop.
  - **Acceptance criteria:**
    - [ ] Invalid/malformed YAML raises a clear validation error at load time, not at classification time
    - [ ] `needs-review` category always present regardless of what's configured
  - **Verification:** `pytest tests/unit/test_taxonomy.py`
  - **Dependencies:** None
  - **Files:** `src/taxonomy.py`, `taxonomy/taxonomy.yaml`
  - **Estimated scope:** S

- [x] Task 13: `classifier.py` — Vertex AI Gemini structured-output call + threshold logic
  - **Description:** `classify(subject, body, taxonomy) -> ClassificationResult` returning category/confidence/needs_review only — no summary field (deferred to v2, see SPEC's "Out of Scope (v1)"). Builds a prompt embedding the taxonomy, calls Vertex AI (`aiplatform.googleapis.com` client — never the public Gemini API) with a JSON response schema constrained to the taxonomy's category enum, parses the result, and sets `needs_review=True` when confidence is below `settings.CONFIDENCE_THRESHOLD` (default `0.75`) or the response fails to parse/validate.
  - **Acceptance criteria:**
    - [ ] Uses the Vertex AI SDK/endpoint exclusively — verified by code review, not just tests
    - [ ] Never logs `subject`/`body`
    - [ ] Confidence threshold is read from config, not hardcoded inline
    - [ ] `ClassificationResult` has no summary/content field
  - **Verification:** `pytest tests/unit/test_classifier.py`
  - **Dependencies:** Task 12
  - **Files:** `src/classifier.py`
  - **Estimated scope:** M

- [x] Task 14: Unit tests — prompt construction, response parsing, threshold edge cases
  - **Description:** Cover: valid high-confidence response, valid low-confidence response (→ needs_review), malformed JSON response, category outside taxonomy enum, empty subject/body input, Vertex AI client exception/timeout.
  - **Acceptance criteria:**
    - [ ] All six cases above have a dedicated test
    - [ ] 100% branch coverage on `classifier.py`
  - **Verification:** `pytest --cov=src.classifier --cov-report=term-missing` shows 100% branches
  - **Dependencies:** Task 13
  - **Files:** `tests/unit/test_classifier.py`, `tests/unit/test_taxonomy.py`
  - **Estimated scope:** M

### Checkpoint: Classification
- [ ] `pytest --cov=src` shows ≥85% overall, 100% branch coverage on `classifier.py`
- [ ] Human reviews the actual prompt text (not just the code) before any live Vertex AI call is wired into the handler

## Phase 4: Label Application

- [x] Task 15: `gmail_client.py` — ensure_label_exists / apply_label / dedupe check
  - **Description:** `ensure_label(label_name) -> label_id` (creates via Gmail API if missing, caches lookups), `apply_label(message_id, label_id)`, and `already_labeled(existing_label_ids, taxonomy) -> bool` to detect a message that already carries any taxonomy label (dedupe against Pub/Sub redelivery).
  - **Acceptance criteria:**
    - [ ] Creating a label that already exists (race with another instance) doesn't error — handles the Gmail API's "already exists" response gracefully
    - [ ] `already_labeled` returns `True` for any pre-existing taxonomy or needs-review label
  - **Verification:** `pytest tests/unit/test_gmail_client.py`
  - **Dependencies:** Task 8
  - **Files:** `src/gmail_client.py`
  - **Estimated scope:** S

- [x] Task 16: Wire classify → label into push handler
  - **Description:** Extend the Task 10 handler: for each fetched message not already labeled, call `classify()`, resolve category → label via taxonomy, `ensure_label` + `apply_label`.
  - **Acceptance criteria:**
    - [ ] Already-labeled messages skip classification entirely (saves a Vertex AI call, not just a label write)
    - [ ] A classification/labeling failure on one message doesn't abort processing of the rest of the batch
  - **Verification:** `pytest tests/unit/test_main.py`
  - **Dependencies:** Task 13, Task 15
  - **Files:** `src/main.py`
  - **Estimated scope:** M

- [x] Task 17: Unit tests for label creation/apply/dedupe (covered across Task 15/16's tests)
  - **Description:** Cover label-already-exists race, dedupe skip path, partial-batch-failure isolation.
  - **Acceptance criteria:**
    - [ ] All three cases above have a dedicated test
  - **Verification:** `pytest --cov=src.gmail_client --cov=src.main`
  - **Dependencies:** Task 16
  - **Files:** `tests/unit/test_gmail_client.py`, `tests/unit/test_main.py`
  - **Estimated scope:** S

### Checkpoint: Full Mocked Pipeline
- [ ] A single test exercises push → fetch → classify → label end to end against mocks and passes
- [ ] Dedupe path (redelivered push, already-labeled message) verified to skip classification

## Phase 5: Audit Logging

- [x] Task 18: `audit.py` — BigQuery metadata-only event write
  - **Description:** `write_event(message_id, category, confidence, needs_review, model_version, classified_at)`. Function signature has no parameter through which subject/body could be passed — enforced structurally, not just by convention.
  - **Acceptance criteria:**
    - [ ] Function signature has exactly the metadata fields listed in SPEC-email-triage-core.md's BigQuery schema, nothing else
    - [ ] Write is idempotent on `message_id` (e.g. `MERGE`/upsert, or a uniqueness check) so redelivery doesn't duplicate rows
  - **Verification:** `pytest tests/unit/test_audit.py` with a mocked BigQuery client
  - **Dependencies:** Task 5
  - **Files:** `src/audit.py`
  - **Estimated scope:** S

- [x] Task 19: Wire audit write into handler
  - **Description:** After labeling (or after landing in needs_review), call `audit.write_event(...)`.
  - **Acceptance criteria:**
    - [ ] Every classified message — including needs_review — produces exactly one audit row
  - **Verification:** `pytest tests/unit/test_main.py`; code review confirms no call site can pass subject/body into `audit.write_event`
  - **Dependencies:** Task 16, Task 18
  - **Files:** `src/main.py`, `tests/unit/test_audit.py`
  - **Estimated scope:** S

### Checkpoint: Audit
- [ ] Tests pass
- [ ] Manual code review: grep the codebase for any call to `audit.write_event` and confirm none pass `subject`/`body`

## Phase 6: Watch Renewal

- [x] Task 20: Scheduler-triggered `watch()` renewal
  - **Description:** A route (or separate entrypoint) invoked daily by Cloud Scheduler that calls `gmail_client.start_watch()` again before the 7-day expiry. Infra: `google_cloud_scheduler_job` targeting the Cloud Run renewal route with OIDC auth.
  - **Acceptance criteria:**
    - [ ] Renewal failure logs an actionable error (expiry timestamp, last successful renewal) rather than failing silently
  - **Verification:** `pytest tests/unit/test_renew_watch.py`; `terraform plan` for the scheduler job
  - **Dependencies:** Task 8
  - **Files:** `src/main.py` or `src/renew_watch.py`, `infra/scheduler.tf`
  - **Estimated scope:** S

- [x] Task 21: Unit tests for renewal + failure logging
  - **Description:** Cover successful renewal, Gmail API error during renewal, OIDC auth rejection on the endpoint.
  - **Acceptance criteria:**
    - [ ] All three cases above have a dedicated test
  - **Verification:** `pytest --cov=src.renew_watch`
  - **Dependencies:** Task 20
  - **Files:** `tests/unit/test_renew_watch.py`
  - **Estimated scope:** XS

### Checkpoint: Renewal
- [ ] Tests pass
- [ ] Manual trigger of the renewal endpoint succeeds once sandbox infra exists (Phase 7)

## Phase 7: End-to-End Integration & Deploy

- [ ] Task 22: `terraform apply` to sandbox project; deploy Cloud Run
  - **Description:** Apply the full Phase 0 Terraform against a sandbox GCP project in the partner's org (not the client's real mailbox yet), deploy the built container via `gcloud run deploy`, and manually complete the cross-org domain-wide delegation authorization against a sandbox/test Workspace mailbox the team controls.
  - **Acceptance criteria:**
    - [ ] `terraform apply` completes with no manual out-of-band steps beyond the documented cross-org delegation authorization
    - [ ] Cloud Run service is reachable only via the Pub/Sub push subscription (no public ingress)
  - **Verification:** Manual — service deployed, `gcloud run services describe` shows `ingress: internal`
  - **Dependencies:** All of Phase 0–6
  - **Files:** `infra/*`, deploy script/CI config
  - **Estimated scope:** M

- [ ] Task 23: Integration tests against synthetic sandbox mailbox
  - **Description:** Send a handful of synthetic (invented, non-PHI) test emails covering each placeholder taxonomy category plus one deliberately ambiguous one, and assert the correct label lands within a timeout, plus a matching BigQuery audit row.
  - **Acceptance criteria:**
    - [ ] At least one test per placeholder category, plus the ambiguous → needs_review case
    - [ ] Test also asserts Cloud Logging output for the run contains no subject/body substrings from the sent test emails
  - **Verification:** `pytest tests/integration/test_end_to_end.py` (requires sandbox credentials, run manually/CI-gated, not part of the default `pytest` run)
  - **Dependencies:** Task 22
  - **Files:** `tests/integration/test_end_to_end.py`
  - **Estimated scope:** M

### Checkpoint: Integration
- [ ] Full pipeline verified in sandbox against synthetic messages only
- [ ] Human reviews VPC-SC perimeter's dry-run violation log (or enforced-mode logs) and confirms zero non-Google destinations were contacted
- [ ] VPC-SC perimeter switched from dry-run to enforced mode after this review

## Phase 8: Documentation & Handoff Prep

- [ ] Task 24: `docs/SETUP.md` — client Workspace admin steps
  - **Description:** Step-by-step instructions for the client's Workspace admin covering the cross-org domain-wide delegation flow specifically: authorize the (partner-project-hosted) service account's client ID for domain-wide delegation with the exact OAuth scopes needed, confirm the shared mailbox exists, and the one-time manual bootstrap call to start the initial `watch()`. Written for someone who has never worked across two GCP orgs before.
  - **Acceptance criteria:**
    - [ ] A Workspace admin unfamiliar with this codebase, and unfamiliar with cross-org delegation, could follow it without asking a follow-up question
  - **Verification:** Manual read-through / walkthrough with the client
  - **Dependencies:** Task 3, Task 20
  - **Files:** `docs/SETUP.md`
  - **Estimated scope:** S

- [ ] Task 25: `README.md`
  - **Description:** Architecture overview (including the cross-org hosting model), how to edit `taxonomy/taxonomy.yaml` and what happens after (no code change needed), how to tune `CONFIDENCE_THRESHOLD`, and a short runbook for whoever owns the "Needs Review" label.
  - **Acceptance criteria:**
    - [ ] Covers all four topics listed above
  - **Verification:** Manual read-through
  - **Dependencies:** Task 12, Task 13
  - **Files:** `README.md`
  - **Estimated scope:** S

### Checkpoint: MVP Complete
- [ ] All `SPEC-email-triage-core.md` success criteria met
- [ ] Ready for the client's real taxonomy and a live pilot on real mail
- [ ] Validate with the client before starting any `phi-dashboard` work
