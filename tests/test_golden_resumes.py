"""Phase 6 — Golden resume tests.

Run the deterministic pipeline (classifier → validator → audit builder) against
representative resumes embedded as pass1-shaped evidence, and assert the final
canonical values for Geography / SaaS Experience / Market Segment.

Golden 1 mirrors the real Snehasish Das pass1 evidence (raw_ai_response.json),
plus the "multiple regions, both domestic and international" item that the
current pass1 extraction misses — the Phase 3 extraction fix targets that gap.

Golden 2 is a synthetic enterprise SaaS AE resume exercising the new rules.
"""

from core.audit_builder import build_audit_rows
from core.classifier import classify_candidate_audited
from core.validator import validate_extracted_fields

# ---------------------------------------------------------------------------
# Golden 1 — Snehasish Das (ed-sales, consumer audience, multi-region)
# ---------------------------------------------------------------------------

GOLDEN_SNEHASISH = {
    "candidate_metadata": {
        "full_name": "Snehasish Das",
        "email": "snehasishdas786@gmail.com",
        "phone_number": "+91 7319481736",
        "linkedin_url": None,
        "college": "MAKAUT - Durgapur, BBA, Business Administration And Management",
    },
    "document_evidence": [
        {"text": "Dynamic professional with four years of experience in educational support and customer relations, driving enhanced student engagement and satisfaction.", "source_section": "PROFILE"},
        {"text": "Proven ability to develop tailored learning plans and implement innovative approaches that foster academic success.", "source_section": "PROFILE"},
        {"text": "Expertise in managing customer relationships and delivering outstanding service, resulting in improved retention and enrolment rates.", "source_section": "PROFILE"},
        {"text": "Providing detailed information about course offerings, degree programs, specializations, and learning paths to help potential students make informed decisions.", "source_section": "WORK EXPERIENCE"},
        {"text": "Responding to queries from prospective businesses via email, chat, or phone, ensuring a high level of customer service to enhance the enrollment experience.", "source_section": "WORK EXPERIENCE"},
        {"text": "Dialing around 120-170 cold calls on daily basis maintaining a 10% contact rate and a qualifying a minimum of 3 leads on a daily basis.", "source_section": "WORK EXPERIENCE"},
        {"text": "Connecting with learners from across different geographies like the US, Canada, Middle East, Africa, Asia and Southeast Asia.", "source_section": "WORK EXPERIENCE"},
        {"text": "Actively engaging with students to keep them motivated and invested in their learning journey.", "source_section": "WORK EXPERIENCE"},
        {"text": "Provided academic guidance and support to students, addressing any questions or concerns related to the curriculum and learning materials.", "source_section": "WORK EXPERIENCE"},
        {"text": "Maintained effective communication with students and their parents.", "source_section": "WORK EXPERIENCE"},
        {"text": "Through strategic initiatives, successfully reduced refund rates by 30% while simultaneously increasing customer engagement by 75%.", "source_section": "WORK EXPERIENCE"},
        {"text": "Dialed about 200 calls with a minimum of 50 connected calls and minimus 4 hours of talk time.", "source_section": "WORK EXPERIENCE"},
        {"text": "Actively reached out to potential students who have expressed interest in upGrad's programs.", "source_section": "WORK EXPERIENCE"},
        {"text": "Provided detailed information about upGrad's offerings, including course content, duration, fees, faculty, and the career outcomes associated with the programs.", "source_section": "WORK EXPERIENCE"},
        {"text": "Met the monthly sales target by 100%+, which maintaining a good quality score.", "source_section": "WORK EXPERIENCE"},
        {"text": "Dialed around 200-250 cold calls on a daily basis, with a minimum of 3 hours of talk and 5 video conferencing calls with the learners.", "source_section": "WORK EXPERIENCE"},
        # ── Phase 3 fix target: current pass1 misses this territory item ──
        {"text": "Troubleshooting technical issues or addressing concerns for multiple regions, both domestic and international.", "source_section": "WORK EXPERIENCE"},
    ],
    "role_analysis": [
        {
            "role_title": "Enrolment Associate",
            "employer": "Coursera",
            "date_raw": "04/2023 - Current",
            "evidence_quotes": [
                "Enrolment Associate",
                "Dialing around 120-170 cold calls on daily basis maintaining a 10% contact rate",
                "Responding to queries from prospective businesses via email, chat, or phone",
                "Connecting with learners from across different geographies like the US, Canada, Middle East, Africa, Asia and Southeast Asia",
            ],
        },
        {
            "role_title": "Student Success Specialist",
            "employer": "Byjus Education Pvt.Ltd",
            "date_raw": "03/2022 - 03/2023",
            "evidence_quotes": [
                "Student Success Specialist",
                "Actively engaging with students to keep them motivated and invested in their learning journey",
                "Maintained effective communication with students and their parents",
            ],
        },
        {
            "role_title": "Admission Counselor",
            "employer": "Upgrad Education Pvt. Ltd",
            "date_raw": "09/2021 - 03/2022",
            "evidence_quotes": [
                "Admission Counselor",
                "Dialed about 200 calls with a minimum of 50 connected calls",
                "Actively reached out to potential students who have expressed interest in upGrad's programs",
                "Met the monthly sales target by 100%+",
            ],
        },
    ],
}

# ---------------------------------------------------------------------------
# Golden 2 — Enterprise SaaS AE (B2B, Fortune 500, North America)
# ---------------------------------------------------------------------------

GOLDEN_ENTERPRISE_AE = {
    "candidate_metadata": {
        "full_name": "Alex Rivera",
        "email": "alex.rivera@example.com",
        "phone_number": None,
        "linkedin_url": "https://linkedin.com/in/alexrivera",
        "college": None,
    },
    "document_evidence": [
        {"text": "Sold enterprise SaaS platform to Fortune 500 clients across North America.", "source_section": "PROFESSIONAL SUMMARY"},
        {"text": "Full-cycle sales with MEDDIC qualification and pipeline management.", "source_section": "PROFESSIONAL SUMMARY"},
        {"text": "Managed key accounts and drove contract renewals.", "source_section": "WORK EXPERIENCE"},
    ],
    "role_analysis": [
        {
            "role_title": "Enterprise Account Executive",
            "employer": "Acme Software",
            "date_raw": "Jan 2022 - Present",
            "evidence_quotes": [
                "Enterprise Account Executive",
                "Closed complex enterprise deals with long sales cycles",
            ],
        }
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(pass1):
    """Classifier → validator → audit builder. Returns (validated, audit, rows)."""
    final_answer, audit = classify_candidate_audited(pass1)
    validated = validate_extracted_fields(final_answer)
    pre_enrichment = {
        "geography": validated.get("geography"),
        "saas_experience": validated.get("saas_experience"),
        "market_segment": validated.get("market_segment"),
    }
    rows = build_audit_rows(audit, pre_enrichment=pre_enrichment,
                            validated_data=validated, enrichment_info=None)
    return validated, audit, rows


def _tags(value):
    return set(str(value).split("; ")) if value else set()


# ---------------------------------------------------------------------------
# Golden 1 — Snehasish Das
# ---------------------------------------------------------------------------

class TestGoldenSnehasish:
    def test_geography_all_regions_sold_into(self):
        validated, _, _ = _run(GOLDEN_SNEHASISH)
        assert _tags(validated["geography"]) == {"APAC", "India", "NA", "MEA", "SEA", "Global"}

    def test_saas_experience(self):
        validated, _, _ = _run(GOLDEN_SNEHASISH)
        assert _tags(validated["saas_experience"]) == {
            "Outbound/Prospecting", "Customer Retention", "B2B",
        }

    def test_market_segment_b2c(self):
        validated, _, _ = _run(GOLDEN_SNEHASISH)
        assert _tags(validated["market_segment"]) == {"B2C"}

    def test_audit_rows_consistent(self):
        validated, _, rows = _run(GOLDEN_SNEHASISH)
        assert len(rows) == 3
        labels = {row[2]: row for row in rows}
        assert labels["Geography"][3] == "APAC; India; NA; MEA; SEA; Global"
        assert labels["SaaS Experience"][3] == "Outbound/Prospecting; Customer Retention; B2B"
        assert labels["Market Segment"][3] == "B2C"
        # Every populated field must have a Why Selected explanation and evidence
        for field in ("Geography", "SaaS Experience", "Market Segment"):
            row = labels[field]
            assert row[8], f"{field}: Why Selected missing"
            assert row[4], f"{field}: Evidence missing"


# ---------------------------------------------------------------------------
# Golden 2 — Enterprise SaaS AE
# ---------------------------------------------------------------------------

class TestGoldenEnterpriseAE:
    def test_geography_na(self):
        validated, _, _ = _run(GOLDEN_ENTERPRISE_AE)
        assert _tags(validated["geography"]) == {"NA"}

    def test_saas_experience(self):
        validated, _, _ = _run(GOLDEN_ENTERPRISE_AE)
        assert _tags(validated["saas_experience"]) == {
            "Full-Cycle Sales", "Account Management", "Customer Retention",
            "SaaS Sales", "Funnel Management", "MEDDIC", "Enterprise Sales Cycle",
        }

    def test_market_segment_enterprise(self):
        validated, _, _ = _run(GOLDEN_ENTERPRISE_AE)
        assert _tags(validated["market_segment"]) == {"Enterprise"}

    def test_no_blanks(self):
        validated, _, _ = _run(GOLDEN_ENTERPRISE_AE)
        assert validated["geography"]
        assert validated["saas_experience"]
        assert validated["market_segment"]
