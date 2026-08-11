# Email → WhatsApp Forwarder (Titan IMAP)

Forwards feedback emails from a **Titan Email** inbox to WhatsApp via [Green API](https://green-api.com), scheduled through GitHub Actions (**12:00 PM and 12:00 AM Pakistan time** + manual trigger).

**Stack:** Python 3.12, IMAP (`imaplib`), `requests` (Green API), `python-dotenv`, GitHub Actions cron.

## Which accounts do what?

| Role | Account | What happens |
| --- | --- | --- |
| Feedback inbox | Titan mailbox (e.g. support@…) | Users send feedback **here**. This inbox is read over IMAP. |
| IMAP login | Same Titan mailbox email + password | Script logs in like a normal email app (`imap.titan.email:993`). |
| Optional sender filter | `SENDER_EMAIL_FILTER` | Leave empty to forward **all** new inbox mail. Set only if you want mail from one From-address. |
| WhatsApp sender | Number linked to Green API | Sends the WhatsApp messages. |
| WhatsApp receiver | `WHATSAPP_CHAT_ID` | Individual `@c.us` or group `@g.us` that receives forwards. |

No Google Cloud / Gmail OAuth is required for this setup.

## Setup Order

### 1. Titan Email (IMAP)

1. Confirm you can log in to Titan webmail for the feedback address.
2. Use IMAP settings from [Titan docs](https://support.titan.email/hc/en-us/articles/900000215446-Configure-Titan-on-other-apps-using-IMAP-POP):
   - Host: `imap.titan.email`
   - Port: `993`
   - Encryption: SSL/TLS
   - Username: full email address
   - Password: the mailbox password (same as webmail)
3. If login fails from an app/script, check Titan settings for third-party / IMAP access if available.

### 2. Green API Setup

1. Sign up at [green-api.com](https://green-api.com) and create an instance.
2. Scan the QR code with the WhatsApp number that should **send** messages.
3. Note your **Instance ID** and **API Token** from the dashboard.
4. **Free tier limit:** Only 3 unique chats/month. Reuse the same test chat/group while developing.

### 3. Local Testing

```bash
cp .env.example .env
# Fill EMAIL_ADDRESS, EMAIL_PASSWORD, Green API + WhatsApp values.
# Leave SENDER_EMAIL_FILTER empty for all feedback mail.
pip install -r requirements.txt
python main.py
```

On start, the script prints `Monitoring inbox: ...` — confirm it shows the Titan feedback address.

### 4. Push to GitHub & Add Secrets

1. Push this repo to GitHub.
2. Settings → Secrets and variables → Actions → add:
   - `EMAIL_ADDRESS`
   - `EMAIL_PASSWORD`
   - `EMAIL_IMAP_HOST` (optional; default `imap.titan.email`)
   - `EMAIL_IMAP_PORT` (optional; default `993`)
   - `SENDER_EMAIL_FILTER` (optional)
   - `GREEN_API_ID_INSTANCE`
   - `GREEN_API_TOKEN`
   - `GREEN_API_URL` (optional)
   - `GREEN_API_MEDIA_URL` (optional)
   - `WHATSAPP_CHAT_ID` — individual: `923001234567@c.us` | group: `120363012345678901@g.us`

Remove any old Gmail OAuth secrets (`GMAIL_*`) if they were added earlier — they are unused now.

### 5. Test End-to-End

1. Send a test feedback email **to** the Titan feedback mailbox.
2. Run locally or Actions → **Forward Emails to WhatsApp** → **Run workflow**.
3. Confirm WhatsApp receives text (+ attachments if any).

## How It Works

- Connects to Titan over IMAP SSL and searches INBOX for messages from the last **2 days** (max **5** per run).
- Optional `FROM` filter if `SENDER_EMAIL_FILTER` is set.
- Sends email text + attachments to WhatsApp via Green API.
- After a successful text send, moves the message to an IMAP folder named `Forwarded-WA` (created automatically) so it won’t be forwarded again.

## Known Limitations

- **GitHub Actions disables scheduled workflows after 60 days of repo inactivity.** Push a commit or trigger a manual run to keep it active.
- **Green API free tier:** 3 unique chats/month. Stick to one test chat/group while developing.
- You need the **mailbox password** for IMAP (or an app password if Titan provides one). Store it only in `.env` / GitHub Secrets — never commit it.
