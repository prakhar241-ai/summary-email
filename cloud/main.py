"""
Google Cloud Function: read the last 24h of Gmail and email a summary.

Runs headlessly on a schedule (Cloud Scheduler), so it authenticates with a
stored refresh token instead of a browser. No files are written (Cloud
Functions have a read-only filesystem apart from /tmp).

Entry point: summarize  (HTTP-triggered)
Required env vars: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN
Optional env vars: SUMMARY_TO (recipient; default = the account itself),
                   ANTHROPIC_API_KEY (enables AI Highlights)
"""

import base64
import os
import re
from collections import Counter
from datetime import datetime
from email.message import EmailMessage

import functions_framework
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]
AI_MODEL = "claude-haiku-4-5-20251001"


def get_service():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return build("gmail", "v1", credentials=creds)


def header(headers, name):
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def clean_sender(raw):
    name = re.sub(r"<[^>]*>", "", raw).strip().strip('"')
    return name or raw


def fetch_messages(service, query, max_results=50):
    resp = service.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()
    messages = []
    for m in resp.get("messages", []):
        msg = service.users().messages().get(
            userId="me", id=m["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()
        h = msg.get("payload", {}).get("headers", [])
        messages.append({
            "from": clean_sender(header(h, "From")),
            "subject": header(h, "Subject") or "(no subject)",
            "date": header(h, "Date"),
            "snippet": msg.get("snippet", "").strip(),
            "unread": "UNREAD" in msg.get("labelIds", []),
        })
    return messages


def ai_highlights(messages):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
        lines = [f"{i}. From: {m['from']} | Subject: {m['subject']} | {m['snippet']}"
                 for i, m in enumerate(messages, 1)]
        prompt = (
            "Summarize this inbox from the last 24 hours. Give a 1-2 sentence "
            "overview, then a short bullet list of anything that likely needs a "
            "reply or action (skip newsletters/promos). Under 150 words, plain "
            "text, no preamble.\n\nEmails:\n" + "\n".join(lines)
        )
        resp = anthropic.Anthropic(api_key=api_key).messages.create(
            model=AI_MODEL, max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        print(f"AI summary skipped: {e}")
        return None


def build_digest(messages, ai_text=None):
    lines = [f"# Inbox summary — {datetime.now():%Y-%m-%d %H:%M}", ""]
    lines.append(f"- **{len(messages)}** emails")
    lines.append(f"- **{sum(1 for m in messages if m['unread'])}** unread")
    top = Counter(m["from"] for m in messages).most_common(5)
    if top:
        lines.append("- Top senders: " + ", ".join(f"{n} ({c})" for n, c in top))
    lines.append("")
    if ai_text:
        lines += ["## 🔎 Highlights", ai_text, ""]
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


def send_email(service, to_addr, subject, body):
    msg = EmailMessage()
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()


@functions_framework.http
def summarize(request):
    service = get_service()
    query = 'in:inbox newer_than:1d -subject:"Inbox summary"'
    messages = fetch_messages(service, query)

    if not messages:
        digest = f"# Inbox summary — {datetime.now():%Y-%m-%d %H:%M}\n\nNo new emails in the last 24h."
    else:
        digest = build_digest(messages, ai_highlights(messages))

    to_addr = os.environ.get("SUMMARY_TO") or \
        service.users().getProfile(userId="me").execute()["emailAddress"]
    subject = f"📬 Inbox summary — {datetime.now():%a %d %b} ({len(messages)} emails)"
    send_email(service, to_addr, subject, digest)
    return f"Sent {len(messages)}-email summary to {to_addr}\n"
