"""Custom exception hierarchy for the Resume Processing Automation System.

Every exception inherits from ResumeBotError so callers can catch the full
family with a single except clause when needed, while still handling
specific failures individually.
"""


class ResumeBotError(Exception):
    """Base exception for all resume-bot errors.

    Args:
        message: Human-readable description of the failure.
        details: Optional dict with structured context for logging
                 (e.g. filename, stage, provider). Never include
                 sensitive data (API keys, resume text).
    """

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        if self.details:
            return f"{self.message} | details={self.details}"
        return self.message


class ConfigurationError(ResumeBotError):
    """Missing or invalid configuration value in .env."""


class FileValidationError(ResumeBotError):
    """File failed pre-pipeline validation.

    Examples: unsupported extension, exceeds size limit, corrupt,
    password-protected.

    Args:
        filename: Name of the rejected file.
        reason: Why the file was rejected.
    """

    def __init__(self, filename: str, reason: str) -> None:
        super().__init__(
            message=f"File validation failed for '{filename}': {reason}",
            details={"filename": filename, "reason": reason},
        )
        self.filename = filename
        self.reason = reason


class ParsingError(ResumeBotError):
    """Text extraction from a resume file failed.

    Args:
        filename: Name of the file that couldn't be parsed.
        reason: Why parsing failed.
    """

    def __init__(self, filename: str, reason: str) -> None:
        super().__init__(
            message=f"Parsing failed for '{filename}': {reason}",
            details={"filename": filename, "reason": reason},
        )
        self.filename = filename
        self.reason = reason


class AIProviderError(ResumeBotError):
    """Gemini AI call failed or returned unusable output.

    Args:
        provider: Which AI provider was in use ("gemini").
        reason: What went wrong (timeout, malformed JSON, etc.).
    """

    def __init__(self, provider: str, reason: str) -> None:
        super().__init__(
            message=f"AI provider '{provider}' error: {reason}",
            details={"provider": provider, "reason": reason},
        )
        self.provider = provider
        self.reason = reason


class ValidationError(ResumeBotError):
    """Post-extraction field validation found problems.

    Args:
        field: The field that failed validation.
        reason: Why the value was rejected.
    """

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(
            message=f"Validation failed for field '{field}': {reason}",
            details={"field": field, "reason": reason},
        )
        self.field = field
        self.reason = reason


class DriveError(ResumeBotError):
    """Google Drive API operation failed.

    Args:
        operation: What was attempted (upload, move, list, etc.).
        reason: Why it failed.
    """

    def __init__(self, operation: str, reason: str) -> None:
        super().__init__(
            message=f"Drive {operation} failed: {reason}",
            details={"operation": operation, "reason": reason},
        )
        self.operation = operation
        self.reason = reason


class SheetsError(ResumeBotError):
    """Google Sheets API operation failed.

    Args:
        operation: What was attempted (append, read, etc.).
        reason: Why it failed.
    """

    def __init__(self, operation: str, reason: str) -> None:
        super().__init__(
            message=f"Sheets {operation} failed: {reason}",
            details={"operation": operation, "reason": reason},
        )
        self.operation = operation
        self.reason = reason


class DuplicateFoundError(ResumeBotError):
    """Candidate appears to already exist in the sheet.

    Args:
        matched_field: Which field matched (email, phone, linkedin_url).
        matched_value: The value that matched.
        matched_row: Row number of the existing record.
    """

    def __init__(
        self, matched_field: str, matched_value: str, matched_row: int
    ) -> None:
        super().__init__(
            message=(
                f"Duplicate candidate: '{matched_field}' = '{matched_value}' "
                f"matches row {matched_row}"
            ),
            details={
                "matched_field": matched_field,
                "matched_value": matched_value,
                "matched_row": matched_row,
            },
        )
        self.matched_field = matched_field
        self.matched_value = matched_value
        self.matched_row = matched_row


class TelegramError(ResumeBotError):
    """Telegram API operation failed.

    Args:
        operation: What was attempted (download, reply, etc.).
        reason: Why it failed.
    """

    def __init__(self, operation: str, reason: str) -> None:
        super().__init__(
            message=f"Telegram {operation} failed: {reason}",
            details={"operation": operation, "reason": reason},
        )
        self.operation = operation
        self.reason = reason


class EnrichmentError(ResumeBotError):
    """Company enrichment lookup failed.

    Always caught and logged in pipeline.py — never allowed to propagate
    and kill the Telegram reply.  The pipeline continues with blank enriched
    fields rather than crashing.

    Args:
        company: The company name being researched when the error occurred.
        reason: What went wrong (network error, AI failure, scrape failure, etc.).
    """

    def __init__(self, company: str, reason: str) -> None:
        super().__init__(
            message=f"Enrichment failed for '{company}': {reason}",
            details={"company": company, "reason": reason},
        )
        self.company = company
        self.reason = reason
