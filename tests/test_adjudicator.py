"""Tests for core/adjudicator.py — the deterministic/AI merge layer.

Covers every branch: agreement, additive additions, blank-fills, fabricated
quotes, off-allowlist tags, unsupported reasoning, title-only evidence,
segment conflicts, deterministic preservation, and confidence labels.
All AI responses are mocked (plain dicts) — no external APIs.
"""

import pytest

from core.adjudicator import adjudicate

RESUME_TEXT = """SNEHASISH DAS
Enrolment Associate
Coursera
Dialing around 120-170 cold calls on daily basis maintaining a high level of customer service.
Connecting with learners from across different geographies like the US, Canada, Middle East, Africa, Asia and Southeast Asia.
Actively engaging with students to keep them motivated and invested in their learning journey.
Through strategic initiatives, successfully reduced refund rates by 30% while simultaneously increasing customer engagement by 75%.
Responding to queries from prospective businesses via email, chat, and phone.
Worked with SMB customers across North America.
Also covered customers across Europe and the Middle East.
Handling inbound leads and demo requests from enterprise buyers.
Tools: Salesforce, HubSpot.
Account Manager
Globex
Managed enterprise accounts across EMEA.
"""

PASS1_DATA = {
    "candidate_metadata": {"full_name": "Snehasish Das"},
    "role_analysis": [
        {
            "role_title": "Enrolment Associate",
            "employer": "Coursera",
            "evidence_quotes": ["Enrolment Associate", "Dialing around 120-170 cold calls on daily basis."],
        },
        {
            "role_title": "Account Manager",
            "employer": "Globex",
            "evidence_quotes": ["Account Manager", "Managed enterprise accounts across EMEA."],
        },
    ],
}


def _deterministic(geo=None, saas=None, seg=None):
    """Build a deterministic final_answer dict with the three fields set."""
    return {
        "full_name": "Snehasish Das",
        "geography": geo,
        "saas_experience": saas,
        "market_segment": seg,
    }


def _run(final, proposals, pass1=PASS1_DATA, resume=RESUME_TEXT):
    """Run adjudicate and return (merged, audit, field_audit)."""
    merged, audit = adjudicate(
        deterministic_final=final,
        classification_audit={},
        ai_proposals=proposals,
        resume_text=resume,
        pass1_data=pass1,
    )
    return merged, audit, audit["fields"]


def _proposals(**fields):
    """Build a proposals dict from per-field proposal lists."""
    return {f: fields.get(f, []) for f in ("geography", "saas_experience", "market_segment")}


def _prop(tag, evidence, reasoning, confidence="high"):
    return {"tag": tag, "confidence": confidence, "evidence": evidence, "reasoning": reasoning}


GEO_QUOTE = "Connecting with learners from across different geographies like the US, Canada, Middle East, Africa, Asia and Southeast Asia."


# ── Baseline preservation ────────────────────────────────────────────────────

def test_no_ai_proposals_keeps_deterministic_unchanged():
    final = _deterministic(geo=["APAC"], saas=["Outbound/Prospecting"], seg=["SMB"])
    merged, _, fields = _run(final, _proposals())

    assert merged["geography"] == ["APAC"]
    assert merged["saas_experience"] == ["Outbound/Prospecting"]
    assert merged["market_segment"] == ["SMB"]
    # Every field still gets an ai section for the forensic trace
    assert fields["geography"]["ai"]["confidence"] == "Deterministic"
    assert fields["saas_experience"]["ai"]["confidence"] == "Deterministic"
    assert fields["market_segment"]["ai"]["confidence"] == "Deterministic"


def test_ai_never_removes_deterministic_tags():
    final = _deterministic(geo=["APAC", "NA"], saas=["Outbound/Prospecting"], seg=["B2C"])
    # AI proposes valid but different tags in every field
    proposals = _proposals(
        geography=[_prop("EU", ["Also covered customers across Europe and the Middle East."], "Served European territory")],
        saas_experience=[_prop("Inbound Sales", ["Handling inbound leads and demo requests from enterprise buyers."], "Handled inbound pipeline")],
        market_segment=[_prop("Enterprise", ["Managed enterprise accounts across EMEA."], "Sold to enterprise")],
    )
    merged, _, fields = _run(final, proposals)

    assert "APAC" in merged["geography"]
    assert "NA" in merged["geography"]
    assert "Outbound/Prospecting" in merged["saas_experience"]
    assert merged["market_segment"] == ["B2C"]  # segment conflict → deterministic kept
    assert fields["market_segment"]["ai"]["confidence"] == "Low"


# ── Acceptance branches ──────────────────────────────────────────────────────

def test_ai_agrees_with_deterministic_very_high():
    final = _deterministic(geo=["APAC"], saas=None, seg=None)
    proposals = _proposals(
        geography=[_prop("APAC", [GEO_QUOTE], "APAC — the bullet lists Asia as covered customer territory.")]
    )
    merged, _, fields = _run(final, proposals)

    assert merged["geography"] == ["APAC"]  # no duplicate
    ai = fields["geography"]["ai"]
    assert ai["proposals"][0]["decision"] == "accepted"
    assert ai["proposals"][0]["overlaps_deterministic"] is True
    assert ai["confidence"] == "Very High"


def test_ai_adds_tag_on_top_of_deterministic_high():
    final = _deterministic(saas=["Outbound/Prospecting"])
    proposals = _proposals(
        saas_experience=[
            _prop(
                "Customer Retention",
                ["Through strategic initiatives, successfully reduced refund rates by 30% while simultaneously increasing customer engagement by 75%."],
                "Reducing refund rates is retention work.",
            )
        ]
    )
    merged, _, fields = _run(final, proposals)

    assert merged["saas_experience"] == ["Outbound/Prospecting", "Customer Retention"]
    assert fields["saas_experience"]["ai"]["confidence"] == "High"


def test_ai_fills_blank_field_medium():
    final = _deterministic(seg=None)
    proposals = _proposals(
        market_segment=[
            _prop(
                "B2C",
                ["Actively engaging with students to keep them motivated and invested in their learning journey."],
                "Engaging with students means the customer is the consumer.",
            )
        ]
    )
    merged, _, fields = _run(final, proposals)

    assert merged["market_segment"] == ["B2C"]
    assert fields["market_segment"]["ai"]["confidence"] == "Medium"


def test_ai_geography_context_read_multi_region():
    """The Snehasish case — context the deterministic regexes miss."""
    final = _deterministic(geo=None, saas=["Outbound/Prospecting"], seg=None)
    proposals = _proposals(
        geography=[
            _prop("NA", [GEO_QUOTE], "US and Canada are covered customer territories."),
            _prop("MEA", [GEO_QUOTE], "Middle East and Africa are covered customer territories."),
            _prop("SEA", [GEO_QUOTE], "Southeast Asia is a covered customer territory."),
        ]
    )
    merged, _, fields = _run(final, proposals)

    assert set(merged["geography"]) == {"NA", "MEA", "SEA"}
    assert fields["geography"]["ai"]["confidence"] == "Medium"


# ── Rejection branches ───────────────────────────────────────────────────────

def test_fabricated_quote_rejected():
    final = _deterministic(geo=None)
    proposals = _proposals(
        geography=[_prop("MEA", ["Sold into Dubai, Abu Dhabi and Qatar."], "Gulf territory coverage.")]
    )
    merged, _, fields = _run(final, proposals)

    assert merged["geography"] is None
    ai = fields["geography"]["ai"]
    assert ai["proposals"][0]["decision"] == "rejected"
    assert "quote_not_found" in ai["proposals"][0]["reject_reason"]
    assert ai["confidence"] == "No Match"


def test_one_fabricated_quote_among_valid_rejects_whole_proposal():
    final = _deterministic(saas=None)
    proposals = _proposals(
        saas_experience=[
            _prop(
                "Customer Retention",
                ["Through strategic initiatives, successfully reduced refund rates by 30%.", "This quote does not exist anywhere in the resume."],
                "Retention evidence.",
            )
        ]
    )
    merged, _, fields = _run(final, proposals)
    ai = fields["saas_experience"]["ai"]

    assert merged["saas_experience"] is None
    assert ai["proposals"][0]["decision"] == "rejected"
    assert "quote_not_found" in ai["proposals"][0]["reject_reason"]


def test_no_evidence_quotes_rejected():
    final = _deterministic(geo=None)
    proposals = _proposals(
        geography=[_prop("EU", [], "Europe territory.")]
    )
    merged, _, fields = _run(final, proposals)

    assert merged["geography"] is None
    assert "no_evidence" in fields["geography"]["ai"]["proposals"][0]["reject_reason"]


def test_off_allowlist_rejected():
    final = _deterministic(geo=None)
    proposals = _proposals(
        geography=[_prop("Europe", ["Also covered customers across Europe and the Middle East."], "European territory.")]
    )
    merged, _, fields = _run(final, proposals)

    assert merged["geography"] is None
    assert "off_allowlist" in fields["geography"]["ai"]["proposals"][0]["reject_reason"]


def test_off_allowlist_saas_rejected():
    final = _deterministic(saas=None)
    proposals = _proposals(
        saas_experience=[_prop("Big Data Sales", ["Dialing cold calls daily."], "Big data selling.")]
    )
    merged, _, fields = _run(final, proposals)

    assert merged["saas_experience"] is None
    assert "off_allowlist" in fields["saas_experience"]["ai"]["proposals"][0]["reject_reason"]


def test_reasoning_unsupported_rejected():
    """Canonical bad case from the spec: Evidence 'Salesforce' cannot support 'SaaS Sales'."""
    final = _deterministic(saas=None)
    proposals = _proposals(
        saas_experience=[
            _prop("SaaS Sales", ["Tools: Salesforce, HubSpot."], "Used a CRM so the company is SaaS.")
        ]
    )
    merged, _, fields = _run(final, proposals)

    assert merged["saas_experience"] is None
    assert "reasoning_unsupported" in fields["saas_experience"]["ai"]["proposals"][0]["reject_reason"]


def test_reasoning_not_anchored_rejected():
    """Reasoning that argues for a different concept than the proposed tag."""
    final = _deterministic(seg=None)
    proposals = _proposals(
        market_segment=[
            _prop("Enterprise", ["Managed enterprise accounts across EMEA."], "High call volume indicates consumer focus.")
        ]
    )
    merged, _, fields = _run(final, proposals)

    assert merged["market_segment"] is None
    assert "reasoning_unsupported" in fields["market_segment"]["ai"]["proposals"][0]["reject_reason"]


def test_title_only_rejected():
    final = _deterministic(saas=None, seg=None)
    proposals = _proposals(
        saas_experience=[
            _prop("Account Management", ["Account Manager"], "Title shows account management."),
        ],
        market_segment=[
            _prop("Enterprise", ["Account Manager"], "Title implies enterprise."),
        ],
    )
    merged, _, fields = _run(final, proposals)

    assert merged["saas_experience"] is None
    assert merged["market_segment"] is None
    assert "title_only" in fields["saas_experience"]["ai"]["proposals"][0]["reject_reason"]
    assert "title_only" in fields["market_segment"]["ai"]["proposals"][0]["reject_reason"]


# ── Conflict resolution ──────────────────────────────────────────────────────

def test_segment_conflict_deterministic_wins_low_confidence():
    final = _deterministic(seg=["Enterprise"])
    proposals = _proposals(
        market_segment=[
            _prop("SMB", ["Worked with SMB customers across North America."], "SMB customer base."),
        ]
    )
    merged, _, fields = _run(final, proposals)

    assert merged["market_segment"] == ["Enterprise"]  # deterministic kept
    ai = fields["market_segment"]["ai"]
    assert ai["proposals"][0]["decision"] == "rejected"
    assert "conflicts_with_deterministic" in ai["proposals"][0]["reject_reason"]
    assert ai["confidence"] == "Low"


def test_segment_agree_when_same():
    final = _deterministic(seg=["Enterprise"])
    proposals = _proposals(
        market_segment=[
            _prop("Enterprise", ["Managed enterprise accounts across EMEA."], "Enterprise accounts."),
        ]
    )
    merged, _, fields = _run(final, proposals)

    assert merged["market_segment"] == ["Enterprise"]
    assert fields["market_segment"]["ai"]["confidence"] == "Very High"


def test_geography_and_saas_remain_additive_on_difference():
    """Additive fields: different AI tags are additions, not conflicts."""
    final = _deterministic(geo="APAC", saas=["Outbound/Prospecting"])
    proposals = _proposals(
        geography=[_prop("MEA", [GEO_QUOTE], "Middle East territory.")],
        saas_experience=[_prop("Inbound Sales", ["Handling inbound leads and demo requests from enterprise buyers."], "Inbound pipeline.")],
    )
    merged, _, fields = _run(final, proposals)

    assert set(merged["geography"]) == {"APAC", "MEA"}
    assert set(merged["saas_experience"]) == {"Outbound/Prospecting", "Inbound Sales"}
    assert fields["geography"]["ai"]["confidence"] == "High"
    assert fields["saas_experience"]["ai"]["confidence"] == "High"


# ── Sanity ───────────────────────────────────────────────────────────────────

def test_acceptance_requires_every_condition():
    """A verbatim quote that contains no support keyword for the tag fails."""
    final = _deterministic(saas=None)
    proposals = _proposals(
        saas_experience=[
            _prop(
                "B2B2C",
                ["Actively engaging with students to keep them motivated and invested in their learning journey."],
                "Engaging with students implies a b2b2c motion.",
            )
        ]
    )
    merged, _, fields = _run(final, proposals)

    assert merged["saas_experience"] is None
    assert "reasoning_unsupported" in fields["saas_experience"]["ai"]["proposals"][0]["reject_reason"]


def test_rejected_proposals_carry_reject_reason_in_audit():
    final = _deterministic(geo=None)
    proposals = _proposals(
        geography=[
            _prop("Europe", ["Also covered customers across Europe and the Middle East."], "European territory."),
            _prop("APAC", [GEO_QUOTE], "APAC — Asia is covered territory."),
        ]
    )
    merged, _, fields = _run(final, proposals)

    decisions = fields["geography"]["ai"]["proposals"]
    by_tag = {d["tag"]: d for d in decisions}
    assert by_tag["Europe"]["decision"] == "rejected"
    assert "off_allowlist" in by_tag["Europe"]["reject_reason"]
    assert by_tag["APAC"]["decision"] == "accepted"
    assert merged["geography"] == ["APAC"]
