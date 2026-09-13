<#
  Deploys the Summary_Email cloud function + a daily 7 AM IST scheduler.

  Prereqs (see cloud/README.md): gcloud installed & logged in, a GCP project
  with billing enabled, and the OAuth app published to "In production".

  Usage:
    ./deploy.ps1 -ProjectId your-gcp-project-id
    ./deploy.ps1 -ProjectId your-gcp-project-id -Region asia-south1
#>
param(
  [Parameter(Mandatory = $true)][string]$ProjectId,
  [string]$Region = "asia-south1",          # Mumbai — good for IST
  [string]$FnName = "summary-email",
  [string]$Schedule = "0 7 * * *",          # 07:00 daily
  [string]$TimeZone = "Asia/Kolkata"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# --- read secrets from ../.env ---------------------------------------------
$envMap = @{}
Get-Content "..\.env" | ForEach-Object {
  $l = $_.Trim()
  if ($l -and -not $l.StartsWith('#') -and $l.Contains('=')) {
    $k, $v = $l.Split('=', 2); $envMap[$k.Trim()] = $v.Trim()
  }
}
foreach ($req in 'GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GOOGLE_REFRESH_TOKEN') {
  if (-not $envMap[$req]) { throw "Missing $req in ..\.env" }
}

# --- write a temp env-vars file (keeps secrets off the command line) -------
$yaml = @()
$yaml += "GOOGLE_CLIENT_ID: `"$($envMap['GOOGLE_CLIENT_ID'])`""
$yaml += "GOOGLE_CLIENT_SECRET: `"$($envMap['GOOGLE_CLIENT_SECRET'])`""
$yaml += "GOOGLE_REFRESH_TOKEN: `"$($envMap['GOOGLE_REFRESH_TOKEN'])`""
if ($envMap['ANTHROPIC_API_KEY']) { $yaml += "ANTHROPIC_API_KEY: `"$($envMap['ANTHROPIC_API_KEY'])`"" }
if ($envMap['SUMMARY_TO'])        { $yaml += "SUMMARY_TO: `"$($envMap['SUMMARY_TO'])`"" }
$envFile = New-TemporaryFile
Set-Content -Path $envFile -Value ($yaml -join "`n") -Encoding utf8

try {
  Write-Host "==> Project + APIs" -ForegroundColor Cyan
  gcloud config set project $ProjectId | Out-Null
  gcloud services enable cloudfunctions.googleapis.com run.googleapis.com `
    cloudbuild.googleapis.com cloudscheduler.googleapis.com `
    artifactregistry.googleapis.com | Out-Null

  Write-Host "==> Deploying function ($FnName)" -ForegroundColor Cyan
  gcloud functions deploy $FnName `
    --gen2 --runtime python312 --region $Region --source . `
    --entry-point summarize --trigger-http --no-allow-unauthenticated `
    --timeout 120 --memory 256Mi --env-vars-file $envFile

  $uri = gcloud functions describe $FnName --gen2 --region $Region `
    --format "value(serviceConfig.uri)"
  Write-Host "Function URL: $uri"

  Write-Host "==> Invoker service account" -ForegroundColor Cyan
  $sa = "summary-email-invoker"
  $saEmail = "$sa@$ProjectId.iam.gserviceaccount.com"
  if (-not (gcloud iam service-accounts list --filter="email:$saEmail" --format="value(email)")) {
    gcloud iam service-accounts create $sa --display-name "Summary Email Scheduler" | Out-Null
  }
  gcloud run services add-iam-policy-binding $FnName --region $Region `
    --member "serviceAccount:$saEmail" --role roles/run.invoker | Out-Null

  Write-Host "==> Scheduler job (daily $Schedule $TimeZone)" -ForegroundColor Cyan
  $job = "$FnName-daily"
  $exists = gcloud scheduler jobs list --location $Region --filter="name~$job" --format="value(name)"
  $action = if ($exists) { "update" } else { "create" }
  gcloud scheduler jobs $action http $job --location $Region `
    --schedule $Schedule --time-zone $TimeZone `
    --uri $uri --http-method POST `
    --oidc-service-account-email $saEmail --oidc-token-audience $uri

  Write-Host "`n✅ Done. It will run every day at 07:00 $TimeZone." -ForegroundColor Green
  Write-Host "Test it now with:  gcloud scheduler jobs run $job --location $Region"
}
finally {
  Remove-Item $envFile -ErrorAction SilentlyContinue
}
