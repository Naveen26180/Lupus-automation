"""Post-extraction field validation for AI output.

Second safety net after the AI prompt — catches invalid emails,
malformed LinkedIn URLs, unparseable phone numbers, and non-numeric
YOE values that slipped through.
"""

import logging
import re

import phonenumbers

logger = logging.getLogger(__name__)

# Simple email regex — catches most real addresses without being overly strict
_EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)


def validate_extracted_fields(data: dict) -> dict:
    """Validate and sanitize AI-extracted fields.

    Applies field-level rules per the Phase 1 spec. Invalid values are
    set to null rather than raising — we want partial data, not total
    failure.

    Args:
        data: Dict with the 12 Phase 1 keys (from AI response).

    Returns:
        A new dict with validated/sanitized values. Original dict
        is not mutated.
    """
    result = dict(data)  # shallow copy

    result["email"] = _validate_email(result.get("email"))
    result["linkedin_url"] = _validate_linkedin_url(result.get("linkedin_url"))
    result["phone_number"] = _validate_phone(result.get("phone_number"))
    result["years_of_experience"] = _validate_yoe(result.get("years_of_experience"))

    # --- geography: normalize to canonical tags ---
    result["geography"] = _validate_geography(result.get("geography"))

    # --- saas_experience: ensure it's a tag list, join with "; " ---
    result["saas_experience"] = _validate_saas_experience(result.get("saas_experience"))

    # --- market_segment: enforce closed vocabulary ---
    result["market_segment"] = _validate_market_segment(result.get("market_segment"))

    # Free-text scalar fields (no tag validation needed)
    # NOTE: past_companies is intentionally excluded — it must stay as a list
    # so that sheets_client._cell_list() can join it with "; " correctly.
    # Converting it to str() here would produce Python repr e.g. "['Acme', 'Foo']".
    for field in ("college", "current_company"):
        value = result.get(field)
        if value is not None and not isinstance(value, str):
            result[field] = str(value)

    # past_companies: must always be a list (never None, never a plain string).
    pc = result.get("past_companies")
    if pc is None:
        result["past_companies"] = []
    elif not isinstance(pc, list):
        # Shouldn't happen in normal flow, but guard against unexpected str input
        result["past_companies"] = [s.strip() for s in str(pc).split(";") if s.strip()]

    # full_name: should be a non-empty string or null
    name = result.get("full_name")
    if name is not None:
        name = str(name).strip()
        result["full_name"] = name if name else None

    return result


def _validate_email(value: str | None) -> str | None:
    """Validate email format.

    Args:
        value: Email string from AI output, or None.

    Returns:
        The email if valid, otherwise None.
    """
    if value is None:
        return None

    value = str(value).strip()
    if not value or not _EMAIL_REGEX.match(value):
        logger.info(
            "Invalid email format nullified: %s",
            value[:30] if value else "(empty)",
        )
        return None

    return value


def _validate_linkedin_url(value: str | None) -> str | None:
    """Validate LinkedIn URL.

    Args:
        value: URL string from AI output, or None.

    Returns:
        The URL if it contains 'linkedin.com', otherwise None.
    """
    if value is None:
        return None

    value = str(value).strip()
    if not value or "linkedin.com" not in value.lower():
        logger.info(
            "Invalid LinkedIn URL nullified: %s",
            value[:50] if value else "(empty)",
        )
        return None

    return value


def _validate_phone(value: str | None) -> str | None:
    """Validate and normalize phone number.

    Uses the phonenumbers library to attempt parsing. If parsing fails,
    keeps the original value if it looks phone-like (has digits), otherwise
    nullifies it.

    Args:
        value: Phone number string from AI output, or None.

    Returns:
        Normalized phone number, original value, or None.
    """
    if value is None:
        return None

    value = str(value).strip()
    if not value:
        return None

    # Try to parse with phonenumbers library
    try:
        # Try with country code first
        parsed = phonenumbers.parse(value, None)
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.E164
            )
    except phonenumbers.NumberParseException:
        pass

    # Try common country codes for parsing
    for region in ("US", "IN", "GB"):
        try:
            parsed = phonenumbers.parse(value, region)
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(
                    parsed, phonenumbers.PhoneNumberFormat.E164
                )
        except phonenumbers.NumberParseException:
            continue

    # If it has at least 7 digits, keep as-is (international formats vary)
    digits = re.sub(r"\D", "", value)
    if len(digits) >= 7:
        logger.debug(
            "Phone number couldn't be parsed but has digits, keeping: %s",
            value,
        )
        return value

    logger.info("Unparseable phone number nullified: %s", value)
    return None


def _validate_yoe(value) -> float | int | None:
    """Validate years of experience is numeric.

    Args:
        value: YOE value from AI output — could be int, float, str, or None.

    Returns:
        A numeric value (int or float), or None if not parseable.
    """
    if value is None:
        return None

    # Already numeric
    if isinstance(value, (int, float)):
        return value

    # String like "5" or "3.5" or "5+"
    text = str(value).strip().rstrip("+")
    try:
        num = float(text)
        # Return int if it's a whole number for cleanliness
        return int(num) if num == int(num) else round(num, 1)
    except (ValueError, TypeError):
        logger.info(
            "Non-numeric years_of_experience nullified: %s", value
        )
        return None


# ── Canonical geography tags ──────────────────────────────────────────────
_GEO_TAGS = frozenset([
    "NA", "LATAM", "EMEA", "EU", "UKI", "DACH", "Benelux", "Nordics",
    "CEE", "Iberia", "CIS", "GCC", "MEA", "APAC", "APJ", "ANZ",
    "ASEAN", "SEA", "India", "Global", "ROW",
])

_GEO_ALIASES: dict[str, str] = {
    "north america": "NA", "us": "NA", "usa": "NA", "united states": "NA",
    "u.s.": "NA", "u.s.a.": "NA", "the us": "NA", "united states of america": "NA",
    "canada": "NA",
    "latam": "LATAM", "latin america": "LATAM",
    "europe": "EU", "eu": "EU",
    "uk": "UKI", "united kingdom": "UKI", "great britain": "UKI",
    "dach": "DACH", "germany": "DACH", "austria": "DACH", "switzerland": "DACH",
    "nordics": "Nordics", "scandinavia": "Nordics",
    "middle east": "MEA", "gcc": "GCC", "gulf": "GCC", "gcc countries": "GCC",
    "africa": "MEA", "middle east and africa": "MEA",
    "asia": "APAC", "asia-pacific": "APAC", "asia pacific": "APAC",
    "apac": "APAC", "apj": "APJ",
    "south east asia": "SEA", "southeast asia": "SEA", "asean": "ASEAN",
    "india": "India",
    "anz": "ANZ", "australia": "ANZ", "new zealand": "ANZ",
    "global": "Global", "worldwide": "Global", "international": "Global",
}

# Noise words appended after region names that should be stripped before matching
_GEO_NOISE_SUFFIXES = (
    " territory", " region", " sales", " market", " accounts",
    " operations", " business", " coverage",
)


def _strip_geo_noise(text: str) -> str:
    """Remove trailing geography noise words to improve alias matching."""
    low = text.lower().strip()
    for suffix in _GEO_NOISE_SUFFIXES:
        if low.endswith(suffix):
            low = low[: -len(suffix)].strip()
    # Also strip leading noise
    for prefix in ("the ",):
        if low.startswith(prefix):
            low = low[len(prefix) :].strip()
    return low


def _validate_geography(value) -> str | None:
    """Normalize geography to canonical semicolon-separated tags.
    
    Strictly accepts only canonical tags emitted by the Classification Engine.
    """
    if value is None:
        return None
    if isinstance(value, list):
        raw_items = [str(v).strip() for v in value if v]
    else:
        raw_items = [s.strip() for s in str(value).replace(",", ";").split(";") if s.strip()]

    normalized = []
    for item in raw_items:
        if item in _GEO_TAGS:
            if item not in normalized:
                normalized.append(item)
        else:
            logger.info(
                "[VALIDATOR] Field: geography | Dropped: '%s' | Reason: no canonical tag match",
                item,
            )

    return "; ".join(normalized) if normalized else None


# ── SaaS experience tags ─────────────────────────────────────────────────
_SAAS_EXP_ALLOWED = frozenset([
    "Full-Cycle Sales", "Outbound/Prospecting", "Inbound Sales",
    "Account Management", "Consultative Selling", "Inside Sales",
    "Field Sales", "Channel Sales", "Sales Operations",
    "Customer Retention", "Upsell/Cross-Sell", "Sales Engineering",
    "Partner Sales", "Pre-Sales",
    "BANT", "SPIN", "MEDDIC", "MEDDPICC", "Challenger Sale",
    "Solution Selling", "Value Selling", "Sandler",
    "B2B", "B2C", "B2B2C", "SaaS Sales", "Transactional",
    "Enterprise Sales Cycle", "PLG",
    "Team Lead", "P&L Ownership", "Funnel Management",
])


def _validate_saas_experience(value) -> str | None:
    """Normalize saas_experience to semicolon-separated canonical tags.

    Accepts a list, a semicolon/comma-separated string, or a free-text
    paragraph (which is parsed as potential tags). Unknown tags are dropped.
    Maximum of 8 tags allowed based on the schema limits.
    """
    if value is None:
        return None

    # Handle list coercion correctly
    if isinstance(value, list):
        tags = [str(v).strip() for v in value if v]
    else:
        text = str(value).strip()
        if not text:
            return None
        # Try splitting as a delimited tag list
        tags = [s.strip() for s in text.replace(",", ";").split(";") if s.strip()]

    # Validate each tag against allowlist
    valid_tags = []
    for tag in tags:
        if tag in _SAAS_EXP_ALLOWED:
            if tag not in valid_tags:
                valid_tags.append(tag)
        else:
            logger.warning(
                "[VALIDATOR] Field: saas_experience | Dropped: '%s' | Reason: not in canonical allowlist",
                tag[:50],
            )

    if len(valid_tags) > 8:
        logger.warning("saas_experience exceeded 8 tags, keeping first 8")
        valid_tags = valid_tags[:8]

    return "; ".join(valid_tags) if valid_tags else None


# ── Market segment ────────────────────────────────────────────────────────
_SEGMENT_ALLOWED = frozenset(["SMB", "Mid-Market", "Enterprise", "B2C", "B2B2C", "SME", "D2C"])

_SEGMENT_ALIASES: dict[str, str] = {
    "smb": "SMB", "small business": "SMB",
    "mid-market": "Mid-Market", "mid market": "Mid-Market",
    "midmarket": "Mid-Market", "commercial": "Mid-Market",
    "enterprise": "Enterprise", "large enterprise": "Enterprise",
    "fortune 500": "Enterprise", "fortune 100": "Enterprise",
    "b2c": "B2C", "consumer": "B2C", "consumers": "B2C",
    "b2b2c": "B2B2C",
    "sme": "SME", "small and medium enterprises": "SME", "small medium enterprise": "SME",
    "d2c": "D2C", "direct to consumer": "D2C", "direct-to-consumer": "D2C",
    "mixed": "SMB; Mid-Market; Enterprise",
}


def _validate_market_segment(value) -> str | None:
    """Normalize market_segment to canonical semicolon-separated segments.
    
    Accepts list of tags or semicolon-separated string.
    Strictly enforces canonical vocabulary: SMB, Mid-Market, Enterprise.
    Unrecognized tags are dropped.
    """
    if value is None:
        return None

    # Handle list coercion correctly
    if isinstance(value, list):
        parts = [str(v).strip() for v in value if v]
    else:
        text = str(value).strip()
        if not text:
            return None

        # Check if the entire string maps to an alias combination first (e.g., "mixed")
        low = text.lower()
        if low in _SEGMENT_ALIASES:
            parts = [p.strip() for p in _SEGMENT_ALIASES[low].split(";")]
        else:
            # Try splitting (e.g. "SMB; Enterprise" or "SMB, Enterprise")
            parts = [s.strip() for s in text.replace(",", ";").split(";") if s.strip()]

    normalized = []
    for part in parts:
        alias = _SEGMENT_ALIASES.get(part.lower())
        if alias:
            if alias not in normalized:
                normalized.append(alias)
        elif part in _SEGMENT_ALLOWED:
            if part not in normalized:
                normalized.append(part)
        else:
            logger.warning(
                "[VALIDATOR] Field: market_segment | Dropped: '%s' | Reason: no canonical alias match",
                part[:50],
            )
            
    # Always return in ascending tier order
    tier_order = {"B2C": 1, "D2C": 2, "B2B2C": 3, "SMB": 4, "SME": 5, "Mid-Market": 6, "Enterprise": 7}
    normalized.sort(key=lambda t: tier_order.get(t, 99))

    return "; ".join(normalized) if normalized else None
