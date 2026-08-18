# Task 4 (tasks/todo.md, Phase 0): Pub/Sub topic, dead-letter topic, and the two
# Google-managed-service-agent IAM grants both require to function at all.
#
# The push subscription itself is deliberately NOT here -- see tasks/plan.md's
# Architecture Decisions. A push subscription needs a live endpoint URL at creation
# time; Cloud Run doesn't exist until Task 6. Creating the subscription here would be
# a forward reference to a resource this configuration doesn't have yet.
#
# Both grants below are easy to miss and fail silently rather than loudly:
#   - Without the gmail-api-push grant, watch() succeeds but nothing is ever delivered.
#   - Without the dead-letter publisher grant, Pub/Sub doesn't count delivery attempts
#     correctly and messages just keep retrying past what dead-lettering was meant to
#     bound.
# Verified against Google's Pub/Sub and Gmail API documentation, not assumed.

resource "google_pubsub_topic" "gmail_watch" {
  project = google_project.this.project_id
  name    = "email-triage-${var.client_name}-gmail-watch"
}

resource "google_pubsub_topic" "dead_letter" {
  project = google_project.this.project_id
  name    = "email-triage-${var.client_name}-dead-letter"
}

# Gmail's own push-notification service account -- not a service account this project
# owns or creates, it's a fixed Google-managed identity used for every Gmail API
# watch() integration across all of GCP.
resource "google_pubsub_topic_iam_member" "gmail_push_publisher" {
  project = google_project.this.project_id
  topic   = google_pubsub_topic.gmail_watch.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:gmail-api-push@system.gserviceaccount.com"
}

# Pub/Sub's own per-project service agent needs Publisher on the dead-letter topic to
# forward messages there. The matching roles/pubsub.subscriber grant on the *source*
# subscription is below, now that Task 6 has created it.
resource "google_pubsub_topic_iam_member" "dead_letter_publisher" {
  project = google_project.this.project_id
  topic   = google_pubsub_topic.dead_letter.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:service-${google_project.this.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

# --- Task 6 additions: the push subscription itself, deferred from above because it
# needs Cloud Run's real URL (infra/cloud_run.tf), which didn't exist until now. ---

resource "google_pubsub_subscription" "gmail_watch_push" {
  project = google_project.this.project_id
  name    = "email-triage-${var.client_name}-gmail-watch-push"
  topic   = google_pubsub_topic.gmail_watch.name

  push_config {
    # The real Cloud Run URL -- required for actual HTTP delivery and for Cloud Run's
    # ingress rule to recognize this as same-project internal traffic (infra/cloud_run.tf).
    push_endpoint = google_cloud_run_v2_service.this.uri

    oidc_token {
      service_account_email = google_service_account.invoker.email
      # Fixed string, not the Cloud Run URL -- see local.push_oidc_audience's comment
      # in infra/main.tf for why.
      audience = local.push_oidc_audience
    }
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter.id
    max_delivery_attempts = 5
  }

  ack_deadline_seconds = 60

  depends_on = [
    google_cloud_run_v2_service_iam_member.invoker_can_invoke,
    google_pubsub_topic_iam_member.dead_letter_publisher,
  ]
}

# The matching half of dead_letter_publisher above: Pub/Sub's service agent needs
# Subscriber on *this* subscription to read undeliverable messages and forward them.
resource "google_pubsub_subscription_iam_member" "dead_letter_subscriber" {
  project      = google_project.this.project_id
  subscription = google_pubsub_subscription.gmail_watch_push.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:service-${google_project.this.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

# Required for Pub/Sub to mint OIDC tokens as the invoker SA when calling the push
# endpoint -- without this, push delivery fails with a permission error regardless of
# how the subscription itself is configured.
resource "google_service_account_iam_member" "pubsub_can_mint_invoker_tokens" {
  service_account_id = google_service_account.invoker.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:service-${google_project.this.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}
