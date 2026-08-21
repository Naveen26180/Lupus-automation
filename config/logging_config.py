"""Logging configuration for the Resume Processing Automation System.

Sets up console + file logging. Called once at application startup.
Never logs sensitive data (API keys, resume text, .env values).
"""

import logging
from pathlib import Path


# Log file lives in resume_bot/logs/app.log
_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_LOG_FILE = _LOG_DIR / "app.log"

# Consistent format across all handlers
_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(log_level: str = "INFO") -> None:
    """Configure application-wide logging with console and file handlers.

    Creates the logs/ directory if it doesn't exist. Safe to call multiple
    times (removes existing handlers first to avoid duplicates during tests).

    Args:
        log_level: Minimum level for console output. File handler always
                   logs at DEBUG level for maximum detail.

    Raises:
        ValueError: If log_level is not a valid Python logging level.
    """
    # Validate the level string
    numeric_level = getattr(logging, log_level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"Invalid log level: '{log_level}'")

    # Ensure logs directory exists
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Root logger — process all messages
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # Remove any existing handlers (prevents duplicate logs on re-init)
    root_logger.handlers.clear()

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # Console handler — respects the configured log level
    console_handler = logging.StreamHandler()
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File handler — always DEBUG for full traceability
    file_handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Quiet noisy third-party loggers.
    # CRITICAL: the Gemini SDK and other network clients can log the FULL
    # request payload — including the resume text with candidate names,
    # emails and phones — at DEBUG level. Because the file handler logs at
    # DEBUG, those payloads would otherwise be written to app.log on every
    # resume. Silencing these loggers is what keeps candidate PII out of the
    # logs; do not remove these lines.
    _QUIET_LOGGERS = (
        "httpx",
        "httpcore",
        "urllib3",       # requests/bs4 HTTP internals
        "requests",
        "telegram",
        "google",
        "googleapiclient",
        "gspread",
        "pdfminer",
    )
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    # Belt-and-braces: any logger named like a network client that reaches
    # DEBUG would risk dumping payloads. Cap unknown third-party loggers
    # (anything not under the app's own namespaces) at INFO.
    app_namespaces = ("config", "core", "integrations", "prompts")
    for name, logger_obj in logging.Logger.manager.loggerDict.items():
        if isinstance(logger_obj, logging.Logger) and not name.startswith(app_namespaces):
            if logger_obj.level == logging.NOTSET or logger_obj.level < logging.INFO:
                logger_obj.setLevel(logging.INFO)

    logging.getLogger(__name__).info(
        "Logging initialized — console=%s, file=DEBUG → %s",
        log_level.upper(),
        _LOG_FILE,
    )
