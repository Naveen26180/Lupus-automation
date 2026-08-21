# Resume Processing Automation Bot

Telegram bot that automates resume processing for recruiters. It extracts
structured candidate data from PDF/DOCX files using an **evidence-first
pipeline** (LLM evidence extraction + deterministic classification), stores
files in Google Drive, and logs records to Google Sheets.

The bot runs in two modes:

- **Vercel webhook (production)** — event-driven, no server, wakes only when a
  recruiter uploads a resume. See [`DEPLOYMENT-VERCEL.md`](DEPLOYMENT-VERCEL.md).
- **Polling bot (local / always-on server)** — `python main.py` on your machine
  or a 24/7 free VPS. See [`DEPLOYMENT.md`](DEPLOYMENT.md).

## Features

- **Telegram intake** — send a resume, get a structured summary back
- **Evidence-first extraction** — Pass 1 LLM extraction of verbatim evidence →
  deterministic Python classifier → optional Pass 2 context classification
  (additive-only, off by default) → adjudicator with strict quote/allowlist checks
- **13 candidate fields** extracted and stored (22-column sheet schema)
- **Deterministic recompute** — years of experience, current company, current
  title, and past companies are recomputed from role metadata (not LLM output)
- **File validation** — extension, size, corruption, and password-protection checks
- **Duplicate detection** — matches email, phone, LinkedIn against existing records
- **Google Drive storage** — organized folders: Incoming → Processed / Duplicates / Rejected
- **Google Sheets logging** — candidate records, an **Open Sales Roles** tab
  (scraped sales job openings), and a read-only **Classification Audit** tab
  (per-field explainability: what rule fired, what evidence, what was rejected)
- **Forensic audit** — one JSON + one Markdown report per resume under `audit/`,
  referenced from the sheet via the `Audit File` column
- **SaaS classification** — knowledge-only Groq check of the current company
  (always on, non-fatal)
- **Company enrichment stack** — domain resolution, company profiling, job
  openings scraper (controlled by `ENRICHMENT_ENABLED`, currently paused)

## Extracted Fields

| Field | Notes |
|---|---|
| Full Name | |
| Email | validated |
| LinkedIn URL | validated |
| Phone Number | validated + normalized (E.164 where possible) |
| Years of Experience | deterministic, from full-time role dates |
| Current Company | deterministic, from the ongoing full-time role |
| Title | role title at the current company (same role as Current Company) |
| Is SaaS Company | Yes / No / blank — knowledge-only classification |
| Past Companies | full-time employers, excluding current |
| College | |
| Geography | canonical tags — regions sold INTO, not where the candidate lives |
| SaaS Experience | canonical role-type + motion tags (e.g. Outbound/Prospecting) |
| Market Segment | canonical tags — SMB / Mid-Market / Enterprise / B2C / etc. |

## Architecture

```
Resume
  ↓
Pass 1 — LLM evidence extraction (verbatim quotes, role analysis)
  ↓
Deterministic Python Classifier (rule-based, canonical tags only)
  ↓
Pass 2 — Context classification (OPTIONAL, AI_CLASSIFICATION_ENABLED)
  ↓
Adjudicator (AI can only ADD evidence-backed tags; deterministic always wins)
  ↓
Validator (closed-vocabulary allowlists, field sanitization)
  ↓
SaaS classification → Company enrichment (paused) → Forensic audit
  ↓
Google Sheets
```

Key properties:

- **Deterministic baseline** — the classifier output never changes with the
  LLM; Pass 2 (when enabled) can only propose additions backed by verbatim
  resume quotes that exist in the allowlist. Any Pass 2 failure falls back to
  deterministic-only.
- **Evidence never invented** — every classification is anchored to verbatim
  resume text; the audit trail records matched rules, rejected rules, and
  blank reasons.
- **Channel-agnostic** — the pipeline accepts `(file_path, recruiter_metadata,
  source)` and never knows whether Telegram polling or the webhook sent the
  request.

## Project Structure

```
resume_bot/
├── main.py                     # Entry point — Telegram polling bot
├── index.py                    # Vercel entry point (re-exports webhook app)
├── vercel.json                 # Vercel function config
├── api/
│   └── webhook.py              # FastAPI webhook endpoint (serverless intake)
├── deploy/
│   └── set_webhook.py          # Register/delete the Telegram webhook
├── config/
│   ├── settings.py             # .env loader + validation
│   ├── logging_config.py       # Console + file logging
│   └── sales_titles.py         # Sales role title keywords (job openings)
├── core/
│   ├── pipeline.py             # Orchestration (8-stage flow + enrichment/audit)
│   ├── classifier.py           # Deterministic rule-based classifier
│   ├── adjudicator.py          # Merges Pass 2 proposals (safety rules)
│   ├── validator.py            # Post-extraction field validation + allowlists
│   ├── post_processing.py      # Deterministic YOE / company / title recompute
│   ├── duplicate_checker.py    # Email/phone/LinkedIn matching
│   ├── audit_builder.py        # Per-field audit rows
│   ├── audit_reporter.py       # JSON + Markdown forensic reports
│   └── exceptions.py           # Custom exception hierarchy
├── integrations/
│   ├── telegram/               # Polling bot + handlers
│   ├── parsers/                # PDF + DOCX text extraction
│   ├── ai/                     # Groq + Gemini clients (Pass 1 / Pass 2)
│   ├── drive/                  # Google Drive operations
│   ├── sheets/                 # Google Sheets operations (3 tabs)
│   └── enrichment/             # SaaS classifier, profiler, scraper, cache
├── prompts/
│   ├── phase1/
│   │   ├── pass1.txt           # Evidence extraction prompt
│   │   ├── pass2.txt           # Context classification prompt
│   │   └── v1.txt              # Legacy prompt (kept for reference)
│   └── company_profile/v1.txt  # Company profiling prompt
├── tests/                      # 14 test files — see Testing below
├── audit/                      # Generated forensic reports (JSON + MD)
├── DEPLOYMENT.md               # Oracle Cloud always-free VPS runbook
├── DEPLOYMENT-VERCEL.md        # Vercel webhook runbook
└── DECISIONS.md                # Architecture decision log
```

## Setup

### 1. Prerequisites

- Python 3.12+
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Google Cloud service account with Drive + Sheets API enabled
- Groq API key (free at [console.groq.com](https://console.groq.com)) or Gemini API key

### 2. Install

```bash
cd resume_bot
python -m venv venv
# Windows:
.\venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
# Edit .env with your actual credentials
```

Key settings:

- `AI_PROVIDER` — `groq` (default) or `gemini`
- `AI_CLASSIFICATION_ENABLED` — set `true` to enable Pass 2 context
  classification (default `false`, deterministic only)
- `ENRICHMENT_ENABLED` — company enrichment/scraper toggle (default `true`
  locally; keep `false` on serverless)
- `GOOGLE_DRIVE_CREDENTIALS` — path to the service-account JSON. On Vercel,
  use `GOOGLE_DRIVE_CREDENTIALS_JSON` instead (paste the full JSON contents).
- `LOG_LEVEL` — use `INFO` in production; `DEBUG` logs full resume text (PII)

### 4. Google Drive Setup

Create four folders in Google Drive and share them with the service account email:
- `Incoming/`
- `Processed/`
- `Duplicates/`
- `Rejected/`

Copy each folder's ID from its URL into `.env`.

### 5. Google Sheets Setup

Create a spreadsheet and share it with the service account email. Copy the
sheet ID from the URL into `.env`. The header row is created automatically on
first run (and new columns are added automatically on subsequent runs).

### 6. Run (polling mode)

```bash
python main.py
```

## Deployment

Two supported paths — full runbooks live in the repo:

- **[`DEPLOYMENT-VERCEL.md`](DEPLOYMENT-VERCEL.md)** — Vercel free Hobby plan,
  event-driven webhook, no credit card, $0 forever. This is the current
  production path.
- **[`DEPLOYMENT.md`](DEPLOYMENT.md)** — Oracle Cloud always-free VPS (2 OCPU /
  12 GB ARM), runs the polling bot 24/7 and can host a local LLM later.

## Testing

```bash
python -m pytest
```

Current status: **317 tests passed, 1 skipped** covering the classifier rule
coverage, adjudicator safety rules, evidence extraction, golden resumes,
post-processing, audit reports, and the webhook endpoint (all mocked — no
external API calls in tests).

## License

Private project — not licensed for redistribution.




