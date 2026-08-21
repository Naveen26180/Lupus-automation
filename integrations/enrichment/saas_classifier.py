"""Knowledge-only SaaS classifier for a candidate's current company.

Asks Groq directly using its own general knowledge — no scraping, no domain
lookup, no website fetch.  Fast enough to run synchronously inside the same
pipeline that produces the Telegram reply with no noticeable delay added.

Return values
-------------
'Yes'   → model is confident this is a SaaS company
'No'    → model is confident this is not a SaaS company
''      → model answered "Unsure", the response was unparseable, or the
          API failed.  Blank is a valid, correct output — not a failure state.
          It tells the recruiter "we don't know" honestly, rather than a
          confident-looking guess.

Caching
-------
Results (including blank/unsure) are stored in the existing SQLite cache
(company_profiles table) under is_saas_company + classification_source.
The same 30-day TTL logic that governs all other cached fields applies here.
Cache hits skip the AI call entirely — same company on multiple resumes within
30 days = 1 AI call total.

Audit log
---------
Every real AI call (NOT cache hits) is appended as a JSON line to
data/saas_classification_log.jsonl so that any classification can be
spot-checked after the fact against the raw model response.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from integrations.enrichment import company_cache
from integrations.enrichment.company_cache import normalize_company_name

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LOG_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "saas_classification_log.jsonl"
)
_CLASSIFICATION_SOURCE = "knowledge_only"

# Three-way prompt — deliberately asks for "Unsure" as a first-class option
# so the model doesn't feel forced to guess when it doesn't actually know.
_PROMPT_TEMPLATE = (
    'Question: Is "{company_name}" primarily a SaaS (Software-as-a-Service) '
    "company — meaning their core business is selling cloud-hosted, "
    "subscription-based software as their main product?\n\n"
    "Answer with exactly one word: Yes, No, or Unsure.\n\n"
    "Only answer Yes or No if you have real, specific knowledge of this "
    "company. If you do not clearly recognize this company, or are not "
    "confident about what its core business actually is, answer Unsure — "
    "do not guess."
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_prompt(company_name: str) -> str:
    return _PROMPT_TEMPLATE.format(company_name=company_name)


def _call_ai(prompt: str) -> str:
    """Call the active AI provider and return the raw text response.

    Dispatches on AI_PROVIDER (groq / cerebras / gemini).  Raises on failure
    or if the provider is unknown / its API key is missing.
    """
    provider = os.getenv("AI_PROVIDER", "groq").lower().strip()

    if provider == "groq":
        from groq import Groq  # local import — keeps module import fast

        api_key = os.getenv("GROQ_API_KEY", "")
        if not api_key:
            raise ValueError("GROQ_API_KEY not set")
        model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,   # deterministic
            max_tokens=10,     # we only need one word
        )
        return (response.choices[0].message.content or "").strip()

    if provider == "cerebras":
        from cerebras.cloud.sdk import Cerebras

        api_key = os.getenv("CEREBRAS_API_KEY", "")
        if not api_key:
            raise ValueError("CEREBRAS_API_KEY not set")
        model = os.getenv("CEREBRAS_MODEL", "llama-3.1-8b")
        client = Cerebras(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,   # deterministic
            max_tokens=10,     # we only need one word
        )
        return (response.choices[0].message.content or "").strip()

    if provider == "gemini":
        import google.generativeai as genai

        api_key = os.getenv("GEMINI_API_KEY", "")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not set")
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.0-flash")
        response = model.generate_content(prompt)
        return (response.text or "").strip()

    raise ValueError(f"Unknown AI_PROVIDER: '{provider}'")


def _parse_response(raw: str) -> str:
    """Map raw model text → 'Yes', 'No', or '' (blank).

    Anything other than a clear 'yes' or 'no' (including 'unsure', empty
    response, extra words, or garbage text) becomes blank.  We never force
    an unrecognised response into Yes or No.
    """
    word = raw.strip().lower()
    if word == "yes":
        return "Yes"
    if word == "no":
        return "No"
    # 'unsure', empty, multi-word, or anything else → blank
    return ""


def _append_log(
    company_name: str,
    prompt_sent: str,
    raw_response: str,
    parsed_result: str,
) -> None:
    """Append one JSON line to the audit log file.  Never raises."""
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "company_name": company_name,
            "prompt_sent": prompt_sent,
            "raw_response": raw_response,
            "parsed_result": parsed_result,
        }
        with _LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not write to saas_classification_log.jsonl: %s", exc)


def _cache_result(company_name: str, parsed: str) -> None:
    """Persist classification (including blank) to cache.  Never raises."""
    try:
        existing = company_cache.get_company(company_name) or {}
        company_cache.save_company(
            company_name,
            {
                # Preserve any previously scraped fields — don't overwrite with None
                "domain": existing.get("domain"),
                "sells_what": existing.get("sells_what"),
                "geography": existing.get("geography"),
                "market_segment": existing.get("market_segment"),
                "is_saas_company": parsed,
                "classification_source": _CLASSIFICATION_SOURCE,
                "source": existing.get("source") or _CLASSIFICATION_SOURCE,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not cache SaaS classification for '%s': %s", company_name, exc
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_saas_classification(company_name: str) -> str:
    """Return 'Yes', 'No', or '' for the given company.

    Cache is checked first (30-day TTL, same as all company data).
    On a cache miss the model is asked once and the result is always cached
    — even if the result is blank — so the same uncertain company is not
    re-asked on every resume within the TTL window.

    Never raises.  On any failure the function logs the error and returns ''.

    Args:
        company_name: The candidate's current employer, as extracted and
                      post-processed by the pipeline.  May be raw text
                      (normalisation is handled internally).

    Returns:
        'Yes', 'No', or '' (blank / unknown).
    """
    if not company_name or not company_name.strip():
        return ""

    # ------------------------------------------------------------------
    # 1. Cache check
    # ------------------------------------------------------------------
    cached = company_cache.get_company(company_name)
    if cached is not None and not company_cache.is_stale(company_name):
        # is_saas_company key is always present once the schema migration runs.
        # NULL  (None)  → not yet classified   → fall through to AI call
        # ''            → previously unsure     → return blank (cached)
        # 'Yes' / 'No' → use cached value
        saas_val = cached.get("is_saas_company")
        if saas_val is not None:
            logger.debug(
                "SaaS cache hit for '%s': %r (source=%s)",
                company_name,
                saas_val,
                cached.get("classification_source", "?"),
            )
            return saas_val

    # ------------------------------------------------------------------
    # 2. AI call
    # ------------------------------------------------------------------
    prompt = _build_prompt(company_name)
    raw_response = ""
    parsed = ""

    try:
        raw_response = _call_ai(prompt)
        parsed = _parse_response(raw_response)
        logger.info(
            "SaaS classification for '%s': raw=%r → %r",
            company_name,
            raw_response,
            parsed if parsed else "(blank/unsure)",
        )
    except Exception as exc:  # noqa: BLE001
        raw_response = f"ERROR: {exc}"
        parsed = ""
        logger.error(
            "SaaS classification AI call failed for '%s': %s — storing blank",
            company_name,
            exc,
        )

    # ------------------------------------------------------------------
    # 3. Audit log  (every real AI call, including failures)
    # ------------------------------------------------------------------
    _append_log(company_name, prompt, raw_response, parsed)

    # ------------------------------------------------------------------
    # 4. Cache  (always, including blanks — prevents repeat AI calls for
    #            companies the model is uncertain about)
    # ------------------------------------------------------------------
    _cache_result(company_name, parsed)

    return parsed
