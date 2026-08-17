# Work Breakdown Structure & Pricing: PHI Email Triage (MVP)

Scope: `email-triage-core` only (per `CAPABILITY_MAP.md` / `SPEC-email-triage-core.md`).
`phi-dashboard` is a separate future phase, priced separately once greenlit.

**Team:** You — sole developer, implementing the full build · Senior Developer —
consulted only at specific high-risk decision points, not a co-implementer
**Rates:** You — $50/hr · Senior consultation — $60/hr
**Pricing model:** Fixed-bid, based on the estimate below plus a 15% contingency buffer
for infra/compliance unknowns (cross-org domain delegation, VPC-SC perimeter tuning)

## Breakdown by Phase

| # | WBS Item | Your hrs | Senior consult hrs | Total hrs | Your cost | Consult cost | Line total |
|---|---|---:|---:|---:|---:|---:|---:|
| 0 | Discovery & Taxonomy Workshop | 4 | 0 | 4 | $200 | $0 | $200 |
| 1 | Foundation Infra — Terraform project, VPC-SC perimeter, IAM, Pub/Sub, BigQuery | 20 | 3 | 23 | $1,000 | $180 | $1,180 |
| 2 | App Skeleton — Cloud Run service + Pub/Sub push ingestion | 12 | 0 | 12 | $600 | $0 | $600 |
| 3 | Gmail Integration — watch/history/fetch + historyId cursor | 18 | 1 | 19 | $900 | $60 | $960 |
| 4 | Taxonomy + Classification — Vertex AI Gemini structured-output pipeline | 18 | 2 | 20 | $900 | $120 | $1,020 |
| 5 | Label Application — apply/create labels, dedupe logic | 12 | 0 | 12 | $600 | $0 | $600 |
| 6 | Audit Logging — metadata-only BigQuery event trail | 9 | 1 | 10 | $450 | $60 | $510 |
| 7 | Watch Renewal — Cloud Scheduler + renewal logic | 8 | 0 | 8 | $400 | $0 | $400 |
| 8 | End-to-End Integration & Sandbox Deploy | 18 | 2 | 20 | $900 | $120 | $1,020 |
| 9 | Documentation & Handoff — SETUP.md, README, runbook | 10 | 0 | 10 | $500 | $0 | $500 |
| 10 | Project Management & Client Check-ins | 6 | 0 | 6 | $300 | $0 | $300 |
| 11 | Final QA / Security & Compliance Review Pass | 4 | 2 | 6 | $200 | $120 | $320 |
| | **Subtotal** | **139** | **11** | **150** | **$6,950** | **$660** | **$7,610** |
| | Contingency (15%) | | | ~23 | | | $1,141.50 |
| | **Fixed-Bid Total** | | | **~173** | | | **$8,751.50** |

## Where the senior consultation hours go

Only 11 of 150 hours — deliberately narrow, at the points where a second opinion is
worth the higher rate rather than throughout:

- **VPC-SC perimeter + IAM design (Phase 1, 3h):** the compliance backbone of the whole
  system — worth a sanity check before it's built on top of
- **Gmail `history`/`watch` semantics (Phase 3, 1h):** a known-fiddly part of the Gmail
  API; a quick check saves rework if the fetch logic is subtly wrong
- **Vertex AI prompt/schema design (Phase 4, 2h):** classification quality drives the
  whole product's usefulness — worth a second set of eyes on the prompt itself
- **Audit boundary review (Phase 6, 1h):** confirming the metadata/PHI split holds up
- **VPC-SC egress log review before go-live (Phase 8, 2h):** compliance sign-off moment,
  not implementation work
- **Final security/compliance review pass (Phase 11, 2h):** a last check before this
  touches real PHI

Everything else is yours to build solo.

## Timeline note

150 hours of solo work (before contingency) is roughly **4 weeks full-time**, or
proportionally longer at partial allocation — worth setting that expectation with the
client up front, since a two-person team would compress calendar time even at the same
total labor hours; a solo build doesn't.

## What this excludes

- **GCP infrastructure/consumption costs** (Vertex AI API calls, Cloud Run compute,
  BigQuery storage, Pub/Sub) — these are the client's own ongoing GCP billing, separate
  from this labor estimate
- **Legal/compliance paperwork** — your company's BAA with Google is already in place;
  the partner–client BA/subcontractor agreement is a separate legal work item, not
  developer labor
- **Post-launch support/maintenance** — not a retainer; would be scoped separately once
  the MVP is live and validated
- **`phi-dashboard` (v2)** — priced separately if/when greenlit, per `CAPABILITY_MAP.md`

## Assumptions this estimate depends on

- Single shared mailbox, single taxonomy, no multi-mailbox fan-out (per spec)
- Client's Workspace admin is available for the one cross-org domain-wide delegation
  authorization step without extended back-and-forth
- Taxonomy is finalized (or close to it) after the discovery workshop — significant
  taxonomy churn mid-build would be a change order, not covered by the 15% contingency
- No unexpected VPC-SC/Vertex AI service availability issues in the client's target region
- Senior developer availability for the ~11 consultation hours doesn't itself become a
  scheduling bottleneck
