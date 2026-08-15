"""SQLite-backed cache for company profiles.

Why SQLite: built into Python, zero additional dependencies, zero cost.
The DB file lives at data/company_cache.db (relative to the project root).
Add data/ to .gitignore — this is local state, not something we commit.

Schema:
    company_profiles
        company_name  TEXT PRIMARY KEY  -- normalized (lowercased, trimmed, stripped of suffixes)
        domain        TEXT              -- resolved official domain, e.g. "salesforce.com"
        sells_what    TEXT              -- AI-extracted product/service description
        geography     TEXT              -- regions the company sells into
        market_segment TEXT             -- SMB | Mid-Market | Enterprise | Mixed
        is_saas_company TEXT            -- 'Yes' | 'No' | NULL (null = not yet determined)
        source        TEXT              -- which resolution tier succeeded
        last_updated  TEXT              -- ISO-8601 UTC timestamp

    job_openings
        company_name  TEXT              -- same normalized key
        role_title    TEXT
        required_exp  TEXT              -- "2-4 years" or "" if not stated
        link          TEXT
        last_checked  TEXT              -- ISO-8601 UTC timestamp

Normalization rule (applied in _normalize_name):
    Lowercase, strip leading/trailing whitespace, remove common legal suffixes
    ("inc", "llc", "ltd", "co", "corp", "limited", "incorporated") and punctuation.
    "Apple Inc." → "apple"
    "Salesforce.com, Inc." → "salesforcecom"  (dot also stripped)

    This is intentionally aggressive — two rows for "Acme" and "Acme Inc"
    should never exist. If a false collision occurs in practice,
    adjust the stripping pattern rather than loosening the normalization.
"""

import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# DB path: always relative to project root (parent of integrations/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = _PROJECT_ROOT / "data" / "company_cache.db"

# Legal suffix patterns to strip during normalization
_SUFFIX_RE = re.compile(
    r"\b(inc|llc|ltd|co|corp|limited|incorporated|plc|gmbh|sas|bv|ag)\b",
    re.IGNORECASE,
)

# Characters to collapse after suffix stripping
_PUNCT_RE = re.compile(r"[^a-z0-9\s]")
_SPACE_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def normalize_company_name(name: str) -> str:
    """Normalize a company name to a consistent cache key.

    Args:
        name: Raw company name, e.g. "Salesforce.com, Inc."

    Returns:
        Normalized string, e.g. "salesforcecom"
    """
    if not name:
        return ""
    text = name.strip().lower()
    text = _SUFFIX_RE.sub("", text)       # remove legal suffixes
    text = _PUNCT_RE.sub("", text)        # remove punctuation
    text = _SPACE_RE.sub("", text)        # collapse all whitespace
    return text.strip()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# DB lifecycle
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    """Open (or create) the SQLite DB file and return a connection.

    Creates the data/ directory if it doesn't exist.
    """
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist yet. Idempotent."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS company_profiles (
            company_name           TEXT PRIMARY KEY,
            domain                 TEXT,
            sells_what             TEXT,
            geography              TEXT,
            market_segment         TEXT,
            is_saas_company        TEXT,
            classification_source  TEXT,
            source                 TEXT,
            last_updated           TEXT
        );

        CREATE TABLE IF NOT EXISTS job_openings (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name  TEXT NOT NULL,
            role_title    TEXT,
            required_exp  TEXT,
            link          TEXT,
            last_checked  TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_job_openings_company
            ON job_openings(company_name);
    """)
    conn.commit()

    # Add columns to existing DBs that pre-date these fields.
    # ALTER TABLE ... ADD COLUMN is idempotent only via try/except in SQLite.
    for col_stmt in [
        "ALTER TABLE company_profiles ADD COLUMN is_saas_company TEXT",
        "ALTER TABLE company_profiles ADD COLUMN classification_source TEXT",
    ]:
        try:
            conn.execute(col_stmt)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists — harmless


# ---------------------------------------------------------------------------
# Company profile cache API
# ---------------------------------------------------------------------------

def get_company(name: str) -> dict | None:
    """Look up a company profile from the cache.

    Args:
        name: Raw or normalised company name.

    Returns:
        Dict with keys: company_name, domain, sells_what, geography,
        market_segment, is_saas_company, classification_source, source,
        last_updated — or None if not found.
    """
    key = normalize_company_name(name)
    if not key:
        return None

    try:
        with _get_conn() as conn:
            _ensure_schema(conn)
            row = conn.execute(
                "SELECT * FROM company_profiles WHERE company_name = ?", (key,)
            ).fetchone()
            if row:
                return dict(row)
            return None
    except sqlite3.Error as exc:
        logger.error("Cache read error for '%s': %s", name, exc)
        return None


def save_company(name: str, profile: dict) -> None:
    """Write (upsert) a company profile into the cache.

    Args:
        name: Raw company name (will be normalized before storage).
        profile: Dict with any subset of: domain, sells_what, geography,
                 market_segment, is_saas_company, classification_source, source.
                 Missing keys are stored as NULL.
    """
    key = normalize_company_name(name)
    if not key:
        logger.warning("save_company called with empty name — skipping")
        return

    try:
        with _get_conn() as conn:
            _ensure_schema(conn)
            conn.execute(
                """
                INSERT INTO company_profiles
                    (company_name, domain, sells_what, geography, market_segment,
                     is_saas_company, classification_source, source, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(company_name) DO UPDATE SET
                    domain                = excluded.domain,
                    sells_what            = excluded.sells_what,
                    geography             = excluded.geography,
                    market_segment        = excluded.market_segment,
                    is_saas_company       = excluded.is_saas_company,
                    classification_source = excluded.classification_source,
                    source                = excluded.source,
                    last_updated          = excluded.last_updated
                """,
                (
                    key,
                    profile.get("domain"),
                    profile.get("sells_what"),
                    profile.get("geography"),
                    profile.get("market_segment"),
                    profile.get("is_saas_company"),
                    profile.get("classification_source"),
                    profile.get("source"),
                    _utc_now_iso(),
                ),
            )
            conn.commit()
        logger.debug("Cached company profile for '%s' (key='%s')", name, key)
    except sqlite3.Error as exc:
        logger.error("Cache write error for '%s': %s", name, exc)


def is_stale(name: str, max_age_days: int = 30) -> bool:
    """Check whether the cached record is older than max_age_days.

    'not_found' records use a longer cooldown (90 days) to avoid
    hammering failed lookups monthly — they're treated as stale only
    after 90 days regardless of what max_age_days says.

    Args:
        name: Raw or normalized company name.
        max_age_days: Days before a successful profile is considered stale.

    Returns:
        True if the record doesn't exist or is stale; False if it's fresh.
    """
    record = get_company(name)
    if record is None:
        return True  # Not in cache at all — definitely stale

    last_updated_str = record.get("last_updated")
    if not last_updated_str:
        return True

    try:
        last_updated = datetime.fromisoformat(last_updated_str)
        if last_updated.tzinfo is None:
            last_updated = last_updated.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        age_days = (now - last_updated).days

        # 'not_found' entries get a 90-day cooldown — avoids hammering failed lookups
        if record.get("source") == "not_found":
            return age_days >= 90

        return age_days >= max_age_days
    except (ValueError, TypeError) as exc:
        logger.warning("Could not parse last_updated for '%s': %s", name, exc)
        return True


# ---------------------------------------------------------------------------
# Job openings cache API
# ---------------------------------------------------------------------------

def get_job_openings(name: str) -> list[dict]:
    """Retrieve cached job openings for a company.

    Args:
        name: Raw company name.

    Returns:
        List of dicts with keys: role_title, required_exp, link, last_checked.
        Empty list if no cached data.
    """
    key = normalize_company_name(name)
    if not key:
        return []

    try:
        with _get_conn() as conn:
            _ensure_schema(conn)
            rows = conn.execute(
                "SELECT role_title, required_exp, link, last_checked "
                "FROM job_openings WHERE company_name = ?",
                (key,),
            ).fetchall()
            return [dict(r) for r in rows]
    except sqlite3.Error as exc:
        logger.error("Job openings read error for '%s': %s", name, exc)
        return []


def are_openings_stale(name: str, max_age_hours: int = 24) -> bool:
    """Check if cached job openings are older than max_age_hours.

    Args:
        name: Raw company name.
        max_age_hours: Hours before openings are considered stale (default 24).

    Returns:
        True if no cache or cache is stale; False if fresh.
    """
    openings = get_job_openings(name)
    if not openings:
        return True

    # Use the last_checked timestamp from the first row
    last_checked_str = openings[0].get("last_checked")
    if not last_checked_str:
        return True

    try:
        last_checked = datetime.fromisoformat(last_checked_str)
        if last_checked.tzinfo is None:
            last_checked = last_checked.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - last_checked).total_seconds() / 3600
        return age_hours >= max_age_hours
    except (ValueError, TypeError):
        return True


def save_job_openings(name: str, openings: list[dict]) -> None:
    """Write (replace) cached job openings for a company.

    Replaces all existing rows for the company — this is a full refresh,
    not an append.

    Args:
        name: Raw company name.
        openings: List of dicts with keys: role_title, required_exp, link.
    """
    key = normalize_company_name(name)
    if not key:
        return

    now_iso = _utc_now_iso()
    try:
        with _get_conn() as conn:
            _ensure_schema(conn)
            # Full replace — delete old rows first
            conn.execute("DELETE FROM job_openings WHERE company_name = ?", (key,))
            conn.executemany(
                """
                INSERT INTO job_openings (company_name, role_title, required_exp, link, last_checked)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (key, o.get("role_title", ""), o.get("required_exp", ""), o.get("link", ""), now_iso)
                    for o in openings
                ],
            )
            conn.commit()
        logger.debug("Cached %d job openings for '%s'", len(openings), name)
    except sqlite3.Error as exc:
        logger.error("Job openings write error for '%s': %s", name, exc)
