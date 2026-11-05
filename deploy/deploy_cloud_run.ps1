# Cloud Run deployment for EdgeForge (Windows). DRY RUN by default: prints every command.
# Nothing is created until -Execute is passed with your own project and billing account.
#   .\deploy\deploy_cloud_run.ps1 -ProjectId my-proj -BillingAccount XXXXXX-XXXXXX-XXXXXX -Execute
param(
  [Parameter(Mandatory = $true)][string]$ProjectId,
  [string]$Region = "europe-west2",
  [string]$Repo = "edgeforge",
  [string]$BillingAccount = "",
  [int]$BudgetUsd = 5,
  [switch]$Public,
  [switch]$Execute
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path "data/serving/bundle.json")) { throw "build the bundle first: uv run edgeforge serve build" }
$sha = (git rev-parse --short HEAD).Trim()
$image = "$Region-docker.pkg.dev/$ProjectId/$Repo/edgeforge:$sha"
$auth = if ($Public) { "--allow-unauthenticated" } else { "--no-allow-unauthenticated" }
function Run([string]$cmd) { Write-Host "+ $cmd"; if ($Execute) { Invoke-Expression $cmd } }
Run "gcloud config set project $ProjectId"
Run "gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com billingbudgets.googleapis.com"
Run "gcloud artifacts repositories create $Repo --repository-format=docker --location=$Region"
Run "gcloud builds submit --tag $image ."
Run "gcloud run deploy edgeforge-api --image $image --region $Region $auth --max-instances 1 --memory 1Gi --cpu 1 --set-env-vars SERVICE=api"
Run "gcloud run deploy edgeforge-app --image $image --region $Region $auth --max-instances 1 --memory 1Gi --cpu 1 --set-env-vars SERVICE=app"
if ($BillingAccount) {
  Run "gcloud billing budgets create --billing-account $BillingAccount --display-name edgeforge-budget --budget-amount ${BudgetUsd}USD --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0"
} else { Write-Host "BillingAccount not set: no budget alert created (set it before executing)" }
