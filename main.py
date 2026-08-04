import os
import sys
import base64
import re
import time
from html import unescape
from io import BytesIO
from typing import Optional

from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import requests

load_dotenv()

# ─── Config ─────────────────────────────────────────────────────────────────
GMAIL_CLIENT_ID = os.environ["GMAIL_CLIENT_ID"]
GMAIL_CLIENT_SECRET = os.environ["GMAIL_CLIENT_SECRET"]
GMAIL_REFRESH_TOKEN = os.environ["GMAIL_REFRESH_TOKEN"]
SENDER_EMAIL_FILTER = os.environ["SENDER_EMAIL_FILTER"]
GREEN_API_ID_INSTANCE = os.environ["GREEN_API_ID_INSTANCE"].strip()
GREEN_API_TOKEN = os.environ["GREEN_API_TOKEN"].strip()
WHATSAPP_CHAT_ID = os.environ["WHATSAPP_CHAT_ID"].strip()
# Dashboard shows the exact host (e.g. https://7107.api.greenapi.com). Fallback: first 4 digits of instance id.
GREEN_API_URL = (
    os.environ.get("GREEN_API_URL", "").strip().rstrip("/")
    or f"https://{GREEN_API_ID_INSTANCE[:4]}.api.greenapi.com"
)
GREEN_API_MEDIA_URL = (
    os.environ.get("GREEN_API_MEDIA_URL", "").strip().rstrip("/")
    or "https://media.green-api.com"
)

MAX_EMAILS_PER_RUN = 5
MAX_ATTACHMENTS_PER_EMAIL = 5
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024  # 20 MB
DEDUP_LABEL = "Forwarded-WA"
TOKEN_URI = "https://oauth2.googleapis.com/token"


# ─── Gmail auth ─────────────────────────────────────────────────────────────
def get_gmail_service():
    creds = Credentials(
        token=None,
        refresh_token=GMAIL_REFRESH_TOKEN,
        client_id=GMAIL_CLIENT_ID,
        client_secret=GMAIL_CLIENT_SECRET,
        token_uri=TOKEN_URI,
    )
    return build("gmail", "v1", credentials=creds)


# ─── Gmail helpers ──────────────────────────────────────────────────────────
def get_or_create_label(service):
    results = service.users().labels().list(userId="me").execute()
    for label in results.get("labels", []):
        if label["name"] == DEDUP_LABEL:
            return label["id"]

    body = {
        "name": DEDUP_LABEL,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show",
    }
    created = service.users().labels().create(userId="me", body=body).execute()
    print(f'Created Gmail label "{DEDUP_LABEL}"')
    return created["id"]


def find_new_emails(service):
    q = f"from:{SENDER_EMAIL_FILTER} -label:{DEDUP_LABEL} newer_than:2d"
    result = (
        service.users()
        .messages()
        .list(userId="me", q=q, maxResults=MAX_EMAILS_PER_RUN)
        .execute()
    )
    return result.get("messages", [])


def strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_body(parts_or_payload) -> str:
    """Recursively extract text/plain (preferred) or text/html from MIME parts."""
    plain, html = "", ""

    def walk(part):
        nonlocal plain, html
        mime = part.get("mimeType", "")
        filename = part.get("filename") or ""
        # Skip attachment parts when collecting body text
        if filename:
            for sub in part.get("parts", []):
                walk(sub)
            return
        data = part.get("body", {}).get("data")
        if data:
            decoded = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            if mime == "text/plain":
                plain += decoded
            elif mime == "text/html":
                html += decoded
        for sub in part.get("parts", []):
            walk(sub)

    walk(parts_or_payload)
    return plain if plain else strip_html(html) if html else ""


def _collect_attachment_parts(payload) -> list:
    """Collect MIME parts that look like file attachments (have a filename)."""
    found = []

    def walk(part):
        filename = (part.get("filename") or "").strip()
        body = part.get("body", {})
        if filename and (body.get("attachmentId") or body.get("data")):
            found.append(part)
        for sub in part.get("parts", []):
            walk(sub)

    walk(payload)
    return found


def _download_attachment(service, msg_id: str, part: dict) -> Optional[dict]:
    filename = part.get("filename") or "attachment"
    mime = part.get("mimeType") or "application/octet-stream"
    body = part.get("body", {})
    size = int(body.get("size") or 0)

    if size and size > MAX_ATTACHMENT_BYTES:
        print(f'  ⚠ Skipping oversized attachment "{filename}" ({size} bytes)')
        return None

    if body.get("data"):
        data = base64.urlsafe_b64decode(body["data"])
    elif body.get("attachmentId"):
        att = (
            service.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=msg_id, id=body["attachmentId"])
            .execute()
        )
        data = base64.urlsafe_b64decode(att["data"])
    else:
        return None

    if len(data) > MAX_ATTACHMENT_BYTES:
        print(f'  ⚠ Skipping oversized attachment "{filename}" ({len(data)} bytes)')
        return None

    return {"filename": filename, "mimeType": mime, "data": data}


def get_email_content(service, msg_id: str) -> dict:
    msg = (
        service.users()
        .messages()
        .get(userId="me", id=msg_id, format="full")
        .execute()
    )
    headers = {h["name"].lower(): h["value"] for h in msg["payload"]["headers"]}
    body = _extract_body(msg["payload"])

    attachments = []
    for part in _collect_attachment_parts(msg["payload"])[:MAX_ATTACHMENTS_PER_EMAIL]:
        downloaded = _download_attachment(service, msg_id, part)
        if downloaded:
            attachments.append(downloaded)

    return {
        "from": headers.get("from", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "body": body[:3000],
        "attachments": attachments,
    }


def label_email(service, msg_id: str, label_id: str):
    service.users().messages().modify(
        userId="me", id=msg_id, body={"addLabelIds": [label_id]}
    ).execute()


# ─── Green API ──────────────────────────────────────────────────────────────
def send_whatsapp(chat_id: str, message: str):
    url = (
        f"{GREEN_API_URL}"
        f"/waInstance{GREEN_API_ID_INSTANCE}"
        f"/sendMessage/{GREEN_API_TOKEN}"
    )
    resp = requests.post(url, json={"chatId": chat_id, "message": message}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def send_whatsapp_file(chat_id: str, filename: str, data: bytes, caption: str = ""):
    url = (
        f"{GREEN_API_MEDIA_URL}"
        f"/waInstance{GREEN_API_ID_INSTANCE}"
        f"/sendFileByUpload/{GREEN_API_TOKEN}"
    )
    files = {"file": (filename, BytesIO(data))}
    form = {"chatId": chat_id, "fileName": filename}
    if caption:
        form["caption"] = caption

    resp = requests.post(url, data=form, files=files, timeout=120)
    resp.raise_for_status()
    return resp.json()


def format_message(email: dict) -> str:
    attachment_note = ""
    if email.get("attachments"):
        names = ", ".join(a["filename"] for a in email["attachments"])
        attachment_note = f"\n\n📎 *Attachments:* {names}"

    return (
        "📧 *Email Forwarded*\n"
        "\n"
        f"*From:* {email['from']}\n"
        f"*Date:* {email['date']}\n"
        f"*Subject:* {email['subject']}\n"
        "\n"
        "───── Body ─────\n"
        "\n"
        f"{email['body'] or '(empty)'}"
        f"{attachment_note}"
    )


# ─── Main ───────────────────────────────────────────────────────────────────
def main():
    print("Starting email-to-whatsapp forwarding run...")

    service = get_gmail_service()
    label_id = get_or_create_label(service)
    messages = find_new_emails(service)

    if not messages:
        print("No new emails found. Done.")
        return

    print(f"Found {len(messages)} new email(s) to forward.")

    sent = 0
    for msg in messages:
        try:
            email = get_email_content(service, msg["id"])
            text = format_message(email)
            send_whatsapp(WHATSAPP_CHAT_ID, text)
            print(f'  ✓ Text sent: "{email["subject"]}"')

            att_ok = 0
            attachments = email.get("attachments", [])
            for att in attachments:
                try:
                    print(f'  → Sending attachment: {att["filename"]}')
                    send_whatsapp_file(
                        WHATSAPP_CHAT_ID,
                        att["filename"],
                        att["data"],
                        caption=att["filename"],
                    )
                    att_ok += 1
                    time.sleep(2)  # avoid Green API rate limits between media sends
                except Exception as att_err:
                    print(
                        f'  ✗ Attachment failed ({att["filename"]}): {att_err}',
                        file=sys.stderr,
                    )

            # Label after text succeeds so retries don't spam duplicate text.
            # Attachment failures are reported but do not block labeling.
            label_email(service, msg["id"], label_id)
            sent += 1
            print(
                f'✓ Done: "{email["subject"]}" '
                f'(attachments {att_ok}/{len(attachments)})'
            )
        except Exception as err:
            # Don't label if text send failed — retry next run
            print(f'✗ Failed to forward message {msg["id"]}: {err}', file=sys.stderr)

    print(f"Done. Forwarded {sent}/{len(messages)} emails.")


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        print(f"Fatal error: {err}", file=sys.stderr)
        sys.exit(1)
