#!/bin/sh
# Cloud Run deployment for EdgeForge. DRY RUN by default: prints every command.
# Nothing is created until you run with EXECUTE=1 and your own project and billing account.
#   PROJECT_ID=my-proj BILLING_ACCOUNT=XXXXXX-XXXXXX-XXXXXX EXECUTE=1 sh deploy/deploy_cloud_run.sh
# Services are private unless PUBLIC=1. Budget alert: BUDGET_USD (default 5) at 50/90/100%.
set -eu
: "${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-europe-west2}"
REPO="${REPO:-edgeforge}"
BUDGET_USD="${BUDGET_USD:-5}"
IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/edgeforge:$(git rev-parse --short HEAD)"
AUTH="--no-allow-unauthenticated"
[ "${PUBLIC:-0}" = "1" ] && AUTH="--allow-unauthenticated"
run() { echo "+ $*"; [ "${EXECUTE:-0}" = "1" ] && "$@" || true; }
[ -f data/serving/bundle.json ] || { echo "build the bundle first: uv run edgeforge serve build"; exit 1; }
run gcloud config set project "$PROJECT_ID"
run gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com billingbudgets.googleapis.com
run gcloud artifacts repositories create "$REPO" --repository-format=docker --location="$REGION"
run gcloud builds submit --tag "$IMAGE" .
run gcloud run deploy edgeforge-api --image "$IMAGE" --region "$REGION" $AUTH --max-instances 1 --memory 1Gi --cpu 1 --set-env-vars SERVICE=api
run gcloud run deploy edgeforge-app --image "$IMAGE" --region "$REGION" $AUTH --max-instances 1 --memory 1Gi --cpu 1 --set-env-vars SERVICE=app
if [ -n "${BILLING_ACCOUNT:-}" ]; then
  run gcloud billing budgets create --billing-account "$BILLING_ACCOUNT" --display-name edgeforge-budget --budget-amount "${BUDGET_USD}USD" --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0
else
  echo "BILLING_ACCOUNT not set: no budget alert created (set it before executing)"
fi
echo "health checks after deploy: curl \$(gcloud run services describe edgeforge-api --region $REGION --format 'value(status.url)')/healthz  (needs an identity token if private)"
