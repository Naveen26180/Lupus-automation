"""Tests for integrations/enrichment/company_cache.py.

Covers: normalize_company_name, save_company, get_company, is_stale,
save_job_openings, get_job_openings, are_openings_stale.

No network required — purely SQLite in a temp directory.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import integrations.enrichment.company_cache as cc


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point _DB_PATH at a fresh temp file for every test."""
    db_file = tmp_path / "test_cache.db"
    monkeypatch.setattr(cc, "_DB_PATH", db_file)
    yield db_file


# ---------------------------------------------------------------------------
# normalize_company_name
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_strips_inc(self):
        assert cc.normalize_company_name("Apple Inc.") == "apple"

    def test_strips_llc(self):
        assert cc.normalize_company_name("Acme LLC") == "acme"

    def test_strips_ltd(self):
        assert cc.normalize_company_name("Globex Ltd") == "globex"

    def test_lowercases(self):
        assert cc.normalize_company_name("SALESFORCE") == "salesforce"

    def test_removes_spaces(self):
        assert cc.normalize_company_name("  Stripe  ") == "stripe"

    def test_empty_string(self):
        assert cc.normalize_company_name("") == ""

    def test_same_company_different_suffix(self):
        assert cc.normalize_company_name("Acme Inc.") == cc.normalize_company_name("Acme LLC")


# ---------------------------------------------------------------------------
# save_company / get_company
# ---------------------------------------------------------------------------

class TestSaveAndGet:
    def test_round_trip(self):
        profile = {
            "domain": "acme.com",
            "sells_what": "Widgets for enterprises.",
            "geography": "North America",
            "market_segment": "Enterprise",
            "source": "direct_guess",
        }
        cc.save_company("Acme Corp", profile)
        result = cc.get_company("Acme Corp")
        assert result is not None
        assert result["domain"] == "acme.com"
        assert result["sells_what"] == "Widgets for enterprises."
        assert result["market_segment"] == "Enterprise"

    def test_name_normalization_hits_same_row(self):
        cc.save_company("Apple Inc.", {"domain": "apple.com", "source": "direct_guess"})
        result = cc.get_company("apple")  # different form — same key
        assert result is not None
        assert result["domain"] == "apple.com"

    def test_returns_none_for_unknown(self):
        result = cc.get_company("definitely-not-a-real-company-xyz-12345")
        assert result is None

    def test_upsert_overwrites(self):
        cc.save_company("Stripe", {"domain": "stripe.com", "source": "direct_guess"})
        cc.save_company("Stripe", {"domain": "stripe.com", "market_segment": "Enterprise", "source": "google_cse"})
        result = cc.get_company("Stripe")
        assert result["market_segment"] == "Enterprise"
        assert result["source"] == "google_cse"

    def test_null_fields_stored(self):
        cc.save_company("Unknown Co", {"source": "not_found"})
        result = cc.get_company("Unknown Co")
        assert result is not None
        assert result["domain"] is None
        assert result["sells_what"] is None


# ---------------------------------------------------------------------------
# is_stale
# ---------------------------------------------------------------------------

class TestIsStale:
    def _save_with_timestamp(self, name: str, source: str, days_ago: int) -> None:
        """Helper: save a company with a specific last_updated timestamp."""
        ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
        with cc._get_conn() as conn:
            cc._ensure_schema(conn)
            conn.execute(
                """
                INSERT INTO company_profiles
                    (company_name, domain, sells_what, geography, market_segment, source, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(company_name) DO UPDATE SET
                    source = excluded.source,
                    last_updated = excluded.last_updated
                """,
                (cc.normalize_company_name(name), None, None, None, None, source, ts),
            )
            conn.commit()

    def test_fresh_record_is_not_stale(self):
        self._save_with_timestamp("Stripe", "direct_guess", days_ago=0)
        assert cc.is_stale("Stripe", max_age_days=30) is False

    def test_old_record_is_stale(self):
        self._save_with_timestamp("Stripe", "direct_guess", days_ago=31)
        assert cc.is_stale("Stripe", max_age_days=30) is True

    def test_not_found_has_90_day_cooldown(self):
        self._save_with_timestamp("Ghost Corp", "not_found", days_ago=60)
        assert cc.is_stale("Ghost Corp") is False  # 60 < 90 days

    def test_not_found_expires_after_90_days(self):
        self._save_with_timestamp("Ghost Corp", "not_found", days_ago=91)
        assert cc.is_stale("Ghost Corp") is True

    def test_missing_record_is_stale(self):
        assert cc.is_stale("completely-nonexistent-co") is True


# ---------------------------------------------------------------------------
# Job openings cache
# ---------------------------------------------------------------------------

class TestJobOpenings:
    def test_save_and_get_openings(self):
        openings = [
            {"role_title": "SDR", "required_exp": "1-2 years", "link": "https://example.com/sdr"},
            {"role_title": "AE", "required_exp": "", "link": "https://example.com/ae"},
        ]
        cc.save_job_openings("Acme", openings)
        result = cc.get_job_openings("Acme")
        assert len(result) == 2
        assert result[0]["role_title"] == "SDR"
        assert result[1]["role_title"] == "AE"

    def test_full_replace_on_save(self):
        cc.save_job_openings("Acme", [{"role_title": "SDR", "required_exp": "", "link": ""}])
        cc.save_job_openings("Acme", [{"role_title": "AE", "required_exp": "", "link": ""}])
        result = cc.get_job_openings("Acme")
        # Old SDR row should be gone
        assert len(result) == 1
        assert result[0]["role_title"] == "AE"

    def test_empty_openings_returns_empty_list(self):
        result = cc.get_job_openings("nonexistent-company-xyz")
        assert result == []

    def test_fresh_openings_not_stale(self):
        cc.save_job_openings("Stripe", [{"role_title": "SDR", "required_exp": "", "link": ""}])
        assert cc.are_openings_stale("Stripe", max_age_hours=24) is False

    def test_missing_openings_are_stale(self):
        assert cc.are_openings_stale("no-such-company") is True
