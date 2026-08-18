# PHI Email Triage

Gmail-triggered, Vertex AI-classified email triage for one HIPAA practice's
shared mailbox — entirely inside Google's BAA-covered service boundary. See
`SPEC-email-triage-core.md` for the full spec and `CAPABILITY_MAP.md` for how
this relates to `phi-dashboard` (a separate, not-yet-started v2 module).

## Architecture, briefly

```
Gmail (shared mailbox)
   │ watch() + Pub/Sub push
   ▼
Cloud Run (this repo, src/)
   │ classify (Vertex AI Gemini) ──► label + audit row
   ▼
Gmail label applied  +  BigQuery audit_events row (metadata only)
```

- **Hosting:** one dedicated GCP project per client, inside the **partner's**
  GCP org — not the client's own org. See `SPEC-email-triage-core.md`'s
  "Hosting model" and `CAPABILITY_MAP.md` for why, and `docs/SETUP.md` for
  what that means for the client's Workspace admin specifically.
- **Auth to Gmail:** cross-org domain-wide delegation, keyless — Cloud Run's
  attached service account impersonates the mailbox via `google.auth.iam.Signer`
  (remote JWT signing through the IAM Credentials API), not a downloaded key
  file and not the standard `.with_subject()` flow, which doesn't work from a
  keyless identity. See `src/gmail_client.py`'s module docstring.
- **Classification:** Vertex AI Gemini only, structured JSON output
  constrained to the taxonomy's category enum — never the public Gemini
  Developer API. See `src/classifier.py`'s module docstring; this is the
  single most load-bearing constraint in the project and has two dedicated
  regression tests protecting it.
- **Network isolation:** a VPC-SC service perimeter (the six GCP services
  that support it) *plus* a separate no-public-egress VPC (Serverless VPC
  Access connector, no internet route) — two different controls, not one.
  See `infra/vpc_sc.tf` and `infra/network.tf`'s header comments for why
  both are needed.
- **Audit trail:** BigQuery, metadata-only (`message_id`, `category`,
  `confidence`, `needs_review`, `model_version`, `classified_at` — never
  subject/body), written via a `MERGE` upsert for genuine idempotency, not a
  streaming insert's best-effort dedup. See `src/audit.py`.

## Project structure

```
src/               Application code (Cloud Run / functions-framework)
infra/             Terraform — all GCP infrastructure
taxonomy/          taxonomy.yaml — the category list (see below)
tests/unit/        Fully mocked, no live GCP calls, run by default
tests/integration/ Requires a real deployed sandbox, run explicitly
docs/               SETUP.md — client Workspace admin walkthrough
tasks/               plan.md, todo.md — the implementation plan this was built from
```

## Commands

```
Setup (local):     python -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt
Infra plan:         terraform -chdir=infra plan
Infra apply:        terraform -chdir=infra apply   (or infra/deploy.sh for the full sandbox sequence)
Run tests:          pytest --cov=src --cov-report=term-missing
Lint:               ruff check src tests
Format:              ruff format src tests
Local invoke (dev):  (from src/) functions-framework --target=handle_pubsub_push --debug
Deploy:               gcloud run deploy <service> --source . --region <region> --no-allow-unauthenticated
```

## Updating the taxonomy

Edit `taxonomy/taxonomy.yaml`. Each category needs `name`, `description`,
and `label`:

```yaml
categories:
  - name: billing
    description: Invoices, payments, insurance claims, billing disputes.
    label: Triage/Billing
```

- **`description` is what the model actually reads** — it's embedded directly
  into the classification prompt (`src/classifier.py`'s `_build_prompt`). A
  vague description produces vague classification; be as specific as the real
  category actually is.
- **Don't add a `needs-review` category** — it's reserved, always present
  regardless of what's in the file, and the loader (`src/taxonomy.py`)
  rejects the file if you try.
- **No code change needed.** The taxonomy is baked into the container image
  at deploy time (`Dockerfile` copies `taxonomy/`), so a taxonomy edit needs
  a redeploy (`gcloud run deploy`) to take effect — but not a code change, a
  PR review of `src/`, or touching `classifier.py` at all.
- The current file is a **placeholder**, explicitly marked as such in its own
  header — it exists so the pipeline has something concrete to build and test
  against before the client's real taxonomy is defined.

## Tuning the confidence threshold

`CONFIDENCE_THRESHOLD` (Cloud Run env var, `infra/cloud_run.tf`) is the
cutoff below which a classification becomes `needs-review` instead of the
model's actual category choice. Default: `0.75`, explicitly flagged as a
placeholder pending real pilot data (`SPEC-email-triage-core.md` Open
Questions) — the model's confidence is self-reported as part of its
structured output, not a calibrated statistical score, so this number is
about tuning against observed behavior, not a principled cutoff derived from
first principles.

To change it: update the value in `infra/cloud_run.tf`'s `CONFIDENCE_THRESHOLD`
env block, then `terraform apply` (this is explicitly an "ask first" item in
`SPEC-email-triage-core.md` — don't change it in production without sign-off).

## "Needs Review" runbook

Any message the model couldn't confidently classify lands under the
**Triage/Needs Review** label instead of a real category — this is a
deliberate fallback (`SPEC-email-triage-core.md`), not an error state.

- **Check the label regularly.** There's no separate notification system for
  this v1 — it's a Gmail label like any other, so it needs to be part of
  someone's normal triage routine, same as checking any other queue.
- **A message here got zero automated action beyond the label.** It's exactly
  as unprocessed as it would've been without this pipeline — just flagged as
  "the model wasn't confident," not silently mis-filed into a real category.
- **If the same kind of message keeps landing here,** that's a signal the
  taxonomy might be missing a category, or an existing category's
  `description` needs to be more specific — not necessarily that the
  confidence threshold needs to move.
- **Who owns checking this queue day to day is still an open question** —
  see `SPEC-email-triage-core.md`'s Open Questions. Until that's decided,
  default to whoever already owns the shared mailbox's general triage.
