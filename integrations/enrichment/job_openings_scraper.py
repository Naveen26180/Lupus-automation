"""Sales job openings scraper.

Only triggered per-company when needed (not a background crawler).
Tries ATS public APIs first (more reliable than raw HTML scraping),
then falls back to HTML scraping + AI extraction.

Supported ATS platforms:
  Greenhouse  — boards-api.greenhouse.io/v1/boards/{slug}/jobs
  Lever       — api.lever.co/v0/postings/{slug}
  Ashby       — api.ashbyhq.com/posting-api/job-board/{slug}

Fallback: scrape /careers or /jobs page HTML, send to AI to extract listings.

All results are filtered to sales roles using config/sales_titles.SALES_TITLE_KEYWORDS.
Results are cached in the job_openings table (24-hour TTL).

Output format per opening:
    {"role_title": "...", "required_exp": "2-4 years" or "", "link": "https://..."}
"""

import json
import logging
import os
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config.sales_titles import SALES_TITLE_KEYWORDS
from integrations.enrichment import company_cache

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 10
_USER_AGENT = "Mozilla/5.0 (compatible; ResumeBot/1.0)"
_HEADERS = {"User-Agent": _USER_AGENT}

# Common careers page paths to probe
_CAREERS_PATHS = ["/careers", "/jobs", "/join-us", "/join", "/work-with-us", "/openings"]

# Known ATS hostname patterns → (platform_name, slug_extraction_fn)
_ATS_PATTERNS = [
    (re.compile(r"greenhouse\.io"), "greenhouse"),
    (re.compile(r"lever\.co"), "lever"),
    (re.compile(r"ashbyhq\.com"), "ashby"),
    (re.compile(r"smartrecruiters\.com"), "smartrecruiters"),
]

# Experience pattern extractor from job description text
_EXP_RE = re.compile(
    r"(\d+[\+\-–]?\s*(?:to|[-–])?\s*\d*)\s*(?:\+\s*)?(?:year|yr)s?",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Sales title filter
# ---------------------------------------------------------------------------

def _is_sales_role(title: str) -> bool:
    """Return True if the job title matches any sales keyword."""
    title_lower = title.lower()
    for kw in SALES_TITLE_KEYWORDS:
        if kw.strip().lower() in title_lower:
            return True
    return False


def _extract_experience(text: str) -> str:
    """Pull e.g. '2-4 years' from freeform text. Returns '' if not found."""
    match = _EXP_RE.search(text or "")
    if match:
        raw = match.group(0).strip()
        # Normalise: "2 to 4 years" → "2-4 years"
        raw = re.sub(r"\s+to\s+", "-", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s+", " ", raw)
        return raw
    return ""


# ---------------------------------------------------------------------------
# ATS API methods
# ---------------------------------------------------------------------------

def _try_greenhouse(domain: str) -> list[dict] | None:
    """Attempt to fetch jobs from Greenhouse boards API."""
    # Derive slug from domain: "stripe.com" → "stripe"
    slug = domain.split(".")[0]
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_REQUEST_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            jobs = data.get("jobs") or []
            logger.info("[Greenhouse] Found %d total jobs for slug '%s'", len(jobs), slug)
            results = []
            for job in jobs:
                title = job.get("title", "")
                if not _is_sales_role(title):
                    continue
                link = job.get("absolute_url") or ""
                exp = _extract_experience(job.get("content", ""))
                results.append({"role_title": title, "required_exp": exp, "link": link})
            return results
    except (requests.RequestException, ValueError) as exc:
        logger.debug("[Greenhouse] Error for slug '%s': %s", slug, exc)
    return None


def _try_lever(domain: str) -> list[dict] | None:
    """Attempt to fetch jobs from Lever posting API."""
    slug = domain.split(".")[0]
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_REQUEST_TIMEOUT)
        if resp.status_code == 200:
            jobs = resp.json()
            if not isinstance(jobs, list):
                return None
            logger.info("[Lever] Found %d total jobs for slug '%s'", len(jobs), slug)
            results = []
            for job in jobs:
                title = job.get("text", "")
                if not _is_sales_role(title):
                    continue
                link = job.get("hostedUrl") or job.get("applyUrl") or ""
                desc_text = json.dumps(job.get("descriptionPlain") or job.get("description") or "")
                exp = _extract_experience(desc_text)
                results.append({"role_title": title, "required_exp": exp, "link": link})
            return results
    except (requests.RequestException, ValueError) as exc:
        logger.debug("[Lever] Error for slug '%s': %s", slug, exc)
    return None


def _try_ashby(domain: str) -> list[dict] | None:
    """Attempt to fetch jobs from Ashby posting API."""
    slug = domain.split(".")[0]
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_REQUEST_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            jobs = data.get("jobPostings") or []
            logger.info("[Ashby] Found %d total jobs for slug '%s'", len(jobs), slug)
            results = []
            for job in jobs:
                title = job.get("title", "")
                if not _is_sales_role(title):
                    continue
                link = job.get("jobUrl") or ""
                exp = _extract_experience(job.get("descriptionPlain") or "")
                results.append({"role_title": title, "required_exp": exp, "link": link})
            return results
    except (requests.RequestException, ValueError) as exc:
        logger.debug("[Ashby] Error for slug '%s': %s", slug, exc)
    return None


# ---------------------------------------------------------------------------
# ATS detection from careers page links
# ---------------------------------------------------------------------------

def _detect_ats_from_page(base_url: str) -> str | None:
    """Look for ATS links on the homepage or careers page.

    Returns the ATS name if detected, else None.
    """
    for path in [""] + _CAREERS_PATHS[:3]:
        check_url = urljoin(base_url, path) if path else base_url
        try:
            resp = requests.get(check_url, headers=_HEADERS, timeout=_REQUEST_TIMEOUT, allow_redirects=True)
            if resp.status_code != 200:
                continue
            # Check final URL after redirects
            final_url = resp.url
            for pattern, ats_name in _ATS_PATTERNS:
                if pattern.search(final_url):
                    logger.info("Detected ATS '%s' via redirect on %s", ats_name, check_url)
                    return ats_name
            # Check links in page body
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                for pattern, ats_name in _ATS_PATTERNS:
                    if pattern.search(href):
                        logger.info("Detected ATS '%s' via link on %s", ats_name, check_url)
                        return ats_name
        except requests.RequestException:
            pass
    return None


# ---------------------------------------------------------------------------
# HTML scraping fallback
# ---------------------------------------------------------------------------

def _scrape_careers_html(base_url: str) -> list[dict]:
    """Scrape careers/jobs page HTML and use AI to extract listings."""
    careers_text = ""
    for path in _CAREERS_PATHS:
        url = urljoin(base_url, path)
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=_REQUEST_TIMEOUT)
            if resp.status_code == 200 and len(resp.text) > 500:
                soup = BeautifulSoup(resp.text, "html.parser")
                for tag in soup(["script", "style", "nav", "footer"]):
                    tag.decompose()
                text = soup.get_text(separator="\n", strip=True)
                if len(text.strip()) > 200:
                    careers_text = text[:5000]
                    logger.debug("Fetched careers page at %s (len=%d)", url, len(careers_text))
                    break
        except requests.RequestException:
            pass

    if not careers_text:
        logger.debug("No careers page found for %s", base_url)
        return []

    # Ask AI to extract job listings
    prompt = f"""Below is text scraped from a company's careers/jobs page.

Extract a list of job postings from this text. Return ONLY a JSON array of objects.
Each object must have:
  - "title": the job title (string)
  - "required_exp": years of experience if stated (string, e.g. "2-4 years"), else ""
  - "link": the application or job detail link if present (string), else ""

Return only the JSON array, no explanation.

CAREERS PAGE TEXT:
{careers_text}
"""
    try:
        from integrations.enrichment.company_profiler import _call_ai
        raw = _call_ai(prompt)
        text = raw.strip()
        if text.startswith("```"):
            text = text[text.find("\n") + 1:]
        if text.endswith("```"):
            text = text[:-3]
        jobs = json.loads(text.strip())
        if not isinstance(jobs, list):
            return []

        results = []
        for job in jobs:
            title = job.get("title", "")
            if _is_sales_role(title):
                results.append({
                    "role_title": title,
                    "required_exp": job.get("required_exp", ""),
                    "link": job.get("link", ""),
                })
        logger.info("[HTML scrape] Extracted %d sales roles from careers page", len(results))
        return results
    except Exception as exc:
        logger.warning("AI-based careers scraping failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scrape_job_openings(company_name: str, domain: str) -> list[dict]:
    """Fetch live sales job openings for a company.

    Uses cached data if fresh (<24h). Otherwise:
      1. Tries known ATS APIs (Greenhouse, Lever, Ashby).
      2. Detects ATS from page links.
      3. Falls back to HTML scraping + AI extraction.

    Results are cached and returned.

    Args:
        company_name: Raw company name.
        domain: Resolved domain, e.g. "salesforce.com".

    Returns:
        List of dicts: [{role_title, required_exp, link}, ...]
        Returns [] if no sales roles found or all methods fail.
    """
    if not domain:
        logger.debug("No domain for '%s' — skipping job scraping", company_name)
        return []

    # Check cache freshness
    if not company_cache.are_openings_stale(company_name):
        cached = company_cache.get_job_openings(company_name)
        logger.info("Using cached job openings for '%s' (%d roles)", company_name, len(cached))
        return cached

    base_url = f"https://{domain}" if not domain.startswith("http") else domain
    slug = domain.split(".")[0]

    logger.info("Scraping live job openings for '%s' at %s", company_name, base_url)

    openings: list[dict] | None = None

    # --- Try ATS APIs directly by well-known slug ---
    for try_fn, label in [
        (_try_greenhouse, "greenhouse"),
        (_try_lever, "lever"),
        (_try_ashby, "ashby"),
    ]:
        result = try_fn(domain)
        if result is not None:
            logger.info("[%s] Successfully fetched %d sales roles for '%s'", label, len(result), company_name)
            openings = result
            break

    # --- Try detecting ATS from page links ---
    if openings is None:
        ats = _detect_ats_from_page(base_url)
        if ats == "greenhouse":
            openings = _try_greenhouse(domain) or []
        elif ats == "lever":
            openings = _try_lever(domain) or []
        elif ats == "ashby":
            openings = _try_ashby(domain) or []

    # --- Fallback: HTML scraping ---
    if openings is None:
        openings = _scrape_careers_html(base_url)

    openings = openings or []

    # Cache and return
    company_cache.save_job_openings(company_name, openings)
    logger.info("Found %d sales roles for '%s'", len(openings), company_name)
    return openings


def format_openings_for_display(company_name: str, openings: list[dict]) -> str:
    """Format job openings as the canonical display block.

    Example output:
        Salesforce:
        SDR | 2-4 years | https://...
        AE | 5+ years | https://...

    Args:
        company_name: Display name (not normalized).
        openings: List of opening dicts.

    Returns:
        Formatted string. Returns "" if openings is empty.
    """
    if not openings:
        return ""

    lines = [f"{company_name}:"]
    for o in openings:
        title = o.get("role_title", "")
        exp = o.get("required_exp", "")
        link = o.get("link", "")
        segments = [title]
        if exp:
            segments.append(exp)
        if link:
            segments.append(link)
        lines.append(" | ".join(segments))
    return "\n".join(lines)
