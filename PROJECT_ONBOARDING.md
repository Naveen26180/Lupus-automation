# 📘 Resume Bot — Complete Project Onboarding Guide
> **For new team members.** Read this top-to-bottom before touching any code.
> Last updated: 2026-07-20

---

## 0. What Is This Project?

This is an **AI-powered Resume Processing Automation Bot**.

A recruiter sends a resume file (PDF or DOCX) to a **Telegram bot**. The bot:
1. Downloads the file
2. Extracts the raw text
3. Sends it to an **AI model** (Groq or Gemini) to pull out structured candidate info
4. Post-processes and deterministically re-computes key fields (experience, company, etc.)
5. Validates the data
6. Checks for duplicate candidates
7. Saves the resume file to **Google Drive**
8. Logs the candidate data to a **Google Sheet**
9. Replies back to the recruiter with a structured summary

**The pipeline handles everything automatically. The recruiter just drops a file and gets a reply.**

---

## 1. Project Structure — The Full Map

```
Automation project/
├── gen-lang-client-XXXXX.json         ← Google Service Account credentials (NEVER commit this)
└── resume_bot/                        ← All project code lives here
    ├── main.py                        ← 🚀 Entry point — starts the bot
    ├── requirements.txt               ← All Python dependencies
    ├── .env                           ← 🔑 Your secrets (NEVER commit this)
    ├── .env.example                   ← Template showing what goes in .env
    ├── DECISIONS.md                   ← Architecture reasoning log
    │
    ├── config/                        ← Configuration layer
    │   ├── settings.py                ← Loads .env, validates, exposes as Settings dataclass
    │   └── logging_config.py         ← Sets up log format and log level
    │
    ├── core/                          ← Business logic (no external API calls here)
    │   ├── pipeline.py                ← 🎯 The main orchestrator — runs all 8 stages
    │   ├── post_processing.py         ← Deterministic calculation of YOE, current company, etc.
    │   ├── validator.py               ← Validates AI output field-by-field
    │   ├── duplicate_checker.py       ← Compares candidate against existing Sheet records
    │   └── exceptions.py             ← All custom exception classes
    │
    ├── integrations/                  ← All external API integrations
    │   ├── ai/
    │   │   ├── base_client.py        ← Abstract AI client + prompt loading + response parsing
    │   │   ├── groq_client.py        ← Groq-specific API implementation
    │   │   └── gemini_client.py      ← Gemini-specific API implementation
    │   ├── drive/
    │   │   └── drive_client.py       ← Upload/move files in Google Drive
    │   ├── sheets/
    │   │   └── sheets_client.py      ← Read/write rows in Google Sheets
    │   ├── parsers/
    │   │   ├── pdf_parser.py         ← Extract text from PDF files
    │   │   └── docx_parser.py        ← Extract text from DOCX files
    │   └── telegram/
    │       ├── bot.py                ← Creates and runs the Telegram bot
    │       └── handlers.py           ← Handles Telegram messages/commands
    │
    ├── prompts/
    │   └── phase1/
    │       ├── v1.txt                ← 🧠 The AI prompt template (two-stage extraction)
    │       └── v2.txt                ← Newer version (experimental)
    │
    ├── tests/                         ← Automated tests
    │   └── test_post_processing.py   ← Tests for post_processing.py
    │
    ├── mock_pipeline_run.py           ← Dev tool: test pipeline using saved raw_ai_response.json
    ├── inspect_sheet.py               ← Dev tool: peek at current sheet data
    ├── download_test_resumes.py       ← Dev tool: download test resumes from Drive
    └── raw_ai_response.json          ← Debug dump: latest raw AI response
```

---

## 2. The 8-Stage Pipeline — Step by Step

Every resume goes through exactly these 8 stages in `core/pipeline.py`.

```
Recruiter sends file on Telegram
        │
        ▼
[Stage 1] FILE VALIDATION
  • Is it .pdf or .docx?
  • Is it under 20 MB?
  • Does it exist and is non-empty?
  ❌ Fail → Upload to Drive/Rejected/, reply "❌ File rejected"
        │
        ▼
[Stage 2] DRIVE UPLOAD (Incoming/)
  • Upload file to Google Drive "Incoming" folder
  • Get back a file_id and a shareable link
  ❌ Fail → Reply "❌ Failed to upload file"
        │
        ▼
[Stage 3] TEXT EXTRACTION
  • PDF → pdfplumber
  • DOCX → python-docx
  • Returns raw text string
  ❌ Fail → Reply "❌ Could not read the file"
        │
        ▼
[Stage 4] AI EXTRACTION
  • Inject text into prompts/phase1/v1.txt
  • Call Groq or Gemini (2 retry attempts)
  • AI returns JSON with role_analysis + final_answer keys
  • base_client.py parses and validates the JSON
  • Immediately calls post_processing.recompute_derived_fields()
  ❌ Fail → Reply "❌ AI extraction failed"
        │
        ▼
[Stage 5] POST-PROCESSING (inside base_client.parse_ai_response)
  • Overrides AI's YOE with deterministic Python calculation
  • Sets current_company based on ongoing FULL_TIME roles
  • Calculates internship months
  • Computes past_companies list
  (This happens automatically after Stage 4 — not a separate call in pipeline.py)
        │
        ▼
[Stage 6] FIELD VALIDATION (core/validator.py)
  • Email: must match regex
  • LinkedIn URL: must contain "linkedin.com"
  • Phone: tries phonenumbers library, falls back to digit-count check
  • YOE / experience_months / internship: must be numeric
  • Other fields: converted to string or left as null
  • Invalid values → set to null (never crash the whole pipeline)
        │
        ▼
[Stage 7] DUPLICATE CHECK (core/duplicate_checker.py)
  • Fetch all existing rows from Google Sheet
  • Compare email, phone, LinkedIn against every existing row
  • Match found? → Move file to Drive/Duplicates/, write flagged row to Sheet
  ❌ Duplicate → Reply "⚠️ This looks like a duplicate"
        │
        ▼
[Stage 8] SHEETS WRITE + DRIVE MOVE
  • Append a row to the Google Sheet (20 columns)
  • Move file from Drive/Incoming/ → Drive/Processed/
  ✅ Done → Reply with full candidate summary
```

---

## 3. The AI Prompt — How It Works

**File:** `prompts/phase1/v1.txt`

The prompt uses a **Two-Stage Chain-of-Thought** design:

### Stage 1: `role_analysis` (AI builds a table)
For every role on the resume, the AI must output:
- `role_title`, `employer`
- `start_date_raw`, `end_date_raw` (exact text from resume)
- `is_explicitly_ongoing` (true/false — text match only, not judgment)
- `end_date_vs_today` (ongoing / ended_in_past / unparseable)
- `bucket` → one of: `FULL_TIME`, `INTERNSHIP`, `VOLUNTEER_EXTRACURRICULAR`
- `duration_months` (calculated by AI)

### Stage 2: `final_answer` (AI derives from Stage 1 only)
The AI must fill exactly 12 fields:
`full_name`, `email`, `linkedin_url`, `phone_number`, `years_of_experience`,
`internship_experience`, `college`, `geography`, `saas_experience`,
`market_segment`, `current_company`, `past_companies`

> **Key Rule:** The AI is forbidden from re-reading the resume for Stage 2. It can only
> use what it put in `role_analysis`. This is the "two-stage" guarantee.

### Why Two Stages?
LLMs are unreliable when doing multi-step reasoning in one shot. By forcing a
structured intermediate table first, the AI is less likely to miscalculate
experience or confuse internships with full-time work.

---

## 4. Post-Processing — Why It Exists and What It Does

**File:** `core/post_processing.py`  
**Called from:** `integrations/ai/base_client.py` → `parse_ai_response()`

Even with a great prompt, the AI can still get date math wrong. So **after** the AI
returns its JSON, Python re-computes the critical derived fields deterministically.

### What `recompute_derived_fields()` does:

| Step | What it computes | Rule |
|------|-----------------|------|
| 1 | Filter valid roles | Skip EDUCATION, skip empty entries |
| 2 | Normalize bucket | If role_title contains "intern/trainee/apprentice" → force INTERNSHIP |
| 3 | Parse all dates | Uses `python-dateutil` with fuzzy parsing; bare years (e.g. "2024") are skipped |
| 4 | `current_company` | Only FULL_TIME + `_is_ongoing == True`; if multiple, pick latest start date |
| 5 | `years_of_experience` / `experience_months` | Sum FULL_TIME months, merge overlapping intervals, then: **< 12 months → `experience_months`**, **≥ 12 months → `years_of_experience`** |
| 6 | `internship_experience` | Sum all INTERNSHIP duration months |
| 7 | `past_companies` | All FULL_TIME + VOLUNTEER roles except current_company |

> **Exactly one of `years_of_experience` or `experience_months` will be non-null at any time.**
> Never both, never neither (unless zero full-time data exists).

---

## 5. Google Sheets Schema

**File:** `integrations/sheets/sheets_client.py`

The sheet has **20 columns** in this exact order:

| Col | Header | Source |
|-----|--------|--------|
| A | Timestamp | Auto-generated (UTC) |
| B | Full Name | AI → post_processing |
| C | Email | AI → validator |
| D | LinkedIn URL | AI → validator |
| E | Phone Number | AI → validator |
| F | Years of Experience | post_processing (≥ 12 months) |
| G | Experience (Months) | post_processing (< 12 months) |
| H | Internship Experience (Months) | post_processing |
| I | Current Company | post_processing |
| J | Past Companies | post_processing |
| K | College | AI |
| L | Geography | AI |
| M | SaaS Experience | AI |
| N | Market Segment | AI |
| O | Drive File Link | DriveClient.get_file_link() |
| P | Source | "telegram" |
| Q | Status | "Processed" or "Possible Duplicate" |
| R | Duplicate Reason | e.g. "Email Match" |
| S | Matched Field | e.g. "email" |
| T | Matched Row ID | e.g. "row 5" |

> **Columns F and G are mutually exclusive.** If experience < 12 months, only G is filled.
> If ≥ 12 months, only F is filled. They never both have a value.

---

## 6. Google Drive Folder Structure

4 Drive folders are used, each with a unique `FOLDER_ID` in `.env`:

| Folder | Purpose |
|--------|---------|
| `Incoming/` | Files are uploaded here first |
| `Processed/` | Successfully processed resumes go here |
| `Duplicates/` | Duplicate candidates go here |
| `Rejected/` | Invalid files (wrong format, too big) go here |

**Upload method:** The `DriveClient` supports two modes:
1. **Apps Script Webhook** (primary) — if `APPS_SCRIPT_URL` is set in `.env`
2. **Service Account direct upload** (fallback) — uses the service account JSON

---

## 7. Duplicate Detection Logic

**File:** `core/duplicate_checker.py`

Three fields are checked in priority order:
1. **Email** (column "Email" in sheet)
2. **Phone Number** (column "Phone Number" in sheet)
3. **LinkedIn URL** (column "LinkedIn URL" in sheet)

Matching is **case-insensitive + normalized**:
- Emails → lowercased
- Phone numbers → stripped to digits + leading `+`
- LinkedIn URLs → lowercased

If any field matches an existing row, `DuplicateFoundError` is raised.

---

## 8. Exception Hierarchy

**File:** `core/exceptions.py`

All custom exceptions inherit from `ResumeBotError`. This means you can always
catch the entire family with `except ResumeBotError`.

```
ResumeBotError
├── ConfigurationError    ← Missing/invalid .env value
├── FileValidationError   ← Wrong extension, too large, empty
├── ParsingError          ← PDF/DOCX parsing failed
├── AIProviderError       ← AI call failed or returned bad JSON
├── ValidationError       ← Field-level validation failure
├── DriveError            ← Google Drive API error
├── SheetsError           ← Google Sheets API error
├── DuplicateFoundError   ← Candidate already in sheet
└── TelegramError         ← Telegram download/reply failed
```

Each exception carries `.message` (human-readable) and `.details` (dict for logging).

---

## 9. Configuration — The `.env` File

**Never edit `settings.py` to change values. Always update `.env`.**

```
# Telegram bot token from @BotFather
BOT_TOKEN=

# Which AI to use: "groq" or "gemini" (toggle this to switch providers)
AI_PROVIDER=groq

# API Keys — only the active provider's key is strictly required
GROQ_API_KEY=
GEMINI_API_KEY=

# Path to your Google service account JSON file
GOOGLE_DRIVE_CREDENTIALS=path/to/gen-lang-client-XXXXXX.json

# The spreadsheet ID (from the Google Sheet URL)
GOOGLE_SHEET_ID=

# Google Drive folder IDs (from each folder's URL)
INCOMING_FOLDER_ID=
PROCESSED_FOLDER_ID=
DUPLICATE_FOLDER_ID=
REJECTED_FOLDER_ID=

# Optional: Controls log verbosity (DEBUG / INFO / WARNING / ERROR)
LOG_LEVEL=INFO
```

`settings.py` validates everything at startup. If any required key is missing,
the bot refuses to start with a clear error message.

---

## 10. Dependencies (requirements.txt)

| Package | Purpose |
|---------|---------|
| `python-telegram-bot==21.5` | Telegram bot framework |
| `python-dotenv==1.1.0` | Load `.env` files |
| `pdfplumber==0.11.4` | Extract text from PDFs |
| `python-docx==1.1.2` | Extract text from DOCX files |
| `groq==0.15.0` | Groq AI API client |
| `google-generativeai==0.8.5` | Gemini AI API client |
| `google-api-python-client==2.165.0` | Google Drive API |
| `google-auth==2.38.0` | Google authentication |
| `gspread==6.1.4` | Google Sheets (higher-level wrapper) |
| `phonenumbers==8.13.53` | Phone number parsing & validation |

Install all with:
```bash
pip install -r requirements.txt
```

---

## 11. How to Run the Bot

```bash
# From the resume_bot/ directory:
python main.py
```

What `main.py` does in order:
1. Loads `.env` → creates frozen `Settings` object
2. Sets up logging
3. Creates AI client (Groq or Gemini based on `AI_PROVIDER`)
4. Creates `DriveClient` with all 4 folder IDs
5. Creates `SheetsClient` (also auto-ensures headers in row 1)
6. Creates `Pipeline` with all clients injected
7. Creates Telegram `ResumeHandlers` with the pipeline
8. Starts the bot in polling mode

---

## 12. Development & Testing Tools

### A. `mock_pipeline_run.py` — Fastest testing loop
```bash
python mock_pipeline_run.py
```
Uses the **saved `raw_ai_response.json`** from the last real bot run.
Skips Telegram and the AI call entirely. Runs only post-processing,
validation, and sheet append. Use this to test logic changes without
sending real requests.

### B. `inspect_sheet.py` — Peek at the sheet
```bash
python inspect_sheet.py
```
Prints the current sheet rows to console for debugging.

### C. `download_test_resumes.py` — Get test data
```bash
python download_test_resumes.py
```
Downloads sample resume files from Drive for manual testing.

### D. `raw_ai_response.json` — Debug dump
Every time the bot calls the AI, the raw response is saved here.
If something looks wrong in the output, check this file first.

### E. Running tests
```bash
cd resume_bot
python -m pytest tests/ -v
```

---

## 13. Architecture Principles (Why Things Are the Way They Are)

### Dependency Injection
`Pipeline` never creates API clients itself. All clients are passed in during
construction (`__init__`). This makes the pipeline fully testable in isolation
by passing in mock objects.

### AI Providers are Swappable
`GeminiClient` and `GroqClient` both extend `BaseAIClient`. The pipeline only
knows about `BaseAIClient`. To switch from Groq to Gemini: change one `.env` line.

### Post-Processing Overrides AI
AI math is unreliable. Date calculations and YOE are **always re-computed by Python**
regardless of what the AI says. The AI's `final_answer` values are discarded for
computed fields.

### Validator Never Crashes
Bad AI output for one field sets that field to `null`. The pipeline continues
with partial data. Better to store 90% correct data than to fail entirely.

### Service Account Auth (Not OAuth)
The bot is headless — no browser. Service accounts authenticate without user
interaction. The side-effect is that every Google resource (Drive folders, Sheet)
must be explicitly **shared with the service account email**.

### Prompt as a File (Not Hardcoded)
The AI prompt lives in `prompts/phase1/v1.txt`. Prompt updates don't require
code changes. Versioned files allow A/B testing.

---

## 14. Common Gotchas for New Team Members

| Gotcha | What Actually Happens | Fix |
|--------|----------------------|-----|
| Updating `v1.txt` but seeing old behavior | The prompt is loaded fresh from disk on every call — there's no caching | Check you saved the file and there's no `.pyc` issue |
| Sheet shows blank for `0` experience | Old bug was fixed — `_cell()` helper uses `is not None` check now | Check `sheets_client.py` `_cell()` function |
| AI returns correct JSON but YOE is wrong | Post-processing overwrites AI's value — check `post_processing.py`, not the prompt | Debug `raw_ai_response.json` to isolate where the error is |
| Drive upload fails with quota error | Service account has 0 GB quota — use the Apps Script webhook approach | Set `APPS_SCRIPT_URL` in `.env` |
| Bot doesn't start, says "Missing required env vars" | `.env` is incomplete | Copy `.env.example`, fill every field |
| Column data is shifted in the sheet | Sheet headers don't match `COLUMN_HEADERS` in `sheets_client.py` | Run `inspect_sheet.py` and compare headers |
| Duplicate not caught | Duplicate check compares normalized values — formatting differences (spaces, dashes in phone) are handled, but completely different formats may not be | Check `duplicate_checker._normalize()` |
| `experience_months` AND `years_of_experience` both None | Only happens when ALL full-time roles have bare-year dates (unparseable). Normal behavior. | Verify dates in resume have month+year format |

---

## 15. Typical Trainee Task Examples

Here are examples of things you might be asked to do, with a clear map to where you'd work:

### Task: "Add a new extracted field (e.g. `notice_period`)"
1. Add `notice_period` to `EXPECTED_KEYS` in `integrations/ai/base_client.py`
2. Add the extraction instruction to `prompts/phase1/v1.txt`
3. Add validation logic in `core/validator.py` (inside `validate_extracted_fields`)
4. Add the column to `COLUMN_HEADERS` in `integrations/sheets/sheets_client.py`
5. Add the row value in `append_row()` in `sheets_client.py`
6. Update the Telegram reply message in `core/pipeline.py`

### Task: "Change the AI provider to Gemini"
1. Open `.env`
2. Set `AI_PROVIDER=gemini`
3. Make sure `GEMINI_API_KEY` is set
4. Restart the bot — no code changes needed

### Task: "The YOE calculation is wrong for a specific resume"
1. Trigger the bot with that resume (or use `mock_pipeline_run.py` if you have `raw_ai_response.json`)
2. Check `raw_ai_response.json` — what did the AI put in `role_analysis`?
3. Trace through `post_processing.recompute_derived_fields()` step by step
4. Look at `_start_date`, `_end_date`, `_duration_months` on each role
5. Fix the logic bug in `post_processing.py` and add a test in `tests/test_post_processing.py`

### Task: "Add a new duplicate check field (e.g. `full_name`)"
1. Open `core/duplicate_checker.py`
2. Add `("full_name", "Full Name")` to the `checks` list in `check_for_duplicates()`
3. The rest of the logic is generic — it will work automatically

### Task: "Change the max file size from 20MB to 10MB"
1. Open `.env`
2. Set `MAX_FILE_SIZE_MB=10`
3. Restart bot — `settings.py` reads this on startup

---

## 16. End-to-End Data Flow Summary

```
Telegram Message
      │ (document)
      ▼
handlers.py → downloads file → temp directory
      │
      ▼
pipeline.process()
      ├─ _validate_file()        → checks extension, size
      ├─ drive.upload_file()     → Drive/Incoming/
      ├─ _extract_text()         → pdf_parser / docx_parser
      ├─ ai.extract_fields()
      │     ├─ load_prompt_template()    → reads v1.txt
      │     ├─ _call_api()              → Groq or Gemini API
      │     └─ parse_ai_response()
      │           └─ recompute_derived_fields()  ← Python overrides AI math
      ├─ validate_extracted_fields()    → nullify bad fields
      ├─ sheets.get_all_records()       → fetch all rows
      ├─ check_for_duplicates()         → compare email/phone/linkedin
      ├─ sheets.append_row()            → write 20 columns
      └─ drive.move_file()              → Incoming/ → Processed/
            │
            ▼
      PipelineResult → reply message back to recruiter on Telegram
```

---

> **You are now equipped to work on this project.** When in doubt, read the code
> in the order: `exceptions.py` → `settings.py` → `pipeline.py` → the specific
> integration or core module you need to change.
> 
> Always run `mock_pipeline_run.py` to test before doing a live bot run.
> Always add a test in `tests/` when fixing a logic bug.
