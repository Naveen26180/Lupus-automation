"""Tests for the Classification Audit explainability layer.

Covers:
  - classify_candidate_audited() returns the same result as classify_candidate()
  - the audit trail records matches (verbatim evidence, source, matched phrase,
    match type) and title-blocked suppressions
  - build_audit_rows() emits exactly 3 rows × 13 columns
  - validator-dropped tags and enrichment status are reported
  - end-to-end consistency on the saved Snehasish Das pass1 response

All tests are fully deterministic — no network, no Sheets, no AI calls.
"""

import json
from pathlib import Path

import pytest

from core.audit_builder import build_audit_rows, _FIELD_LABELS
from core.classifier import classify_candidate, classify_candidate_audited
from core.validator import validate_extracted_fields

_RAW_AI = Path(__file__).resolve().parent.parent / "raw_ai_response.json"

_EXPECTED_COLUMNS = 13


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_pass1():
    """A minimal, deterministic pass1 payload (Snehasish Das style)."""
    return {
        "candidate_metadata": {
            "full_name": "Snehasish Das",
            "email": "snehasishdas786@gmail.com",
            "phone_number": "+91 7319481736",
            "linkedin_url": None,
            "college": "MAKAUT - Durgapur",
        },
        "document_evidence": [
            {
                "text": "Connecting with learners from across different geographies like the US, Canada, Middle East, Africa, Asia and Southeast Asia",
                "source_section": "WORK EXPERIENCE",
            },
            {
                "text": "Proven ability to prospect into mid-market and enterprise accounts",
                "source_section": "PROFESSIONAL SUMMARY",
            },
        ],
        "role_analysis": [
            {
                "role_title": "Enrolment Associate",
                "employer": "Coursera",
                "date_raw": "04/2023 - Current",
                "evidence_quotes": [
                    "Enrolment Associate",
                    "Dialing around 120-170 cold calls on daily basis",
                    "Responding to queries from prospective businesses via email, chat, or phone",
                ],
            }
        ],
    }


def _full_flow(pass1, enrichment_info=None):
    """classifier → validator → builder; returns (result, audit, rows)."""
    final_answer, audit = classify_candidate_audited(pass1)
    validated = validate_extracted_fields(final_answer)
    pre_enrichment = {
        "geography": validated.get("geography"),
        "saas_experience": validated.get("saas_experience"),
        "market_segment": validated.get("market_segment"),
    }
    rows = build_audit_rows(audit, pre_enrichment=pre_enrichment,
                            validated_data=validated,
                            enrichment_info=enrichment_info)
    return final_answer, audit, rows


def _row_for(rows, field_key):
    label = _FIELD_LABELS[field_key]
    for row in rows:
        if row[2] == label:
            return row
    raise AssertionError(f"No audit row for {label}")


# ---------------------------------------------------------------------------
# Classifier audit trail
# ---------------------------------------------------------------------------

class TestClassifierAuditTrail:
    def test_audited_matches_plain_classification(self):
        pass1 = _make_pass1()
        plain = classify_candidate(pass1)
        audited, audit = classify_candidate_audited(pass1)
        assert audited == plain

    def test_audit_records_saas_match_with_verbatim_evidence(self):
        _, audit = classify_candidate_audited(_make_pass1())
        saas = audit["fields"]["saas_experience"]
        match = next(
            m for m in saas["matches"]
            if m["tag"] == "Outbound/Prospecting" and "cold calls" in m["evidence"]
        )
        assert match["evidence"] == "Dialing around 120-170 cold calls on daily basis"
        assert match["phrase"] == "cold calls"
        assert match["source"] == "role:Coursera"
        assert match["match_type"] == "Explicit"
        assert match["title_blocked"] is False

    def test_audit_records_segment_match_from_document_evidence(self):
        _, audit = classify_candidate_audited(_make_pass1())
        seg = audit["fields"]["market_segment"]
        enterprise = next(m for m in seg["matches"] if m["tag"] == "Enterprise")
        assert enterprise["source"] == "document:PROFESSIONAL SUMMARY"
        assert "mid-market and enterprise accounts" in enterprise["evidence"]

    def test_audit_records_contextual_geography_match_type(self):
        pass1 = _make_pass1()
        pass1["role_analysis"][0]["evidence_quotes"].append("Sold into India for enterprise accounts")
        _, audit = classify_candidate_audited(pass1)
        geo = audit["fields"]["geography"]
        india = next(m for m in geo["matches"] if m["tag"] == "India")
        assert india["match_type"] == "Contextual"
        assert india["evidence"] == "Sold into India for enterprise accounts"

    def test_audit_records_rejected_rules(self):
        _, audit = classify_candidate_audited(_make_pass1())
        geo = audit["fields"]["geography"]
        # 'geographies like ... Asia' now fires APAC; EMEA/LATAM still have no match
        matched_tags = {m["tag"] for m in geo["matches"]}
        assert "APAC" in matched_tags
        rejected_tags = {r["tag"] for r in geo["rejected"]}
        assert "EMEA" in rejected_tags
        assert "LATAM" in rejected_tags
        # Triggers are human-readable strings
        emea = next(r for r in geo["rejected"] if r["tag"] == "EMEA")
        assert any("emea" in t for t in emea["triggers"])

    def test_audit_records_title_blocked_suppression(self):
        pass1 = {
            "candidate_metadata": {"full_name": "T", "email": None, "phone_number": None,
                                   "linkedin_url": None, "college": None},
            "document_evidence": [],
            "role_analysis": [
                {
                    "role_title": "Account Manager",
                    "employer": "Acme",
                    "date_raw": None,
                    "evidence_quotes": ["Account Manager"],
                }
            ],
        }
        final_answer, audit = classify_candidate_audited(pass1)
        saas = audit["fields"]["saas_experience"]
        blocked = next(m for m in saas["matches"] if m["tag"] == "Account Management")
        assert blocked["title_blocked"] is True
        # The blocked tag must NOT be in the final classification
        assert "Account Management" not in (final_answer.get("saas_experience") or [])
        # But the rule still appears in 'rejected' (never fired into final tags)
        assert "Account Management" in {r["tag"] for r in saas["rejected"]}


# ---------------------------------------------------------------------------
# Audit row builder
# ---------------------------------------------------------------------------

class TestAuditRowBuilder:
    def test_builds_three_rows_with_thirteen_columns(self):
        _, _, rows = _full_flow(_make_pass1())
        assert len(rows) == 3
        for row in rows:
            assert len(row) == _EXPECTED_COLUMNS

    def test_field_labels_and_candidate(self):
        _, _, rows = _full_flow(_make_pass1())
        assert [r[2] for r in rows] == ["Geography", "SaaS Experience", "Market Segment"]
        assert all(r[1] == "Snehasish Das" for r in rows)

    def test_saas_row_contents(self):
        _, _, rows = _full_flow(_make_pass1())
        row = _row_for(rows, "saas_experience")
        # Cold calls → Outbound; prospective businesses → B2B (rule order)
        assert row[3] == "Outbound/Prospecting; B2B"
        assert "120-170 cold calls" in row[4]          # verbatim evidence
        assert "Work Experience → Coursera" in row[5]  # source location
        assert 'Matched: "cold calls"' in row[6]       # rule reference
        assert row[7] == "Explicit"                    # match type
        assert "Therefore: Outbound/Prospecting" in row[8]
        assert row[12] == "Deterministic"              # confidence

    def test_segment_row_contents(self):
        _, _, rows = _full_flow(_make_pass1())
        row = _row_for(rows, "market_segment")
        assert row[3] == "Mid-Market; Enterprise"
        assert "Professional Summary" in row[5]
        assert "Enterprise" in row[6]

    def test_geography_row_contents(self):
        _, _, rows = _full_flow(_make_pass1())
        row = _row_for(rows, "geography")
        # 'geographies like the US, Canada, Middle East, Africa, Asia and
        # Southeast Asia' → NA; MEA; APAC; SEA (rule order)
        assert row[3] == "APAC; NA; MEA; SEA"
        assert "geographies like the US" in row[4]  # verbatim evidence
        assert row[12] == "Deterministic"

    def test_geography_row_blank_for_location_only(self):
        """Candidate-location evidence must not fire geography."""
        pass1 = _make_pass1()
        pass1["document_evidence"] = [
            {"text": "Based in India", "source_section": "PROFILE"},
            {"text": "Lives in Singapore", "source_section": "PROFILE"},
        ]
        pass1["role_analysis"] = []
        _, _, rows = _full_flow(pass1)
        row = _row_for(rows, "geography")
        assert row[3] == "(blank)"
        assert row[10]  # blank reason present
        assert row[12] == "No Match"

    def test_empty_audit_returns_no_rows(self):
        assert build_audit_rows({}, {}, {}) == []

    def test_rejected_list_in_why_others(self):
        _, _, rows = _full_flow(_make_pass1())
        row = _row_for(rows, "saas_experience")
        # Other SaaS rules (e.g. Customer Retention) must appear as rejected
        assert "Rejected: Customer Retention" in row[9]

    def test_validator_dropped_tag_reported(self):
        # Hand-built audit simulating a tag the validator would drop
        audit = {
            "candidate_name": "Test Person",
            "fields": {
                "geography": {"raw_value": None, "matches": [], "rejected": []},
                "saas_experience": {"raw_value": None, "matches": [], "rejected": []},
                "market_segment": {
                    "raw_value": ["SMB", "NotARealSegment"],
                    "matches": [
                        {"tag": "SMB", "phrase": "smb", "evidence": "SMB accounts",
                         "source": "document:SUMMARY", "match_type": "Explicit",
                         "title_blocked": False, "triggers": ["smb"]},
                    ],
                    "rejected": [],
                },
            },
        }
        validated = {"full_name": "Test Person", "market_segment": "SMB"}
        rows = build_audit_rows(audit, pre_enrichment={}, validated_data=validated)
        row = _row_for(rows, "market_segment")
        assert row[3] == "SMB"
        assert "validator dropped it" in row[9]
        assert "NotARealSegment" in row[9]
        assert row[12] == "Deterministic"


# ---------------------------------------------------------------------------
# Enrichment status
# ---------------------------------------------------------------------------

class TestEnrichmentStatus:
    def _rows_with(self, audit, validated, info, pre):
        return build_audit_rows(audit, pre_enrichment=pre, validated_data=validated,
                                enrichment_info=info)

    @staticmethod
    def _blank_audit(name="X"):
        return {
            "candidate_name": name,
            "fields": {
                "geography": {"raw_value": None, "matches": [], "rejected": []},
                "saas_experience": {"raw_value": None, "matches": [], "rejected": []},
                "market_segment": {"raw_value": None, "matches": [], "rejected": []},
            },
        }

    def test_enrichment_succeeded(self):
        audit = self._blank_audit()
        validated = {"full_name": "X", "geography": "NA"}
        info = {"ran": True, "scraped_geo": "North America"}
        rows = self._rows_with(audit, validated, info, pre={"geography": None})
        row = _row_for(rows, "geography")
        assert row[7] == "Enrichment"
        assert row[8] == "Tag: NA\nOriginal: (blank)\nEnrichment source: company research (North America)\nTherefore: NA"
        assert "Resume blank; Enrichment succeeded" in row[11]
        assert row[12] == "Enriched"

    def test_enrichment_rejected_when_scraped_fails_validation(self):
        audit = self._blank_audit()
        validated = {"full_name": "X", "geography": None}
        info = {"ran": True, "scraped_geo": "Outer Space (Acme)"}
        rows = self._rows_with(audit, validated, info, pre={"geography": None})
        row = _row_for(rows, "geography")
        assert row[3] == "(blank)"
        assert "Enrichment rejected" in row[11]
        assert "failed validation" in row[10]
        assert row[12] == "No Match"

    def test_enrichment_skipped_when_fields_populated(self):
        # Realistic audit: the resume produced Enterprise via a classifier match,
        # so enrichment had nothing to fill and was skipped.
        audit = self._blank_audit()
        audit["fields"]["market_segment"]["raw_value"] = ["Enterprise"]
        audit["fields"]["market_segment"]["matches"] = [
            {"tag": "Enterprise", "phrase": "enterprise accounts",
             "evidence": "Managed enterprise accounts", "source": "document:SUMMARY",
             "match_type": "Explicit", "title_blocked": False,
             "triggers": ["enterprise accounts", "enterprise customers"]},
        ]
        validated = {"full_name": "X", "market_segment": "Enterprise"}
        info = {"ran": False, "reason": "fields_populated"}
        rows = self._rows_with(audit, validated, info, pre={"market_segment": "Enterprise"})
        row = _row_for(rows, "market_segment")
        assert "Enrichment skipped (both fields already populated)" in row[11]
        assert row[12] == "Deterministic"

    def test_enrichment_disabled(self):
        audit = self._blank_audit()
        validated = {"full_name": "X", "market_segment": "SMB"}
        info = {"ran": False, "reason": "disabled"}
        rows = self._rows_with(audit, validated, info, pre={"market_segment": "SMB"})
        row = _row_for(rows, "market_segment")
        assert "Enrichment skipped" in row[11]

    def test_saas_experience_never_enriched(self):
        audit = self._blank_audit()
        validated = {"full_name": "X", "saas_experience": "Outbound/Prospecting"}
        rows = self._rows_with(audit, validated, {}, pre={"saas_experience": "Outbound/Prospecting"})
        row = _row_for(rows, "saas_experience")
        assert "Enrichment N/A" in row[11]

    def test_enrichment_attempted_no_usable_value(self):
        audit = self._blank_audit()
        validated = {"full_name": "X", "market_segment": None}
        info = {"ran": True}
        rows = self._rows_with(audit, validated, info, pre={"market_segment": None})
        row = _row_for(rows, "market_segment")
        assert "Enrichment attempted (no usable value found)" in row[11]


# ---------------------------------------------------------------------------
# Source label formatting
# ---------------------------------------------------------------------------

class TestSourceLabels:
    def test_document_section_title_cased(self):
        from core.audit_builder import _source_label
        assert _source_label("document:PROFESSIONAL SUMMARY") == "Professional Summary"
        assert _source_label("document:SKILLS") == "Skills"
        assert _source_label("role:Coursera") == "Work Experience → Coursera"
        assert _source_label("role:unknown_employer") == "Role Analysis"


# ---------------------------------------------------------------------------
# End-to-end consistency against the saved real pass1 response
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _RAW_AI.exists(), reason="raw_ai_response.json not present")
class TestRealResponseConsistency:
    def test_rows_consistent_with_classifier_and_validator(self):
        with open(_RAW_AI, encoding="utf-8") as f:
            pass1 = json.load(f)

        final_answer, audit, rows = _full_flow(pass1)
        validated = validate_extracted_fields(final_answer)

        assert len(rows) == 3
        for field_key, label in _FIELD_LABELS.items():
            row = _row_for(rows, field_key)
            expected_final = validated.get(field_key)
            if expected_final is None:
                assert row[3] == "(blank)"
            else:
                expected_text = "; ".join(str(expected_final).split("; "))
                assert row[3] == expected_text
                # Every final tag must be explained in 'Why Selected'
                for tag in str(expected_final).split("; "):
                    assert tag in row[8]
            # Verbatim evidence: every recorded match's evidence must come
            # from the pass1 payload (evidence stream) — nothing invented.
            for m in audit["fields"][field_key]["matches"]:
                assert m["evidence"], "evidence must never be empty"
