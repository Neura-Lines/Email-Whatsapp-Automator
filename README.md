# Email → WhatsApp Forwarder

Forwards Gmail emails from a specific sender to WhatsApp via [Green API](https://green-api.com), scheduled through GitHub Actions (twice daily + manual trigger).

**Stack:** Python 3.12, `google-api-python-client` (Gmail API), `requests` (Green API), `python-dotenv`, GitHub Actions cron.

## Setup Order

### 1. Gmail OAuth2 Credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → create a project (or use existing).
2. Enable the **Gmail API** under APIs & Services → Library.
3. Create **OAuth 2.0 Client ID** (Desktop app) under APIs & Services → Credentials.
4. Go to **OAuth consent screen**:
   - **IMPORTANT:** Set publishing status to **"In production"**. The "Testing" mode expires refresh tokens every 7 days. "In production" + "unverified" is fine for personal use — Google just shows a warning screen during consent, which you only do once.
5. Get your refresh token using [OAuth Playground](https://developers.google.com/oauthplayground/):
   - Click the gear icon → check "Use your own OAuth credentials" → enter your Client ID and Secret.
   - Authorize scope: `https://www.googleapis.com/auth/gmail.modify`
   - Exchange authorization code for tokens → copy the **refresh token**.

### 2. Green API Setup

1. Sign up at [green-api.com](https://green-api.com) and create an instance.
2. Scan the QR code with your WhatsApp to link.
3. Note your **Instance ID** and **API Token** from the dashboard.
4. **Free tier limit:** Only 3 unique chats/month. Reuse the same test chat during development to avoid hitting this cap.

### 3. Local Testing

```bash
cp .env.example .env
# Fill in all values in .env
pip install -r requirements.txt
python main.py
```

### 4. Push to GitHub & Add Secrets

1. Push this repo to GitHub.
2. Go to Settings → Secrets and variables → Actions → add each secret:
   - `GMAIL_CLIENT_ID`
   - `GMAIL_CLIENT_SECRET`
   - `GMAIL_REFRESH_TOKEN`
   - `SENDER_EMAIL_FILTER` (e.g. `notifications@example.com`)
   - `GREEN_API_ID_INSTANCE`
   - `GREEN_API_TOKEN`
   - `GREEN_API_URL` (optional — exact API URL from Green API dashboard)
   - `WHATSAPP_CHAT_ID` — individual: `923001234567@c.us` | group: `120363012345678901@g.us`

### 5. Test End-to-End

1. Go to Actions tab → "Forward Emails to WhatsApp" → Run workflow.
2. Check the logs and your WhatsApp for the forwarded message.

## How It Works

- Searches Gmail for emails `from:<sender> -label:Forwarded-WA newer_than:2d` (max 5 per run).
- Sends each email's content to WhatsApp via Green API's `sendMessage` endpoint.
- Labels the email `Forwarded-WA` **only after** a successful send, so failed emails retry next run.
- Runs on cron (twice daily) via GitHub Actions. Also supports `workflow_dispatch` for manual runs.

## Known Limitations

- **GitHub Actions disables scheduled workflows after 60 days of repo inactivity.** Push a commit or trigger a manual run to keep it active.
- **Green API free tier:** 3 unique chats/month. Stick to one test chat while developing.
