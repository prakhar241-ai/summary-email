# Summary_Email

A daily job that reads the **last 24 hours** of your Gmail inbox and **emails you
a summary at 7:00 AM IST**. It reads your inbox and sends the digest to yourself —
it never deletes or modifies existing email.

## What you get

```
Summary_Email/
├── summarize_inbox.py   # the program
├── run_daily.cmd        # launcher the 7 AM task runs
├── requirements.txt     # libraries it needs
├── README.md            # this file
├── .gitignore           # keeps secrets/personal data out of git
├── summaries/           # each run saves a dated .md file + run.log
├── credentials.json     # YOUR Google OAuth desktop credential (you add this)
└── token.json           # created after first login (do not share)
```

## Setup for a DIFFERENT Gmail account (one time)

You do **not** share a password — you pick the account in a browser login.

1. **Install the libraries**
   ```powershell
   pip install -r requirements.txt
   ```
2. **Create a Desktop OAuth credential** for the account you want to use:
   - https://console.cloud.google.com/apis/credentials →
     **Create Credentials → OAuth client ID → Application type: Desktop app**
   - Download the JSON, rename to `credentials.json`, place it in **this** folder.
3. **Enable the Gmail API** for that project:
   https://console.cloud.google.com/apis/library/gmail.googleapis.com → **Enable**
4. **Add that Gmail as a Test user**:
   APIs & Services → **OAuth consent screen** → **Test users** → add the address.
5. **Log in once** with that account:
   ```powershell
   python summarize_inbox.py --hours 24 --send
   ```
   A browser opens — **sign in with the different Gmail** and approve read + send.
   The login is saved to `token.json`; every 7 AM run reuses it silently.

> Switching accounts later? Delete `token.json` and run step 5 again, signing in
> with the other account.

## AI highlights (optional but recommended)

If an Anthropic API key is present, each summary starts with a short
**🔎 Highlights** section — a 1-2 sentence overview plus a bullet list of
anything that likely needs a reply. Without a key, you still get the plain
digest (no AI call, no cost).

To enable:
1. Get a key at https://console.anthropic.com/settings/keys
2. Copy `.env.example` to `.env` and paste your key:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ```
The `.env` file is git-ignored and is read automatically by both manual and
7 AM runs. Uses the cheap, fast `claude-haiku-4-5` model (one call per run).

## The daily 7 AM job

A Windows Scheduled Task named **`SummaryEmail_Daily7AM`** runs `run_daily.cmd`
every day at **07:00 (your PC is on IST, so this is 7 AM IST)**. It's set to
**wake the PC** if asleep and to **run late** if the PC was off at 7 AM.

- The PC must be **powered on** (or asleep — it will wake). If it's fully shut
  down, the run happens the next time it's on.
- Check the last run:
  ```powershell
  Get-ScheduledTaskInfo -TaskName SummaryEmail_Daily7AM
  ```
- See output/errors: open `summaries\run.log`.
- Run it right now to test: `run_daily.cmd` (or the command in step 5).

## Manual usage

```powershell
python summarize_inbox.py                 # last 24h, print + save only
python summarize_inbox.py --send          # also email it to yourself
python summarize_inbox.py --hours 48      # last 48 hours
python summarize_inbox.py --unread        # only unread
python summarize_inbox.py --to me@x.com   # send to a specific address
```

## Safety notes

- Only `gmail.readonly` + `gmail.send` are requested (read inbox, send yourself
  the digest). It cannot delete or edit existing email.
- `credentials.json`, `token.json`, and saved summaries are git-ignored.

## Ideas for later

- Group by category (Primary / Updates / Promotions).
- Deliver as nicely formatted HTML instead of plain text.
