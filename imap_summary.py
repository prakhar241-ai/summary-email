"""
Summary_Email (App Password edition) — no OAuth, no consent screen, no 7-day
expiry. Reads the last 24h of your inbox over IMAP and emails you a summary
over SMTP, using a Gmail App Password.

Setup:
  1. Turn ON 2-Step Verification for the Gmail account.
  2. Create an App Password: https://myaccount.google.com/apppasswords
  3. Put these in .env (same folder):
        GMAIL_ADDRESS=you@gmail.com
        GMAIL_APP_PASSWORD=abcd efgh ijkl mnop   (spaces are fine)
        # optional:
        SUMMARY_TO=someone@else.com
        ANTHROPIC_API_KEY=sk-ant-...

Run:  python imap_summary.py
"""

import email
import imaplib
import os
import re
import smtplib
import ssl
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parsedate_to_datetime

IMAP_HOST = "imap.gmail.com"
SMTP_HOST = "smtp.gmail.com"
AI_MODEL = "claude-haiku-4-5-20251001"

HERE = os.path.dirname(os.path.abspath(__file__))


def load_env():
    path = os.path.join(HERE, ".env")
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def decode(s):
    try:
        return str(make_header(decode_header(s)))
    except Exception:
        return s or ""


def clean_sender(raw):
    raw = decode(raw)
    name = re.sub(r"<[^>]*>", "", raw).strip().strip('"')
    return name or raw


def text_snippet(msg, limit=180):
    """Pull a short plain-text preview from an email message."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition")):
                body = part.get_payload(decode=True) or b""
                break
        if not body:  # fall back to html, stripped
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    body = part.get_payload(decode=True) or b""
                    break
    else:
        body = msg.get_payload(decode=True) or b""
    text = body.decode(errors="replace") if isinstance(body, bytes) else str(body)
    text = re.sub(r"<[^>]+>", " ", text)          # strip tags
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def fetch_last_24h():
    user = os.environ["GMAIL_ADDRESS"]
    pw = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    M = imaplib.IMAP4_SSL(IMAP_HOST)
    M.login(user, pw)
    M.select("INBOX")
    # IMAP SINCE is date-granular; search from yesterday, then filter by time.
    since = (cutoff - timedelta(days=1)).strftime("%d-%b-%Y")
    typ, data = M.search(None, f'(SINCE "{since}")')
    ids = data[0].split()

    messages = []
    for num in ids:
        typ, fetched = M.fetch(num, "(FLAGS RFC822)")
        flags, raw = "", None
        for part in fetched:
            if isinstance(part, tuple):
                raw = part[1]
            if isinstance(part, bytes):
                flags += part.decode(errors="replace")
            elif isinstance(part, tuple) and isinstance(part[0], bytes):
                flags += part[0].decode(errors="replace")
        if raw is None:
            continue
        msg = email.message_from_bytes(raw)
        try:
            when = parsedate_to_datetime(msg.get("Date"))
            if when and when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
        except Exception:
            when = None
        if when and when < cutoff:
            continue  # older than 24h
        subject = decode(msg.get("Subject") or "(no subject)")
        if subject.startswith("📬 Inbox summary") or subject.startswith("Inbox summary"):
            continue  # skip our own past digests
        messages.append({
            "from": clean_sender(msg.get("From", "")),
            "subject": subject,
            "date": msg.get("Date", ""),
            "snippet": text_snippet(msg),
            "unread": "\\Seen" not in flags,
        })
    M.logout()
    messages.sort(key=lambda m: m["date"], reverse=True)
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
    lines.append(f"- {len(messages)} emails")
    lines.append(f"- {sum(1 for m in messages if m['unread'])} unread")
    top = Counter(m["from"] for m in messages).most_common(5)
    if top:
        lines.append("- Top senders: " + ", ".join(f"{n} ({c})" for n, c in top))
    lines.append("")
    if ai_text:
        lines += ["## Highlights", ai_text, ""]
    lines.append("## Emails")
    for m in messages:
        flag = "[unread] " if m["unread"] else ""
        lines.append(f"### {flag}{m['subject']}")
        lines.append(f"- From: {m['from']}")
        if m["date"]:
            lines.append(f"- Date: {m['date']}")
        if m["snippet"]:
            lines.append(f"- {m['snippet']}")
        lines.append("")
    return "\n".join(lines)


def send_email(subject, body):
    user = os.environ["GMAIL_ADDRESS"]
    pw = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "")
    to_addr = os.environ.get("SUMMARY_TO") or user
    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP_SSL(SMTP_HOST, 465, context=ssl.create_default_context()) as s:
        s.login(user, pw)
        s.send_message(msg)
    return to_addr


def main():
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    load_env()
    if not os.environ.get("GMAIL_ADDRESS") or not os.environ.get("GMAIL_APP_PASSWORD"):
        sys.exit("Missing GMAIL_ADDRESS / GMAIL_APP_PASSWORD (see setup in this file's docstring).")

    messages = fetch_last_24h()
    digest = build_digest(messages, ai_highlights(messages)) if messages else \
        f"# Inbox summary — {datetime.now():%Y-%m-%d %H:%M}\n\nNo new emails in the last 24h."
    print(digest)
    to_addr = send_email(f"📬 Inbox summary — {datetime.now():%a %d %b} ({len(messages)} emails)", digest)
    print(f"\nSent summary to {to_addr}")


if __name__ == "__main__":
    main()
