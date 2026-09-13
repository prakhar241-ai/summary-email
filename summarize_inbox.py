"""
Summary_Email — summarize your Gmail inbox.

What it does:
  1. Connects to your Gmail account (read-only) using OAuth.
  2. Fetches your most recent inbox messages.
  3. Prints a clean digest and saves it to the `summaries/` folder.

First-time setup and usage are explained in README.md.

Run it with:
    python summarize_inbox.py                # last 7 days, up to 25 emails
    python summarize_inbox.py --days 3       # last 3 days
    python summarize_inbox.py --max 50       # up to 50 emails
    python summarize_inbox.py --unread       # only unread emails
"""

import argparse
import base64
import os
import re
import sys
from collections import Counter
from datetime import datetime
from email.message import EmailMessage

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# readonly = read your inbox; send = email the summary to yourself.
# It still cannot delete or modify existing email.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

# Optional AI summaries. Only used if ANTHROPIC_API_KEY is set in the
# environment; otherwise the plain digest is produced with no AI call.
AI_MODEL = "claude-haiku-4-5-20251001"

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(HERE, "token.json")
SUMMARIES_DIR = os.path.join(HERE, "summaries")

# Look for credentials.json in this folder first, then fall back to the
# copy that already lives in the parent Claude_code folder.
CREDENTIALS_CANDIDATES = [
    os.path.join(HERE, "credentials.json"),
    os.path.join(os.path.dirname(HERE), "credentials.json"),
]


def load_env():
    """Load KEY=VALUE lines from a local .env file into the environment, so the
    scheduled 7 AM run (which has no interactive shell) still sees your keys."""
    env_path = os.path.join(HERE, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def find_credentials():
    for path in CREDENTIALS_CANDIDATES:
        if os.path.exists(path):
            return path
    raise FileNotFoundError(
        "Could not find credentials.json. Put your Google OAuth client file "
        "(a Desktop app credential — see README.md) next to this script."
    )


def build_flow():
    """Create the OAuth flow. Prefer client id/secret from .env; otherwise
    fall back to a credentials.json file in the folder."""
    cid = os.environ.get("GOOGLE_CLIENT_ID")
    csec = os.environ.get("GOOGLE_CLIENT_SECRET")
    if cid and csec:
        config = {
            "installed": {
                "client_id": cid,
                "client_secret": csec,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "redirect_uris": ["http://localhost"],
            }
        }
        return InstalledAppFlow.from_client_config(config, SCOPES)
    return InstalledAppFlow.from_client_secrets_file(find_credentials(), SCOPES)


def get_service():
    """Authenticate and return a Gmail API client."""
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    # Refresh or run the browser login the first time.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = build_flow()
            # Opens your browser once so you can grant read + send access.
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def header(headers, name):
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def clean_sender(raw):
    """Turn 'Jane Doe <jane@x.com>' into a short, readable name."""
    name = re.sub(r"<[^>]*>", "", raw).strip().strip('"')
    return name or raw


def fetch_messages(service, query, max_results):
    resp = service.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()
    ids = [m["id"] for m in resp.get("messages", [])]

    messages = []
    for mid in ids:
        msg = service.users().messages().get(
            userId="me", id=mid, format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()
        headers = msg.get("payload", {}).get("headers", [])
        messages.append({
            "from": clean_sender(header(headers, "From")),
            "subject": header(headers, "Subject") or "(no subject)",
            "date": header(headers, "Date"),
            "snippet": msg.get("snippet", "").strip(),
            "unread": "UNREAD" in msg.get("labelIds", []),
        })
    return messages


def get_my_email(service):
    return service.users().getProfile(userId="me").execute().get("emailAddress", "")


def send_email(service, to_addr, subject, body_text):
    """Send the digest as a plain-text email from/to the connected account."""
    msg = EmailMessage()
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body_text)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()


def ai_highlights(messages):
    """Ask Claude for a short prioritized summary. Returns text, or None if
    no API key / SDK / on any error (the plain digest still works)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
    except ImportError:
        return None

    lines = []
    for i, m in enumerate(messages, 1):
        lines.append(f"{i}. From: {m['from']} | Subject: {m['subject']} | {m['snippet']}")
    email_list = "\n".join(lines)

    prompt = (
        "You are summarizing someone's email inbox from the last 24 hours. "
        "Below are the emails. Write a brief, skimmable summary with:\n"
        "1. A 1-2 sentence overview of what's in the inbox.\n"
        "2. A short bulleted list of anything that likely needs a reply or action "
        "(skip newsletters/promos). If nothing needs action, say so.\n"
        "Keep it under 150 words. Plain text, no preamble.\n\n"
        f"Emails:\n{email_list}"
    )
    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=AI_MODEL,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:  # network/quota/etc — degrade gracefully
        print(f"(AI summary skipped: {e})")
        return None


def build_digest(messages, query_label, ai_text=None):
    lines = []
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    unread = sum(1 for m in messages if m["unread"])

    lines.append(f"# Inbox summary — {stamp}")
    lines.append(f"_Filter: {query_label}_")
    lines.append("")
    lines.append(f"- **{len(messages)}** emails")
    lines.append(f"- **{unread}** unread")

    top_senders = Counter(m["from"] for m in messages).most_common(5)
    if top_senders:
        lines.append("- Top senders: " + ", ".join(
            f"{name} ({count})" for name, count in top_senders
        ))
    lines.append("")

    if ai_text:
        lines.append("## 🔎 Highlights")
        lines.append(ai_text)
        lines.append("")

    lines.append("## Emails")
    for m in messages:
        flag = "🔵 " if m["unread"] else ""
        lines.append(f"### {flag}{m['subject']}")
        lines.append(f"- From: {m['from']}")
        if m["date"]:
            lines.append(f"- Date: {m['date']}")
        if m["snippet"]:
            lines.append(f"- {m['snippet']}")
        lines.append("")

    return "\n".join(lines)


def main():
    # Windows consoles default to cp1252, which can't print emoji; force utf-8
    # so printing/logging the digest (and the run.log redirect) never crashes.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    load_env()
    parser = argparse.ArgumentParser(description="Summarize your Gmail inbox.")
    parser.add_argument("--hours", type=int, default=24, help="How many hours back to look (default 24).")
    parser.add_argument("--max", type=int, default=50, help="Max emails to fetch (default 50).")
    parser.add_argument("--unread", action="store_true", help="Only unread emails.")
    parser.add_argument("--send", action="store_true", help="Email the summary to yourself.")
    parser.add_argument("--to", default=None, help="Recipient (default: the connected account itself).")
    args = parser.parse_args()

    # Gmail's search granularity is days; newer_than:1d covers the last 24h.
    days = max(1, round(args.hours / 24))
    # Exclude the digests this tool emails to itself, so they don't pile up.
    query = f'in:inbox newer_than:{days}d -subject:"Inbox summary"'
    if args.unread:
        query += " is:unread"

    service = get_service()
    messages = fetch_messages(service, query, args.max)

    label = f"last {args.hours}h" + (" (unread)" if args.unread else "")
    if not messages:
        digest = f"# Inbox summary — {datetime.now():%Y-%m-%d %H:%M}\n\nNo new emails in the {label}."
    else:
        ai_text = ai_highlights(messages)
        digest = build_digest(messages, label, ai_text)
    print(digest)

    os.makedirs(SUMMARIES_DIR, exist_ok=True)
    out_path = os.path.join(SUMMARIES_DIR, datetime.now().strftime("%Y-%m-%d_%H%M") + ".md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(digest)
    print(f"\nSaved to {out_path}")

    if args.send:
        to_addr = args.to or get_my_email(service)
        subject = f"📬 Inbox summary — {datetime.now():%a %d %b} ({len(messages)} emails)"
        send_email(service, to_addr, subject, digest)
        print(f"Sent summary to {to_addr}")


if __name__ == "__main__":
    main()
