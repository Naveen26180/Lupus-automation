# Deployment — Vercel (event-driven, no credit card, $0 forever)

This guide deploys the bot as an **event-driven webhook** on Vercel's free
Hobby plan. No server runs 24/7 — the bot wakes up only when a recruiter sends
a resume to Telegram, processes it, replies, and goes back to sleep.

**Why this path:** no credit card needed (sign up with your GitHub account),
free forever, nothing to keep alive. The trade-off: Vercel cannot run a local
LLM — this is the Groq-only deployment.

---

## 0. How it works

```
Recruiter sends resume to Telegram bot
        │
        ▼
Telegram webhook ──POST──▶ Vercel function (api/webhook.py)
                                  │
                                  ├── downloads the file from Telegram
                                  ├── runs the exact same Pipeline
                                  │     (parse → Groq → classifier → validator
                                  │      → Drive → Sheets)
                                  └── replies to the recruiter via Bot API
```

- The code for this lives in `api/webhook.py` and reuses the **same
  `core/pipeline.py`** — zero classification logic changed.
- Setting a webhook **disables polling** for your bot token, so your local bot
  must be stopped while the webhook is live.

---

## 1. Push the code (already done for you)

The webhook code (`api/webhook.py`), `vercel.json`, and this guide are already
in the repo. If you've pulled the latest, you're ready.

---

## 2. Create the Vercel project (no card)

1. Go to **vercel.com** → **Sign up** → **Continue with GitHub** (authorize).
   No credit card is requested on the free Hobby plan.
2. Click **Add New… → Project**.
3. Find **Lupus-automation** in the list → **Import**.
4. **Framework Preset:** leave as detected (Python). **Root Directory:**
   leave as `/` (the repo root *is* the project).
5. Vercel auto-installs from `requirements.txt` (no `pyproject.toml` in
   the repo — its presence makes Vercel's uv installer demand a
   `[project]` table) and reads `vercel.json` for the function config
   (300s max duration). `index.py` re-exports the FastAPI webhook app at
   a *default* entrypoint, so Vercel's detector ignores `main.py` (the
   polling entry point) and serves the webhook instead.
6. **Deploy** (skip env vars for now — we add them next, then redeploy).

After the first deploy you get a URL like:
`https://lupus-automation-abc123.vercel.app`

---

## 3. Add the environment variables

In the Vercel dashboard: **Project → Settings → Environment Variables**.
Add each of these (they apply to all environments):

| Key | Value |
|---|---|
| `BOT_TOKEN` | Your Telegram bot token |
| `AI_PROVIDER` | `groq` |
| `GROQ_API_KEY` | Your Groq API key |
| `GOOGLE_DRIVE_CREDENTIALS_JSON` | **The full contents** of `gen-lang-client-….json` (open the file, copy everything, paste here) |
| `GOOGLE_SHEET_ID` | From your `.env` |
| `INCOMING_FOLDER_ID` | From your `.env` |
| `PROCESSED_FOLDER_ID` | From your `.env` |
| `DUPLICATE_FOLDER_ID` | From your `.env` |
| `REJECTED_FOLDER_ID` | From your `.env` |
| `LOG_LEVEL` | `INFO` (never DEBUG — PII in payloads) |
| `ENRICHMENT_ENABLED` | `false` (keep the scraper paused — it can exceed the function window) |
| `AI_CLASSIFICATION_ENABLED` | `false` (flip later if you want Pass 2) |

⚠️ `GOOGLE_DRIVE_CREDENTIALS` must be **empty** — the webhook materializes the
JSON from `GOOGLE_DRIVE_CREDENTIALS_JSON` into a temp file at startup (that's
the ephemeral-disk handling in `api/webhook.py`).

Then trigger a redeploy: **Deployments → … → Redeploy**.

---

## 4. Point Telegram at the webhook

From your **local** project folder (Git Bash), register the URL:

```bash
python deploy/set_webhook.py https://lupus-automation-abc123.vercel.app/
```

You should see `Webhook was set` plus the webhook info. The webhook path is
`/api/webhook` but `vercel.json` rewrites every path there, so the root URL
works.

> 🛑 **Stop your local bot first.** A polling instance and a webhook cannot
> coexist on the same token — and with the webhook set, local polling will fail
> with a conflict error anyway.

---

## 5. Test it

Send a resume to your bot on Telegram. Expected flow:
1. Bot replies `⏳ Processing *resume.pdf*...`
2. ~10–20 seconds later, that message is edited to the extraction summary.

Check Vercel logs if anything looks off: **Project → Logs** (function
invocations + errors).

---

## 6. Changing credentials after deployment — YES

**All credentials are changeable at any time, no code change, no redeploy of
code** — but a **redeploy is required** for the new value to take effect:

1. **Vercel dashboard → Project → Settings → Environment Variables**.
2. Edit the value (e.g. new `BOT_TOKEN` from BotFather, new `GROQ_API_KEY`,
   new folder IDs, new service-account JSON).
3. **Deployments → Redeploy** (the latest build). Takes ~1 minute.

That's it — the new credential is live. You can even have **different values
per environment** (Production / Preview / Development) if you ever want a
staging bot.

---

## 7. Going back to polling (e.g. moving to a server later)

```bash
python deploy/set_webhook.py --delete
```

Then run the bot normally (`python main.py`). No other changes needed — the
polling path (`integrations/telegram/`) was never touched.

---

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| Bot never replies | Check Vercel Logs — most common cause: missing env var (the function exits at `load_settings`) |
| `Conflict: terminated by other getUpdates request` | A polling instance is still running — stop it (webhook is set) |
| `Bad Request: webhook can only be used in combination with...` | Wrong URL — must be HTTPS (Vercel URLs are) |
| Google 403 errors | Service-account email not shared as **Editor** on the Sheet + 4 Drive folders |
| Slow first reply after idle | Vercel cold start (~2–5s for Python) — normal, and cheap |
| Webhook not firing at all | Re-run `python deploy/set_webhook.py <url>` and check `getWebhookInfo` shows `pending_update_count` moving |

---

## 9. Cost recap

- **Vercel Hobby:** $0/month. Function time is well within the free allowance
  (4 CPU-hours/month ≈ hundreds of resumes). Non-commercial personal use only.
- **Groq:** free tier (rate-limited) — fine for this traffic.
- **Telegram / Google:** free.
- **No credit card anywhere.**

## 10. What this deployment gives up (be honest with yourself)

- ❌ **No local LLM ever** — serverless has no GPU and no persistent process.
  If the local-model goal matters more, this is the wrong host.
- ❌ **No persistent disk** — `audit/*.json` forensic reports and the company
  cache live only for the duration of each invocation. If you need the audit
  trail, they should be written to Drive instead.
- ⚠️ **Hobby = personal use**, pauses at limits (unlikely at this volume).

If those trade-offs are acceptable, this is the cheapest, simplest way to run
the bot.
