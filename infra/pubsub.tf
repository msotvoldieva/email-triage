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
# subscription is added in Task 6, once that subscription exists.
resource "google_pubsub_topic_iam_member" "dead_letter_publisher" {
  project = google_project.this.project_id
  topic   = google_pubsub_topic.dead_letter.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:service-${google_project.this.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}
