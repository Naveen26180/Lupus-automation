# Technical Review Workflow: Resume Processing Bot

Welcome to the **Resume Processing Bot** project! This guide is designed for developers and technical reviewers to quickly understand the architecture, run the system locally, and evaluate the core components.

---

## 1. Project Overview & Architecture

This project is a Python-based Telegram bot that automates resume intake. When a recruiter uploads a PDF or DOCX file to the bot, it:
1. **Validates** the file (size, format).
2. **Uploads** the raw file to a Google Drive "Incoming" folder.
3. **Extracts** text using `pdfplumber` or `python-docx`.
4. **Parses** structured data using an LLM (Groq or Gemini via dependency injection).
5. **Validates & Sanitizes** the returned JSON (checking emails, classifying phone numbers).
6. **Checks for Duplicates** by comparing against existing entries in Google Sheets.
7. **Stores** the validated JSON into Google Sheets and moves the file to the "Processed" Drive folder.

### 🏛️ Module Responsibility (Clean Architecture)
The codebase strictly separates domain logic from infrastructure:
* **`main.py`**: The composition root. Instantiates all clients and injects them into the pipeline.
* **`core/`**: Pure business logic. Completely ignorant of third-party APIs. Includes the `Pipeline`, `validator`, `duplicate_checker`, and custom exception hierarchy.
* **`integrations/`**: Infrastructure layer. Communicates with external APIs (Telegram, Google Drive, Google Sheets, Groq/Gemini). None of these modules depend on each other.
* **`config/`**: Configuration management using frozen dataclasses to prevent runtime mutations.
* **`prompts/`**: Stores LLM prompts as separate text files (e.g., `v1.txt`) for easy A/B testing and hot-reloading without codebase changes.

---

## 2. Setup & Execution (Local Environment)

### Prerequisites
1. Python 3.10+
2. A `.env` file (see `.env.example` for required keys)
3. Google Service Account JSON file (for Drive/Sheets access)

### Getting Started
1. **Activate the Virtual Environment**:
   ```ps1
   .\venv\Scripts\Activate.ps1
   ```
2. **Install Dependencies**:
   ```ps1
   pip install -r requirements.txt
   ```
   *(Note: Ensure `python-telegram-bot` is installed and the generic conflicting `telegram` package is uninstalled.)*
3. **Run the Smoke Test**:
   ```ps1
   python _smoke_test.py
   ```
   *(Validates your configuration dataclass and exception catching.)*
4. **Start the Bot in Polling Mode**:
   ```ps1
   python main.py
   ```
5. **Send a Test Resume**: Message the bot on Telegram `/start` and upload a `.pdf` file.

---

## 3. Recommended Code Review Path

If you are reviewing this codebase for quality, optimization, or security, please follow this sequence:

### Step 1: Configuration & Entry Point
* **Read**: `config/settings.py` and `main.py`
* **What to look for**: Notice how settings are validated immediately on load using a frozen `dataclass`. Review how `main.py` acts as a Dependency Injection container, initializing clients (e.g., `DriveClient`, `GroqClient`) and injecting them into the `Pipeline`.

### Step 2: The Core Pipeline
* **Read**: `core/pipeline.py`
* **What to look for**: This is the heart of the application. Review the 8-stage processing flow. Notice how it catches specific integration errors and returns a unified `PipelineResult` rather than crashing.

### Step 3: Domain Defenses (Validation & Duplicates)
* **Read**: `core/validator.py` and `core/duplicate_checker.py`
* **What to look for**: 
  * The validator acts as a safety-net for LLM hallucinations. It explicitly standardizes phone numbers (via `phonenumbers`), enforces email regex, and sanitizes numeric outputs (YOE). 
  * The duplicate checker compares incoming fields against the live Google Sheet, ignoring null/empty fields during the check.

### Step 4: AI Prompt Engineering
* **Read**: `integrations/ai/base_client.py` and `prompts/phase1/v1.txt`
* **What to look for**: Review the hot-reloading mechanism implemented in `extract_fields()` which dynamically reads the prompt file. Evaluate the prompt structure in `v1.txt`—especially how it strictly defines calculations for `internship_experience` vs `years_of_experience`.

### Step 5: External Integrations
* **Read**: `integrations/telegram/handlers.py`, `integrations/drive/drive_client.py`
* **What to look for**: Check how the Telegram handlers use `asyncio.to_thread` to execute the synchronous pipeline so the bot's event loop isn't blocked. Review Google API error handling and retry logic.

---

## 4. Known Issues & Future Optimization Opportunities

As a technical reviewer, you might want to look into the following areas for future scalability:

1. **Synchronous Execution Pipeline**: Currently, the pipeline is mostly synchronous (Google API calls, Groq/Gemini calls). While `asyncio.to_thread` prevents the Telegram bot from blocking, migrating the clients inside `integrations/` to native `async/await` would significantly increase throughput.
2. **AI Provider Latency**: Depending on the resume size and the selected AI provider (e.g., Gemini vs Groq), processing can take upwards of 20 - 100 seconds. 
3. **Database Migration**: Currently, Google Sheets serves as the database for candidate lookup (`duplicate_checker.py`). At higher volumes, replacing this with a Postgres database + SQLAlchemy will improve performance and query safety.

---
*Created automatically for tech review onboarding.*
