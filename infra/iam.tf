# Task 3 (tasks/todo.md, Phase 0): service accounts + least-privilege IAM.
#
# Two service accounts, both least-privilege by construction:
#   - cloud_run_runtime: the identity Cloud Run runs as. This is also the SA the
#     client's Workspace admin authorizes for domain-wide delegation (see the output
#     below) -- Terraform can prepare the SA, but cannot perform that authorization
#     itself; it happens in the client's own Admin console (Task 24).
#   - invoker: exists only to be impersonated for OIDC tokens -- Pub/Sub push
#     (Task 4) and Cloud Scheduler's watch-renewal calls (Task 20) both use it to
#     authenticate to Cloud Run. It gets no roles here because its only job (Cloud
#     Run Invoker on the specific service) can't be scoped until that service exists
#     in Task 6.
#
# What's deliberately NOT granted here, and why: BigQuery and Pub/Sub both support
# resource-level IAM (a dataset, a subscription) that don't exist as Terraform
# resources until Task 5 and Task 4 respectively. Granting a project-level BigQuery or
# Pub/Sub role now, just to avoid a forward reference, would be a broader grant than
# the code needs -- so those two bindings are added alongside the resources they scope
# to, in infra/bigquery.tf and infra/pubsub.tf. Vertex AI and Firestore don't have
# finer-than-project IAM to begin with, so their bindings belong here.

resource "google_service_account" "cloud_run_runtime" {
  project      = google_project.this.project_id
  account_id   = "email-triage-runtime"
  display_name = "email-triage-core runtime (Cloud Run, domain-wide delegated)"
  description  = "Identity Cloud Run runs as. Authorized for domain-wide delegation in the client's own Workspace Admin console -- see the cloud_run_runtime_sa_client_id output."
}

resource "google_service_account" "invoker" {
  project      = google_project.this.project_id
  account_id   = "email-triage-invoker"
  display_name = "email-triage-core OIDC invoker (Pub/Sub push, Cloud Scheduler)"
  description  = "No roles granted here by design -- gets Cloud Run Invoker on the specific service once it exists (Task 6)."
}

# Vertex AI has no finer-than-project IAM surface relevant to this app (there's no
# per-model or per-endpoint scoping this code would use), so project-level is the
# narrowest grant actually available -- not an over-grant.
resource "google_project_iam_member" "runtime_vertex_ai_user" {
  project = google_project.this.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.cloud_run_runtime.email}"
}

# Same reasoning as Vertex AI: native Firestore access control is project/database
# level, not per-collection, for a standard (non-custom-rules) setup like the
# historyId cursor store.
resource "google_project_iam_member" "runtime_firestore_user" {
  project = google_project.this.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.cloud_run_runtime.email}"
}

# The runtime SA's unique numeric ID -- this is what the client's Workspace admin
# pastes into Admin console > Security > API Controls > Domain-wide Delegation as the
# "Client ID", authorizing it for gmail.readonly + gmail.modify scoped to the one
# shared mailbox. Not a secret (it's meant to be handed to the client), but it should
# only go to that specific admin, not posted anywhere public.
output "cloud_run_runtime_sa_client_id" {
  description = "Runtime service account's unique ID. Hand this to the client's Workspace admin for domain-wide delegation authorization (see docs/SETUP.md, Task 24). Not a secret, but scope distribution to that one admin."
  value       = google_service_account.cloud_run_runtime.unique_id
}

output "cloud_run_runtime_sa_email" {
  description = "Runtime service account email, for reference in domain-wide delegation troubleshooting and IAM review."
  value       = google_service_account.cloud_run_runtime.email
}
