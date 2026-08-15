"""Enrichment pipeline — orchestrates company research for a candidate.

Enforces THE GOLDEN RULE throughout:
  "The resume always wins. We only ever use company research to fill
   genuine gaps. We never overwrite something the resume already stated."

Entry point for core/pipeline.py:
    from integrations.enrichment.enrichment_pipeline import enrich_candidate
    enrich_candidate(validated_data, settings)

What this does, per company (current + up to N past companies):
  1. Check what the resume AI already extracted for geography / saas_experience /
     market_segment.
  2. For the CURRENT company: always run company profiling (even if geography/segment
     are resume-complete) — required to answer is_saas_company, which has no
     resume-derived equivalent.
  3. For PAST companies: resume-first, scrape-as-fallback for geography/segment only.
  4. Cross-check: if we have BOTH a resume value AND a scraped value for the
     same field and they look different → write a note in data_source_note
     (never overwrite the resume value).
  5. Combine per-company data into the single cell values the sheet expects.
  6. Scrape job openings (cached, 24h TTL) and return them separately.

Judgment calls (confirm with lead before finalizing):
  - Multi-company field formatting: "North America (Acme), EMEA (Globex)"
    This module uses that pattern by default. Change _combine_values() to alter it.
  - ENRICHMENT_PAST_COMPANY_LIMIT: read from settings (default 2).
"""

import logging
import os
from typing import Any

from integrations.enrichment import company_cache
from integrations.enrichment.domain_resolver import resolve_domain
from integrations.enrichment.company_profiler import profile_company
from integrations.enrichment.job_openings_scraper import scrape_job_openings

logger = logging.getLogger(__name__)

# Fields we enrich from company research
_ENRICH_FIELDS = ("geography", "market_segment")

# These are the rough values that count as "resume said nothing"
_NULL_LIKE = {None, "", "null", "n/a", "not specified", "unknown"}

# Loose similarity check threshold — we compare lowercased substrings
_COMMON_SEGMENT_ALIASES: dict[str, set[str]] = {
    "smb": {"smb", "small business", "small-business", "startup", "startups"},
    "mid-market": {"mid-market", "mid market", "midmarket", "mid", "growth"},
    "enterprise": {"enterprise", "large enterprise", "large companies", "fortune"},
    "mixed": {"mixed", "all sizes", "smb to enterprise"},
}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _is_blank(value: Any) -> bool:
    """True if a value is functionally empty / not provided by the resume."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip().lower() in _NULL_LIKE:
        return True
    return False


def _values_conflict(resume_val: str, scraped_val: str, field: str) -> bool:
    """Loose conflict check between resume and scraped values.

    Returns True only when the values are clearly incompatible — not just
    expressed differently.  Better to under-flag than to spam the reviewer.
    """
    if not resume_val or not scraped_val:
        return False

    r = resume_val.strip().lower()
    s = scraped_val.strip().lower()

    if r == s:
        return False

    if field == "market_segment":
        # Map both values to their canonical bucket
        def _bucket(val: str) -> str | None:
            for canon, aliases in _COMMON_SEGMENT_ALIASES.items():
                if any(alias in val for alias in aliases):
                    return canon
            return None

        r_bucket = _bucket(r)
        s_bucket = _bucket(s)
        if r_bucket and s_bucket and r_bucket != s_bucket:
            return True
        return False

    if field == "geography":
        # Flag only if the resume explicitly mentions a region NOT found in scraped data
        # (e.g. resume says "APAC" but company site only mentions "North America")
        regions = ["north america", "emea", "apac", "latam", "europe", "asia", "middle east"]
        r_regions = {reg for reg in regions if reg in r}
        s_regions = {reg for reg in regions if reg in s}
        if r_regions and s_regions and not r_regions.intersection(s_regions):
            return True
        return False

    return False


def _combine_values(per_company: list[tuple[str, str | None]]) -> str:
    """Combine per-company field values into a single cell string.

    Args:
        per_company: List of (company_name, value) tuples.

    Returns:
        Combined string, e.g. "North America (Acme), EMEA (Globex)"
        If all values are the same, deduplicates: "Enterprise"
        If no values, returns "".

    Judgment call (confirmed format — check with lead):
        Uses "value (Company)" pattern. Change only this function to alter it.
    """
    # Filter out blank values
    valid = [(name, val) for name, val in per_company if val and not _is_blank(val)]
    if not valid:
        return ""

    # If all values are identical, just return the value once
    unique_vals = {val.strip().lower() for _, val in valid}
    if len(unique_vals) == 1:
        return valid[0][1].strip()

    parts = [f"{val.strip()} ({name})" for name, val in valid]
    return "; ".join(parts)


def _get_company_profile(company_name: str) -> dict:
    """Get a company profile from cache or scrape it fresh."""
    cached = company_cache.get_company(company_name)

    if cached and not company_cache.is_stale(company_name):
        logger.debug("Cache hit for '%s'", company_name)
        return cached

    # Cache miss or stale — resolve domain then profile
    logger.info("Cache miss/stale for '%s' — resolving domain", company_name)
    domain, source = resolve_domain(company_name)

    if not domain:
        # Store not_found so we don't retry for 90 days
        company_cache.save_company(company_name, {"source": "not_found"})
        logger.warning("Could not resolve domain for '%s'", company_name)
        return {"domain": None, "sells_what": None, "geography": None, "market_segment": None, "source": "not_found"}

    profile = profile_company(company_name, domain, source)
    return profile


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def enrich_candidate(validated_data: dict, settings=None) -> dict:
    """Enrich a candidate's extracted data with company research.

    Mutates and returns validated_data with these additions/modifications:
      - is_saas_company  → always set from current company's profile ("Yes"/"No")
      - geography        → filled from company research if resume left it blank
      - saas_experience  → unchanged (resume-stated role descriptions aren't
                          replaced — we only enrich geography & segment here)
      - market_segment   → filled from company research if resume left it blank
      - data_source_note → cross-check notes (conflict flags for a human)
      - job_openings     → list of sales openings per company (not stored in
                          the main row; passed back for the "Open Sales Roles" tab)

    The resume ALWAYS wins for geography/segment. Scraped data only fills genuine nulls.
    NOTE: company profiling always runs for the current company specifically
    (regardless of whether geography/segment are resume-complete) because
    is_saas_company has no resume-derived equivalent and always needs the
    scrape-based answer.

    Args:
        validated_data: The post-processed, validated candidate dict.
        settings: Optional Settings object (used to read ENRICHMENT_PAST_COMPANY_LIMIT).

    Returns:
        Enriched validated_data dict (same object, mutated in-place).
    """
    # Audit plumbing (read-only) — records whether/why enrichment ran so the
    # Classification Audit sheet can report per-field enrichment status.
    # Never consumed by any classification logic; popped by the pipeline.
    validated_data["_enrichment_info"] = {"ran": False, "reason": ""}

    # Determine enrichment limit
    try:
        limit = int(os.getenv("ENRICHMENT_PAST_COMPANY_LIMIT", "2"))
    except (ValueError, TypeError):
        limit = 2

    # Build company list: current + up to `limit` past companies
    current = validated_data.get("current_company")
    past_raw = validated_data.get("past_companies") or []

    # past_companies can be a list or a comma-separated string (from sheet round-trips)
    if isinstance(past_raw, str):
        past_list = [c.strip() for c in past_raw.split(",") if c.strip()]
    else:
        past_list = list(past_raw)

    companies: list[str] = []
    if current and not _is_blank(current):
        companies.append(current)
    for c in past_list[:limit]:
        if c and not _is_blank(c) and c not in companies:
            companies.append(c)

    if not companies:
        logger.info("No companies to enrich for candidate '%s'", validated_data.get("full_name", "?"))
        validated_data["_enrichment_info"]["reason"] = "no_companies"
        validated_data.setdefault("data_source_note", "")
        validated_data.setdefault("job_openings", [])
        validated_data.setdefault("is_saas_company", validated_data.get("is_saas_company", "No"))
        return validated_data

    # Resume-stated values (ground truth — never overwritten)
    resume_geography = validated_data.get("geography")
    resume_market_segment = validated_data.get("market_segment")
    
    # Enrichment Blank-Check Fix: If classification already resolved both, we skip network calls entirely.
    if not _is_blank(resume_geography) and not _is_blank(resume_market_segment):
        logger.info("Both geography and market_segment are present. Skipping enrichment entirely.")
        validated_data["_enrichment_info"]["reason"] = "fields_populated"
        validated_data.setdefault("data_source_note", "")
        validated_data.setdefault("job_openings", [])
        validated_data.setdefault("is_saas_company", validated_data.get("is_saas_company", "No"))
        return validated_data

    logger.info(
        "Enriching candidate '%s' — companies: %s",
        validated_data.get("full_name", "?"),
        companies,
    )

    validated_data["_enrichment_info"]["ran"] = True

    # Per-company scraped data and conflict notes
    geo_per_company: list[tuple[str, str | None]] = []
    seg_per_company: list[tuple[str, str | None]] = []
    conflict_notes: list[str] = []
    all_job_openings: list[dict] = []
    current_company_saas: str = "No"  # Default for current company SaaS classification

    for company in companies:
        is_current = (company == current)
        logger.info("Processing company: '%s' (is_current=%s)", company, is_current)
        try:
            profile = _get_company_profile(company)
        except Exception as exc:
            logger.error("Failed to get profile for '%s': %s", company, exc)
            profile = {}

        scraped_geo = profile.get("geography")
        scraped_seg = profile.get("market_segment")
        scraped_domain = profile.get("domain")

        # --- is_saas_company (current company only) ---
        # Always captured from profiling regardless of resume completeness.
        if is_current:
            current_company_saas = profile.get("is_saas_company") or "No"
            logger.info(
                "is_saas_company for '%s': %s",
                company,
                current_company_saas,
            )

        # --- Geography ---
        geo_per_company.append((company, scraped_geo))

        if not _is_blank(resume_geography) and not _is_blank(scraped_geo):
            if _values_conflict(str(resume_geography), str(scraped_geo), "geography"):
                note = (
                    f"Geography: resume says '{resume_geography}'; "
                    f"'{company}' site suggests '{scraped_geo}' — please verify"
                )
                conflict_notes.append(note)
                logger.info("Geography conflict for '%s': %s", company, note)

        # --- Market Segment ---
        seg_per_company.append((company, scraped_seg))

        if not _is_blank(resume_market_segment) and not _is_blank(scraped_seg):
            if _values_conflict(str(resume_market_segment), str(scraped_seg), "market_segment"):
                note = (
                    f"Market Segment: resume says '{resume_market_segment}'; "
                    f"'{company}' site suggests '{scraped_seg}' — please verify"
                )
                conflict_notes.append(note)
                logger.info("Segment conflict for '%s': %s", company, note)

        # --- Job Openings ---
        if scraped_domain:
            try:
                openings = scrape_job_openings(company, scraped_domain)
                for o in openings:
                    o["company"] = company
                all_job_openings.extend(openings)
            except Exception as exc:
                logger.warning("Job scraping failed for '%s': %s", company, exc)

    # --- Apply enrichment (resume wins, scraping fills gaps) ---
    from core.validator import _validate_geography, _validate_market_segment
    if _is_blank(resume_geography):
        enriched_geo = _combine_values(geo_per_company)
        validated_data["_enrichment_info"]["scraped_geo"] = enriched_geo
        validated_data["geography"] = _validate_geography(enriched_geo) if enriched_geo else None
        if validated_data["geography"]:
            logger.info("Filled geography from company research: '%s'", validated_data["geography"])

    if _is_blank(resume_market_segment):
        # We want to extract ALL segments found across the companies, but they MUST pass
        # the validator. Do NOT use _combine_values because we do not want "(Company)" appended.
        from core.validator import _validate_market_segment
        raw_segs = "; ".join(val for name, val in seg_per_company if val and not _is_blank(val))
        validated_data["_enrichment_info"]["scraped_seg"] = raw_segs
        if raw_segs:
            validated_seg = _validate_market_segment(raw_segs)
            if validated_seg:
                validated_data["market_segment"] = validated_seg
                logger.info("Filled market_segment from company research: '%s'", validated_seg)

    # --- Write is_saas_company for the current company ---
    # Only overwrite if enrichment found a value, else keep the 5b knowledge-based fallback
    if current_company_saas and current_company_saas != "No":
        validated_data["is_saas_company"] = current_company_saas
    else:
        validated_data.setdefault("is_saas_company", "No")

    # --- Write cross-check notes ---
    validated_data["data_source_note"] = "; ".join(conflict_notes) if conflict_notes else ""

    # --- Attach job openings (for the Open Sales Roles tab) ---
    validated_data["job_openings"] = all_job_openings

    return validated_data
