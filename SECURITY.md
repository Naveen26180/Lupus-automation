# Security

Security notes for the Resume Processing Bot. Read this before deploying.

## Secrets — what exists and where it lives

| Secret | Where | Never commit |
|---|---|---|
| `BOT_TOKEN` (Telegram) | `resume_bot/.env` / server env var | ✅ |
| `GROQ_API_KEY` / `GEMINI_API_KEY` | `resume_bot/.env` / server env var | ✅ |
| `GOOGLE_DRIVE_CREDENTIALS` — **service-account private key JSON** | file on disk; path in `.env` | ✅ |

All credentials are read from the environment at startup (`config/settings.py` →
`load_dotenv` + `os.getenv`). **Nothing is hardcoded in code.** To rotate any
credential: update `.env` (or the platform env var), restart the process. No
redeploy required.

## ⚠️ The service-account key file

`gen-lang-client-*.json` is a **live Google service-account private key**. Anyone
holding it can read your Drive folders and the candidate Google Sheet.

- It is excluded from git by `.gitignore` (repo-level and root-level safety net).
- **If this key was ever pushed to a repository, rotate it immediately**:
  Google Cloud Console → IAM & Admin → Service Accounts → select the account →
  Keys → delete the exposed key → create a new one → update `GOOGLE_DRIVE_CREDENTIALS`.
- On the server, store the JSON in a location only the app user can read
  (e.g. `/etc/resumebot/credentials.json` or a secrets manager), not inside the
  repo checkout.

## Candidate PII in runtime artifacts

The pipeline handles resumes containing names, emails, phone numbers and
employment history. These artifacts are **never committed** and should be
cleaned periodically:

| Artifact | Contains PII | Notes |
|---|---|---|
| `logs/app.log` | Yes (if a third-party SDK leaks payloads) | SDK loggers are silenced to WARNING; file handler still logs app-level DEBUG. Clear on deploy. |
| `raw_ai_response.json` (debug dump) | Yes — full Pass 1 evidence | **Only written when `LOG_LEVEL=DEBUG`.** Never set DEBUG in production. |
| `audit/` (JSON + MD forensic reports) | Yes | Written per resume; gitignored. Purge on a schedule. |
| `data/` (company cache + classification log) | Company names, prompts | Local state; gitignored; regenerated automatically. |

## Production checklist

1. `LOG_LEVEL=INFO` (never DEBUG — DEBUG enables the PII debug dump).
2. `AI_CLASSIFICATION_ENABLED=false` unless you have reviewed Pass 2 output
   on real resumes.
3. Git repo must be **private**. `.env`, `*.json`, `*.pdf`, `*.db`, `*.log`,
   `audit/`, `data/`, `logs/`, venvs and caches are all ignored — verify with
   `git status` before pushing.
4. Telegram: only one process may poll a bot token. After rotating `BOT_TOKEN`,
   revoke the old token in BotFather and stop old processes first.
5. Google permissions: the service account needs access only to the specific
   Drive folders + the one spreadsheet. Grant the minimum.
