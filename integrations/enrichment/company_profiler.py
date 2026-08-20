"""Company profiler — scrapes a domain and extracts a structured profile.

Given a resolved domain (e.g. "salesforce.com"), this module:
  1. Fetches the homepage + common sub-pages (/about, /about-us, /company,
     /solutions, /customers, /pricing) using requests + BeautifulSoup.
  2. Feeds the combined text to an AI model via a small dedicated prompt
     (prompts/company_profile/v1.txt) to extract:
       sells_what    — one or two sentences on the product/service
       geography     — regions the company sells into (null if not explicit)
       market_segment — SMB | Mid-Market | Enterprise | Mixed (null if unclear)
  3. Returns the profile dict and saves it to the cache via company_cache.

Design rule: if a field can't be supported by something actually on the page,
the AI is instructed to return null. Better null than wrong.

We use requests + BeautifulSoup first. We do NOT default to Playwright
(headless browser) — that's slower and heavier. We only note the possibility
for future extension if a site is clearly JS-only.
"""

import json
import logging
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from integrations.enrichment import company_cache

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 10  # seconds
_USER_AGENT = "Mozilla/5.0 (compatible; ResumeBot/1.0)"
_HEADERS = {"User-Agent": _USER_AGENT}
_MAX_TEXT_CHARS = 6000  # Cap text sent to AI to keep prompt tokens manageable

# Sub-pages to probe for additional company context
_SUB_PATHS = ["/about", "/about-us", "/company", "/solutions", "/customers", "/pricing"]

# Prompt path
_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "prompts" / "company_profile" / "v1.txt"
)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _fetch_text(url: str) -> str:
    """Fetch a URL and return clean visible text (no scripts/styles)."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove script/style/nav/footer noise
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            tag.decompose()

        text = soup.get_text(separator=" ", strip=True)
        return text[:_MAX_TEXT_CHARS]
    except requests.RequestException as exc:
        logger.debug("Could not fetch %s: %s", url, exc)
        return ""


def _build_base_url(domain: str) -> str:
    """Ensure the domain has an https:// prefix."""
    domain = domain.strip().lower()
    if domain.startswith("http://") or domain.startswith("https://"):
        return domain
    return f"https://{domain}"


def _load_prompt_template() -> str:
    """Load the company profile prompt from disk."""
    try:
        return _PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.error("Company profile prompt not found at %s", _PROMPT_PATH)
        raise


def _call_ai(prompt: str) -> str:
    """Call the active AI provider using the same pattern as base_client.py.

    We call the AI directly here rather than reusing extract_fields()
    because this is a different, smaller prompt — not a resume extraction.
    We share the same underlying Groq/Gemini client initialisation pattern.

    Returns raw text response.
    """
    import os

    provider = os.getenv("AI_PROVIDER", "groq").lower().strip()

    if provider == "groq":
        from groq import Groq
        api_key = os.getenv("GROQ_API_KEY", "")
        if not api_key:
            raise ValueError("GROQ_API_KEY not set")
        # Use the same model as the main GroqClient — read from env so it
        # never drifts out of sync when the model is updated in .env.
        model = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=512,
        )
        return response.choices[0].message.content or ""

    elif provider == "gemini":
        import google.generativeai as genai
        api_key = os.getenv("GEMINI_API_KEY", "")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not set")
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content(prompt)
        return response.text or ""

    raise ValueError(f"Unknown AI_PROVIDER: '{provider}'")


def _parse_profile_response(raw: str) -> dict:
    """Parse the AI's JSON response into a profile dict.

    Strips markdown fences if present. Returns defaults on parse failure.
    """
    text = raw.strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        text = text[first_nl + 1:] if first_nl != -1 else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        data = json.loads(text)
        # is_saas_company must always be "Yes" or "No" — never null
        raw_saas = data.get("is_saas_company") or ""
        is_saas = "Yes" if str(raw_saas).strip().lower() == "yes" else "No"
        return {
            "sells_what": data.get("sells_what") or None,
            "geography": data.get("geography") or None,
            "market_segment": data.get("market_segment") or None,
            "is_saas_company": is_saas,
        }
    except (json.JSONDecodeError, AttributeError) as exc:
        logger.warning("Could not parse company profile AI response: %s | raw=%s", exc, raw[:200])
        return {"sells_what": None, "geography": None, "market_segment": None, "is_saas_company": "No"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def profile_company(
    company_name: str,
    domain: str,
    source: str = "direct_guess",
) -> dict:
    """Scrape a company's domain and extract a structured profile.

    The profile is saved to the cache before returning.

    Args:
        company_name: Raw company name (used as the cache key).
        domain: Resolved domain, e.g. "salesforce.com".
        source: Which resolution tier found the domain (passed through to cache).

    Returns:
        Dict with keys: domain, sells_what, geography, market_segment, source.
        Any field the site doesn't clearly support will be None.
    """
    base_url = _build_base_url(domain)
    logger.info("Profiling company '%s' at %s", company_name, base_url)

    # --- 1. Collect text from homepage + sub-pages ---
    all_text_parts: list[str] = []

    homepage_text = _fetch_text(base_url)
    if homepage_text:
        all_text_parts.append(f"[Homepage]\n{homepage_text}")

    for path in _SUB_PATHS:
        sub_url = urljoin(base_url, path)
        # Don't re-fetch the homepage itself
        if urlparse(sub_url).path in ("", "/", ""):
            continue
        logger.debug("Fetching sub-page: %s", sub_url)
        sub_text = _fetch_text(sub_url)
        if sub_text and len(sub_text.strip()) > 100:
            all_text_parts.append(f"[{path}]\n{sub_text}")
            if sum(len(p) for p in all_text_parts) > _MAX_TEXT_CHARS * 3:
                break  # Enough context — don't over-fetch

    if not all_text_parts:
        logger.warning("No text retrieved from '%s' (%s) — storing null profile", company_name, domain)
        profile = {
            "domain": domain,
            "sells_what": None,
            "geography": None,
            "market_segment": None,
            "is_saas_company": "No",  # Default when site not reachable
            "source": source,
        }
        company_cache.save_company(company_name, profile)
        return profile

    combined_text = "\n\n".join(all_text_parts)
    # Cap at a safe size for the AI prompt
    combined_text = combined_text[:_MAX_TEXT_CHARS * 3]

    # --- 2. Call AI ---
    try:
        prompt_template = _load_prompt_template()
        prompt = prompt_template.replace("{website_text}", combined_text)
        raw_response = _call_ai(prompt)
        ai_profile = _parse_profile_response(raw_response)
    except Exception as exc:
        logger.warning(
            "AI call failed while profiling '%s': %s — using null profile",
            company_name, exc
        )
        ai_profile = {"sells_what": None, "geography": None, "market_segment": None, "is_saas_company": "No"}

    # --- 3. Assemble and cache ---
    profile = {
        "domain": domain,
        "sells_what": ai_profile.get("sells_what"),
        "geography": ai_profile.get("geography"),
        "market_segment": ai_profile.get("market_segment"),
        "is_saas_company": ai_profile.get("is_saas_company", "No"),
        "source": source,
    }

    company_cache.save_company(company_name, profile)
    logger.info(
        "Profiled '%s': segment=%s, geography=%s, saas=%s",
        company_name,
        profile.get("market_segment"),
        profile.get("geography"),
        profile.get("is_saas_company"),
    )
    return profile
