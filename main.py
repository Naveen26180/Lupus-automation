"""Application entry point for the Resume Processing Bot.

Loads configuration, initializes all integration clients, wires
them into the pipeline, and starts the Telegram bot.
"""

import logging
import sys
from pathlib import Path

# Ensure the project root is on the Python path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import load_settings
from config.logging_config import setup_logging
from core.exceptions import ConfigurationError
from core.pipeline import Pipeline
from integrations.ai.gemini_client import GeminiClient
from integrations.drive.drive_client import DriveClient
from integrations.sheets.sheets_client import SheetsClient
from integrations.telegram.bot import create_bot, run_bot
from integrations.telegram.handlers import ResumeHandlers

logger = logging.getLogger(__name__)


def main() -> None:
    """Initialize all components and start the bot.

    Startup sequence:
    1. Load settings from .env
    2. Configure logging
    3. Initialize AI client (Gemini — the sole AI provider)
    4. Initialize Google Drive client
    5. Initialize Google Sheets client
    6. Create the processing pipeline
    7. Create and start the Telegram bot
    """
    # --- 1. Load settings ---
    try:
        settings = load_settings()
    except ConfigurationError as exc:
        # Can't log yet (logging not configured), so print
        print(f"FATAL: Configuration error — {exc}", file=sys.stderr)
        sys.exit(1)

    # --- 2. Configure logging ---
    setup_logging(settings.LOG_LEVEL)
    logger.info("=" * 60)
    logger.info("Resume Processing Bot starting up")
    logger.info("AI Provider: gemini")
    logger.info("=" * 60)

    # --- 3. Initialize AI client (Gemini is the sole provider) ---
    ai_client = GeminiClient(api_key=settings.GEMINI_API_KEY)

    logger.info("AI client initialized: gemini")

    # --- 4. Initialize Google Drive client ---
    drive_client = DriveClient(
        credentials_path=settings.GOOGLE_DRIVE_CREDENTIALS,
        incoming_folder_id=settings.INCOMING_FOLDER_ID,
        processed_folder_id=settings.PROCESSED_FOLDER_ID,
        duplicate_folder_id=settings.DUPLICATE_FOLDER_ID,
        rejected_folder_id=settings.REJECTED_FOLDER_ID,
    )

    # --- 5. Initialize Google Sheets client ---
    sheets_client = SheetsClient(
        credentials_path=settings.GOOGLE_DRIVE_CREDENTIALS,
        sheet_id=settings.GOOGLE_SHEET_ID,
    )

    # --- 6. Create the pipeline ---
    pipeline = Pipeline(
        settings=settings,
        drive_client=drive_client,
        sheets_client=sheets_client,
        ai_client=ai_client,
    )
    logger.info("Processing pipeline assembled")

    # --- 7. Create and start the Telegram bot ---
    handlers = ResumeHandlers(pipeline=pipeline)
    app = create_bot(bot_token=settings.BOT_TOKEN, handlers=handlers)

    logger.info("Bot is ready — starting polling...")
    run_bot(app)


if __name__ == "__main__":
    main()
