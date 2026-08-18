# Task 2a (tasks/todo.md, Phase 0): VPC-SC service perimeter.
#
# IMPORTANT: Gmail API is not a VPC-SC-supported service (confirmed against Google's
# official supported-products documentation, see tasks/plan.md's Architecture
# Decisions). It is deliberately absent from restricted_services below. Gmail access
# is governed instead by IAM/domain-wide delegation scope (infra/iam.tf, Task 3).
#
# This perimeter alone does not stop arbitrary outbound calls to non-Google
# destinations -- see infra/network.tf (Task 2b) for the control that does.

locals {
  # The only six GCP services this pipeline touches that are confirmed to support
  # VPC-SC restriction. Deliberately NOT derived from main.tf's required_apis list --
  # that list answers "what APIs does this project need enabled", this one answers
  # "what can actually be perimeter-restricted", and conflating the two would silently
  # let an unsupported service (like Gmail) slip in here if main.tf's list ever grows.
  vpc_sc_restricted_services = [
    "aiplatform.googleapis.com",     # Vertex AI
    "pubsub.googleapis.com",
    "bigquery.googleapis.com",
    "secretmanager.googleapis.com",
    "firestore.googleapis.com",      # historyId cursor state store only
    "logging.googleapis.com",
  ]

  # Access Context Manager perimeter short names must be alphanumeric + underscores
  # only (no hyphens), unlike most other GCP resource names.
  perimeter_short_name = "email_triage_${replace(var.client_name, "-", "_")}"
}

resource "google_access_context_manager_service_perimeter" "this" {
  parent = "accessPolicies/${var.access_policy_id}"
  name   = "accessPolicies/${var.access_policy_id}/servicePerimeters/${local.perimeter_short_name}"
  title  = local.perimeter_short_name

  perimeter_type = "PERIMETER_TYPE_REGULAR"

  # Dry-run only for now: violations are logged, nothing is blocked yet. The Phase 0
  # checkpoint (tasks/todo.md) requires human review of the allowed-service list before
  # this goes further; switching to enforced mode is a deliberate, separate step at the
  # Phase 7 integration checkpoint, once perimeter logs have been reviewed in the
  # sandbox and show no unexpected violations.
  use_explicit_dry_run_spec = true

  spec {
    resources = [
      "projects/${google_project.this.number}",
    ]

    restricted_services = local.vpc_sc_restricted_services

    vpc_accessible_services {
      enable_restriction = true
      allowed_services   = local.vpc_sc_restricted_services
    }
  }
}
