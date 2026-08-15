"""Phase 4 — Regression tests for the deterministic classifier expansion.

Covers every rule added in the coverage expansion:
  - positive cases (one per new or broadened rule)
  - negative cases (over-broad phrases must NOT fire)
  - boundary cases (title-only, location-only, bare words)

All tests are fully deterministic — no AI, no network, no Sheets.
"""

import pytest

from core.classifier import classify_candidate
from core.validator import (
    _validate_geography,
    _validate_market_segment,
    _validate_saas_experience,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _classify(quote, role_title="Sales Representative"):
    """Classify a single evidence quote and return the raw final_answer."""
    pass1 = {
        "candidate_metadata": {
            "full_name": "Test Candidate", "email": None, "phone_number": None,
            "linkedin_url": None, "college": None,
        },
        "document_evidence": [
            {"text": quote, "source_section": "PROFESSIONAL SUMMARY"},
        ],
        "role_analysis": [],
    }
    return classify_candidate(pass1)


def _classify_role_quote(quote, role_title):
    """Classify a quote placed as a role evidence quote (needed for title-only logic)."""
    pass1 = {
        "candidate_metadata": {
            "full_name": "Test Candidate", "email": None, "phone_number": None,
            "linkedin_url": None, "college": None,
        },
        "document_evidence": [],
        "role_analysis": [
            {
                "role_title": role_title,
                "employer": "TestCorp",
                "date_raw": None,
                "evidence_quotes": [quote],
            }
        ],
    }
    return classify_candidate(pass1)


def _saas(quote):
    return _validate_saas_experience(_classify(quote).get("saas_experience")) or ""


def _segment(quote):
    return _validate_market_segment(_classify(quote).get("market_segment")) or ""


def _geo(quote):
    return _validate_geography(_classify(quote).get("geography")) or ""


def _assert_has(value, *tags):
    parts = value.split("; ") if value else []
    for tag in tags:
        assert tag in parts, f"Expected {tag!r} in {value!r}"


def _assert_missing(value, *tags):
    parts = value.split("; ") if value else []
    for tag in tags:
        assert tag not in parts, f"{tag!r} must NOT fire for: {value!r}"


# ---------------------------------------------------------------------------
# Positive cases — every new SaaS rule
# ---------------------------------------------------------------------------

class TestSaaSNewRules:
    def test_inbound_sales(self):
        _assert_has(_saas("Handled inbound leads and inbound calls from prospects"), "Inbound Sales")

    def test_field_sales(self):
        _assert_has(_saas("Field sales role covering on-site accounts"), "Field Sales")

    def test_channel_sales(self):
        _assert_has(_saas("Managed channel partners and resellers across EMEA"), "Channel Sales")

    def test_partner_sales(self):
        _assert_has(_saas("Built partner sales motion with alliances"), "Partner Sales")

    def test_pre_sales(self):
        _assert_has(_saas("Led RFP responses and proof of concept demos"), "Pre-Sales")

    def test_sales_engineering(self):
        _assert_has(_saas("Worked as a sales engineer doing technical sales"), "Sales Engineering")

    def test_funnel_management(self):
        _assert_has(_saas("Owned pipeline management and forecasting"), "Funnel Management")

    def test_team_lead(self):
        _assert_has(_saas("Team lead managing a team of 5 SDRs"), "Team Lead")

    def test_pnl_ownership(self):
        _assert_has(_saas("P&L ownership for the region"), "P&L Ownership")

    def test_bant(self):
        _assert_has(_saas("Qualified opportunities with BANT"), "BANT")

    def test_spin(self):
        _assert_has(_saas("Trained in SPIN selling methodology"), "SPIN")

    def test_meddic(self):
        _assert_has(_saas("MEDDIC qualification on every deal"), "MEDDIC")

    def test_meddpicc(self):
        _assert_has(_saas("Used the MEDDPICC framework"), "MEDDPICC")

    def test_challenger(self):
        _assert_has(_saas("Challenger Sale methodology"), "Challenger Sale")

    def test_solution_selling(self):
        _assert_has(_saas("Solution Selling approach"), "Solution Selling")

    def test_value_selling(self):
        _assert_has(_saas("Value Selling training"), "Value Selling")

    def test_sandler(self):
        _assert_has(_saas("Sandler training"), "Sandler")

    def test_b2c_motion(self):
        _assert_has(_saas("Sold to consumer customers"), "B2C")

    def test_b2b2c_motion(self):
        _assert_has(_saas("B2B2C marketplace sales"), "B2B2C")

    def test_transactional(self):
        _assert_has(_saas("High-volume transactional sales"), "Transactional")

    def test_enterprise_sales_cycle(self):
        _assert_has(_saas("Complex sales with long sales cycles"), "Enterprise Sales Cycle")

    def test_plg(self):
        _assert_has(_saas("Product-led growth motion"), "PLG")


# ---------------------------------------------------------------------------
# Positive cases — broadened existing SaaS rules
# ---------------------------------------------------------------------------

class TestSaaSBroadened:
    def test_customer_retention_improved_retention(self):
        _assert_has(
            _saas("Resulting in improved retention and enrolment rates"),
            "Customer Retention",
        )

    def test_customer_retention_reduced_refunds(self):
        _assert_has(
            _saas("Reduced refund rates by 30% while increasing customer engagement"),
            "Customer Retention",
        )

    def test_b2b_prospective_businesses(self):
        _assert_has(_saas("Responding to queries from prospective businesses"), "B2B")

    def test_b2b_corporate_clients(self):
        _assert_has(_saas("Prospected corporate clients"), "B2B")

    def test_outbound_dialed_calls(self):
        _assert_has(_saas("Dialed about 200 calls with 50 connected"), "Outbound/Prospecting")

    def test_outbound_lead_generation(self):
        _assert_has(_saas("Lead generation and cold outreach"), "Outbound/Prospecting")

    def test_outbound_account_based(self):
        _assert_has(_saas("Account-based prospecting into named accounts"), "Outbound/Prospecting")

    def test_saas_software_subscriptions(self):
        _assert_has(_saas("Sold software subscriptions"), "SaaS Sales")

    def test_account_management_key_accounts(self):
        _assert_has(_saas("Managed key accounts"), "Account Management")

    def test_full_cycle_still_works(self):
        _assert_has(_saas("Full-cycle sales from prospecting to close"), "Full-Cycle Sales")


# ---------------------------------------------------------------------------
# Positive cases — geography territory phrasing and new regions
# ---------------------------------------------------------------------------

class TestGeographyPositives:
    def test_worked_across_apac(self):
        _assert_has(_geo("Worked across APAC"), "APAC")

    def test_sold_into_india(self):
        _assert_has(_geo("Sold into India"), "India")

    def test_customers_in_europe(self):
        _assert_has(_geo("Customers in Europe"), "EU")

    def test_managed_us_accounts(self):
        _assert_has(_geo("Managed US accounts"), "NA")

    def test_supported_middle_east(self):
        _assert_has(_geo("Supported Middle East region"), "MEA")

    def test_served_gcc_clients(self):
        _assert_has(_geo("Served GCC clients"), "GCC")

    def test_worked_across_asean(self):
        _assert_has(_geo("Worked across ASEAN"), "ASEAN")

    def test_north_america_customers(self):
        _assert_has(_geo("North America customers"), "NA")

    def test_global_customers(self):
        _assert_has(_geo("Global customers"), "Global")

    def test_multiple_regions(self):
        _assert_has(_geo("Covered multiple regions"), "Global")

    def test_domestic_and_international(self):
        value = _geo("Handled both domestic and international markets")
        _assert_has(value, "India", "Global")

    def test_international_customers(self):
        _assert_has(_geo("International customers"), "Global")

    def test_customers_across_southeast_asia(self):
        _assert_has(_geo("Customers across Southeast Asia"), "SEA")

    def test_geographies_like_list(self):
        value = _geo(
            "Connecting with learners from across different geographies like the US, "
            "Canada, Middle East, Africa, Asia and Southeast Asia"
        )
        _assert_has(value, "NA", "MEA", "APAC", "SEA")

    def test_benelux_territory(self):
        _assert_has(_geo("Benelux territory"), "Benelux")

    def test_nordics_market(self):
        _assert_has(_geo("Nordics market"), "Nordics")

    def test_cee_region(self):
        _assert_has(_geo("Covered CEE region"), "CEE")

    def test_iberia_sales(self):
        _assert_has(_geo("Iberia sales territory"), "Iberia")

    def test_cis_territory(self):
        _assert_has(_geo("CIS territory"), "CIS")

    def test_apj_region(self):
        _assert_has(_geo("Responsible for APJ region"), "APJ")

    def test_rest_of_world(self):
        _assert_has(_geo("Rest of world territory"), "ROW")

    def test_anz(self):
        _assert_has(_geo("Managed accounts across Australia"), "ANZ")

    def test_european_customers(self):
        _assert_has(_geo("European customers"), "EU")


# ---------------------------------------------------------------------------
# Positive cases — segment
# ---------------------------------------------------------------------------

class TestSegmentPositives:
    def test_b2b2c_segment(self):
        _assert_has(_segment("B2B2C customers"), "B2B2C")

    def test_b2c_students(self):
        _assert_has(_segment("Actively engaging with students to keep them motivated"), "B2C")

    def test_b2c_students_and_parents(self):
        _assert_has(_segment("Maintained communication with students and their parents"), "B2C")

    def test_b2c_potential_students(self):
        _assert_has(_segment("Actively reached out to potential students who expressed interest"), "B2C")

    def test_fortune_500_normalizes_to_enterprise(self):
        _assert_has(_segment("Fortune 500 clients"), "Enterprise")

    def test_sme_customers(self):
        _assert_has(_segment("SME customers"), "SME")

    def test_smb_prospecting(self):
        _assert_has(_segment("Prospecting SMB customers"), "SMB")


# ---------------------------------------------------------------------------
# Negative cases — over-broad phrases must NOT fire
# ---------------------------------------------------------------------------

class TestNegativeCases:
    def test_enterprise_architecture(self):
        assert _segment("Enterprise architecture design") == ""

    def test_enterprise_apis(self):
        assert _segment("Built enterprise-grade APIs") == ""

    def test_enterprise_software(self):
        assert _segment("Enterprise software development") == ""

    def test_team_expansion_not_upsell(self):
        assert _saas("Team expansion") == ""

    def test_employee_retention_not_customer_retention(self):
        assert _saas("Employee retention strategy") == ""

    def test_talent_retention_not_customer_retention(self):
        assert _saas("Talent retention program") == ""

    def test_product_discovery_not_consultative(self):
        assert _saas("Product discovery workshops") == ""

    def test_subject_matter_expert_not_sme(self):
        assert _segment("Subject Matter Expert") == ""

    def test_located_in_chennai(self):
        assert _geo("Located in Chennai") == ""

    def test_remote_from_india(self):
        assert _geo("Remote from India") == ""

    def test_based_in_india(self):
        assert _geo("Based in India") == ""

    def test_lives_in_singapore(self):
        assert _geo("Lives in Singapore") == ""

    def test_worked_in_india_not_territory(self):
        assert _geo("Worked in India") == ""

    def test_spin_up_not_spin_methodology(self):
        assert _saas("Managed spin-up environments") == ""

    def test_bare_pipeline_not_funnel(self):
        assert _saas("Built data pipelines for analytics") == ""

    def test_leadership_not_team_lead(self):
        assert _saas("Demonstrated leadership skills") == ""

    def test_customer_service_not_retention(self):
        assert _saas("Managed client relationships") == ""


# ---------------------------------------------------------------------------
# Boundary cases
# ---------------------------------------------------------------------------

class TestBoundaryCases:
    def test_title_only_account_manager_blocked(self):
        # Title-only evidence must not fire Account Management (existing contract)
        result = _classify_role_quote("Account Manager", role_title="Account Manager")
        value = _validate_saas_experience(result.get("saas_experience")) or ""
        _assert_missing(value, "Account Management")

    def test_inbound_title_fires(self):
        # 'Inside Sales Representative' title legitimately fires Inside Sales
        _assert_has(_saas("Inside Sales Representative"), "Inside Sales")

    def test_high_volume_cold_calling_not_transactional(self):
        # 'high-volume' must not fire Transactional without a sales-qualifier
        value = _saas("High-volume cold calling on a daily basis")
        _assert_missing(value, "Transactional")
        _assert_has(value, "Outbound/Prospecting")

    def test_solution_selling_no_longer_double_fires_consultative(self):
        # 'Solution Selling' is now its own tag, not Consultative Selling
        value = _saas("Solution Selling approach")
        _assert_has(value, "Solution Selling")
        _assert_missing(value, "Consultative Selling")
