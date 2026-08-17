# Implementation Plan: `email-triage-core` (MVP)

Traces to: `SPEC-email-triage-core.md`. Module of: `CAPABILITY_MAP.md`.
Tasks tracked in `tasks/todo.md` (no external tracker designated).

This plan covers `email-triage-core` only — the MVP. `phi-dashboard` is v2, not
started, and out of scope for this plan; see `SPEC-phi-dashboard.md`'s "Status" note
for what has to happen to this module before that one can begin.

## Overview

Build a Cloud Run service, hosted in a GCP project dedicated to this one client inside
the partner company's GCP org, that watches one shared Gmail inbox via Pub/Sub push
notifications, fetches new messages through the Gmail API, classifies each against a
client-defined taxonomy using Vertex AI Gemini, applies the matching Gmail label (or a
"Needs Review" fallback), and writes a metadata-only audit row to BigQuery — all inside
a VPC-SC-perimetered project with no third-party or non-Vertex endpoint ever in the
data path, and no summary generation or PHI-derived storage (that's deferred to v2).

The build order runs infra foundation → ingestion skeleton → Gmail fetch →
classification → labeling → audit → renewal → full integration → docs. Application
logic (classifier, taxonomy mapping) is unit-testable against mocks independent of
live infra, so it can be built and verified well before the sandbox deploy in Phase 7.

## Architecture Decisions

- **Cross-org domain-wide delegation:** the service account being authorized lives in
  the partner's GCP project, not the client's own org. The client's Workspace admin
  still does the authorization in their own Admin console (client ID + scopes), but the
  identity itself is issued and rotated entirely within the partner's project. This is
  the one place client and partner trust boundaries touch, and it's a manual,
  one-time, documented step (Task 24) rather than anything Terraform can automate on
  the client's side.
- **History-based fetch, not payload-based:** Gmail Pub/Sub push messages carry only
  `emailAddress` + `historyId`, never message content. The handler must call
  `history.list(startHistoryId=...)` to discover new message IDs, then `messages.get`
  each one. This requires persisting the last-seen `historyId` between invocations —
  added as a small Firestore-backed state store (`src/state_store.py`), which stays
  inside the VPC-SC perimeter and holds no PHI (just a message ID/historyId cursor).
  Note this is a *cursor* store, unrelated to the PHI content store deferred to v2 —
  same underlying GCP product, different purpose and different data sensitivity.
- **Structured output + taxonomy-enum validation, not free-text classification:**
  Gemini's response schema constrains output to the exact category set from
  `taxonomy.yaml`. Anything that fails to parse or validate against that enum is treated
  as `needs_review=True` rather than trusted as a best guess — prevents a hallucinated
  category from silently becoming a Gmail label.
- **Idempotency via dedupe check, not exactly-once assumptions:** Pub/Sub push
  subscriptions are at-least-once. Before applying a label or writing an audit row, the
  handler checks whether the message already carries a taxonomy label (Gmail side) —
  this makes redelivery a no-op instead of a duplicate classification/audit write.
- **Dead-letter topic on the push subscription:** so a message that repeatedly fails
  processing (e.g. a transient Vertex AI error) doesn't retry forever or get silently
  dropped — it lands somewhere visible for manual follow-up.
- **Confidence threshold as a configurable value with a placeholder default (0.75):**
  matches the spec's "Ask first" boundary on changing it in production; the real value
  is an open question pending pilot data.
- **No summary generation, no content store (v1):** per `SPEC-email-triage-core.md`'s
  "Out of Scope (v1)" — `classifier.py` returns category/confidence/needs_review only.

## Task List

### Phase 0: Foundation Infra
- [ ] Task 1: Terraform project bootstrap (partner org, dedicated to this client)
- [ ] Task 2: VPC-SC perimeter
- [ ] Task 3: IAM — service accounts + cross-org domain-wide delegation prep
- [ ] Task 4: Pub/Sub topic, push subscription, dead-letter topic
- [ ] Task 5: BigQuery audit dataset + table

### Checkpoint: Foundation Infra
- [ ] `terraform plan` succeeds cleanly
- [ ] Human reviews IAM scopes and VPC-SC perimeter membership before `terraform apply`

### Phase 1: App Skeleton + Push Ingestion
- [ ] Task 6: Cloud Run service scaffold + Pub/Sub push endpoint
- [ ] Task 7: Unit tests for push envelope parsing/auth

### Checkpoint: App Skeleton
- [ ] `pytest` passes
- [ ] Local invocation with a sample Pub/Sub envelope returns 200, logs contain no PHI

### Phase 2: Gmail Integration (Fetch)
- [ ] Task 8: `gmail_client.py` — watch/history.list/get
- [ ] Task 9: Firestore-backed `historyId` cursor state store
- [ ] Task 10: Wire push handler to fetch (no classify/label yet)
- [ ] Task 11: Unit tests for gmail_client + state_store

### Checkpoint: Gmail Fetch
- [ ] Unit tests pass
- [ ] Manual sandbox check: historyId advances correctly, no reprocessing across two consecutive pushes

### Phase 3: Taxonomy + Classification
- [ ] Task 12: `taxonomy.py` + placeholder `taxonomy.yaml`
- [ ] Task 13: `classifier.py` — Vertex AI Gemini structured-output call + threshold logic
- [ ] Task 14: Unit tests — prompt construction, response parsing, threshold edge cases

### Checkpoint: Classification
- [ ] ≥85% overall coverage, 100% branch coverage on `classifier.py`
- [ ] Human reviews prompt wording before any live Vertex AI wiring

### Phase 4: Label Application
- [ ] Task 15: `gmail_client.py` — ensure_label_exists / apply_label / dedupe check
- [ ] Task 16: Wire classify → label into push handler
- [ ] Task 17: Unit tests for label creation/apply/dedupe

### Checkpoint: Full Mocked Pipeline
- [ ] End-to-end mocked test (push → fetch → classify → label) passes

### Phase 5: Audit Logging
- [ ] Task 18: `audit.py` — BigQuery metadata-only event write
- [ ] Task 19: Wire audit write into handler; tests proving subject/body can't reach it

### Checkpoint: Audit
- [ ] Tests pass
- [ ] Manual/code review confirms `audit.write()`'s signature has no path for subject/body

### Phase 6: Watch Renewal
- [ ] Task 20: Scheduler-triggered `watch()` renewal + infra wiring
- [ ] Task 21: Unit tests for renewal + failure logging

### Checkpoint: Renewal
- [ ] Tests pass
- [ ] Manual trigger of renewal endpoint succeeds in sandbox

### Phase 7: End-to-End Integration & Deploy
- [ ] Task 22: `terraform apply` to sandbox project; deploy Cloud Run
- [ ] Task 23: Integration tests against synthetic sandbox mailbox

### Checkpoint: Integration
- [ ] Full pipeline verified in sandbox against synthetic (non-PHI) messages
- [ ] Human reviews VPC-SC egress logs: zero non-Google destinations contacted

### Phase 8: Documentation & Handoff Prep
- [ ] Task 24: `docs/SETUP.md` — client Workspace admin steps (cross-org delegation)
- [ ] Task 25: `README.md` — architecture, taxonomy updates, threshold tuning, Needs-Review runbook

### Checkpoint: MVP Complete
- [ ] All `SPEC-email-triage-core.md` success criteria met
- [ ] Ready for client's real taxonomy + pilot on real mail
- [ ] MVP validated with client before any `phi-dashboard` work begins

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Cross-org domain-wide delegation misconfigured, or the client's admin is unclear on a step spanning two orgs | High — blocks all Gmail access | Document exact admin steps in Task 24, written for someone who has never worked across two GCP orgs; verify with one read-only test call before building further (end of Phase 2) |
| Partner's own Google BAA doesn't (yet) cover this hosting project, or the partner–client subcontractor BA agreement isn't executed | High — compliance/legal blocker, independent of engineering progress | Confirmed as an open item in `SPEC-email-triage-core.md`; build can continue against a sandbox with synthetic data regardless, but real PHI must not flow until both are confirmed |
| Gemini returns a category outside the taxonomy enum or malformed JSON | Medium — could mislabel PHI-adjacent mail | Structured output schema + enum validation in `classifier.py`; anything invalid → `needs_review` |
| Pub/Sub at-least-once redelivery causes duplicate labels/audit rows | Medium — noisy audit trail, redundant Vertex AI cost | Dedupe check before labeling (Task 15); audit writes keyed by message_id |
| VPC-SC perimeter blocks a legitimate call, or is misconfigured to fail open | High — either breaks the app or defeats the compliance purpose | Test perimeter in dry-run mode before switching to enforce; explicit human checkpoint before `terraform apply` in Phase 0 |
| Firestore historyId cursor lost or corrupted | Low/Medium — could reprocess or skip a backlog | On missing state, start from "now" and log a warning rather than replaying full history |
| Inbox volume/cost scaling | Low (single shared inbox expected low volume) | Revisit if the client later asks to add mailboxes (already gated as "ask first" in the spec) |

## Open Questions

Carried forward from `SPEC-email-triage-core.md` — none are resolved by this plan, and
each is called out at the point in the task list where a placeholder default stands in
for it:

- Final taxonomy/categories (Task 12 ships a placeholder)
- Confidence threshold value (Task 13 defaults to 0.75, pending pilot tuning)
- Who owns/monitors the "Needs Review" label day to day
- BigQuery audit log retention period
- Confirmation that the partner's own Google BAA covers this hosting project, and that
  the partner–client BA/subcontractor agreement is executed, before real PHI flows
- Whether a mis-classification feedback/correction loop is in scope for a future version
