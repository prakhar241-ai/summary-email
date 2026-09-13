# Cloud deployment — exact 7 AM IST, laptop-independent

This runs the summary on **Google Cloud** instead of your laptop, so it fires at
**exactly 07:00 IST every day whether your laptop is on or off**.

- **Cloud Function** (`main.py`) — reads the last 24h and emails you the summary.
- **Cloud Scheduler** — triggers it daily at 07:00 `Asia/Kolkata`.
- Reuses the same Gmail login (via the refresh token already saved in `../.env`).

Free-tier usage easily covers one run per day.

## One-time prerequisites (you do these once)

### 1. Publish the OAuth app to production  ⚠️ important
Because this uses restricted Gmail scopes, a **"Testing"** app expires the login
every 7 days. Fix it once:
- https://console.cloud.google.com/auth/overview → **Audience** → **Publish app**
  → confirm. (An "unverified app" note is fine for personal use.)

> After publishing, re-run the local login once so the saved token is a
> long-lived one:
> ```powershell
> cd ..
> del token.json
> python summarize_inbox.py --hours 24        # sign in again, then:
> python -c "import json;d=json.load(open('token.json'));open('.env','a').write('\nGOOGLE_REFRESH_TOKEN='+d['refresh_token']+'\n')"
> ```
> (Or just tell me and I'll refresh it for you.)

### 2. Install the gcloud CLI + log in
- Download: https://cloud.google.com/sdk/docs/install
- Then:
  ```powershell
  gcloud auth login
  gcloud projects create summary-email-app     # or use an existing project id
  gcloud config set project summary-email-app
  ```

### 3. Enable billing on the project
- https://console.cloud.google.com/billing → link a billing account.
  (Required to deploy; the daily run stays within the free tier.)

## Deploy (one command)

```powershell
cd cloud
./deploy.ps1 -ProjectId summary-email-app
```

This enables the needed APIs, deploys the function, creates a locked-down
invoker service account, and schedules the daily 07:00 IST job.

## Test / manage

```powershell
# run it right now (should email you within seconds)
gcloud scheduler jobs run summary-email-daily --location asia-south1

# see logs
gcloud functions logs read summary-email --gen2 --region asia-south1 --limit 20

# change the time (example: 6:30 AM) then redeploy
./deploy.ps1 -ProjectId summary-email-app -Schedule "30 6 * * *"

# pause / resume
gcloud scheduler jobs pause  summary-email-daily --location asia-south1
gcloud scheduler jobs resume summary-email-daily --location asia-south1
```

## After this works, you can retire the laptop job

```powershell
Unregister-ScheduledTask -TaskName SummaryEmail_Daily7AM -Confirm:$false
```
