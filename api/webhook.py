"""Telegram webhook entry point for serverless deployment (Vercel).

Event-driven intake: Telegram POSTs each update to this endpoint, the
pipeline runs once, and the result is replied back over the Bot API.
No long-running process — the function only wakes when a recruiter
uploads a resume.

This module intentionally does NOT use python-telegram-bot's Application
lifecycle (initialize/start/shutdown) because serverless functions are
stateless and short-lived. It talks to the Telegram Bot API directly
with `requests` and reuses the exact same core pipeline.

Why this is safe:
- The deterministic classifier and validator are untouched — this is
  purely a new intake channel for the same Pipeline.
- Every webhook failure still returns HTTP 200 so Telegram does not
  retry-storm; the recruiter gets an error message instead.
- Duplicate update_ids (Telegram webhook retries) are dropped in-memory.
"""

import logging
import os
import tempfile
import time
from pathlib import Path

import requests
from fastapi import FastAPI, Request
from mangum import Mangum

from config.settings import load_settings
from core.pipeline import Pipeline, RecruiterMetadata
from integrations.ai.gemini_client import GeminiClient
from integrations.drive.drive_client import DriveClient
from integrations.sheets.sheets_client import SheetsClient

logger = logging.getLogger(__name__)

app = FastAPI(title="Lupus Automation — Telegram Webhook")

# Lazy-built pipeline (reused across warm invocations).
_pipeline: Pipeline | None = None

# Telegram retries webhooks whose processing outlives its timeout.
# Drop repeated update_ids so a retry can never double-process a resume.
_DEDUP_TTL_SECONDS = 600
_seen_update_ids: dict[int, float] = {}

_MAX_DOWNLOAD_RETRIES = 3
_RETRY_DELAY_SECONDS = 2

_WELCOME_MESSAGE = (
    "👋 Welcome to the Resume Processing Bot!\n\n"
    "📄 Send me a resume file (.pdf or .docx) and I'll:\n"
    "• Extract key candidate information\n"
    "• Store the file in Google Drive\n"
    "• Save the data to Google Sheets\n\n"
    "📏 File limits:\n"
    "• Formats: .pdf, .docx only\n"
    "• Max size: 20 MB\n\n"
    "Just send a file to get started!"
)

_HELP_MESSAGE = (
    "📋 *Resume Bot Help*\n\n"
    "*Commands:*\n"
    "/start — Welcome message\n"
    "/help — This help message\n\n"
    "*How to use:*\n"
    "1. Send a .pdf or .docx resume file\n"
    "2. Wait for processing (usually 5–15 seconds)\n"
    "3. Receive confirmation with extracted details\n\n"
    "*Supported fields:*\n"
    "Name, Email, LinkedIn, Phone, Experience, "
    "College, Geography, SaaS Experience, Market Segment, "
    "Current Company, Past Companies, Internship Experience"
)

_PROMPT_MESSAGE = (
    "📄 Please send a resume file (.pdf or .docx).\n"
    "Type /help for more information."
)


# ---------------------------------------------------------------------------
# Configuration / pipeline construction
# ---------------------------------------------------------------------------
def _ensure_credentials_file() -> None:
    """Materialize the Google service-account JSON on an ephemeral disk.

    Serverless platforms (Vercel) have no persistent filesystem, so the
    key travels as the GOOGLE_DRIVE_CREDENTIALS_JSON env var and is
    written to a temp file before settings load.
    """
    if os.getenv("GOOGLE_DRIVE_CREDENTIALS"):
        return
    creds_json = os.getenv("GOOGLE_DRIVE_CREDENTIALS_JSON")
    if not creds_json:
        return
    tmp_path = Path(tempfile.gettempdir()) / "lupus-credentials.json"
    tmp_path.write_text(creds_json, encoding="utf-8")
    os.environ["GOOGLE_DRIVE_CREDENTIALS"] = str(tmp_path)


def build_pipeline() -> Pipeline:
    """Build the full pipeline from environment configuration."""
    _ensure_credentials_file()
    settings = load_settings()

    ai_client = GeminiClient(api_key=settings.GEMINI_API_KEY)

    drive_client = DriveClient(
        credentials_path=settings.GOOGLE_DRIVE_CREDENTIALS,
        incoming_folder_id=settings.INCOMING_FOLDER_ID,
        processed_folder_id=settings.PROCESSED_FOLDER_ID,
        duplicate_folder_id=settings.DUPLICATE_FOLDER_ID,
        rejected_folder_id=settings.REJECTED_FOLDER_ID,
    )
    sheets_client = SheetsClient(
        credentials_path=settings.GOOGLE_DRIVE_CREDENTIALS,
        sheet_id=settings.GOOGLE_SHEET_ID,
    )
    return Pipeline(
        settings=settings,
        drive_client=drive_client,
        sheets_client=sheets_client,
        ai_client=ai_client,
    )


def get_pipeline() -> Pipeline:
    """Return the cached pipeline, building it on first use."""
    global _pipeline
    if _pipeline is None:
        _pipeline = build_pipeline()
    return _pipeline


# ---------------------------------------------------------------------------
# Telegram Bot API helpers (plain HTTP — no PTB Application needed)
# ---------------------------------------------------------------------------
def telegram_api(token: str, method: str, payload: dict | None = None) -> dict:
    """POST a method to the Telegram Bot API and return the JSON body."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    resp = requests.post(url, json=payload or {}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def send_message(token: str, chat_id: int, text: str, **extra) -> dict:
    return telegram_api(
        token, "sendMessage", {"chat_id": chat_id, "text": text, **extra}
    )


def edit_message(token: str, chat_id: int, message_id: int, text: str) -> dict:
    return telegram_api(
        token,
        "editMessageText",
        {"chat_id": chat_id, "message_id": message_id, "text": text},
    )


def download_file(token: str, file_path: str, dest: Path) -> None:
    """Download a Telegram file (from getFile's file_path) with retries."""
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    for attempt in range(1, _MAX_DOWNLOAD_RETRIES + 1):
        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            return
        except Exception as exc:
            if attempt < _MAX_DOWNLOAD_RETRIES:
                logger.warning(
                    "Download attempt %d/%d failed for %s: %s",
                    attempt,
                    _MAX_DOWNLOAD_RETRIES,
                    file_path,
                    exc,
                )
                time.sleep(_RETRY_DELAY_SECONDS)
            else:
                raise


# ---------------------------------------------------------------------------
# Update handling
# ---------------------------------------------------------------------------
def _is_duplicate(update_id: int) -> bool:
    now = time.time()
    # Prune stale ids so the dict never grows unbounded.
    stale = [uid for uid, ts in _seen_update_ids.items() if now - ts > _DEDUP_TTL_SECONDS]
    for uid in stale:
        _seen_update_ids.pop(uid, None)
    if update_id in _seen_update_ids:
        return True
    _seen_update_ids[update_id] = now
    return False


def process_update(update: dict) -> None:
    """Handle one Telegram update: commands, prompts, and resume intake."""
    token = os.getenv("BOT_TOKEN", "")
    update_id = update.get("update_id")
    if update_id is not None and _is_duplicate(int(update_id)):
        logger.info("Dropping duplicate update_id=%s (webhook retry)", update_id)
        return

    message = update.get("message")
    if not message:
        return

    chat_id = message.get("chat", {}).get("id")
    if chat_id is None:
        return
    chat_id = int(chat_id)

    sender = message.get("from", {}) or {}
    text = (message.get("text") or "").strip()

    # --- Commands ---
    if text == "/start":
        send_message(token, chat_id, _WELCOME_MESSAGE, reply_to_message_id=message.get("message_id"))
        return
    if text == "/help":
        send_message(
            token,
            chat_id,
            _HELP_MESSAGE,
            reply_to_message_id=message.get("message_id"),
            parse_mode="Markdown",
        )
        return
    if text and not message.get("document"):
        send_message(token, chat_id, _PROMPT_MESSAGE, reply_to_message_id=message.get("message_id"))
        return

    # --- Document intake ---
    document = message.get("document")
    if not document:
        return
    filename = document.get("file_name") or "unknown_file"
    file_id = document.get("file_id")
    logger.info("Document received via webhook: '%s' (chat %s)", filename, chat_id)

    try:
        processing = send_message(
            token, chat_id, f"⏳ Processing *{filename}*...", parse_mode="Markdown"
        )
    except Exception:
        logger.exception("Failed to send processing indicator")
        return
    message_id = processing.get("result", {}).get("message_id")

    file_path: Path | None = None
    try:
        # 1. Resolve + download the file from Telegram
        get_file = telegram_api(token, "getFile", {"file_id": file_id})
        tg_file_path = get_file.get("result", {}).get("file_path")
        if not tg_file_path:
            raise RuntimeError("Telegram returned no file_path for the document")

        temp_dir = Path(tempfile.mkdtemp(prefix="resume_bot_webhook_"))
        file_path = temp_dir / filename
        download_file(token, tg_file_path, file_path)
        logger.info("Downloaded '%s' (%.1f KB)", filename, file_path.stat().st_size / 1024)

        # 2. Run the exact same pipeline
        result = get_pipeline().process(
            resume_file_path=file_path,
            recruiter_metadata=RecruiterMetadata(
                user_id=str(sender.get("id", "unknown")),
                username=sender.get("username") or "no_username",
            ),
            source="telegram-webhook",
        )
        reply = result.message
    except Exception as exc:
        logger.critical("Pipeline error for '%s': %s", filename, exc, exc_info=True)
        reply = "❌ An unexpected error occurred — please try again later."
    finally:
        if file_path is not None:
            try:
                file_path.unlink(missing_ok=True)
            except OSError:
                pass

    try:
        edit_message(token, chat_id, message_id, reply)
    except Exception:
        logger.exception("Failed to send final result")


@app.post("/api/webhook")
async def webhook(request: Request) -> dict:
    """Receive a Telegram update and process it synchronously.

    Must be async: Starlette's Request.json() is a coroutine. In a sync
    endpoint it returns an unawaited coroutine and the update is never
    read (observed in production: 200 + 10ms + zero outgoing calls).
    """
    try:
        update = await request.json()
    except Exception:
        logger.exception("Invalid webhook payload")
        return {"ok": True}

    if not isinstance(update, dict):
        return {"ok": True}

    try:
        process_update(update)
    except Exception:
        # Never let Telegram retry-storm us; log and move on.
        logger.exception("Webhook handler failed")
    return {"ok": True}


# Vercel Python runtime entry point (ASGI via Mangum).
handler = Mangum(app)
