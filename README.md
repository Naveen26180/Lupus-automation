# Resume Processing Automation Bot

Telegram bot that automates resume processing for recruiters — extracts structured candidate data from PDF/DOCX files using AI (Groq or Gemini), stores files in Google Drive, and logs records to Google Sheets.

## Features (Phase 1)

- **Telegram intake** — Send a resume, get structured data back
- **AI extraction** — 9 candidate fields extracted via Llama 3.3 70B (Groq) or Gemini Flash
- **File validation** — Extension, size, corruption, and password-protection checks
- **Duplicate detection** — Matches email, phone, LinkedIn against existing records
- **Google Drive storage** — Organized folders: Incoming → Processed / Duplicates / Rejected
- **Google Sheets logging** — Structured candidate records with Drive links

## Extracted Fields

Full Name, Email, LinkedIn URL, Phone Number, Years of Experience, College, Geography, SaaS Experience, Market Segment

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

### 4. Google Drive Setup

Create four folders in Google Drive and share them with the service account email:
- `Incoming/`
- `Processed/`
- `Duplicates/`
- `Rejected/`

Copy each folder's ID from its URL into `.env`.

### 5. Google Sheets Setup

Create a spreadsheet and share it with the service account email. Copy the sheet ID from the URL into `.env`.

### 6. Run

```bash
python main.py
```

## Project Structure

```
resume_bot/
├── main.py                     # Entry point
├── config/
│   ├── settings.py             # .env loader + validation
│   └── logging_config.py       # Console + file logging
├── core/
│   ├── pipeline.py             # Orchestration (8-stage flow)
│   ├── validator.py            # Post-extraction field validation
│   ├── duplicate_checker.py    # Email/phone/LinkedIn matching
│   └── exceptions.py           # Custom exception hierarchy
├── integrations/
│   ├── telegram/               # Bot + handlers
│   ├── parsers/                # PDF + DOCX text extraction
│   ├── ai/                     # Groq + Gemini clients
│   ├── drive/                  # Google Drive operations
│   └── sheets/                 # Google Sheets operations
├── prompts/phase1/v1.txt       # Extraction prompt
└── DECISIONS.md                # Architecture decision log
```

## Architecture

The pipeline accepts `(file_path, recruiter_metadata, source)` — it never knows which intake channel sent the request. Business logic calls integration modules, never APIs directly. Any integration can be swapped without touching the pipeline.

## License

Private project — not licensed for redistribution.

<img width="1267" height="881" alt="telegram-chat" src="https://github.com/user-attachments/assets/9960e068-ccd6-4d15-b113-f49d5a3ab9af" />

<img width="1898" height="322" alt="google-sheet" src="https://github.com/user-attachments/assets/73814fc9-4df8-4579-a498-658fe3507919" />


