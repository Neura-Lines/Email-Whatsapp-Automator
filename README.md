# Email → WhatsApp Forwarder

Forwards feedback emails received in a Gmail mailbox to WhatsApp via [Green API](https://green-api.com), scheduled through GitHub Actions (twice daily + manual trigger).

**Stack:** Python 3.12, `google-api-python-client` (Gmail API), `requests` (Green API), `python-dotenv`, GitHub Actions cron.

## Which accounts do what?

| Role | Account | What happens |
| --- | --- | --- |
| Feedback inbox | Your feedback Gmail mailbox | Users send feedback **here**. This inbox is monitored. |
| OAuth authorize login | **Must be that same feedback mailbox** | When generating the refresh token, sign in as the feedback inbox. |
| GCP project owner | Company/personal Google Cloud account | Can create the Cloud project/client IDs, but does **not** decide which inbox is read. |
| Optional sender filter | `SENDER_EMAIL_FILTER` | Leave empty to forward **all** new inbox mail. Set only if you want mail from one From-address. |
| WhatsApp sender | Number linked to Green API | Sends the WhatsApp messages. |
| WhatsApp receiver | `WHATSAPP_CHAT_ID` | Individual `@c.us` or group `@g.us` that receives forwards. |

### OAuth: which Google account for what? (read this carefully)

These are **two different choices** and they do **not** have to be the same Google account:

1. **Who creates the Google Cloud project?**  
   Any admin/company Google account is fine. This account only owns the GCP project, OAuth client ID, and client secret.

2. **Whose inbox gets accessed?**  
   Whichever account you **sign in as** during OAuth Playground authorization.  
   That **must** be the feedback mailbox (the inbox that receives user emails).

3. **Common correct setup**  
   - Create GCP project with company account  
   - Create OAuth client in that project  
   - In OAuth Playground, authorize while logged into the **feedback mailbox**  
   - If app is in Testing mode, add the feedback mailbox as a Test user first  

4. **Common mistake**  
   Creating GCP + authorizing with your personal Gmail → script monitors your personal inbox, not the feedback mailbox.

5. **How to verify**  
   After setup, run the script and check the log line `Monitoring inbox: ...` — it must show the feedback mailbox.

## Setup Order

### 1. Gmail OAuth2 Credentials (feedback mailbox)

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → create a project (company account is fine).
2. Enable the **Gmail API** under APIs & Services → Library.
3. Create **OAuth 2.0 Client ID**:
   - For local OAuth Playground token generation, use **Web application** and add redirect URI: `https://developers.google.com/oauthplayground`
4. Go to **OAuth consent screen / Audience**:
   - If status is **Testing**, add the feedback mailbox as a **Test user**.
   - Prefer **"In production"** later so refresh tokens don’t expire every 7 days. Unverified is fine for internal use.
5. Get your refresh token using [OAuth Playground](https://developers.google.com/oauthplayground/):
   - Gear icon → **Use your own OAuth credentials** → paste Client ID/Secret.
   - Scope: `https://www.googleapis.com/auth/gmail.modify`
   - Click **Authorize APIs** and sign in as the **feedback mailbox**.
   - Exchange code for tokens → copy the **refresh token**.

### 2. Green API Setup

1. Sign up at [green-api.com](https://green-api.com) and create an instance.
2. Scan the QR code with the WhatsApp number that should **send** messages.
3. Note your **Instance ID** and **API Token** from the dashboard.
4. **Free tier limit:** Only 3 unique chats/month. Reuse the same test chat/group while developing.

### 3. Local Testing

```bash
cp .env.example .env
# Fill values. Leave SENDER_EMAIL_FILTER empty for all feedback mail.
pip install -r requirements.txt
python main.py
```

On start, the script prints `Monitoring inbox: ...` — confirm it shows your feedback mailbox.

### 4. Push to GitHub & Add Secrets

1. Push this repo to GitHub.
2. Settings → Secrets and variables → Actions → add:
   - `GMAIL_CLIENT_ID`
   - `GMAIL_CLIENT_SECRET`
   - `GMAIL_REFRESH_TOKEN` (authorized as the feedback mailbox)
   - `SENDER_EMAIL_FILTER` (optional; leave empty/unset for all inbox mail)
   - `GREEN_API_ID_INSTANCE`
   - `GREEN_API_TOKEN`
   - `GREEN_API_URL` (optional)
   - `GREEN_API_MEDIA_URL` (optional)
   - `WHATSAPP_CHAT_ID` — individual: `923001234567@c.us` | group: `120363012345678901@g.us`

### 5. Test End-to-End

1. Send a test feedback email **to** the feedback mailbox.
2. Run locally or Actions → **Forward Emails to WhatsApp** → **Run workflow**.
3. Confirm WhatsApp receives text (+ attachments if any).

## How It Works

- Uses the authorized feedback mailbox and searches: `-label:Forwarded-WA newer_than:2d in:inbox` (max 5 per run).
- Optional `from:<SENDER_EMAIL_FILTER>` if that env var is set.
- Sends email text + attachments to WhatsApp via Green API.
- Labels each email `Forwarded-WA` after the text send succeeds (so retries don’t spam duplicate text).

## Known Limitations

- **GitHub Actions disables scheduled workflows after 60 days of repo inactivity.** Push a commit or trigger a manual run to keep it active.
- **Green API free tier:** 3 unique chats/month. Stick to one test chat/group while developing.
