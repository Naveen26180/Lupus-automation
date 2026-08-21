"""Application configuration loaded from .env.

Single responsibility: load, validate, and expose every configuration
value the application needs. No other module reads .env directly.
"""

from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
import os

from core.exceptions import ConfigurationError


# Locate the .env file relative to this file's parent (resume_bot/)
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


@dataclass(frozen=True)
class Settings:
    """Immutable container for all application configuration.

    Frozen to prevent accidental mutation after startup.
    Every attribute maps to an environment variable documented in .env.example.
    """

    # Telegram
    BOT_TOKEN: str

    # AI
    AI_PROVIDER: str          # "groq", "gemini", or "cerebras"
    GROQ_API_KEY: str
    GEMINI_API_KEY: str
    CEREBRAS_API_KEY: str

    # Google
    GOOGLE_DRIVE_CREDENTIALS: str   # path to service account JSON
    GOOGLE_SHEET_ID: str

    # Drive folder IDs
    INCOMING_FOLDER_ID: str
    PROCESSED_FOLDER_ID: str
    DUPLICATE_FOLDER_ID: str
    REJECTED_FOLDER_ID: str

    # Operational
    LOG_LEVEL: str
    MAX_FILE_SIZE_MB: int


def load_settings() -> Settings:
    """Load and validate configuration from the .env file.

    Returns:
        A frozen Settings dataclass with all config values.

    Raises:
        ConfigurationError: If a required variable is missing or
            AI_PROVIDER has an invalid value.
    """
    load_dotenv(_ENV_PATH, encoding="utf-8-sig")

    ai_provider = os.getenv("AI_PROVIDER", "groq").lower().strip()
    if ai_provider not in ("groq", "gemini", "cerebras"):
        raise ConfigurationError(
            f"AI_PROVIDER must be 'groq', 'gemini', or 'cerebras', got '{ai_provider}'"
        )

    # --- Required keys (always needed) ---
    required_keys = [
        "BOT_TOKEN",
        "GOOGLE_DRIVE_CREDENTIALS",
        "GOOGLE_SHEET_ID",
        "INCOMING_FOLDER_ID",
        "PROCESSED_FOLDER_ID",
        "DUPLICATE_FOLDER_ID",
        "REJECTED_FOLDER_ID",
    ]

    missing = [key for key in required_keys if not os.getenv(key)]
    if missing:
        raise ConfigurationError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    # --- Provider-specific key ---
    if ai_provider == "groq" and not os.getenv("GROQ_API_KEY"):
        raise ConfigurationError(
            "AI_PROVIDER is 'groq' but GROQ_API_KEY is not set"
        )
    if ai_provider == "gemini" and not os.getenv("GEMINI_API_KEY"):
        raise ConfigurationError(
            "AI_PROVIDER is 'gemini' but GEMINI_API_KEY is not set"
        )
    if ai_provider == "cerebras" and not os.getenv("CEREBRAS_API_KEY"):
        raise ConfigurationError(
            "AI_PROVIDER is 'cerebras' but CEREBRAS_API_KEY is not set"
        )

    # Resolve GOOGLE_DRIVE_CREDENTIALS to an absolute path.
    # Accepts either an absolute path (local Windows setup) or a path
    # relative to the project root (deployment-friendly: keep the JSON
    # next to .env and reference it by name).
    creds_path = os.getenv("GOOGLE_DRIVE_CREDENTIALS", "")
    if creds_path:
        _p = Path(creds_path)
        if not _p.is_absolute():
            _p = Path(__file__).resolve().parent.parent / _p
        creds_path = str(_p)

    # --- Parse MAX_FILE_SIZE_MB (optional, defaults to 20) ---
    try:
        max_file_size = int(os.getenv("MAX_FILE_SIZE_MB", "20"))
    except ValueError:
        raise ConfigurationError(
            f"MAX_FILE_SIZE_MB must be an integer, got '{os.getenv('MAX_FILE_SIZE_MB')}'"
        )

    return Settings(
        BOT_TOKEN=os.getenv("BOT_TOKEN", ""),
        AI_PROVIDER=ai_provider,
        GROQ_API_KEY=os.getenv("GROQ_API_KEY", ""),
        GEMINI_API_KEY=os.getenv("GEMINI_API_KEY", ""),
        CEREBRAS_API_KEY=os.getenv("CEREBRAS_API_KEY", ""),
        GOOGLE_DRIVE_CREDENTIALS=creds_path,
        GOOGLE_SHEET_ID=os.getenv("GOOGLE_SHEET_ID", ""),
        INCOMING_FOLDER_ID=os.getenv("INCOMING_FOLDER_ID", ""),
        PROCESSED_FOLDER_ID=os.getenv("PROCESSED_FOLDER_ID", ""),
        DUPLICATE_FOLDER_ID=os.getenv("DUPLICATE_FOLDER_ID", ""),
        REJECTED_FOLDER_ID=os.getenv("REJECTED_FOLDER_ID", ""),
        LOG_LEVEL=os.getenv("LOG_LEVEL", "INFO").upper().strip(),
        MAX_FILE_SIZE_MB=max_file_size,
    )
