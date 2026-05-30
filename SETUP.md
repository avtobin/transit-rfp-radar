# Transit RFP Radar — GitHub Actions Setup Guide

Estimated setup time: **20 minutes**. No subscriptions, no servers, completely free.

---

## What you'll end up with

A private GitHub repository that runs every morning at 7 AM Pacific,
searches all 13 transit agencies for new RFPs, and emails you a formatted digest.
The `data/seen_rfps.json` file in the repo acts as the memory — it tracks every
RFP already sent to you so you only ever see new ones.

---

## Prerequisites

- A free GitHub account (github.com)
- A Gmail address to send from (you'll create an App Password — takes 2 minutes)
- Your Anthropic API key (from console.anthropic.com)

---

## Step 1 — Create the repository

1. Go to github.com → click **+** (top right) → **New repository**
2. Name it: `transit-rfp-radar`
3. Set visibility to **Private** (keeps your API keys safer)
4. Do NOT initialize with a README — leave it empty
5. Click **Create repository**

---

## Step 2 — Upload the files

You have four files to upload. In your new empty repo:

1. Click **uploading an existing file** (or drag-and-drop)
2. Upload these files maintaining the folder structure:

```
transit-rfp-radar/
├── rfp_search.py
├── requirements.txt
├── data/
│   └── seen_rfps.json
└── .github/
    └── workflows/
        └── daily-rfp-search.yml
```

**Important:** GitHub's web uploader flattens folders. To keep the structure:
- Upload `rfp_search.py` and `requirements.txt` to the root
- For `.github/workflows/daily-rfp-search.yml`: in the repo, click
  **Add file → Create new file**, type `.github/workflows/daily-rfp-search.yml`
  as the filename, paste the contents, and commit
- For `data/seen_rfps.json`: same process — create new file,
  type `data/seen_rfps.json`, paste `[]`, and commit

---

## Step 3 — Create a Gmail App Password

GitHub Actions will send email via your Gmail account using SMTP.
Gmail requires an "App Password" (not your regular password) for this.

1. Go to **myaccount.google.com → Security**
2. Make sure **2-Step Verification is ON** (required for App Passwords)
3. Search for **"App Passwords"** in the search bar
4. Click **App Passwords**
5. Select app: **Mail** / Select device: **Other** → type "RFP Radar"
6. Click **Generate** → copy the 16-character password shown

Save this password — you'll need it in the next step.

---

## Step 4 — Add secrets to GitHub

Secrets are encrypted environment variables — GitHub never shows them again after you save.

1. In your repo → **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret** for each of these:

| Secret name        | Value                                      |
|--------------------|--------------------------------------------|
| `ANTHROPIC_API_KEY`| Your Anthropic API key (starts with sk-ant-) |
| `SMTP_USER`        | Your Gmail address (e.g. you@gmail.com)    |
| `SMTP_PASSWORD`    | The 16-character App Password from Step 3  |
| `DIGEST_TO_EMAIL`  | Email address to receive the digest        |

> `DIGEST_TO_EMAIL` can be the same as `SMTP_USER`, or a different address.

---

## Step 5 — Test it manually

Don't wait until 7 AM to find out if it works.

1. In your repo → **Actions** tab
2. Click **Daily Transit RFP Search** in the left sidebar
3. Click **Run workflow** → **Run workflow** (green button)
4. Watch the run — click into it to see live logs
5. Check your inbox — email should arrive within 2–3 minutes

If it succeeds, you'll see green checkmarks on every step and an email in your inbox.

---

## Step 6 — You're done

The workflow runs automatically at 7 AM Pacific every day.
After each run, GitHub commits the updated `data/seen_rfps.json` back to the repo
so the next run knows which RFPs have already been sent to you.

---

## Customizing

### Change the run time
Edit `.github/workflows/daily-rfp-search.yml`, line 6:
```yaml
- cron: "0 14 * * *"   # 14:00 UTC = 7 AM Pacific (adjust for your timezone)
```
Use [crontab.guru](https://crontab.guru) to find the right UTC time for your timezone.

### Add or remove agencies
Edit `rfp_search.py` — find the `AGENCIES` list near the top and add/remove entries:
```python
{"id": "myagency", "name": "My Agency Name", "portal": "myagency.gov/procurement"},
```

### Add or remove keywords
Edit `rfp_search.py` — find the `KEYWORDS` variable and update the search terms.

### Reset seen RFPs (get a fresh digest of everything)
Edit `data/seen_rfps.json` in the repo, replace the contents with `[]`, and commit.
The next run will treat all RFPs as new.

### Send to multiple emails
In `rfp_search.py`, find the `send_email` function and change:
```python
msg["To"] = to_address
```
to:
```python
recipients = [to_address, "colleague@example.com"]
msg["To"] = ", ".join(recipients)
server.sendmail(smtp_user, recipients, msg.as_string())
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Authentication error (Gmail) | Make sure you used the App Password, not your regular Gmail password |
| `ANTHROPIC_API_KEY` error | Check the secret name matches exactly (case-sensitive) |
| No email received | Check spam folder; verify `DIGEST_TO_EMAIL` secret is correct |
| JSON parse error in logs | Transient API issue — re-run manually; it self-corrects |
| Workflow not running at 7 AM | GitHub may delay scheduled workflows by up to 30 min under load |
| `git push` permission denied | Make sure the workflow has `permissions: contents: write` (already set) |

---

## Cost estimate

- **GitHub Actions**: Free (public repos unlimited; private repos get 2,000 min/month free — this workflow uses ~3 min/day = ~90 min/month)
- **Anthropic API**: ~$0.10–0.30 per daily run (13 agencies × ~$0.02 each using Sonnet)
- **Gmail SMTP**: Free
- **Total**: roughly **$3–9/month** in API costs only
