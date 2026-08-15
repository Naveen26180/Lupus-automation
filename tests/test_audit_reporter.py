"""Tests for core/audit_reporter.py — the per-resume forensic JSON + Markdown.

Verifies both files are written, all required sections exist, filenames are
sanitized, validator drops are recorded, and write failures are non-fatal.
No external APIs are called.
"""

import json
import logging

import pytest

import core.audit_reporter as reporter
from core.audit_reporter import write_audit_report

RESUME_TEXT = "Snehasish Das\nDialing around 120-170 cold calls on daily basis."

PASS1 = {
    "candidate_metadata": {"full_name": "Snehasish Das", "email": "s@example.com"},
    "role_analysis": [
        {
            "role_title": "Enrolment Associate",
            "employer": "Coursera",
            "evidence_quotes": ["Dialing around 120-170 cold calls on daily basis."],
        }
    ],
}

AUDIT = {
    "candidate_name": "Snehasish Das",
    "fields": {
        "geography": {
            "raw_value": ["APAC"],
            "matches": [{"tag": "APAC", "phrase": "asia", "evidence": "geographies like Asia", "source": "role:Coursera", "match_type": "Contextual", "title_blocked": False, "triggers": ["asia"]}],
            "rejected": [{"tag": "MEA", "triggers": ["middle east", "africa"]}],
            "ai": {
                "proposals": [
                    {"tag": "MEA", "confidence": "high", "evidence": ["Connecting with learners across the Middle East."], "reasoning": "Middle East territory.", "decision": "rejected", "reject_reason": "quote_not_found: 'Connecting with learners across the Middle East.' does not appear verbatim in the resume", "overlaps_deterministic": False},
                    {"tag": "APAC", "confidence": "high", "evidence": ["geographies like Asia"], "reasoning": "APAC territory.", "decision": "accepted", "reject_reason": None, "overlaps_deterministic": True},
                ],
                "confidence": "Very High",
            },
        },
        "saas_experience": {
            "raw_value": ["Outbound/Prospecting"],
            "matches": [],
            "rejected": [],
            "ai": {"proposals": [], "confidence": "Deterministic"},
        },
        "market_segment": {
            "raw_value": None,
            "matches": [],
            "rejected": [],
            "ai": {"proposals": [], "confidence": "No Match"},
        },
    },
}

PRE_ENRICHMENT = {"geography": ["APAC"], "saas_experience": "Outbound/Prospecting", "market_segment": None}

VALIDATED = {
    "full_name": "Snehasish Das",
    "email": "s@example.com",
    "geography": "APAC",
    "saas_experience": "Outbound/Prospecting",
    "market_segment": None,
    "is_saas_company": "Yes",
    "audit_file": "",
}


@pytest.fixture
def audit_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(reporter, "_AUDIT_DIR", tmp_path / "audit")
    return tmp_path / "audit"


def test_writes_json_and_markdown(audit_dir):
    ref = write_audit_report(
        pass1_data=PASS1,
        classification_audit=AUDIT,
        pre_enrichment=PRE_ENRICHMENT,
        validated_data=VALIDATED,
        enrichment_info={"ran": False, "reason": "disabled"},
        resume_text=RESUME_TEXT,
        filename="snehasish_cv.pdf",
        source="telegram",
    )

    assert ref.startswith("audit/")
    assert ref.endswith(".json")
    assert (audit_dir / ref[len("audit/"):]).exists()

    md_path = audit_dir / (ref[len("audit/"):].replace(".json", ".md"))
    assert md_path.exists()


def test_json_contains_required_sections(audit_dir):
    ref = write_audit_report(PASS1, AUDIT, PRE_ENRICHMENT, VALIDATED, {"ran": False}, RESUME_TEXT, "cv.pdf")
    with open(audit_dir / ref[len("audit/"):], encoding="utf-8") as f:
        data = json.load(f)

    for section in ("timestamp", "candidate", "source", "filename", "resume_text",
                    "pass1", "deterministic_output", "ai_output",
                    "adjudicator_decisions", "validator_decisions",
                    "enrichment", "final_output"):
        assert section in data, f"missing section: {section}"

    # Deterministic + AI + adjudicator all captured
    assert data["pass1"]["candidate_metadata"]["full_name"] == "Snehasish Das"
    det = data["deterministic_output"]["geography"]
    assert det["raw_value"] == ["APAC"]
    ai = data["ai_output"]["geography"]
    assert ai["proposals"][0]["decision"] == "rejected"
    assert "quote_not_found" in ai["proposals"][0]["reject_reason"]
    assert ai["proposals"][1]["decision"] == "accepted"
    assert data["final_output"]["is_saas_company"] == "Yes"


def test_validator_drop_recorded(audit_dir):
    """A tag that passed the classifier/AI but was dropped by the validator is recorded."""
    aud = json.loads(json.dumps(AUDIT))
    aud["fields"]["geography"]["ai"]["final_value"] = ["APAC", "MEA"]  # AI merged
    PRE = {"geography": "APAC", "saas_experience": "Outbound/Prospecting", "market_segment": None}

    ref = write_audit_report(PASS1, aud, PRE, VALIDATED, {"ran": False}, RESUME_TEXT, "cv.pdf")
    with open(audit_dir / ref[len("audit/"):], encoding="utf-8") as f:
        data = json.load(f)

    assert data["validator_decisions"]["geography"]["pre_validator"] == ["APAC", "MEA"]
    assert data["validator_decisions"]["geography"]["validated"] == "APAC"
    assert data["validator_decisions"]["geography"]["dropped_by_validator"] == ["MEA"]


def test_markdown_has_field_sections(audit_dir):
    ref = write_audit_report(PASS1, AUDIT, PRE_ENRICHMENT, VALIDATED, {"ran": False}, RESUME_TEXT, "cv.pdf")
    md = (audit_dir / ref[len("audit/"):].replace(".json", ".md")).read_text(encoding="utf-8")

    for heading in ("# Geography", "# SaaS Experience", "# Market Segment",
                    "## Deterministic rules matched", "## AI (Pass 2) proposals",
                    "**Final:**"):
        assert heading in md

    # Forensic detail: rejected AI proposal with reason
    assert "❌ REJECTED" in md
    assert "quote_not_found" in md
    assert "✅ ACCEPTED" in md


def test_filename_sanitized(audit_dir):
    aud = json.loads(json.dumps(AUDIT))
    aud["candidate_name"] = "Rashmi/Kukreja:  (Test)"
    ref = write_audit_report(PASS1, aud, PRE_ENRICHMENT, VALIDATED, {}, RESUME_TEXT, "cv.pdf")

    name = ref.split("/")[-1]
    assert "/" not in name.split("_", 2)[-1]
    assert name.endswith(".json")
    # No forbidden characters
    assert all(c.isalnum() or c in "_.-" for c in name.split("_", 2)[-1])


def test_empty_audit_dir_creates_directory(audit_dir):
    ref = write_audit_report({}, {}, PRE_ENRICHMENT, VALIDATED, {}, RESUME_TEXT, "cv.pdf")
    assert audit_dir.exists()
    assert ref  # still wrote a report with defaults


def test_write_failure_is_non_fatal(tmp_path, monkeypatch, caplog):
    """If the audit directory cannot be created, return '' and never raise."""
    monkeypatch.setattr(reporter, "_AUDIT_DIR", tmp_path / "blocked")
    # Make _AUDIT_DIR a regular FILE so mkdir(parents=True) fails
    (tmp_path / "blocked").write_text("i am a file", encoding="utf-8")

    with caplog.at_level(logging.ERROR):
        ref = write_audit_report(PASS1, AUDIT, PRE_ENRICHMENT, VALIDATED, {}, RESUME_TEXT, "cv.pdf")

    assert ref == ""
    assert any("forensic audit report" in r.message.lower() for r in caplog.records)


def test_markdown_reports_blank_field_reason(audit_dir):
    ref = write_audit_report(PASS1, AUDIT, PRE_ENRICHMENT, VALIDATED, {}, RESUME_TEXT, "cv.pdf")
    md = (audit_dir / ref[len("audit/"):].replace(".json", ".md")).read_text(encoding="utf-8")

    segment_block = md.split("# Market Segment", 1)[1].split("--------------------------------", 1)[0]
    assert "**Final:** (blank)" in segment_block
    assert "## Why blank" in segment_block
