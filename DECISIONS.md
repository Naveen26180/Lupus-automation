# Architecture Decisions

## 14. Serverless Telegram Webhook (Vercel) (2026-08-15)

**Decision:** Added an event-driven intake path alongside polling: `api/webhook.py` (FastAPI + Mangum for Vercel), `vercel.json`, `deploy/set_webhook.py`, and `DEPLOYMENT-VERCEL.md`. Telegram POSTs each update to the webhook; the function runs the **same** `core/pipeline.Pipeline` and replies via the Bot API with plain `requests`.

**Why:** The user has no credit card (Oracle/GCP/AWS all require one). Vercel Hobby is the only serious free host with no card gate, and its functions (300s max) comfortably fit the 10–25s pipeline. The webhook sleeps between uploads, so cost is $0 forever.

**Key design choices:**
1. **No python-telegram-bot in the webhook path** — PTB's Application lifecycle (initialize/start/shutdown + event loop) is hostile to stateless serverless functions. The webhook speaks the Bot API directly and reuses `ResumeHandlers`' exact reply texts + `PipelineResult.message`. The polling path (`integrations/telegram/`) is untouched.
2. **Ephemeral disk handling** — `GOOGLE_DRIVE_CREDENTIALS_JSON` env var is materialized to a temp file at startup (`_ensure_credentials_file`); `GOOGLE_DRIVE_CREDENTIALS` must stay empty on Vercel.
3. **Retry safety** — Telegram retries webhooks whose processing outlives its timeout; an in-memory `update_id` dedup (10-min TTL) prevents double-processing. Endpoint always returns 200 so Telegram never retry-storms.
4. **Enrichment must stay `false`** on this path — the scraper can exceed the function window.

**Accepted trade-offs:** no local LLM on serverless (a hard no), audit JSON/MD reports and caches are ephemeral per invocation, Hobby is personal/non-commercial.

**Unchanged:** pipeline order, classifier, validator, enrichment, duplicate detection, sheet schema, polling bot. New tests: `tests/test_webhook.py` (17) all mocked.

## 13. Pre-Deployment Security Hardening (2026-08-15)

**Decision:** Before first deployment, closed three security gaps and documented the rest in `SECURITY.md`.

1. **PII in logs (HIGH):** The Groq/OpenAI SDK logs the FULL request payload — resume text with names, emails, phones — at DEBUG, and the file handler always wrote at DEBUG, so every processed resume's text landed in `logs/app.log`. Fixed in `config/logging_config.py`: SDK loggers (`groq`, `openai`, `httpx`, `httpcore`, `urllib3`, `requests`, `telegram`, `google*`, `gspread`, `pdfminer`) are forced to WARNING, and unknown third-party loggers are capped at INFO. App-level DEBUG tracing is preserved.
2. **PII debug dump (MEDIUM):** `raw_ai_response.json` (full Pass 1 evidence) was written on every resume regardless of environment. Now written **only when `LOG_LEVEL=DEBUG`** (`integrations/ai/base_client.py`). Production stays INFO, so the file is never created.
3. **Deployment portability (settings):** `GOOGLE_DRIVE_CREDENTIALS` now accepts a path **relative to the project root** (resolved in `config/settings.py`), so the service-account JSON can sit next to `.env` on the server instead of requiring a hardcoded Windows absolute path.
4. **Docs:** Added `SECURITY.md` (rotation steps for the service-account key, PII artifact table, production checklist). `.gitignore` extended with `.pytest_cache/`, `audit/`, `raw_ai_response.json`, `*.log`; a root-level `.gitignore` safety net protects the credentials JSON / PDF / venvs if git is ever initialized one level up. `.env.example` now documents `APPS_SCRIPT_URL`, `ENRICHMENT_ENABLED`, and the DEBUG-logging warning.

**Why:** The bot processes candidate PII and holds a live Google service-account key; deployment to a shared host without these fixes would leak resume text into logs and risk committing the key.

**Unchanged:** Pipeline order, classifier, validator, enrichment, duplicate detection, sheet schema — all untouched. Verified: full suite 295 passed / 1 skipped; `git add -A -n` shows zero sensitive files.

## 1. AI Provider Pattern (Base Class + Subclasses)

**Decision:** Abstract `BaseAIClient` with `GroqClient` and `GeminiClient` subclasses.

**Why:** The master prompt requires a single `.env` toggle (`AI_PROVIDER=groq|gemini`) to swap providers. An ABC with a shared `extract_fields()` contract means `pipeline.py` never imports a specific provider.

**Alternatives considered:** Simple if/else in pipeline — rejected because it puts provider-specific code in the orchestrator, violating the module responsibility table.

## 2. Prompt Template as a Versioned File

**Decision:** Store the extraction prompt in `prompts/phase1/v1.txt`, loaded at runtime.

**Why:** Prompt iteration is expected. Versioned files mean we can A/B test prompts without code changes. The `{resume_text}` placeholder is filled by `base_client.py`.

## 3. Duplicate Check After AI Extraction

**Decision:** Duplicate check runs *after* AI extraction (step 6), not before.

**Why:** We need email, phone, and LinkedIn URL to check — those only exist after the AI processes the resume. Checking before extraction would require parsing contact info with regex, which is fragile and duplicates AI work.

## 4. gspread Over Raw Sheets API

**Decision:** Use `gspread` instead of the raw Google Sheets API.

**Why:** Simpler API for the operations we need (append row, read all rows). The raw API requires manual request/response handling. gspread is well-maintained and handles auth via the same service account credentials.

## 5. pdfplumber Over PyPDF2/PyMuPDF

**Decision:** Use `pdfplumber` for PDF text extraction.

**Why:** Better text extraction quality than PyPDF2, especially for multi-column layouts common in resumes. PyMuPDF (fitz) is also good but has a more complex API. pdfplumber handles most resume PDF formats well.

## 6. Validator Nullifies Instead of Rejecting

**Decision:** Invalid field values are set to `null` rather than causing pipeline failure.

**Why:** Partial data is better than no data. If the AI extracts a name and geography but the email is malformed, we still want to store the candidate record. The recruiter sees what was extracted and can manually correct.

## 7. Phone Normalization with Multi-Region Fallback

**Decision:** Try parsing phone numbers without a region code first, then fall back to US, IN, GB.

**Why:** Resumes from multiple countries. The `phonenumbers` library needs a region hint for numbers without country codes. US/IN/GB cover the most common cases. Numbers with 7+ digits that don't parse are kept as-is rather than nullified.

## 8. Service Account Auth (Not OAuth)

**Decision:** Google Drive and Sheets use service account credentials, not user OAuth.

**Why:** Service accounts don't require interactive browser login — critical for a headless bot. The trade-off is that the service account needs explicit folder/sheet sharing, but that's a one-time setup.

## 9. Frozen Settings Dataclass

**Decision:** Configuration is a frozen (immutable) dataclass, not a mutable dict or global variables.

**Why:** Prevents accidental mutation after startup. Type hints give IDE support. Factory function `load_settings()` validates everything at load time.

## 9.5 Deterministic Classifier Coverage Expansion (2026-08-14)

**Decision:** Expanded the deterministic rule coverage so every canonical value the validator allows is producible by at least one classifier rule. Before: SaaS 10/32, Market Segment 6/7, Geography 7/21 reachable (22+14+1 dead tags). After: 32/32, 7/7, 21/21.

**What was added (Python rules only, no architecture change):**
- **Geography:** territory-phrasing context (`worked across`, `customers in`, `geographies like …`, `domestic and international`, `multiple regions`) plus the 14 missing regions (MEA, GCC, SEA, ASEAN, EU, Global, ANZ, Nordics, Benelux, CEE, Iberia, CIS, APJ, ROW). Location-only statements (`Based in India`, `Lives in Bangalore`) still never fire.
- **SaaS:** ported the pass2.txt mapping table into Python rules — Inbound Sales, Field Sales, Channel Sales, Partner Sales, Pre-Sales, Sales Engineering, Funnel Management, Team Lead, P&L Ownership, methodologies (BANT, SPIN, MEDDIC, MEDDPICC, Challenger, Solution, Value, Sandler), B2C, B2B2C, Transactional, Enterprise Sales Cycle, PLG — plus broadened Customer Retention (improved retention, reduced refunds), B2B (prospective/corporate/business clients), Outbound (dialed/made X calls, lead generation, ABM), and SaaS Sales (software subscriptions).
- **Market Segment:** added B2B2C and consumer-buyer inference for ed-sales (students/learners/parents in sales context → B2C).
- **Determinism fix:** tag lists are now emitted in rule order instead of raw `set` iteration order (set order is hash-randomized across processes).

**Mappings deliberately NOT ported (reviewed, rejected):** bare `expansion` → Upsell/Cross-Sell (conflicts with the locked Bharti regression test), bare `demo` → Pre-Sales and `CRM` → Sales Operations (too generic), bare `channel` → Channel Sales (matches outreach channels, not channel partners).

**Why:** the pass2.txt mapping table was written for an LLM pass that is no longer executed, so 22 SaaS tags and 14 geography regions could never be produced — silently blank cells despite valid evidence (the Snehasish Das resume: geography blank despite "geographies like the US, Canada, Middle East…", market segment blank despite an obviously consumer audience).

**Gates:** a coverage test (`tests/test_rule_coverage.py`) fails if any allowlist tag has no rule; golden-resume tests (`tests/test_golden_resumes.py`) lock the expected output for two representative resumes; every new rule has at least one positive regression test (`tests/test_classifier_expansion.py`).

## 10. Classification Audit Sheet (2026-08-14)

**Decision:** Added a read-only **Classification Audit** worksheet (one row per candidate per field for Geography, SaaS Experience, Market Segment) that explains how each value was determined — the verbatim resume evidence, source section, matched rule + phrase, match type (Explicit / Contextual / Enrichment / Validator Alias / None), why it was selected, why other rules were rejected, why the field is blank, enrichment status, and confidence (Deterministic / Validated / Enriched / No Match).

**How it works:** `core/classifier.py` gained `classify_candidate_audited()` — identical logic to `classify_candidate()`, but it also records every rule that fired (with verbatim evidence, source, matched phrase, match type, and title-block suppression) and every rule that was rejected. `base_client.py` attaches this trail to the extraction result under `_classification_audit`; `enrichment_pipeline.enrich_candidate()` records an `_enrichment_info` marker (ran / reason / scraped values). `core/audit_builder.py` formats these into the 13-column rows, and the pipeline appends them via `SheetsClient.append_audit_rows()` — non-fatal, logged on failure only.

**Why:** The classifier is deterministic but opaque — QA reviewers had no way to tell *why* a resume produced (or failed to produce) a geography, SaaS experience, or market segment tag. This sheet gives a human an evidence-grounded explanation without re-running prompts or reading regexes.

**Guarantees:** The audit never influences classification (it is produced after the fact from the same data the classifier already consumed), never writes to the candidate sheet, and never blocks the pipeline. Evidence is always quoted verbatim from the pass1 evidence stream — nothing is invented.

## 12. Pass 2 Context Classification + Forensic Audit Files (2026-08-15)

**Decision:** Reconnected the dormant Pass 2 (`pass2.txt`) as an optional, additive-only **context classification** layer, and replaced the verbose Classification Audit sheet rows with **per-resume forensic JSON + Markdown files** under `audit/`.

**How it works (toggle `AI_CLASSIFICATION_ENABLED`, default `false` — off = byte-for-byte current behavior):**
1. Pass 1 extracts evidence exactly as before.
2. The deterministic classifier (`core/classifier.py`) remains the production baseline — unchanged.
3. Pass 2 (`integrations/ai/base_client.py::_run_pass2`) sends the FULL resume text + pass1 evidence to the provider and returns per-tag proposals `{tag, confidence, evidence[], reasoning}` with a strict output schema (`_parse_pass2_response`).
4. `core/adjudicator.py` merges proposals into the baseline. An AI proposal is **accepted only if**: the tag is in the validator allowlist, EVERY evidence quote appears verbatim in the resume (whitespace-normalized), the reasoning is supported by the evidence (deterministic keyword grounding — rejects the "Evidence: Salesforce → Reasoning: SaaS Sales" class of error), and the evidence is not just the job title. Deterministic tags are never removed; market_segment conflicts are rejected (deterministic wins); geography/saas remain additive. Confidence per field: Very High (agree) / High (AI added) / Medium (AI filled blank) / Low (segment conflict) / Deterministic / No Match.
5. Any Pass 2 failure (provider, timeout, invalid JSON, schema violation) falls back to deterministic-only — never fails the resume.

**Audit redesign:** Google Sheets stays clean. The pipeline no longer appends verbose Classification Audit rows; instead `core/audit_reporter.py` writes one machine-readable `audit/<timestamp>_<Candidate>.json` (pass1 evidence, deterministic output, AI proposals + reasoning, adjudicator decisions, validator drops, enrichment, final output) plus a human-readable `<Candidate>.md` forensic report. The main sheet gained a single **Audit File** column holding the relative path (`audit/20260815_071543_Snehasish_Das.json`). The `Classification Audit` tab, `core/audit_builder.py`, and `SheetsClient.append_audit_rows()` are retained (tests still cover them) but no longer called by the pipeline.

**Why:** The deterministic rules are evidence-faithful but context-blind — the Snehasish Das resume's territory/buyer signals needed reading within their role context. The AI already runs Pass 1; this gives it a reasoning role again WITHOUT making it the source of truth, and moves explainability out of the spreadsheet (where large evidence blobs were unreadable) into inspectable files.

**Guarantees:** deterministic outputs identical when disabled; AI can only add evidence-backed classifications; every accepted classification carries verbatim quotes + reasoning; every rejection carries a reason; one JSON + one MD per resume; provider-agnostic (uses the existing `_call_api` contract). Tested with mocked AI responses only (`tests/test_adjudicator.py`, `tests/test_pass2_integration.py`, `tests/test_audit_reporter.py`).

## 11. Internship Tracking Removed (2026-07-20)

**Decision:** Removed `internship_experience` as a tracked and output field from every layer of the codebase — the AI prompt, the expected-key contract, post-processing, field validation, the Google Sheet schema, and the Telegram reply message.

**Why:** This bot processes resumes exclusively for **sales hiring**. We do not hire early-career or internship candidates. Tracking internship data wastes AI prompt tokens on data we discard, adds dead columns to the sheet, and confuses future contributors into thinking the field is meaningful.

**What was kept:** The `INTERNSHIP` bucket tag in the AI prompt's `role_analysis` Stage 1 and in `core/post_processing.py` is deliberately preserved. It is used internally to **exclude** internship time from the full-time experience calculation (`years_of_experience` / `experience_months`). Removing the bucket classification entirely would cause internship months to be incorrectly counted as real sales experience.

**Do not reintroduce** `internship_experience` as an output field without a deliberate product decision. If the hiring scope changes to include early-career roles, this decision should be revisited explicitly.
