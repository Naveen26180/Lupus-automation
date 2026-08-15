"""Tests for the Pass 2 wiring inside integrations/ai/base_client.py.

Verifies the AI_CLASSIFICATION_ENABLED toggle, the pass2 prompt assembly
(resume text + pass1 evidence), invalid-JSON / schema / provider failures
falling back to deterministic-only, and the full extract_fields flow with
mocked API responses. No external APIs are called.
"""

import json
import os

import pytest

from core.exceptions import AIProviderError
from integrations.ai.base_client import BaseAIClient, _parse_pass2_response

PASS1_RAW = json.dumps({
    "candidate_metadata": {"full_name": "Snehasish Das"},
    "document_evidence": [],
    "role_analysis": [
        {
            "role_title": "Enrolment Associate",
            "employer": "Coursera",
            "date_raw": "04/2023 - Current",
            "evidence_quotes": [
                "Enrolment Associate",
                "Dialing around 120-170 cold calls on daily basis maintaining a high level of customer service.",
                "Connecting with learners from across different geographies like the US, Canada, Middle East, Africa, Asia and Southeast Asia.",
            ],
        }
    ],
})

RESUME_TEXT = (
    "SNEHASISH DAS\nEnrolment Associate\nCoursera\n"
    "Dialing around 120-170 cold calls on daily basis maintaining a high level of customer service.\n"
    "Connecting with learners from across different geographies like the US, Canada, Middle East, Africa, Asia and Southeast Asia.\n"
)

PASS2_RAW = json.dumps({
    "proposals": {
        "geography": [
            {
                "tag": "MEA",
                "confidence": "high",
                "evidence": [
                    "Connecting with learners from across different geographies like the US, Canada, Middle East, Africa, Asia and Southeast Asia."
                ],
                "reasoning": "Middle East and Africa are covered customer territories.",
            }
        ],
        "saas_experience": [],
        "market_segment": [],
    }
})


class FakeAI(BaseAIClient):
    """Test double — serves canned raw responses; pass2 can be failed."""

    def __init__(self, responses, fail_pass2=False):
        super().__init__(api_key="test-key", provider_name="fake")
        self._responses = list(responses)
        self.calls = []
        self.fail_pass2 = fail_pass2

    def _call_api(self, prompt: str) -> str:
        self.calls.append(prompt)
        if self.fail_pass2 and "proposals" in prompt:
            raise AIProviderError("fake", "simulated provider failure")
        if not self._responses:
            raise AIProviderError("fake", "no canned response left")
        return self._responses.pop(0)


@pytest.fixture(autouse=True)
def _no_debug_dump(monkeypatch, tmp_path):
    """Redirect the raw_ai_response.json debug dump away from the repo."""
    import integrations.ai.base_client as bc
    monkeypatch.setattr(bc, "_DEBUG_DUMP_PATH", tmp_path / "raw_ai_response.json")


def _enable(monkeypatch):
    monkeypatch.setenv("AI_CLASSIFICATION_ENABLED", "true")


def _disable(monkeypatch):
    monkeypatch.delenv("AI_CLASSIFICATION_ENABLED", raising=False)


# ── Toggle ───────────────────────────────────────────────────────────────────

def test_pass2_disabled_by_default_runs_single_call(monkeypatch):
    _disable(monkeypatch)
    client = FakeAI([PASS1_RAW])
    result = client.extract_fields(RESUME_TEXT)

    assert len(client.calls) == 1  # pass 1 only — pass 2 never ran
    assert "proposals" not in client.calls[0]
    # Deterministic output intact
    assert "Outbound/Prospecting" in (result.get("saas_experience") or [])
    assert "APAC" in (result.get("geography") or [])
    # No AI sections in the audit when disabled
    assert result["_classification_audit"]["fields"]["geography"].get("ai") is None
    assert result["_pass1_data"]["candidate_metadata"]["full_name"] == "Snehasish Das"


def test_pass2_disabled_explicit_false(monkeypatch):
    monkeypatch.setenv("AI_CLASSIFICATION_ENABLED", "false")
    client = FakeAI([PASS1_RAW])
    client.extract_fields(RESUME_TEXT)
    assert len(client.calls) == 1


def test_pass2_enabled_runs_two_passes_and_adjudicates(monkeypatch):
    _enable(monkeypatch)
    client = FakeAI([PASS1_RAW, PASS2_RAW])
    result = client.extract_fields(RESUME_TEXT)

    assert len(client.calls) == 2
    # Pass 2 prompt contains BOTH the full resume text and the pass1 evidence
    assert "proposals" in client.calls[1]
    assert RESUME_TEXT.splitlines()[0] in client.calls[1]
    assert "candidate_metadata" in client.calls[1]

    # Adjudicated: deterministic APAC/NA/MEA/SEA (rule expansion) + AI agrees on MEA
    geo = result.get("geography") or []
    assert "APAC" in geo
    assert "MEA" in geo

    # Audit records the AI decision
    ai = result["_classification_audit"]["fields"]["geography"]["ai"]
    assert ai["proposals"][0]["tag"] == "MEA"
    assert ai["proposals"][0]["decision"] == "accepted"
    assert ai["proposals"][0]["overlaps_deterministic"] is True
    assert ai["confidence"] == "Very High"  # deterministic + AI agree


def test_pass2_fills_blank_field(monkeypatch):
    _enable(monkeypatch)
    pass2 = json.dumps({
        "proposals": {
            "geography": [],
            "saas_experience": [],
            "market_segment": [
                {
                    "tag": "B2C",
                    "confidence": "medium",
                    "evidence": ["Connecting with learners from across different geographies like the US, Canada, Middle East, Africa, Asia and Southeast Asia."],
                    "reasoning": "Learners are the consumer customers.",
                }
            ],
        }
    })
    client = FakeAI([PASS1_RAW, pass2])
    result = client.extract_fields(RESUME_TEXT)

    assert result.get("market_segment") == ["B2C"]
    ai = result["_classification_audit"]["fields"]["market_segment"]["ai"]
    assert ai["confidence"] == "Medium"


# ── Failure behaviour ────────────────────────────────────────────────────────

def test_pass2_provider_failure_falls_back_to_deterministic(monkeypatch):
    _enable(monkeypatch)
    client = FakeAI([PASS1_RAW], fail_pass2=True)
    result = client.extract_fields(RESUME_TEXT)  # must not raise

    assert "Outbound/Prospecting" in (result.get("saas_experience") or [])
    assert "APAC" in (result.get("geography") or [])
    assert result["_classification_audit"]["fields"]["geography"].get("ai") is None


def test_pass2_invalid_json_falls_back(monkeypatch):
    _enable(monkeypatch)
    client = FakeAI([PASS1_RAW, "this is not json {{{"])
    result = client.extract_fields(RESUME_TEXT)

    assert result.get("saas_experience") is not None  # deterministic survived
    assert result["_classification_audit"]["fields"]["geography"].get("ai") is None


def test_pass2_missing_keys_falls_back(monkeypatch):
    _enable(monkeypatch)
    # Missing top-level 'proposals'
    client = FakeAI([PASS1_RAW, json.dumps({"final_answer": {}})])
    result = client.extract_fields(RESUME_TEXT)
    assert result.get("geography") is not None


def test_pass2_schema_violation_falls_back(monkeypatch):
    _enable(monkeypatch)
    # Proposal missing confidence/evidence — schema violation → whole pass2 dropped
    bad = json.dumps({"proposals": {"geography": [{"tag": "MEA"}], "saas_experience": [], "market_segment": []}})
    client = FakeAI([PASS1_RAW, bad])
    result = client.extract_fields(RESUME_TEXT)

    # Deterministic-only output (the expansion already yields these from the quote)
    assert result.get("geography") == ["APAC", "NA", "MEA", "SEA"]
    # No AI section at all — the malformed pass2 was discarded wholesale
    assert result["_classification_audit"]["fields"]["geography"].get("ai") is None


def test_pass2_off_allowlist_rejected_through_flow(monkeypatch):
    _enable(monkeypatch)
    bad = json.dumps({
        "proposals": {
            "geography": [{"tag": "Europe", "confidence": "high", "evidence": ["Connecting with learners from across different geographies like the US, Canada, Middle East, Africa, Asia and Southeast Asia."], "reasoning": "European territory."}],
            "saas_experience": [],
            "market_segment": [],
        }
    })
    client = FakeAI([PASS1_RAW, bad])
    result = client.extract_fields(RESUME_TEXT)

    geo = result.get("geography") or []
    assert "Europe" not in geo
    ai = result["_classification_audit"]["fields"]["geography"]["ai"]
    assert ai["proposals"][0]["decision"] == "rejected"
    assert "off_allowlist" in ai["proposals"][0]["reject_reason"]


# ── Pass 2 response parser ───────────────────────────────────────────────────

def test_parse_pass2_response_valid():
    proposals = _parse_pass2_response(PASS2_RAW, "fake")
    assert proposals["geography"][0]["tag"] == "MEA"


def test_parse_pass2_response_empty_fields_ok():
    raw = json.dumps({"proposals": {"geography": [], "saas_experience": [], "market_segment": []}})
    proposals = _parse_pass2_response(raw, "fake")
    assert proposals == {"geography": [], "saas_experience": [], "market_segment": []}


@pytest.mark.parametrize("bad", [
    "not json",
    json.dumps({"final_answer": {"geography": "APAC"}}),            # no proposals
    json.dumps({"proposals": {"geography": "MEA"}}),                # field not a list
    json.dumps({"proposals": {"geography": [{"confidence": "high", "evidence": ["x"], "reasoning": "r"}]}}),  # no tag
    json.dumps({"proposals": {"geography": [{"tag": "MEA", "evidence": ["x"], "reasoning": "r"}]}}),          # no confidence
    json.dumps({"proposals": {"geography": [{"tag": "MEA", "confidence": "certain", "evidence": ["x"], "reasoning": "r"}]}}),  # bad confidence
    json.dumps({"proposals": {"geography": [{"tag": "MEA", "confidence": "high", "evidence": [], "reasoning": "r"}]}}),         # empty evidence
    json.dumps({"proposals": {"geography": [{"tag": "MEA", "confidence": "high", "evidence": ["x"]}]}}),                        # no reasoning
])
def test_parse_pass2_response_rejects_malformed(bad):
    with pytest.raises(AIProviderError):
        _parse_pass2_response(bad, "fake")


def test_pass2_prompt_has_both_inputs(monkeypatch):
    """The pass2 prompt must contain the full resume text AND the pass1 JSON."""
    _enable(monkeypatch)
    client = FakeAI([PASS1_RAW, PASS2_RAW])
    client.extract_fields(RESUME_TEXT)

    pass2_prompt = client.calls[1]
    assert "Connecting with learners from across different geographies" in pass2_prompt
    assert '"role_analysis"' in pass2_prompt
    assert '"candidate_metadata"' in pass2_prompt
