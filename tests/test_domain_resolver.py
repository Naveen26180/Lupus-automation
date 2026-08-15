"""Tests for integrations/enrichment/domain_resolver.py.

All network calls are mocked — no real HTTP traffic happens here.
Tests cover:
  - _clearbit_name_matches (the name-sanity guard)
  - _tier2_clearbit_autocomplete (all edge cases)
  - resolve_domain (chain ordering and source labels)
"""

from unittest.mock import MagicMock, patch

import pytest

import integrations.enrichment.domain_resolver as dr

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(status_code: int, json_data):
    """Build a minimal mock requests.Response object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    return resp


# ---------------------------------------------------------------------------
# _clearbit_name_matches
# ---------------------------------------------------------------------------

class TestClearbitNameMatches:
    """The name-sanity guard that prevents accepting unrelated top results."""

    def test_exact_match(self):
        assert dr._clearbit_name_matches("Slack", "Slack") is True

    def test_query_is_substring_of_result(self):
        # "Slack" contained in "Slack Technologies Inc."
        assert dr._clearbit_name_matches("Slack", "Slack Technologies Inc.") is True

    def test_result_is_substring_of_query(self):
        # Searching "Salesforce.com" — result is "Salesforce"
        assert dr._clearbit_name_matches("Salesforce.com", "Salesforce") is True

    def test_case_insensitive(self):
        assert dr._clearbit_name_matches("stripe", "Stripe Inc") is True

    def test_partial_match_passes_per_spec(self):
        # Per spec: the rule is simple substring — "Apex" IS in "Apex Legends Community Hub".
        # This is a known potential false-accept; flag to lead if it causes real problems.
        # The guard's real purpose is to catch COMPLETELY unrelated names like "SolarWinds".
        assert dr._clearbit_name_matches("Apex", "Apex Legends Community Hub") is True

    def test_completely_different_names_rejected(self):
        assert dr._clearbit_name_matches("Wave", "SolarWinds") is False

    def test_empty_query(self):
        # Edge case — empty query substring matches everything; guard allows it
        # (empty company_name is blocked upstream by resolve_domain)
        assert dr._clearbit_name_matches("", "Anything") is True

    def test_empty_result_name(self):
        # "" is a substring of any string in Python — returns True
        assert dr._clearbit_name_matches("Notion", "") is True


# ---------------------------------------------------------------------------
# _tier2_clearbit_autocomplete
# ---------------------------------------------------------------------------

class TestTier2ClearbitAutocomplete:

    @patch("integrations.enrichment.domain_resolver.requests.get")
    def test_happy_path_returns_domain(self, mock_get):
        mock_get.return_value = _mock_response(200, [
            {"name": "Slack", "domain": "slack.com", "logo": None},
        ])
        result = dr._tier2_clearbit_autocomplete("Slack")
        assert result == "slack.com"

    @patch("integrations.enrichment.domain_resolver.requests.get")
    def test_happy_path_stripe(self, mock_get):
        mock_get.return_value = _mock_response(200, [
            {"name": "Stripe", "domain": "stripe.com", "logo": None},
        ])
        assert dr._tier2_clearbit_autocomplete("Stripe") == "stripe.com"

    @patch("integrations.enrichment.domain_resolver.requests.get")
    def test_happy_path_notion(self, mock_get):
        mock_get.return_value = _mock_response(200, [
            {"name": "Notion", "domain": "notion.so", "logo": None},
        ])
        assert dr._tier2_clearbit_autocomplete("Notion") == "notion.so"

    @patch("integrations.enrichment.domain_resolver.requests.get")
    def test_empty_array_returns_none(self, mock_get):
        mock_get.return_value = _mock_response(200, [])
        assert dr._tier2_clearbit_autocomplete("NonExistentCo") is None

    @patch("integrations.enrichment.domain_resolver.requests.get")
    def test_non_200_returns_none(self, mock_get):
        mock_get.return_value = _mock_response(429, [])
        assert dr._tier2_clearbit_autocomplete("Slack") is None

    @patch("integrations.enrichment.domain_resolver.requests.get")
    def test_name_mismatch_returns_none(self, mock_get):
        # Truly unrelated result: searching "Wave" but getting "SolarWinds"
        mock_get.return_value = _mock_response(200, [
            {"name": "SolarWinds", "domain": "solarwinds.com", "logo": None},
        ])
        assert dr._tier2_clearbit_autocomplete("Wave") is None

    @patch("integrations.enrichment.domain_resolver.requests.get")
    def test_missing_domain_field_returns_none(self, mock_get):
        mock_get.return_value = _mock_response(200, [
            {"name": "Slack", "domain": "", "logo": None},
        ])
        assert dr._tier2_clearbit_autocomplete("Slack") is None

    @patch("integrations.enrichment.domain_resolver.requests.get")
    def test_timeout_returns_none(self, mock_get):
        import requests as real_requests
        mock_get.side_effect = real_requests.Timeout("timed out")
        assert dr._tier2_clearbit_autocomplete("Stripe") is None

    @patch("integrations.enrichment.domain_resolver.requests.get")
    def test_connection_error_returns_none(self, mock_get):
        import requests as real_requests
        mock_get.side_effect = real_requests.ConnectionError("no route to host")
        assert dr._tier2_clearbit_autocomplete("Notion") is None

    @patch("integrations.enrichment.domain_resolver.requests.get")
    def test_json_decode_error_returns_none(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("invalid JSON")
        mock_get.return_value = resp
        assert dr._tier2_clearbit_autocomplete("Wave") is None


# ---------------------------------------------------------------------------
# resolve_domain — chain ordering and source labels
# ---------------------------------------------------------------------------

class TestResolveDomain:

    @patch("integrations.enrichment.domain_resolver._tier1_direct_guess")
    @patch("integrations.enrichment.domain_resolver._tier2_clearbit_autocomplete")
    @patch("integrations.enrichment.domain_resolver._tier3_duckduckgo")
    def test_tier1_success_skips_clearbit_and_ddg(self, mock_ddg, mock_clearbit, mock_t1):
        mock_t1.return_value = "slack.com"
        domain, source = dr.resolve_domain("Slack")
        assert domain == "slack.com"
        assert source == "direct_guess"
        mock_clearbit.assert_not_called()
        mock_ddg.assert_not_called()

    @patch("integrations.enrichment.domain_resolver._tier1_direct_guess")
    @patch("integrations.enrichment.domain_resolver._tier2_clearbit_autocomplete")
    @patch("integrations.enrichment.domain_resolver._tier3_duckduckgo")
    def test_tier2_used_when_tier1_fails(self, mock_ddg, mock_clearbit, mock_t1):
        mock_t1.return_value = None
        mock_clearbit.return_value = "notion.so"
        domain, source = dr.resolve_domain("Notion")
        assert domain == "notion.so"
        assert source == "clearbit"
        mock_ddg.assert_not_called()

    @patch("integrations.enrichment.domain_resolver._tier1_direct_guess")
    @patch("integrations.enrichment.domain_resolver._tier2_clearbit_autocomplete")
    @patch("integrations.enrichment.domain_resolver._tier3_duckduckgo")
    def test_tier3_used_when_tiers_1_and_2_fail(self, mock_ddg, mock_clearbit, mock_t1):
        mock_t1.return_value = None
        mock_clearbit.return_value = None
        mock_ddg.return_value = "obscurecompany.com"
        domain, source = dr.resolve_domain("Obscure Company")
        assert domain == "obscurecompany.com"
        assert source == "duckduckgo"

    @patch("integrations.enrichment.domain_resolver._tier1_direct_guess")
    @patch("integrations.enrichment.domain_resolver._tier2_clearbit_autocomplete")
    @patch("integrations.enrichment.domain_resolver._tier3_duckduckgo")
    def test_all_tiers_fail_returns_not_found(self, mock_ddg, mock_clearbit, mock_t1):
        mock_t1.return_value = None
        mock_clearbit.return_value = None
        mock_ddg.return_value = None
        domain, source = dr.resolve_domain("Totally Made Up Company XYZ 99999")
        assert domain is None
        assert source == "not_found"

    def test_empty_company_name_returns_not_found(self):
        domain, source = dr.resolve_domain("")
        assert domain is None
        assert source == "not_found"

    def test_whitespace_only_name_returns_not_found(self):
        domain, source = dr.resolve_domain("   ")
        assert domain is None
        assert source == "not_found"
