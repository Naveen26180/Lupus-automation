"""Telegram bot initialization and startup.

Creates the Telegram Application, registers handlers, and provides
a run method. Separated from handlers.py so the bot setup can be
tested independently.
"""

import logging

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

from integrations.telegram.handlers import ResumeHandlers

logger = logging.getLogger(__name__)


def create_bot(bot_token: str, handlers: ResumeHandlers) -> Application:
    """Create and configure the Telegram bot application.

    Registers all command and message handlers.

    Args:
        bot_token: Telegram Bot API token.
        handlers: Initialized ResumeHandlers instance.

    Returns:
        Configured Telegram Application ready to run.
    """
    logger.info("Creating Telegram bot application")

    app = Application.builder().token(bot_token).build()

    # Command handlers
    app.add_handler(CommandHandler("start", handlers.handle_start))
    app.add_handler(CommandHandler("help", handlers.handle_help))

    # Document handler — processes file uploads
    app.add_handler(
        MessageHandler(filters.Document.ALL, handlers.handle_document)
    )

    # Catch-all for unrecognized messages
    app.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND & ~filters.Document.ALL,
            handlers.handle_unknown,
        )
    )

    logger.info("Telegram bot configured with all handlers")
    return app


def run_bot(app: Application) -> None:
    """Start the Telegram bot in polling mode.

    Blocks until the bot is stopped (Ctrl+C).

    Args:
        app: Configured Telegram Application.
    """
    logger.info("Starting Telegram bot (polling mode)")
    app.run_polling(drop_pending_updates=True)
