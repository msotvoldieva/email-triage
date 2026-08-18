# No project IDs, org IDs, billing accounts, or any client-identifying values belong
# here as defaults. Every environment (sandbox, this client's real project) supplies
# its own values via a gitignored *.tfvars file — see terraform.tfvars.example.

variable "client_name" {
  description = "Short, stable identifier for the client this project is dedicated to (e.g. \"acme-practice\"). Used in resource naming and labels. This project must never be shared across more than one client — see SPEC-email-triage-core.md."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,20}$", var.client_name))
    error_message = "client_name must be lowercase alphanumeric with hyphens, 3-21 characters, starting with a letter."
  }
}

variable "project_id" {
  description = "Globally unique GCP project ID to create for this client, in the partner org. Not reused across clients."
  type        = string
}

variable "org_id" {
  description = "GCP organization ID that owns this project (the partner company's org, not the client's)."
  type        = string
}

variable "folder_id" {
  description = "Optional GCP folder ID to create the project under, instead of directly under the org. Leave null to create directly under org_id."
  type        = string
  default     = null
}

variable "billing_account" {
  description = "Billing account ID this project's usage is billed to."
  type        = string
  sensitive   = true
}

variable "region" {
  description = "Default GCP region for regional resources (Cloud Run, Pub/Sub, etc.)."
  type        = string
  default     = "us-central1"
}

variable "access_policy_id" {
  description = "Numeric ID of the partner org's existing Access Context Manager policy. An access policy is a singleton per GCP org -- this module references it, it does not create it. Created once, out of band, before the first client project is bootstrapped; every client's service perimeter (infra/vpc_sc.tf) lives under this same policy."
  type        = string
}

variable "container_image" {
  description = "Placeholder image for the Cloud Run service's initial creation only. `gcloud run deploy --source .` (Task 22+) publishes the real application image directly; infra/cloud_run.tf's lifecycle.ignore_changes stops a later `terraform apply` from reverting that deploy back to this placeholder."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello:latest"
}

variable "classifier_model" {
  description = "Exact Vertex AI Gemini model ID to classify against (e.g. \"gemini-2.5-flash\"). No default -- this is something to verify against the live Vertex AI Model Garden at deploy time, not guess at plan-authoring time (SPEC-email-triage-core.md, src/config.py)."
  type        = string
}

variable "mailbox_address" {
  description = "The shared mailbox's email address this deployment watches (e.g. \"intake@clientdomain.com\"). Client-specific -- no default. Used by the watch-renewal route (Task 20), which has no incoming push envelope to read it from the way the Pub/Sub path does."
  type        = string
}
