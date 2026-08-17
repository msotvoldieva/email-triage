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
