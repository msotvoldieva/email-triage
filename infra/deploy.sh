#!/usr/bin/env bash
# Task 22 (tasks/todo.md, Phase 7): sandbox deploy sequence.
#
# This CANNOT run in a machine without real GCP credentials and a real target
# project -- it's meant to be run by whoever has both, not executed
# unattended. Nothing before this point in the build has required either.
#
# Prerequisites, all one-time, all manual (see docs/SETUP.md for the fuller
# walkthrough once it's written -- Task 24):
#   1. `gcloud auth login` and `gcloud auth application-default login`
#      completed, authenticated as a principal with permission to create
#      projects/resources in the partner org.
#   2. infra/terraform.tfvars filled in for the SANDBOX project (copy from
#      terraform.tfvars.example) -- a sandbox project, not the client's real
#      one; a test/sandbox Workspace mailbox, not production mail.
#   3. The org's Access Context Manager policy already exists (it's a
#      singleton, referenced not created -- see variables.tf's
#      access_policy_id description).

set -euo pipefail
cd "$(dirname "$0")"

echo "==> terraform init"
terraform init

echo "==> terraform plan"
terraform plan -out=tfplan

echo
read -r -p "Review the plan above carefully. Apply? [y/N] " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
  echo "Aborted -- no changes made."
  exit 1
fi

echo "==> terraform apply"
terraform apply tfplan
rm -f tfplan

PROJECT_ID=$(terraform output -raw project_id)
REGION=$(terraform output -raw region)
SERVICE_NAME=$(terraform output -raw cloud_run_service_name)

echo "==> gcloud run deploy (publishes the real application image, replacing"
echo "    the placeholder -- infra/cloud_run.tf's lifecycle.ignore_changes"
echo "    means a later terraform apply won't revert this)"
(
  cd ..
  gcloud run deploy "$SERVICE_NAME" \
    --source . \
    --project "$PROJECT_ID" \
    --region "$REGION" \
    --no-allow-unauthenticated
)

echo
echo "==> Deploy complete. Remaining manual steps (not automatable):"
echo "    1. Hand the client's Workspace admin the runtime SA's client ID:"
terraform output -raw cloud_run_runtime_sa_client_id
echo
echo "    2. They authorize it for domain-wide delegation (gmail.readonly +"
echo "       gmail.modify) in Admin console > Security > API Controls >"
echo "       Domain-wide Delegation, scoped to the mailbox in"
echo "       infra/terraform.tfvars' mailbox_address."
echo "    3. Once authorized, bootstrap the initial watch() -- there's no"
echo "       push subscription traffic yet to trigger it, and Cloud Run's"
echo "       internal-only ingress means a human curl can't reach the service"
echo "       directly (ingress filtering happens before auth, regardless of"
echo "       credentials). Instead, fire the already-configured Scheduler job"
echo "       on demand -- it's already set up with the right OIDC"
echo "       audience/invoker identity, so this just works:"
echo "         gcloud scheduler jobs run email-triage-<client_name>-renew-watch \\"
echo "           --location=$REGION --project=$PROJECT_ID"
echo "       Then check Cloud Logging for a watch.renewed entry to confirm."
echo "    4. Review the VPC-SC perimeter's dry-run violation log before"
echo "       switching it to enforced mode (tasks/todo.md, Phase 7 checkpoint)."
