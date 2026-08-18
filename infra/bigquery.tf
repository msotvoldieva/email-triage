# Task 5 (tasks/todo.md, Phase 0): BigQuery audit dataset + table.
#
# Metadata-only, by construction: every column below is an identifier, a category
# label, a number, or a timestamp -- there is no column that could hold subject/body
# text. SPEC-email-triage-core.md's "Never do" boundary (store PHI in BigQuery)
# is enforced structurally here, not just by application-code discipline.

resource "google_bigquery_dataset" "audit" {
  project    = google_project.this.project_id
  dataset_id = "email_triage_audit" # BigQuery dataset IDs: alphanumeric + underscores only
  location   = var.region

  description = "Metadata-only classification audit trail for email-triage-core. Never contains subject/body text -- see SPEC-email-triage-core.md."

  labels = {
    client = var.client_name
    app    = "email-triage-core"
  }
}

resource "google_bigquery_table" "classification_events" {
  project    = google_project.this.project_id
  dataset_id = google_bigquery_dataset.audit.dataset_id
  table_id   = "classification_events"

  # Guard against `terraform destroy` (or a careless `apply` replacing the table)
  # silently taking the audit trail down with it -- this is compliance history, not
  # disposable infrastructure.
  deletion_protection = true

  # Free to add now, and sets up for the retention question in
  # SPEC-email-triage-core.md's Open Questions -- partition_expiration_ms is left unset
  # until that retention period is actually decided with the client.
  time_partitioning {
    type  = "DAY"
    field = "classified_at"
  }

  schema = jsonencode([
    {
      name        = "message_id"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Gmail message ID. Not PHI on its own -- an opaque identifier, not content."
    },
    {
      name = "category"
      type = "STRING"
      mode = "REQUIRED"
    },
    {
      name = "confidence"
      type = "FLOAT64"
      mode = "REQUIRED"
    },
    {
      name = "needs_review"
      type = "BOOL"
      mode = "REQUIRED"
    },
    {
      name = "model_version"
      type = "STRING"
      mode = "REQUIRED"
    },
    {
      name = "classified_at"
      type = "TIMESTAMP"
      mode = "REQUIRED"
    },
  ])
}

# Dataset-scoped, not project-wide -- the runtime SA can read/write rows in this one
# dataset and nothing else BigQuery-related. Deferred from Task 3 (infra/iam.tf) to
# here, once the dataset it scopes to actually exists.
resource "google_bigquery_dataset_iam_member" "runtime_data_editor" {
  project    = google_project.this.project_id
  dataset_id = google_bigquery_dataset.audit.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.cloud_run_runtime.email}"
}
