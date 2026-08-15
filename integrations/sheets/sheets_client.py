"""Google Sheets integration for structured candidate storage.

Handles appending rows, reading existing data for duplicate checks,
and managing the candidate sheet schema.
"""

import logging
import time
from datetime import datetime, timezone

import gspread
from google.oauth2 import service_account

from core.exceptions import SheetsError

logger = logging.getLogger(__name__)

# Required OAuth scope for Sheets
_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Retry config (per master prompt: 3 retries for Sheets)
_MAX_RETRIES = 3
_RETRY_DELAY_SECONDS = 2

# Column headers for Phase 1 — order matters, matches Sheet layout.
# Geography = regions candidate has SOLD INTO (not where they live).
# SaaS Experience = role type + motion (e.g. "Outbound SDR, mid-market SaaS").
# Market Segment = SMB / Mid-Market / Enterprise (per relevant role).
# Data Source Note = human-review flag when resume and company data conflict.
COLUMN_HEADERS = [
    "Timestamp",
    "Full Name",
    "Email",
    "LinkedIn URL",
    "Phone Number",
    "Years of Experience",
    "Current Company",
    "Is SaaS Company",
    "Past Companies",
    "College",
    "Geography",
    "SaaS Experience",
    "Market Segment",
    "Data Source Note",
    "Drive File Link",
    "Source",
    "Status",
    "Duplicate Reason",
    "Matched Field",
    "Matched Row ID",
    "Audit File",
]

# Headers for the secondary "Open Sales Roles" tab
OPEN_ROLES_TAB_NAME = "Open Sales Roles"
OPEN_ROLES_HEADERS = [
    "Company",
    "Role Title",
    "Required Experience",
    "Link",
    "Last Checked",
]

# Headers for the "Classification Audit" tab — read-only explainability data.
# One row per field (Geography / SaaS Experience / Market Segment) per resume.
# Column order must match core/audit_builder.build_audit_rows().
AUDIT_TAB_NAME = "Classification Audit"
AUDIT_HEADERS = [
    "Timestamp",
    "Candidate",
    "Field",
    "Final Value",
    "Evidence",
    "Source Section",
    "Rule Matched",
    "Match Type",
    "Why Selected",
    "Why Others Rejected",
    "Blank Reason",
    "Enrichment Status",
    "Confidence",
]


def _cell(val) -> str:
    """Helper to convert value to string, preserving 0 or other falsy non-None values."""
    return str(val) if val is not None else ""


def _cell_list(val) -> str:
    """Convert a list or string to a comma-joined string for a single cell.

    Handles:
      - None / empty string → ""
      - list → ", ".join(str items)
      - str → returned as-is (already serialized from a previous round-trip)
    """
    if val is None:
        return ""
    if isinstance(val, list):
        return "; ".join(str(v) for v in val if v)
    return str(val)



class SheetsClient:
    """Google Sheets client for candidate data storage.

    Args:
        credentials_path: Path to the service account JSON credentials file.
        sheet_id: The Google Sheets spreadsheet ID.
    """

    def __init__(self, credentials_path: str, sheet_id: str) -> None:
        try:
            creds = service_account.Credentials.from_service_account_file(
                credentials_path, scopes=_SCOPES
            )
            gc = gspread.authorize(creds)
            self._spreadsheet = gc.open_by_key(sheet_id)
            self._worksheet = self._spreadsheet.sheet1
            logger.info("SheetsClient initialized for sheet '%s'", sheet_id)

            # Ensure headers exist on all tabs
            self._ensure_headers()
            self._ensure_open_roles_tab()
            self._ensure_audit_tab()

        except Exception as exc:
            raise SheetsError("init", f"Failed to initialize: {exc}") from exc

    def _ensure_headers(self) -> None:
        """Write column headers to row 1 if missing or stale.

        Writes headers if row 1 is empty OR if the existing headers don't
        match COLUMN_HEADERS (e.g. an old version with missing columns).
        Idempotent when headers are already correct.
        """
        try:
            existing = self._worksheet.row_values(1)
            headers_correct = (
                existing == COLUMN_HEADERS
            )
            if not headers_correct:
                self._worksheet.update(
                    "A1",
                    [COLUMN_HEADERS],
                    value_input_option="RAW",
                )
                if existing:
                    logger.warning(
                        "Stale/mismatched headers detected — overwritten. "
                        "Old count: %d, New count: %d",
                        len(existing), len(COLUMN_HEADERS),
                    )
                else:
                    logger.info("Wrote column headers to sheet (was empty)")
        except Exception as exc:
            logger.warning("Could not ensure headers: %s", exc)

    def _ensure_open_roles_tab(self) -> None:
        """Create the 'Open Sales Roles' worksheet if it doesn't exist.

        Idempotent — skips if the tab already exists with correct headers.
        """
        try:
            try:
                ws = self._spreadsheet.worksheet(OPEN_ROLES_TAB_NAME)
            except gspread.WorksheetNotFound:
                ws = self._spreadsheet.add_worksheet(
                    title=OPEN_ROLES_TAB_NAME, rows=1000, cols=len(OPEN_ROLES_HEADERS)
                )
                logger.info("Created '%s' tab", OPEN_ROLES_TAB_NAME)

            existing = ws.row_values(1)
            if not existing:
                ws.update("A1", [OPEN_ROLES_HEADERS], value_input_option="RAW")
                logger.info("Wrote headers to '%s' tab", OPEN_ROLES_TAB_NAME)
            self._open_roles_ws = ws
        except Exception as exc:
            logger.warning("Could not ensure '%s' tab: %s", OPEN_ROLES_TAB_NAME, exc)
            self._open_roles_ws = None

    def append_row(
        self,
        extracted_data: dict,
        drive_link: str,
        source: str = "telegram",
        status: str = "Processed",
        duplicate_reason: str = "",
        matched_field: str = "",
        matched_row_id: str = "",
    ) -> int:
        """Append a candidate row to the sheet.

        Args:
            extracted_data: The validated 9-field dict from AI extraction.
            drive_link: Google Drive view link for the resume file.
            source: Where the resume came from (e.g. "telegram").
            status: Row status — "Processed" or "Possible Duplicate".
            duplicate_reason: Why it's a duplicate (e.g. "Email Match").
            matched_field: Which field matched (e.g. "email").
            matched_row_id: Row number of the matched record.

        Returns:
            The row number of the newly appended row.

        Raises:
            SheetsError: If the write fails after all retries.
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        # Build row by header name — position-independent.
        # Any new column added to COLUMN_HEADERS is automatically included here.
        row_map = {
            "Timestamp": timestamp,
            "Full Name": extracted_data.get("full_name") or "",
            "Email": extracted_data.get("email") or "",
            "LinkedIn URL": extracted_data.get("linkedin_url") or "",
            "Phone Number": extracted_data.get("phone_number") or "",
            "Years of Experience": _cell(extracted_data.get("years_of_experience")),
            "Current Company": extracted_data.get("current_company") or "",
            # is_saas_company is 'Yes', 'No', or '' (blank = genuinely uncertain).
            # _cell() correctly writes an empty string without shifting later columns.
            "Is SaaS Company": _cell(extracted_data.get("is_saas_company")),
            "Past Companies": _cell_list(extracted_data.get("past_companies")),
            "College": extracted_data.get("college") or "",
            # Enriched fields — new meaning vs. original schema:
            # Geography     = regions sold INTO (not where candidate lives)
            # SaaS Experience = role type + motion description
            # Market Segment  = SMB / Mid-Market / Enterprise
            "Geography": extracted_data.get("geography") or "",
            "SaaS Experience": extracted_data.get("saas_experience") or "",
            "Market Segment": extracted_data.get("market_segment") or "",
            "Data Source Note": extracted_data.get("data_source_note") or "",
            "Drive File Link": drive_link,
            "Source": source,
            "Status": status,
            "Duplicate Reason": duplicate_reason,
            "Matched Field": matched_field,
            "Matched Row ID": matched_row_id,
            # Traceability to the per-resume forensic audit report.
            # Only the relative path is stored — never evidence blobs.
            "Audit File": extracted_data.get("audit_file") or "",
        }
        row = [row_map.get(h, "") for h in COLUMN_HEADERS]

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                logger.info(
                    "Appending row to sheet (attempt %d/%d)",
                    attempt,
                    _MAX_RETRIES,
                )
                self._worksheet.append_row(
                    row, value_input_option="RAW", table_range="A1"
                )
                # Get the row number we just wrote
                row_count = len(self._worksheet.get_all_values())
                logger.info("Row appended successfully at row %d", row_count)
                return row_count

            except Exception as exc:
                if attempt < _MAX_RETRIES:
                    logger.warning(
                        "Sheet write attempt %d failed, retrying: %s",
                        attempt,
                        exc,
                    )
                    time.sleep(_RETRY_DELAY_SECONDS * attempt)
                else:
                    raise SheetsError(
                        "append", f"All {_MAX_RETRIES} attempts failed: {exc}"
                    ) from exc

        # Unreachable but satisfies type checker
        raise SheetsError("append", "Unexpected failure")

    def get_all_records(self) -> list[dict]:
        """Read all rows from the sheet as a list of dicts.

        Used by the duplicate checker to compare against existing candidates.

        Returns:
            List of dicts, one per row, keyed by column headers.
            Includes a '_row_number' key for each record (1-indexed,
            including header row).

        Raises:
            SheetsError: If the read fails.
        """
        try:
            all_values = self._worksheet.get_all_values()

            if len(all_values) <= 1:
                # Only header row or empty
                return []

            headers = all_values[0]
            records = []

            for i, row in enumerate(all_values[1:], start=2):
                record = {}
                for j, header in enumerate(headers):
                    record[header] = row[j] if j < len(row) else ""
                record["_row_number"] = i
                records.append(record)

            logger.info("Read %d existing records from sheet", len(records))
            return records

        except Exception as exc:
            raise SheetsError("read", str(exc)) from exc

    def _ensure_audit_tab(self) -> None:
        """Create the 'Classification Audit' worksheet if it doesn't exist.

        Idempotent — skips if the tab already exists with correct headers.
        The audit sheet is read-only debugging data; it must never influence
        candidate classification.
        """
        try:
            try:
                ws = self._spreadsheet.worksheet(AUDIT_TAB_NAME)
            except gspread.WorksheetNotFound:
                ws = self._spreadsheet.add_worksheet(
                    title=AUDIT_TAB_NAME, rows=1000, cols=len(AUDIT_HEADERS)
                )
                logger.info("Created '%s' tab", AUDIT_TAB_NAME)

            existing = ws.row_values(1)
            if not existing:
                ws.update("A1", [AUDIT_HEADERS], value_input_option="RAW")
                logger.info("Wrote headers to '%s' tab", AUDIT_TAB_NAME)
            elif existing != AUDIT_HEADERS:
                logger.warning(
                    "'%s' tab headers mismatch — expected %d cols, found %d. "
                    "Leaving existing headers untouched to avoid clobbering data.",
                    AUDIT_TAB_NAME, len(AUDIT_HEADERS), len(existing),
                )
            self._audit_ws = ws
        except Exception as exc:
            logger.warning("Could not ensure '%s' tab: %s", AUDIT_TAB_NAME, exc)
            self._audit_ws = None

    def append_audit_rows(self, rows: list[list]) -> None:
        """Append audit rows to the 'Classification Audit' tab.

        Stamps one shared timestamp on every row (column 0). Silently skips if
        the tab was not initialised. Non-fatal by contract — callers must never
        let audit writes break the main pipeline.

        Args:
            rows: List of rows matching AUDIT_HEADERS (13 columns). Column 0
                  (Timestamp) may be empty — it is filled in here.
        """
        ws = getattr(self, "_audit_ws", None)
        if ws is None:
            logger.warning("%s tab not available — skipping audit write", AUDIT_TAB_NAME)
            return

        if not rows:
            return

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        rows_to_append = []
        for row in rows:
            r = list(row)
            if len(r) < len(AUDIT_HEADERS):
                r += [""] * (len(AUDIT_HEADERS) - len(r))
            r = r[: len(AUDIT_HEADERS)]
            r[0] = timestamp
            rows_to_append.append(r)

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                ws.append_rows(rows_to_append, value_input_option="RAW")
                logger.info(
                    "Appended %d classification audit rows (attempt %d)",
                    len(rows_to_append),
                    attempt,
                )
                return
            except Exception as exc:
                if attempt < _MAX_RETRIES:
                    logger.warning(
                        "Audit write attempt %d failed, retrying: %s",
                        attempt, exc,
                    )
                    time.sleep(_RETRY_DELAY_SECONDS * attempt)
                else:
                    logger.error("All retries failed for audit write: %s", exc)

    def append_open_roles_rows(
        self,
        openings: list[dict],
        checked_at: str = "",
    ) -> None:
        """Append sales job openings to the 'Open Sales Roles' tab.

        Each opening is one row: Company | Role Title | Required Experience |
        Link | Last Checked.

        Silently skips if the Open Sales Roles tab was not initialised
        (e.g., if _ensure_open_roles_tab failed during init).

        Args:
            openings: List of dicts with keys: company, role_title,
                      required_exp, link.
            checked_at: ISO timestamp string. Defaults to now (UTC).
        """
        ws = getattr(self, "_open_roles_ws", None)
        if ws is None:
            logger.warning("Open Sales Roles tab not available — skipping write")
            return

        if not openings:
            return

        if not checked_at:
            checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        rows_to_append = [
            [
                o.get("company", ""),
                o.get("role_title", ""),
                o.get("required_exp", ""),
                o.get("link", ""),
                checked_at,
            ]
            for o in openings
        ]

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                ws.append_rows(rows_to_append, value_input_option="RAW")
                logger.info(
                    "Appended %d opening rows to '%s' (attempt %d)",
                    len(rows_to_append),
                    OPEN_ROLES_TAB_NAME,
                    attempt,
                )
                return
            except Exception as exc:
                if attempt < _MAX_RETRIES:
                    logger.warning(
                        "Open Roles write attempt %d failed, retrying: %s",
                        attempt, exc,
                    )
                    time.sleep(_RETRY_DELAY_SECONDS * attempt)
                else:
                    logger.error("All retries failed for Open Sales Roles write: %s", exc)
