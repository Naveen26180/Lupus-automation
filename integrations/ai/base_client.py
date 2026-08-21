"""Abstract base class for AI provider clients.

Defines the contract that gemini_client.py implements, so pipeline.py
never needs to know which provider is active.

Extraction + context-classification architecture
--------------------------------------------------
PASS 1 (pass1.txt) — evidence extraction.
  Input : resume text + today's date
  Output: candidate_metadata + document_evidence + role_analysis

PYTHON CLASSIFIER — deterministic baseline (core/classifier.py).
  Input : Pass 1 evidence
  Output: canonical geography / saas_experience / market_segment tags

PASS 2 (pass2.txt) — context classification (optional, additive-only).
  Input : FULL resume text + Pass 1 evidence
  Output: per-tag proposals {tag, confidence, evidence[], reasoning}

ADJUDICATOR (core/adjudicator.py) — merges Pass 2 proposals into the
deterministic baseline under strict safety rules. Deterministic always
wins; the AI can only ADD evidence-backed tags. Disabled by default via
AI_CLASSIFICATION_ENABLED; any failure falls back to deterministic-only.
"""

import json
import logging
import os
import datetime
from abc import ABC, abstractmethod
from pathlib import Path

from core.exceptions import AIProviderError

logger = logging.getLogger(__name__)

# ── Prompt paths ────────────────────────────────────────────────────────────
_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts" / "phase1"
_PASS1_PATH  = _PROMPTS_DIR / "pass1.txt"
_PASS2_PATH  = _PROMPTS_DIR / "pass2.txt"

# Debug dump of the raw pass1 response (tests redirect this to tmp).
_DEBUG_DUMP_PATH = "raw_ai_response.json"

# Kept for backwards-compat (saas_classifier and enrichment import this path)
_PROMPT_PATH = _PASS1_PATH

# ── Expected keys in final_answer ───────────────────────────────────────────
EXPECTED_KEYS = frozenset([
    "full_name",
    "email",
    "linkedin_url",
    "phone_number",
    "college",
    "geography",
    "saas_experience",
    "market_segment",
    "years_of_experience",
    "current_company",
    "past_companies",
])


# ── Prompt loaders ───────────────────────────────────────────────────────────

def _load_prompt(path: Path) -> str:
    """Load a prompt template from disk.

    Raises:
        AIProviderError: If the file cannot be read.
    """
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise AIProviderError(
            "system", f"Prompt file not found: {path}"
        ) from exc


def load_prompt_template() -> str:
    """Load the Pass 1 extraction prompt. (Legacy compat alias.)"""
    return _load_prompt(_PASS1_PATH)


# ── Pass 2 helpers ───────────────────────────────────────────────────────────

def _parse_pass2_response(raw: str, provider: str) -> dict:
    """Parse and validate the Pass 2 classification response.

    Expected shape:
        { "proposals": { "geography": [...], "saas_experience": [...], "market_segment": [...] } }

    Each proposal item:
        { "tag": str, "confidence": "high|medium|low", "evidence": [str, ...], "reasoning": str }

    Returns the proposals dict. Raises AIProviderError on any structural
    violation — callers treat that as a non-fatal fallback to deterministic.
    """
    text = _strip_fences(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AIProviderError(provider, f"Pass 2 response is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise AIProviderError(provider, f"Pass 2: expected JSON object, got {type(data).__name__}")

    proposals = data.get("proposals")
    if not isinstance(proposals, dict):
        raise AIProviderError(provider, "Pass 2 response missing 'proposals' object")

    for field in ("geography", "saas_experience", "market_segment"):
        items = proposals.get(field, [])
        if not isinstance(items, list):
            raise AIProviderError(provider, f"Pass 2 proposals['{field}'] must be a list")
        for item in items:
            if not isinstance(item, dict):
                raise AIProviderError(provider, f"Pass 2 proposals['{field}'] items must be objects")
            tag = item.get("tag")
            if not isinstance(tag, str) or not tag.strip():
                raise AIProviderError(provider, f"Pass 2 proposals['{field}'] item missing 'tag'")
            if item.get("confidence") not in ("high", "medium", "low"):
                raise AIProviderError(
                    provider, f"Pass 2 proposals['{field}']['{tag}'] confidence must be high|medium|low"
                )
            evidence = item.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                raise AIProviderError(
                    provider, f"Pass 2 proposals['{field}']['{tag}'] evidence must be a non-empty list"
                )
            if not all(isinstance(q, str) and q.strip() for q in evidence):
                raise AIProviderError(
                    provider, f"Pass 2 proposals['{field}']['{tag}'] evidence must be verbatim strings"
                )
            if not isinstance(item.get("reasoning"), str) or not item["reasoning"].strip():
                raise AIProviderError(
                    provider, f"Pass 2 proposals['{field}']['{tag}'] missing 'reasoning'"
                )

    return proposals


# ── Pass 1 helpers ───────────────────────────────────────────────────────────

def _parse_pass1_response(raw: str, provider: str) -> dict:
    """Parse and validate the Pass 1 JSON response.

    Expected shape:
        { "candidate_metadata": {...}, "role_analysis": [...] }

    Returns the raw dict (not yet processed by recompute_derived_fields).
    """
    text = _strip_fences(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AIProviderError(provider, f"Pass 1 response is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise AIProviderError(provider, f"Pass 1: expected JSON object, got {type(data).__name__}")

    if "role_analysis" not in data:
        raise AIProviderError(provider, "Pass 1 response missing 'role_analysis' key")

    if not isinstance(data["role_analysis"], list):
        raise AIProviderError(provider, "Pass 1 'role_analysis' must be a list")

    if "candidate_metadata" not in data:
        logger.warning("Pass 1 response missing 'candidate_metadata' — defaulting to empty")
        data["candidate_metadata"] = {
            "full_name": None, "email": None, "phone_number": None,
            "linkedin_url": None, "college": None,
        }

    # document_evidence is optional — old responses omit it entirely.
    # Ensure it is always a list so classifier can safely iterate.
    doc_ev = data.get("document_evidence")
    if doc_ev is None:
        data["document_evidence"] = []
    elif not isinstance(doc_ev, list):
        logger.warning(
            "Pass 1 'document_evidence' is not a list (got %s) — discarding",
            type(doc_ev).__name__,
        )
        data["document_evidence"] = []

    return data





# ── Shared utilities ─────────────────────────────────────────────────────────

def _strip_fences(text: str) -> str:
    """Remove markdown code fences that some models add despite instructions."""
    text = text.strip()
    if text.startswith("```"):
        first_nl = text.index("\n")
        text = text[first_nl + 1:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def parse_ai_response(raw_response: str, provider: str) -> dict:
    """Legacy single-pass parser — kept for compatibility only.

    In the two-pass architecture this is no longer called by extract_fields.
    Retained so any external tests that call it directly continue to work.
    """
    from core.post_processing import recompute_derived_fields

    text = _strip_fences(raw_response)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AIProviderError(provider, f"Response is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise AIProviderError(provider, f"Expected JSON object, got {type(data).__name__}")

    if "final_answer" not in data or "role_analysis" not in data:
        raise AIProviderError(
            provider, "Response missing 'final_answer' or 'role_analysis' root key"
        )

    final_answer = data["final_answer"]
    if not isinstance(final_answer, dict):
        raise AIProviderError(provider, "'final_answer' must be a dictionary")

    actual_keys = set(final_answer.keys())
    missing = EXPECTED_KEYS - actual_keys
    extra   = actual_keys - EXPECTED_KEYS

    if missing:
        raise AIProviderError(provider, f"Missing keys in AI final_answer: {missing}")
    if extra:
        logger.warning("AI extra keys (removed): %s", extra)
        for k in list(extra):
            del final_answer[k]

    return recompute_derived_fields(data, today=datetime.date.today())


# ── Abstract base class ──────────────────────────────────────────────────────

class BaseAIClient(ABC):
    """Abstract interface for AI resume extraction.

    Subclasses implement _call_api() and optionally _is_token_limit_error().
    The public extract_fields() method runs the two-pass architecture.
    """

    def __init__(self, api_key: str, provider_name: str) -> None:
        self._api_key = api_key
        self._provider_name = provider_name

    @abstractmethod
    def _call_api(self, prompt: str) -> str:
        """Send a prompt and return the raw response text.

        Raises:
            AIProviderError: On provider failures.
        """
        ...

    def _is_token_limit_error(self, exc: AIProviderError) -> bool:
        """Return True if the error is a token-budget/payload-size error.

        Subclasses should override this to detect 413 / TPM-exceeded errors
        from their specific SDK. The default checks for common substrings.
        """
        msg = str(exc).lower()
        return any(k in msg for k in ("413", "payload too large", "token", "tpm", "rate_limit_exceeded"))

    def extract_fields(self, resume_text: str) -> dict:
        """Run the single-pass evidence extraction pipeline.

        Pass 1: Evidence extraction (resume text → structured evidence array)
        Python Classification: Evaluates evidence array and computes canonical taxonomy.

        Args:
            resume_text: Raw text from the resume file.

        Returns:
            Dict with the canonical 11 Phase-1 keys.

        Raises:
            AIProviderError: If the pass fails after retries.
        """
        today = datetime.date.today().isoformat()

        # ── Pass 1: Evidence Extraction ──────────────────────────────────────
        pass1_data = self._run_pass1(resume_text, today)

        # Debug dump — ONLY in debug mode. The file contains full candidate
        # evidence (names, emails, phones, resume quotes) and must never be
        # written in production. Controlled by LOG_LEVEL=DEBUG in .env.
        if os.getenv("LOG_LEVEL", "INFO").upper() == "DEBUG":
            try:
                with open(_DEBUG_DUMP_PATH, "w", encoding="utf-8") as f:
                    json.dump(pass1_data, f, indent=2, ensure_ascii=False)
                logger.debug("Pass 1 evidence saved to %s", _DEBUG_DUMP_PATH)
            except OSError as exc:
                logger.warning("Could not write %s: %s", _DEBUG_DUMP_PATH, exc)

        # ── Python Classification (deterministic baseline) ───────────────────
        from core.classifier import classify_candidate_audited
        final_answer, classification_audit = classify_candidate_audited(pass1_data)

        # ── Pass 2: Context Classification (optional, additive-only) ──────────
        # The LLM proposes additional evidence-backed tags. The adjudicator
        # merges them into the deterministic baseline under strict safety
        # rules (verbatim quotes, allowlist, reasoning support, no title-only).
        # Any Pass 2 failure falls back to deterministic-only — never fails.
        ai_proposals = self._run_pass2(resume_text, pass1_data)
        if ai_proposals:
            try:
                from core.adjudicator import adjudicate
                final_answer, classification_audit = adjudicate(
                    deterministic_final=final_answer,
                    classification_audit=classification_audit,
                    ai_proposals=ai_proposals,
                    resume_text=resume_text,
                    pass1_data=pass1_data,
                )
                logger.info(
                    "Pass 2 adjudication complete — "
                    "geo=%r saas=%r seg=%r",
                    final_answer.get("geography"),
                    final_answer.get("saas_experience"),
                    final_answer.get("market_segment"),
                )
            except Exception as exc:  # noqa: BLE001 — adjudication must never break the pipeline
                logger.error(
                    "Pass 2 adjudication failed (non-fatal, using deterministic output only): %s",
                    exc,
                    exc_info=True,
                )

        # ── Deterministic recompute (YOE, current_company, past_companies) ──
        from core.post_processing import recompute_derived_fields

        # recompute_derived_fields expects the combined shape:
        # { "role_analysis": [...], "final_answer": {...} }
        combined = {
            "role_analysis": pass1_data.get("role_analysis", []),
            "final_answer": final_answer,
        }
        result = recompute_derived_fields(combined, today=datetime.date.today())

        # Attach the read-only audit trail + raw pass1 output for the forensic
        # audit files. The pipeline pops these keys before writing the
        # candidate row, so they never reach Google Sheets as data.
        result["_classification_audit"] = classification_audit
        result["_pass1_data"] = pass1_data

        # Combine results safely for logging
        logger.info(
            "Evidence extraction complete via '%s' — "
            "saas=%r  segment=%r  geo=%r",
            self._provider_name,
            result.get("saas_experience"),
            result.get("market_segment"),
            result.get("geography"),
        )
        return result

    # ── Internal pass runners ─────────────────────────────────────────────────

    def _run_pass1(self, resume_text: str, today: str) -> dict:
        """Execute Pass 1 with one retry on transient errors."""
        template = _load_prompt(_PASS1_PATH)
        prompt = template.replace("{resume_text}", resume_text).replace("{current_date}", today)

        return self._attempt_call(
            prompt=prompt,
            parse_fn=lambda raw: _parse_pass1_response(raw, self._provider_name),
            pass_label="Pass 1 (evidence extraction)",
            max_attempts=2,
        )

    def _run_pass2(self, resume_text: str, pass1_data: dict) -> dict | None:
        """Run Pass 2 (context classification) — additive, non-fatal.

        Controlled by AI_CLASSIFICATION_ENABLED in .env (default: off). When
        disabled, or when the provider fails / times out / returns invalid
        JSON, this returns None and the pipeline continues with the
        deterministic baseline untouched.

        Args:
            resume_text: The full resume text (authoritative for quote checks).
            pass1_data: The parsed Pass 1 evidence dict.

        Returns:
            Proposals dict (per field list of {tag, confidence, evidence,
            reasoning}) or None when disabled/failed.
        """
        enabled = os.getenv("AI_CLASSIFICATION_ENABLED", "false").lower().strip()
        if enabled in ("false", "0", "no", "off", ""):
            logger.debug("Pass 2 (context classification) disabled — AI_CLASSIFICATION_ENABLED not 'true'")
            return None

        try:
            template = _load_prompt(_PASS2_PATH)
            prompt = (
                template.replace("{resume_text}", resume_text)
                .replace("{pass1_json}", json.dumps(pass1_data, ensure_ascii=False))
            )
            return self._attempt_call(
                prompt=prompt,
                parse_fn=lambda raw: _parse_pass2_response(raw, self._provider_name),
                pass_label="Pass 2 (context classification)",
                max_attempts=2,
            )
        except AIProviderError as exc:
            logger.error(
                "Pass 2 failed (non-fatal — continuing with deterministic output only): %s",
                exc,
            )
            return None

    def _attempt_call(self, prompt: str, parse_fn, pass_label: str, max_attempts: int) -> dict:
        """Run an API call with retry, aborting immediately on token-limit errors.

        Args:
            prompt: The complete prompt string.
            parse_fn: Callable that parses raw API response into a dict.
            pass_label: Human-readable name for logging.
            max_attempts: Max number of attempts for transient errors.

        Returns:
            Parsed dict from parse_fn.

        Raises:
            AIProviderError: On exhausted retries or non-retryable errors.
        """
        last_error: AIProviderError | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                logger.info(
                    "%s — attempt %d/%d via '%s'",
                    pass_label,
                    attempt,
                    max_attempts,
                    self._provider_name,
                )
                raw = self._call_api(prompt)
                result = parse_fn(raw)
                logger.info("%s succeeded", pass_label)
                return result

            except AIProviderError as exc:
                # Token-limit errors are permanent — fail immediately, do not retry.
                if self._is_token_limit_error(exc):
                    logger.error(
                        "%s aborted — token limit exceeded (not retrying): %s",
                        pass_label,
                        exc,
                    )
                    raise AIProviderError(
                        self._provider_name,
                        f"{pass_label} failed: token limit exceeded. "
                        f"Original error: {exc}",
                    ) from exc

                last_error = exc
                logger.warning(
                    "%s attempt %d/%d failed (transient): %s",
                    pass_label,
                    attempt,
                    max_attempts,
                    exc,
                )

        raise AIProviderError(
            self._provider_name,
            f"{pass_label}: all {max_attempts} attempts failed. Last: {last_error}",
        )
