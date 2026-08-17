# Capability Map: PHI Email Triage

Approved. Module specs are named by these ids and live alongside this file.

| Module id | Responsibility | Depends on | Spec |
|---|---|---|---|
| `email-triage-core` | Gmail-triggered pipeline: fetch new mail, classify against taxonomy via Vertex AI, generate a short summary, apply Gmail label, write to a secured content store + metadata-only audit log | — | `SPEC-email-triage-core.md` |
| `phi-dashboard` | Staff-facing web app (IAP-gated to the client's Workspace domain) showing labeled emails with their AI summaries, reading from the content store `email-triage-core` writes | `email-triage-core` | `SPEC-phi-dashboard.md` |

**Build order:** `email-triage-core` (MVP — ships and is validated with the client on
its own) → `phi-dashboard` (v2, greenlit separately once the MVP is proven)

## Interface boundary

`email-triage-core` will own and write a **content store** (Firestore, CMEK-encrypted):
one document per classified message —
`{message_id, category, confidence, summary, classified_at}`. This document shape is
the contract between the two modules; `phi-dashboard` only ever reads it, never writes
it.

**Not yet built.** Summary generation and the content store are explicitly out of MVP
scope (see `SPEC-email-triage-core.md` → "Out of Scope (v1)") — no reason to generate
and store PHI-derived summary text before `phi-dashboard` exists to read it. They get
added to `email-triage-core` as a follow-on spec change when `phi-dashboard` is
greenlit, reviewed the same way any other spec change is, before `phi-dashboard`'s own
build starts.

## Hosting model

Both modules deploy into **one dedicated GCP project per client, inside the partner
company's GCP org** — not the client's own org, and not a project shared across
multiple clients. This preserves single-tenant PHI isolation even though the partner
operates the infrastructure.

## Carried-forward business/legal items (not engineering, not resolved here)

- Partner company's own BAA with Google must cover the hosting project(s)
- A direct BA/subcontractor agreement between the partner and the client is required,
  since PHI now flows through infrastructure the partner operates rather than the
  client's own tenant
- Both are prerequisites to any real PHI flowing through this system, independent of
  build progress
