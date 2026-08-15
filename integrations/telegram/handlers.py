"""Telegram bot message handlers for resume processing.

Handles document uploads and commands. Downloads the file to a temp
directory, delegates to the pipeline, and replies to the recruiter
with the result.
"""

import logging
import tempfile
import time
import asyncio
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from core.exceptions import TelegramError
from core.pipeline import Pipeline, RecruiterMetadata, PipelineResult

logger = logging.getLogger(__name__)

# Retry config for Telegram download (per master prompt: 3 retries)
_MAX_DOWNLOAD_RETRIES = 3
_RETRY_DELAY_SECONDS = 2


class ResumeHandlers:
    """Telegram handlers for the resume processing bot.

    Args:
        pipeline: Initialized Pipeline instance.
    """

    def __init__(self, pipeline: Pipeline) -> None:
        self._pipeline = pipeline

    async def handle_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle the /start command.

        Sends a welcome message explaining how to use the bot.

        Args:
            update: Telegram update.
            context: Telegram callback context.
        """
        welcome_message = (
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
        await update.message.reply_text(welcome_message)

    async def handle_help(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle the /help command.

        Args:
            update: Telegram update.
            context: Telegram callback context.
        """
        help_message = (
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
        await update.message.reply_text(help_message, parse_mode="Markdown")

    async def handle_document(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle incoming document uploads.

        Downloads the file, runs the pipeline, and replies with the result.

        Args:
            update: Telegram update containing a document.
            context: Telegram callback context.
        """
        message = update.message
        document = message.document

        if not document:
            await message.reply_text("⚠️ No document found in this message.")
            return

        filename = document.file_name or "unknown_file"
        user = message.from_user

        logger.info(
            "Document received: '%s' from user %s (%s)",
            filename,
            user.id if user else "unknown",
            user.username if user else "unknown",
        )

        # Send a "processing" indicator
        processing_msg = await message.reply_text(
            f"⏳ Processing *{filename}*...", parse_mode="Markdown"
        )

        # Download the file to a temp directory
        try:
            file_path = await self._download_file(document, context)
        except TelegramError as exc:
            logger.error("Download failed: %s", exc)
            await processing_msg.edit_text(
                f"❌ Failed to download *{filename}* — please try again.",
                parse_mode="Markdown",
            )
            return

        # Build recruiter metadata
        recruiter = RecruiterMetadata(
            user_id=str(user.id) if user else "unknown",
            username=user.username or "no_username" if user else "unknown",
        )

        # Run the pipeline
        try:
            result: PipelineResult = await asyncio.to_thread(
                self._pipeline.process,
                resume_file_path=file_path,
                recruiter_metadata=recruiter,
                source="telegram",
            )
        except Exception as exc:
            logger.critical(
                "Unexpected pipeline error for '%s': %s",
                filename,
                exc,
                exc_info=True,
            )
            await processing_msg.edit_text(
                "❌ An unexpected error occurred — please try again later."
            )
            return
        finally:
            # Clean up the temp file
            try:
                if file_path.exists():
                    file_path.unlink()
                    logger.debug("Temp file cleaned up: %s", file_path)
            except OSError as exc:
                logger.warning("Failed to clean up temp file: %s", exc)

        # Reply with the result
        await processing_msg.edit_text(result.message)

    async def handle_unknown(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle unrecognized messages (text, photos, etc.).

        Args:
            update: Telegram update.
            context: Telegram callback context.
        """
        await update.message.reply_text(
            "📄 Please send a resume file (.pdf or .docx).\n"
            "Type /help for more information."
        )

    @staticmethod
    async def _download_file(document, context) -> Path:
        """Download a Telegram document to a temporary file.

        Retries up to 3 times on transient failures.

        Args:
            document: Telegram Document object.
            context: Telegram callback context.

        Returns:
            Path to the downloaded temporary file.

        Raises:
            TelegramError: If download fails after all retries.
        """
        filename = document.file_name or "unknown"
        suffix = Path(filename).suffix

        for attempt in range(1, _MAX_DOWNLOAD_RETRIES + 1):
            try:
                logger.info(
                    "Downloading '%s' (attempt %d/%d)",
                    filename,
                    attempt,
                    _MAX_DOWNLOAD_RETRIES,
                )

                file_obj = await context.bot.get_file(document.file_id)

                # Create temp file with correct extension
                temp_dir = Path(tempfile.mkdtemp(prefix="resume_bot_"))
                temp_path = temp_dir / filename

                await file_obj.download_to_drive(str(temp_path))

                logger.info(
                    "Download complete: '%s' → %s (%.1f KB)",
                    filename,
                    temp_path,
                    temp_path.stat().st_size / 1024,
                )
                return temp_path

            except Exception as exc:
                if attempt < _MAX_DOWNLOAD_RETRIES:
                    logger.warning(
                        "Download attempt %d failed, retrying: %s",
                        attempt,
                        exc,
                    )
                    time.sleep(_RETRY_DELAY_SECONDS)
                else:
                    raise TelegramError(
                        "download",
                        f"Failed after {_MAX_DOWNLOAD_RETRIES} attempts: {exc}",
                    ) from exc

        # Unreachable but satisfies type checker
        raise TelegramError("download", "Unexpected failure")
