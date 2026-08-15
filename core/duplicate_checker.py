"""Duplicate candidate detection by comparing extracted fields against existing sheet records.

Checks email, phone number, and LinkedIn URL against all existing rows.
If any match is found, the candidate is flagged as a possible duplicate.
"""

import logging

from core.exceptions import DuplicateFoundError

logger = logging.getLogger(__name__)


def check_for_duplicates(
    extracted_data: dict,
    existing_records: list[dict],
) -> None:
    """Compare extracted candidate fields against existing sheet records.

    Checks three fields in priority order: email, phone_number, linkedin_url.
    Stops at the first match found.

    Args:
        extracted_data: The validated 9-field dict from AI extraction.
        existing_records: List of dicts from SheetsClient.get_all_records(),
                         each containing column headers as keys and a
                         '_row_number' key.

    Raises:
        DuplicateFoundError: If a matching record is found. Contains
            the matched field name, value, and row number.
    """
    if not existing_records:
        logger.debug("No existing records to check against")
        return

    # Fields to check and their corresponding sheet column names
    checks = [
        ("email", "Email"),
        ("phone_number", "Phone Number"),
        ("linkedin_url", "LinkedIn URL"),
    ]

    for field_key, column_name in checks:
        candidate_value = extracted_data.get(field_key)

        # Skip null / empty values — can't match on nothing
        if not candidate_value:
            continue

        candidate_normalized = _normalize(candidate_value)

        for record in existing_records:
            existing_value = record.get(column_name, "")

            if not existing_value:
                continue

            existing_normalized = _normalize(existing_value)

            if candidate_normalized == existing_normalized:
                row_num = record.get("_row_number", "unknown")
                logger.info(
                    "Duplicate found: %s='%s' matches row %s",
                    field_key,
                    candidate_value,
                    row_num,
                )
                raise DuplicateFoundError(
                    matched_field=field_key,
                    matched_value=candidate_value,
                    matched_row=row_num,
                )

    logger.info("No duplicates found")


def _normalize(value: str) -> str:
    """Normalize a value for comparison.

    Strips whitespace and lowercases for case-insensitive matching.
    For phone numbers, also strips common formatting characters.

    Args:
        value: The string value to normalize.

    Returns:
        Normalized lowercase string.
    """
    result = str(value).strip().lower()

    # If it looks like a phone number (digits + common separators),
    # strip everything except digits and leading +
    if any(c.isdigit() for c in result):
        cleaned = "".join(
            c for c in result if c.isdigit() or c == "+"
        )
        # Only use cleaned version if it has enough digits to be a phone
        digits_only = "".join(c for c in cleaned if c.isdigit())
        if len(digits_only) >= 7:
            return cleaned

    return result
