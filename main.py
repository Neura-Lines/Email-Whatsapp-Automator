import os
import sys
import re
import time
import imaplib
import email
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from html import unescape
from io import BytesIO
from typing import Optional

from dotenv import load_dotenv
import requests

load_dotenv()

# ─── Config ─────────────────────────────────────────────────────────────────
EMAIL_ADDRESS = os.environ["EMAIL_ADDRESS"].strip()
EMAIL_PASSWORD = os.environ["EMAIL_PASSWORD"].strip()
EMAIL_IMAP_HOST = os.environ.get("EMAIL_IMAP_HOST", "imap.titan.email").strip()
EMAIL_IMAP_PORT = int(os.environ.get("EMAIL_IMAP_PORT", "993"))

# Optional: if set, only forward mail From this address. Leave empty to forward
# all new inbox mail in the feedback mailbox.
SENDER_EMAIL_FILTER = os.environ.get("SENDER_EMAIL_FILTER", "").strip()

GREEN_API_ID_INSTANCE = os.environ["GREEN_API_ID_INSTANCE"].strip()
GREEN_API_TOKEN = os.environ["GREEN_API_TOKEN"].strip()
WHATSAPP_CHAT_ID = os.environ["WHATSAPP_CHAT_ID"].strip()
GREEN_API_URL = (
    os.environ.get("GREEN_API_URL", "").strip().rstrip("/")
    or f"https://{GREEN_API_ID_INSTANCE[:4]}.api.greenapi.com"
)
GREEN_API_MEDIA_URL = (
    os.environ.get("GREEN_API_MEDIA_URL", "").strip().rstrip("/")
    or "https://media.green-api.com"
)

MAX_EMAILS_PER_RUN = int(os.environ.get("MAX_EMAILS_PER_RUN", "5"))
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "2"))
MAX_ATTACHMENTS_PER_EMAIL = 5
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024  # 20 MB
# After a successful text send, message is moved here so it won't be resent.
DEDUP_FOLDER = "Forwarded-WA"


# ─── Helpers ────────────────────────────────────────────────────────────────
def decode_mime_header(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def get_body(msg: email.message.Message) -> str:
    plain, html = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            disposition = (part.get_content_disposition() or "").lower()
            if disposition == "attachment":
                continue
            filename = part.get_filename()
            if filename:
                continue
            ctype = part.get_content_type()
            try:
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace")
            except Exception:
                continue
            if ctype == "text/plain":
                plain += text
            elif ctype == "text/html":
                html += text
    else:
        try:
            payload = msg.get_payload(decode=True) or b""
            charset = msg.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                html = text
            else:
                plain = text
        except Exception:
            pass
    return (plain if plain else strip_html(html) if html else "")[:3000]


def get_attachments(msg: email.message.Message) -> list:
    attachments = []
    for part in msg.walk():
        filename = part.get_filename()
        disposition = (part.get_content_disposition() or "").lower()
        if not filename and disposition != "attachment":
            continue
        filename = decode_mime_header(filename) or "attachment"
        try:
            data = part.get_payload(decode=True) or b""
        except Exception:
            continue
        if not data:
            continue
        if len(data) > MAX_ATTACHMENT_BYTES:
            print(f'  Skipping oversized attachment "{filename}" ({len(data)} bytes)')
            continue
        attachments.append(
            {
                "filename": filename,
                "mimeType": part.get_content_type() or "application/octet-stream",
                "data": data,
            }
        )
        if len(attachments) >= MAX_ATTACHMENTS_PER_EMAIL:
            break
    return attachments


def parse_email(raw_bytes: bytes) -> dict:
    msg = email.message_from_bytes(raw_bytes)
    return {
        "from": decode_mime_header(msg.get("From")),
        "subject": decode_mime_header(msg.get("Subject")),
        "date": decode_mime_header(msg.get("Date")),
        "body": get_body(msg),
        "attachments": get_attachments(msg),
    }


# ─── IMAP ───────────────────────────────────────────────────────────────────
def connect_imap() -> imaplib.IMAP4_SSL:
    imap = imaplib.IMAP4_SSL(EMAIL_IMAP_HOST, EMAIL_IMAP_PORT)
    imap.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
    return imap


def ensure_dedup_folder(imap: imaplib.IMAP4_SSL):
    typ, folders = imap.list()
    if typ == "OK" and folders:
        for item in folders:
            line = item.decode(errors="replace") if isinstance(item, bytes) else str(item)
            if DEDUP_FOLDER in line:
                return
    typ, _ = imap.create(DEDUP_FOLDER)
    if typ == "OK":
        print(f'Created IMAP folder "{DEDUP_FOLDER}"')
    else:
        # Folder may already exist under a different listing format
        print(f'Note: create "{DEDUP_FOLDER}" returned {typ}')


def find_new_uids(imap: imaplib.IMAP4_SSL) -> list:
    typ, _ = imap.select("INBOX")
    if typ != "OK":
        raise RuntimeError("Could not select INBOX")

    if LOOKBACK_DAYS > 0:
        since = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%d-%b-%Y")
        if SENDER_EMAIL_FILTER:
            criteria = f'(FROM "{SENDER_EMAIL_FILTER}" SINCE {since})'
        else:
            criteria = f"(SINCE {since})"
    else:
        criteria = f'(FROM "{SENDER_EMAIL_FILTER}")' if SENDER_EMAIL_FILTER else "ALL"

    print(f"IMAP search: {criteria}")
    typ, data = imap.uid("SEARCH", None, criteria)
    if typ != "OK" or not data or not data[0]:
        return []

    uids = data[0].split()
    # Newest first, then cap
    uids = list(reversed(uids))[:MAX_EMAILS_PER_RUN]
    return uids


def fetch_email(imap: imaplib.IMAP4_SSL, uid: bytes) -> dict:
    typ, data = imap.uid("FETCH", uid, "(RFC822)")
    if typ != "OK" or not data or not data[0]:
        raise RuntimeError(f"Failed to fetch UID {uid!r}")
    raw = data[0][1]
    if not isinstance(raw, (bytes, bytearray)):
        raise RuntimeError(f"Unexpected fetch payload for UID {uid!r}")
    return parse_email(bytes(raw))


def mark_forwarded(imap: imaplib.IMAP4_SSL, uid: bytes):
    """Move message to Forwarded-WA so the next run won't pick it again."""
    typ, _ = imap.uid("COPY", uid, DEDUP_FOLDER)
    if typ != "OK":
        raise RuntimeError(f"Failed to copy UID {uid!r} to {DEDUP_FOLDER}")
    imap.uid("STORE", uid, "+FLAGS", r"(\Deleted)")
    imap.expunge()


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


def format_message(parsed: dict) -> str:
    attachment_note = ""
    if parsed.get("attachments"):
        names = ", ".join(a["filename"] for a in parsed["attachments"])
        attachment_note = f"\n\n📎 *Attachments:* {names}"

    return (
        "📧 *Email Forwarded*\n"
        "\n"
        f"*From:* {parsed['from']}\n"
        f"*Date:* {parsed['date']}\n"
        f"*Subject:* {parsed['subject']}\n"
        "\n"
        "───── Body ─────\n"
        "\n"
        f"{parsed['body'] or '(empty)'}"
        f"{attachment_note}"
    )


# ─── Main ───────────────────────────────────────────────────────────────────
def main():
    print("Starting email-to-whatsapp forwarding run (IMAP / Titan)...")
    print(f"Monitoring inbox: {EMAIL_ADDRESS}")
    print(f"IMAP: {EMAIL_IMAP_HOST}:{EMAIL_IMAP_PORT}")
    if SENDER_EMAIL_FILTER:
        print(f"Sender filter (optional): {SENDER_EMAIL_FILTER}")
    else:
            print("Sender filter: (none) — forwarding all new inbox mail")
    print(f"Max emails this run: {MAX_EMAILS_PER_RUN}")
    print(f"Lookback days: {LOOKBACK_DAYS if LOOKBACK_DAYS > 0 else 'all'}")
    print(f"WhatsApp target: {WHATSAPP_CHAT_ID}")

    imap = connect_imap()
    try:
        ensure_dedup_folder(imap)
        uids = find_new_uids(imap)

        if not uids:
            print("No new emails found. Done.")
            return

        print(f"Found {len(uids)} new email(s) to forward.")

        sent = 0
        for uid in uids:
            uid_str = uid.decode() if isinstance(uid, bytes) else str(uid)
            try:
                parsed = fetch_email(imap, uid)
                text = format_message(parsed)
                send_whatsapp(WHATSAPP_CHAT_ID, text)
                print(f'  Text sent: "{parsed["subject"]}"')

                att_ok = 0
                attachments = parsed.get("attachments", [])
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
                        time.sleep(2)
                    except Exception as att_err:
                        print(
                            f'  Attachment failed ({att["filename"]}): {att_err}',
                            file=sys.stderr,
                        )

                # Move out of INBOX only after text succeeded (retry-safe for text).
                mark_forwarded(imap, uid)
                sent += 1
                print(
                    f'Done: "{parsed["subject"]}" '
                    f"(attachments {att_ok}/{len(attachments)})"
                )
            except Exception as err:
                print(f"Failed to forward UID {uid_str}: {err}", file=sys.stderr)

        print(f"Done. Forwarded {sent}/{len(uids)} emails.")
    finally:
        try:
            imap.logout()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        print(f"Fatal error: {err}", file=sys.stderr)
        sys.exit(1)
