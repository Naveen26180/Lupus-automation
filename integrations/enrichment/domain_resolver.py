"""Domain resolver — finds a company's official website.

Resolution is attempted in this exact order (stop at first success):

    Tier 1 — Direct guess
        Strip legal suffixes from the company name, try https://{slug}.com.
        Accept only if: HTTP 200 AND page title/body contains the company name.
        (Parked domains often return 200 with unrelated content.)

    Tier 2 — Clearbit Autocomplete API
        Free, no API key, no account, no credit card required.
        GET https://autocomplete.clearbit.com/v1/companies/suggest?query={name}
        Returns a ranked list of company name+domain pairs.
        We accept index-0 only if the returned company name is a
        reasonably close match to what we searched for (substring check).

    Tier 3 — DuckDuckGo Instant Answer API
        Free, no API key, no rate limit.
        Returns the top instant-answer result for "{company name} official website".
        Often misses niche/lesser-known companies — treat silence as a miss,
        not an error, and fall through.

    All failed → returns None.  Caller should save source='not_found' in
    the cache (90-day cooldown applied by is_stale()).

Every attempt is logged — this is the first place silent failures occur.
"""

import logging
import re
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

_REQUEST_TIMEOUT = 8        # seconds — default for direct-guess and DDG
_CLEARBIT_TIMEOUT = 5       # seconds — shorter for the lightweight Clearbit call
_USER_AGENT = (
    "Mozilla/5.0 (compatible; ResumeBot/1.0; +https://example.com/bot)"
)
_HEADERS = {"User-Agent": _USER_AGENT}

# Legal suffix patterns — strips these before building slug for tier-1 guess.
# Also strips generic business-division words that are rarely part of the domain
# (e.g. 'UpGrad Enterprise' → 'upgrad', 'Simplilearn Solutions' → 'simplilearn').
_SLUG_STRIP_RE = re.compile(
    r"\b(inc|llc|ltd|co|corp|limited|incorporated|plc|gmbh|sas|bv|ag"
    r"|enterprise|enterprises|solutions|technologies|technology|services"
    r"|group|global|international|india|pvt|private)\b\.?",
    re.IGNORECASE,
)
_SLUG_PUNCT_RE = re.compile(r"[^a-z0-9]")


# --------------------------------------------------------------------------
# Internal helpers
# --------------------------------------------------------------------------

def _company_to_slug(company_name: str) -> str:
    """Turn 'Acme Corp.' into 'acmecorp' for a direct .com guess."""
    text = company_name.strip().lower()
    text = _SLUG_STRIP_RE.sub("", text)
    text = _SLUG_PUNCT_RE.sub("", text)
    return text.strip()


def _extract_domain(url: str) -> str:
    """Return 'example.com' from any full URL."""
    parsed = urlparse(url)
    return parsed.netloc or parsed.path


def _page_mentions_company(text: str, company_name: str) -> bool:
    """Loose check: does the page text contain the company name (or any word ≥4 chars from it)?"""
    company_lower = company_name.lower()
    text_lower = text.lower()

    # Direct substring match
    if company_lower in text_lower:
        return True

    # Match on any meaningful word of the company name (≥4 chars)
    words = [w for w in re.split(r"[\s,.\-]+", company_lower) if len(w) >= 4]
    return any(w in text_lower for w in words)


def _try_url(url: str) -> tuple[bool, str]:
    """GET a URL and return (success, response_text).

    Returns (False, "") on any error or non-200 status.
    """
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_REQUEST_TIMEOUT)
        if resp.status_code == 200:
            return True, resp.text
        return False, ""
    except requests.RequestException as exc:
        logger.debug("HTTP error fetching %s: %s", url, exc)
        return False, ""


def _clearbit_name_matches(query: str, result_name: str) -> bool:
    """Return True if the Clearbit result name is a reasonable match for the query.

    Rule: either the query is a substring of the result name, or the result
    name is a substring of the query (both lowercased).  This prevents
    blindly trusting an unrelated top result (e.g. searching "Apex" and
    getting "Apex Legends Community Hub").
    """
    q = query.strip().lower()
    r = result_name.strip().lower()
    return q in r or r in q


# --------------------------------------------------------------------------
# Resolution tiers
# --------------------------------------------------------------------------

def _tier1_direct_guess(company_name: str) -> str | None:
    """Try https://{slug}.com — accept only if content mentions the company.

    Tries two slugs in order:
      1. Full slug (all words joined, legal+generic suffixes stripped)
      2. First-word-only slug (covers 'UpGrad Enterprise' → 'upgrad.com')
    """
    slug = _company_to_slug(company_name)
    if not slug:
        return None

    # Build a first-word-only slug as a fallback candidate
    words = [w for w in re.split(r"[\s]+", company_name.strip().lower()) if len(w) >= 3]
    # Re-apply suffix stripping to each word to skip pure-suffix first words
    _suffix_only = re.compile(
        r"^(inc|llc|ltd|co|corp|limited|incorporated|plc|gmbh|sas|bv|ag"
        r"|enterprise|enterprises|solutions|technologies|technology|services"
        r"|group|global|international|india|pvt|private)$",
        re.IGNORECASE,
    )
    meaningful_words = [re.sub(r"[^a-z0-9]", "", w) for w in words if not _suffix_only.match(w)]
    first_word_slug = meaningful_words[0] if meaningful_words else ""

    slugs_to_try = [slug]
    if first_word_slug and first_word_slug != slug:
        slugs_to_try.append(first_word_slug)

    for candidate_slug in slugs_to_try:
        url = f"https://{candidate_slug}.com"
        logger.debug("[Tier 1] Trying direct guess: %s", url)
        ok, text = _try_url(url)
        if ok and _page_mentions_company(text, company_name):
            domain = f"{candidate_slug}.com"
            logger.info("[Tier 1] Direct guess succeeded for '%s' → %s", company_name, domain)
            return domain
        logger.debug("[Tier 1] Direct guess failed for '%s' (url=%s)", company_name, url)

    return None


def _tier2_clearbit_autocomplete(company_name: str) -> str | None:
    """Clearbit Autocomplete API — free, no key, no account, no card.

    Endpoint: GET https://autocomplete.clearbit.com/v1/companies/suggest?query={name}
    Response:  JSON array of {name, domain, logo} objects, ranked by relevance.

    We only trust index-0 when the returned company name is a reasonably
    close match to the search query (substring both ways, case-insensitive).
    A blank array, a non-200 status, or a name mismatch all degrade gracefully
    to None so the chain can continue to Tier 3.
    """
    logger.debug("[Tier 2] Clearbit Autocomplete query: '%s'", company_name)
    try:
        resp = requests.get(
            "https://autocomplete.clearbit.com/v1/companies/suggest",
            params={"query": company_name},
            headers=_HEADERS,
            timeout=_CLEARBIT_TIMEOUT,
        )

        if resp.status_code != 200:
            logger.debug(
                "[Tier 2] Clearbit returned HTTP %d for '%s'",
                resp.status_code, company_name,
            )
            return None

        results = resp.json()
        if not results:
            logger.debug("[Tier 2] Clearbit returned empty list for '%s'", company_name)
            return None

        top = results[0]
        result_name = top.get("name", "")
        domain = top.get("domain", "")

        if not domain:
            logger.debug(
                "[Tier 2] Clearbit top result has no domain for '%s'", company_name
            )
            return None

        if not _clearbit_name_matches(company_name, result_name):
            logger.debug(
                "[Tier 2] Clearbit top result name '%s' doesn't match query '%s' — skipping",
                result_name, company_name,
            )
            return None

        logger.info(
            "[Tier 2] Clearbit Autocomplete found domain for '%s' → %s (matched as '%s')",
            company_name, domain, result_name,
        )
        return domain

    except (requests.RequestException, ValueError) as exc:
        logger.debug("[Tier 2] Clearbit error for '%s': %s", company_name, exc)
        return None


def _tier3_duckduckgo(company_name: str) -> str | None:
    """DuckDuckGo Instant Answer API — free, no key, no rate limit."""
    query = f"{company_name} official website"
    url = "https://api.duckduckgo.com/"
    params = {"q": query, "format": "json", "no_redirect": "1", "no_html": "1"}

    logger.debug("[Tier 3] DuckDuckGo query: %s", query)
    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=_REQUEST_TIMEOUT)
        if resp.status_code != 200:
            logger.debug("[Tier 3] DDG returned HTTP %d", resp.status_code)
            return None

        data = resp.json()

        # AbstractURL is the most reliable field when DDG has a definitive answer
        abstract_url = data.get("AbstractURL") or data.get("AbstractSource")
        official_site = data.get("OfficialSite")

        candidate = official_site or abstract_url
        if candidate:
            domain = _extract_domain(candidate)
            if domain:
                logger.info("[Tier 3] DuckDuckGo found domain for '%s' → %s", company_name, domain)
                return domain

        # Fallback: try the first Related topic URL
        related = data.get("RelatedTopics") or []
        for topic in related:
            first_url = topic.get("FirstURL", "")
            if first_url and _page_mentions_company(first_url, company_name):
                domain = _extract_domain(first_url)
                if domain and "duckduckgo" not in domain:
                    logger.info(
                        "[Tier 3] DuckDuckGo RelatedTopics found domain for '%s' → %s",
                        company_name, domain
                    )
                    return domain

        logger.debug("[Tier 3] DuckDuckGo found nothing useful for '%s'", company_name)
        return None

    except (requests.RequestException, ValueError) as exc:
        logger.debug("[Tier 3] DuckDuckGo error for '%s': %s", company_name, exc)
        return None


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def resolve_domain(company_name: str) -> tuple[str | None, str]:
    """Find the official domain for a company name.

    Tries three free tiers in order (direct guess → Clearbit → DuckDuckGo)
    and stops at the first success.

    Args:
        company_name: Raw company name from the resume.

    Returns:
        (domain, source) where:
            domain  — e.g. "salesforce.com" or None if all tiers failed.
            source  — one of: "direct_guess" | "clearbit" | "duckduckgo" | "not_found"
    """
    if not company_name or not company_name.strip():
        return None, "not_found"

    logger.info("Resolving domain for company: '%s'", company_name)

    # Tier 1 — Direct guess (free, instant, no API needed)
    domain = _tier1_direct_guess(company_name)
    if domain:
        return domain, "direct_guess"

    # Tier 2 — Clearbit Autocomplete (free, no key, no account, no card)
    domain = _tier2_clearbit_autocomplete(company_name)
    if domain:
        return domain, "clearbit"

    # Tier 3 — DuckDuckGo (free, no key, no rate limit)
    domain = _tier3_duckduckgo(company_name)
    if domain:
        return domain, "duckduckgo"

    logger.warning("All resolution tiers failed for '%s' — domain not found", company_name)
    return None, "not_found"
