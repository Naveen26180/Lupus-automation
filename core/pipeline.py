"""Core pipeline — orchestrates the full resume processing workflow.

This module coordinates the flow from file validation through AI extraction
to storage. It calls integration modules but never accesses external APIs
directly. The pipeline accepts exactly three things: resume_file_path,
recruiter_metadata, and source — it never knows which intake channel sent
the request.
"""

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from config.settings import Settings
from core.duplicate_checker import check_for_duplicates
from core.exceptions import (
    DuplicateFoundError,
    EnrichmentError,
    FileValidationError,
    ParsingError,
    AIProviderError,
    DriveError,
    SheetsError,
    ResumeBotError,
)
from core.validator import validate_extracted_fields
from integrations.ai.base_client import BaseAIClient
from integrations.drive.drive_client import DriveClient
from integrations.parsers.pdf_parser import extract_text_from_pdf
from integrations.parsers.docx_parser import extract_text_from_docx
from integrations.sheets.sheets_client import SheetsClient

logger = logging.getLogger(__name__)

# Allowed file extensions (Phase 1)
_ALLOWED_EXTENSIONS = {".pdf", ".docx"}


@dataclass
class PipelineResult:
    """Result of a pipeline run.

    Attributes:
        success: Whether the pipeline completed successfully.
        message: Human-readable summary for the recruiter.
        stage: The last stage reached (for debugging/logging).
        drive_link: Google Drive view link (if file was uploaded).
        extracted_data: The validated extraction dict (if extraction succeeded).
        is_duplicate: Whether the candidate was flagged as a duplicate.
        error: The exception that caused failure, if any.
    """
    success: bool = False
    message: str = ""
    stage: str = ""
    drive_link: str = ""
    extracted_data: dict = field(default_factory=dict)
    is_duplicate: bool = False
    error: Exception | None = None


@dataclass
class RecruiterMetadata:
    """Metadata about the recruiter who submitted the resume.

    Attributes:
        user_id: Platform-specific user ID (e.g. Telegram user ID).
        username: Platform-specific username.
    """
    user_id: str = ""
    username: str = ""


class Pipeline:
    """Orchestrates the resume processing workflow.

    Coordinates file validation, Drive upload, text extraction,
    AI extraction, validation, duplicate checking, and Sheets storage.
    Each integration is injected via the constructor — the pipeline
    itself never creates API clients.

    Args:
        settings: Application settings.
        drive_client: Initialized DriveClient.
        sheets_client: Initialized SheetsClient.
        ai_client: Initialized AI client (Gemini).
    """

    def __init__(
        self,
        settings: Settings,
        drive_client: DriveClient,
        sheets_client: SheetsClient,
        ai_client: BaseAIClient,
    ) -> None:
        self._settings = settings
        self._drive = drive_client
        self._sheets = sheets_client
        self._ai = ai_client

    def process(
        self,
        resume_file_path: Path,
        recruiter_metadata: RecruiterMetadata,
        source: str = "telegram",
    ) -> PipelineResult:
        """Run the complete resume processing pipeline.

        Steps (per master prompt Flow Phase 1):
        1. File validation (extension, size, corruption, password-protection)
        2. Upload to Drive Incoming/
        3. Extract raw text (PDF/DOCX)
        4. AI extraction → structured JSON
        5. Post-extraction validation
        6. Duplicate check
        7. Append row to Sheets
        8. Move file to Processed/ (or Duplicates/)

        Args:
            resume_file_path: Path to the downloaded resume file.
            recruiter_metadata: Info about the recruiter who sent it.
            source: Intake channel identifier (e.g. "telegram").

        Returns:
            PipelineResult with success status and details.
        """
        start_time = time.time()
        filename = resume_file_path.name
        drive_file_id: str | None = None

        logger.info(
            "Pipeline started — file='%s', user=%s (%s), source=%s",
            filename,
            recruiter_metadata.user_id,
            recruiter_metadata.username,
            source,
        )

        # --- Stage 1: File Validation ---
        try:
            self._validate_file(resume_file_path)
        except FileValidationError as exc:
            logger.warning("File validation failed: %s", exc)
            # Upload to Rejected/ for record-keeping
            try:
                self._drive.upload_file(resume_file_path, folder="rejected")
            except DriveError as drive_exc:
                logger.error("Failed to upload rejected file to Drive: %s", drive_exc)

            return self._make_result(
                success=False,
                message=f"❌ File rejected: {exc.reason}",
                stage="validation",
                error=exc,
                start_time=start_time,
                filename=filename,
            )

        # --- Stage 2: Upload to Drive Incoming/ ---
        try:
            drive_file_id = self._drive.upload_file(
                resume_file_path, folder="incoming"
            )
            drive_link = self._drive.get_file_link(drive_file_id)
            logger.info("File uploaded to Drive Incoming/: %s", drive_file_id)
        except DriveError as exc:
            logger.error("Drive upload failed: %s", exc, exc_info=True)
            return self._make_result(
                success=False,
                message="❌ Failed to upload file — please try again later.",
                stage="drive_upload",
                error=exc,
                start_time=start_time,
                filename=filename,
            )

        # --- Stage 3: Text Extraction ---
        try:
            text = self._extract_text(resume_file_path)
        except (ParsingError, FileValidationError) as exc:
            return self._make_result(
                success=False,
                message=f"❌ Could not read the file: {exc.reason}",
                stage="text_extraction",
                error=exc,
                start_time=start_time,
                filename=filename,
                drive_link=drive_link,
            )

        # --- Stage 4: AI Extraction ---
        try:
            raw_data = self._ai.extract_fields(text)
        except AIProviderError as exc:
            return self._make_result(
                success=False,
                message="❌ AI extraction failed — please try again later.",
                stage="ai_extraction",
                error=exc,
                start_time=start_time,
                filename=filename,
                drive_link=drive_link,
            )

        # --- Stage 5: Post-extraction Processing (deterministic recompute) ---
        validated_data = validate_extracted_fields(raw_data)
        logger.info("Post-extraction validation complete")

        # Snapshot the validated values BEFORE enrichment — used by the
        # Classification Audit sheet to report "Resume populated / blank".
        pre_enrichment = {
            "geography": validated_data.get("geography"),
            "saas_experience": validated_data.get("saas_experience"),
            "market_segment": validated_data.get("market_segment"),
        }

        # --- Stage 5b: SaaS Classification (knowledge-only, always on) ---
        # Independent of the scraper enrichment flag. Asks the active AI
        # provider directly using its general knowledge — no web requests,
        # no domain lookup.
        # Returns 'Yes', 'No', or '' (blank = genuinely uncertain).
        # Never raises; any failure silently returns blank.
        try:
            from integrations.enrichment.saas_classifier import get_saas_classification
            _current_co = validated_data.get("current_company") or ""
            if _current_co.strip():
                validated_data["is_saas_company"] = get_saas_classification(_current_co)
                logger.info(
                    "SaaS classification complete: '%s' → %r",
                    _current_co,
                    validated_data["is_saas_company"] or "(blank/unsure)",
                )
            else:
                validated_data["is_saas_company"] = ""
                logger.debug("No current_company — is_saas_company left blank")
        except Exception as exc:  # noqa: BLE001
            logger.error("SaaS classification failed (non-fatal): %s", exc)
            validated_data.setdefault("is_saas_company", "")

        # --- Stage 5c: Company Enrichment (scraper — currently paused) ---
        # Controlled by ENRICHMENT_ENABLED in .env — set to false to skip all
        # scraping and AI company calls. Re-enable by setting back to true.
        import os as _os
        _enrichment_on = _os.getenv("ENRICHMENT_ENABLED", "true").lower().strip() not in ("false", "0", "no", "off")
        if _enrichment_on:
            try:
                from integrations.enrichment.enrichment_pipeline import enrich_candidate
                validated_data = enrich_candidate(validated_data, self._settings)
                logger.info("Company enrichment complete")
            except Exception as exc:  # noqa: BLE001 — broad by design, matches validator philosophy
                logger.error(
                    "Enrichment failed (non-fatal) — continuing with blank enriched fields: %s",
                    exc,
                    exc_info=True,
                )
                validated_data["_enrichment_info"] = {"ran": False, "reason": "error"}
        else:
            logger.info("Company enrichment disabled (ENRICHMENT_ENABLED=false) — skipping")
            validated_data["_enrichment_info"] = {"ran": False, "reason": "disabled"}

        # --- Stage 5d: Forensic Audit (JSON + Markdown, non-fatal) ---
        # Writes one JSON + one Markdown report per resume under audit/ and
        # records the relative path in the 'Audit File' sheet column. Google
        # Sheets stays clean — no evidence blobs, no rejection logs. Never
        # influences classification and never blocks the pipeline.
        try:
            classification_audit = validated_data.pop("_classification_audit", None)
            pass1_data = validated_data.pop("_pass1_data", None)
            enrichment_info = validated_data.pop("_enrichment_info", None)
            if classification_audit:
                from core.audit_reporter import write_audit_report
                audit_ref = write_audit_report(
                    pass1_data=pass1_data or {},
                    classification_audit=classification_audit,
                    pre_enrichment=pre_enrichment,
                    validated_data=validated_data,
                    enrichment_info=enrichment_info or {},
                    resume_text=text,
                    filename=filename,
                    source=source,
                )
                if audit_ref:
                    validated_data["audit_file"] = audit_ref
        except Exception as exc:  # noqa: BLE001 — audit must never break the pipeline
            logger.error("Forensic audit write failed (non-fatal): %s", exc)

        # --- Stage 6: Duplicate Check ---
        try:
            existing_records = self._sheets.get_all_records()
            check_for_duplicates(validated_data, existing_records)
        except DuplicateFoundError as exc:
            logger.info("Duplicate candidate detected: %s", exc)

            # Move to Duplicates/
            if drive_file_id:
                try:
                    self._drive.move_file(drive_file_id, "duplicates")
                except DriveError as drive_exc:
                    logger.error("Failed to move duplicate to Drive: %s", drive_exc)

            # Write a flagged row
            try:
                self._sheets.append_row(
                    extracted_data=validated_data,
                    drive_link=drive_link,
                    source=source,
                    status="Possible Duplicate",
                    duplicate_reason=f"{exc.matched_field.replace('_', ' ').title()} Match",
                    matched_field=exc.matched_field,
                    matched_row_id=f"row {exc.matched_row}",
                )
            except SheetsError as sheets_exc:
                logger.error("Failed to write duplicate row: %s", sheets_exc)

            return self._make_result(
                success=False,
                message=(
                    f"⚠️ This looks like a duplicate submission — "
                    f"{exc.matched_field.replace('_', ' ')} matches an existing record."
                ),
                stage="duplicate_check",
                error=exc,
                start_time=start_time,
                filename=filename,
                drive_link=drive_link,
                extracted_data=validated_data,
                is_duplicate=True,
            )
        except SheetsError as exc:
            return self._make_result(
                success=False,
                message="❌ Could not check for duplicates — please try again later.",
                stage="duplicate_check",
                error=exc,
                start_time=start_time,
                filename=filename,
                drive_link=drive_link,
            )

        # --- Stage 7: Append Row to Sheets ---
        try:
            self._sheets.append_row(
                extracted_data=validated_data,
                drive_link=drive_link,
                source=source,
            )
        except SheetsError as exc:
            return self._make_result(
                success=False,
                message="❌ Failed to save record — please try again later.",
                stage="sheets_write",
                error=exc,
                start_time=start_time,
                filename=filename,
                drive_link=drive_link,
                extracted_data=validated_data,
            )

        # Write job openings to 'Open Sales Roles' tab (non-fatal if it fails)
        job_openings = validated_data.pop("job_openings", []) or []
        if job_openings:
            try:
                self._sheets.append_open_roles_rows(job_openings)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to write open sales roles (non-fatal): %s", exc)

        # --- Stage 8: Move to Processed/ ---
        if drive_file_id:
            try:
                self._drive.move_file(drive_file_id, "processed")
            except DriveError as exc:
                # Non-fatal: data is saved, file is in Incoming/ as safety net
                logger.error(
                    "Failed to move file to Processed/ (non-fatal): %s", exc
                )

        years = validated_data.get("years_of_experience")
        experience_line = f"💼 Experience: {years} years" if years is not None else "💼 Experience: 0 years"

        return self._make_result(
            success=True,
            message=(
                f"✅ Resume processed successfully!\n"
                f"📄 Name: {validated_data.get('full_name') or 'N/A'}\n"
                f"📧 Email: {validated_data.get('email') or 'N/A'}\n"
                f"📞 Phone: {validated_data.get('phone_number') or 'N/A'}\n"
                f"{experience_line}\n"
                f"🏢 Current: {validated_data.get('current_company') or 'N/A'}"
            ),
            stage="complete",
            start_time=start_time,
            filename=filename,
            drive_link=drive_link,
            extracted_data=validated_data,
        )

    def _validate_file(self, file_path: Path) -> None:
        """Validate a resume file before processing.

        Checks extension, file size, and basic file readability.

        Args:
            file_path: Path to the file to validate.

        Raises:
            FileValidationError: If the file fails any validation check.
        """
        filename = file_path.name

        # Check extension
        suffix = file_path.suffix.lower()
        if suffix not in _ALLOWED_EXTENSIONS:
            raise FileValidationError(
                filename,
                f"Unsupported file type '{suffix}'. Only .pdf and .docx are accepted.",
            )

        # Check file exists and is not empty
        if not file_path.exists():
            raise FileValidationError(filename, "File not found")

        file_size_mb = file_path.stat().st_size / (1024 * 1024)

        if file_size_mb == 0:
            raise FileValidationError(filename, "File is empty (0 bytes)")

        # Check file size
        if file_size_mb > self._settings.MAX_FILE_SIZE_MB:
            raise FileValidationError(
                filename,
                f"File too large ({file_size_mb:.1f} MB). "
                f"Maximum allowed: {self._settings.MAX_FILE_SIZE_MB} MB.",
            )

    def _extract_text(self, file_path: Path) -> str:
        """Extract text from a resume file based on its extension.

        Args:
            file_path: Path to the resume file.

        Returns:
            Extracted text content.

        Raises:
            ParsingError: If text extraction fails.
            FileValidationError: If the file is password-protected.
        """
        suffix = file_path.suffix.lower()

        if suffix == ".pdf":
            return extract_text_from_pdf(file_path)
        elif suffix == ".docx":
            return extract_text_from_docx(file_path)
        else:
            raise FileValidationError(
                file_path.name, f"Unsupported extension: {suffix}"
            )

    def _make_result(
        self,
        success: bool,
        message: str,
        stage: str,
        start_time: float,
        filename: str,
        error: Exception | None = None,
        drive_link: str = "",
        extracted_data: dict | None = None,
        is_duplicate: bool = False,
    ) -> PipelineResult:
        """Build a PipelineResult and log the outcome.

        Args:
            success: Whether the pipeline succeeded.
            message: Recruiter-facing message.
            stage: Last pipeline stage reached.
            start_time: Pipeline start timestamp (for duration calc).
            filename: Name of the processed file.
            error: Exception that caused failure, if any.
            drive_link: Google Drive link, if available.
            extracted_data: Validated extraction data, if available.
            is_duplicate: Whether the candidate was a duplicate.

        Returns:
            Populated PipelineResult.
        """
        elapsed = time.time() - start_time
        level = "INFO" if success else "WARNING"

        getattr(logger, level.lower())(
            "Pipeline %s — file='%s', stage='%s', duration=%.2fs%s",
            "succeeded" if success else "failed",
            filename,
            stage,
            elapsed,
            f", error={error}" if error else "",
        )

        return PipelineResult(
            success=success,
            message=message,
            stage=stage,
            drive_link=drive_link,
            extracted_data=extracted_data or {},
            is_duplicate=is_duplicate,
            error=error,
        )
