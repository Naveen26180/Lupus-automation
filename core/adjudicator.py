"""Adjudicator — merge deterministic classifier output with AI context proposals.

Architecture contract
---------------------
The deterministic classifier (core/classifier.py) is the PRODUCTION BASELINE.
The AI (Pass 2) is a context-aware second opinion that may only ADD
evidence-backed classifications. It can never remove, override, or conflict
with a deterministic value.

An AI proposal is ACCEPTED only when ALL of these hold:

  1. ALLOWLIST   — the tag is a canonical value for that field (validator sets).
  2. VERBATIM    — every evidence quote literally appears in the resume text
                   (whitespace-normalized comparison). A single fabricated
                   quote poisons the whole proposal.
  3. REASONING   — the reasoning must logically follow from the evidence
                   (deterministic keyword grounding — see _reasoning_supported).
  4. NO TITLE    — the evidence must not be just the candidate's job title
                   (reuses the classifier's title-only protection).

Conflict handling: if the AI proposes a tag that differs from the
deterministic value for a field, the deterministic value is kept and the
disagreement is recorded. The AI may propose tags the deterministic rules
missed — that is its entire purpose — but never against them.

Confidence (per field, mirrors the spec):
  Deterministic + AI agree        → Very High
  Deterministic blank, AI accepted → Medium
  Deterministic present, AI added  → High
  Conflict (AI proposed, rejected) → Low
  No AI, deterministic only        → Deterministic
  Blank                            → No Match

This module is pure Python, fully deterministic, and never calls external
APIs. Tests mock all AI responses.
"""

import logging
import re
from typing import Any, Dict, List, Tuple

from core.classifier import _is_title_only
from core.validator import _GEO_TAGS, _SAAS_EXP_ALLOWED, _SEGMENT_ALLOWED

logger = logging.getLogger(__name__)

# Fields the adjudicator merges — the only three the AI may touch.
FIELDS = ("geography", "saas_experience", "market_segment")

_FIELD_LABELS = {
    "geography": "Geography",
    "saas_experience": "SaaS Experience",
    "market_segment": "Market Segment",
}

# Validator allowlists — the AI can never introduce a value outside these.
_ALLOWLISTS: Dict[str, frozenset] = {
    "geography": _GEO_TAGS,
    "saas_experience": _SAAS_EXP_ALLOWED,
    "market_segment": _SEGMENT_ALLOWED,
}


# ---------------------------------------------------------------------------
# Support vocabulary — deterministic keyword grounding for the reasoning check
# ---------------------------------------------------------------------------
# Each tag maps to the phrases that, when present in the evidence, support the
# tag. Seeded from the pass2 mapping table + validator aliases + the
# classifier rule vocabulary. Used ONLY to verify that the AI's reasoning
# follows from its quoted evidence — never to classify on its own.
_SUPPORT: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "geography": {
        "NA": ("north america", "united states", "u.s.", "usa", "canada", "us market", "us accounts", "us"),
        "LATAM": ("latam", "latin america", "mexico", "brazil", "south america"),
        "EMEA": ("emea", "europe, middle east", "middle east and africa", "europe middle east"),
        "EU": ("europe", "european", "eu countries", "western europe"),
        "UKI": ("uk", "united kingdom", "britain", "london", "ireland"),
        "DACH": ("dach", "germany", "austria", "switzerland"),
        "Benelux": ("benelux", "belgium", "netherlands", "luxembourg", "holland"),
        "Nordics": ("nordics", "nordic", "scandinavia", "scandinavian", "denmark", "sweden", "norway", "finland"),
        "CEE": ("cee", "central and eastern europe", "poland", "czech", "hungary", "romania"),
        "Iberia": ("iberia", "spain", "portugal"),
        "CIS": ("cis", "russia", "ukraine", "kazakhstan"),
        "GCC": ("gcc", "gulf", "uae", "dubai", "saudi arabia", "qatar", "kuwait", "bahrain", "oman"),
        "MEA": ("middle east", "mena", "africa", "uae", "saudi arabia", "egypt", "dubai", "north africa"),
        "APAC": ("apac", "asia pacific", "asia-pacific", "asia", "singapore", "japan", "south korea"),
        "APJ": ("apj", "japan", "south korea", "korea"),
        "ANZ": ("anz", "australia", "new zealand"),
        "ASEAN": ("asean", "southeast asian nations", "asean region"),
        "SEA": ("southeast asia", "south east asia", "se asia", "vietnam", "thailand", "indonesia", "philippines", "malaysia"),
        "India": ("india", "indian", "domestic", "domestic market"),
        "Global": ("global", "worldwide", "international", "multiple regions", "multiple markets", "rest of the world"),
        "ROW": ("rest of the world", "rest of world"),
    },
    "saas_experience": {
        "Full-Cycle Sales": ("full-cycle sales", "full cycle sales", "full-cycle", "full cycle", "end-to-end sales", "entire sales cycle", "whole sales cycle", "from prospecting to close"),
        "Outbound/Prospecting": ("cold call", "cold calls", "cold calling", "outbound", "prospecting", "lead generation", "account-based", "abm", "cold email", "cold emailing", "dialed", "dialing", "outreach"),
        "Inbound Sales": ("inbound sales", "inbound lead", "inbound leads", "inbound call", "inbound calls", "inbound inquiry", "inbound demo", "inbound demos", "inbound prospects", "inbound pipeline"),
        "Account Management": ("account management", "account manager", "account managers", "managed accounts", "managing accounts", "key accounts", "strategic accounts", "client management", "managing clients"),
        "Consultative Selling": ("consultative", "discovery call", "discovery calls", "sales discovery", "consultative selling", "consultative approach"),
        "Inside Sales": ("inside sales", "inside sales representative"),
        "Field Sales": ("field sales", "on-site sales", "outside sales", "door-to-door", "in-person sales"),
        "Channel Sales": ("channel sales", "channel partner", "reseller", "distributor", "indirect sales", "sales channels"),
        "Sales Operations": ("sales operations", "sales ops", "revenue operations", "revops"),
        "Customer Retention": ("retention", "renewals", "renewed contracts", "churn", "reduced refund", "refund rate", "refund reduction", "retained customers", "retained clients"),
        "Upsell/Cross-Sell": ("upsell", "upsold", "upselling", "cross-sell", "cross-sold", "cross selling", "additional products", "additional modules", "account expansion"),
        "Sales Engineering": ("sales engineer", "sales engineering", "solutions engineer", "technical sales", "technical pre-sales"),
        "Partner Sales": ("partner sales", "alliance sales", "partner-led", "through partners"),
        "Pre-Sales": ("pre-sales", "presales", "pre sales", "rfp", "rfq", "proof of concept", "proofs of concept", "demo", "demos"),
        "BANT": ("bant",),
        "SPIN": ("spin selling", "spin sales", "spin methodology", "spin questions"),
        "MEDDIC": ("meddic",),
        "MEDDPICC": ("meddpicc",),
        "Challenger Sale": ("challenger sale", "challenger selling", "challenger methodology"),
        "Solution Selling": ("solution selling", "solutions selling", "solution-based selling", "solution-oriented selling"),
        "Value Selling": ("value selling", "value-based selling", "value-based sales"),
        "Sandler": ("sandler",),
        "B2B": ("b2b", "business to business", "business-to-business", "corporate clients", "corporate customers", "business clients", "business customers", "prospective businesses", "enterprise buyers"),
        "B2C": ("b2c", "business to consumer", "business-to-consumer", "consumer customers", "consumer sales", "individual consumers", "end consumers"),
        "B2B2C": ("b2b2c", "b2b and b2c", "b2b & b2c", "b2b/b2c", "business-to-business-to-consumer"),
        "SaaS Sales": ("saas", "software-as-a-service", "software as a service", "software subscription", "software subscriptions", "cloud software", "subscription software"),
        "Transactional": ("transactional", "high-volume sales", "high-velocity sales", "self-serve", "low-ticket"),
        "Enterprise Sales Cycle": ("enterprise sales cycle", "long sales cycle", "complex sales", "multi-stakeholder", "large-ticket", "six-figure deals"),
        "PLG": ("product-led", "product led", "plg", "self-serve funnel", "bottom-up", "land and expand"),
        "Team Lead": ("team lead", "team leader", "leading a team", "led a team", "managing a team", "managed a team", "managing sdrs", "team management"),
        "P&L Ownership": ("p&l", "profit and loss", "profit & loss"),
        "Funnel Management": ("pipeline management", "sales pipeline", "funnel", "forecasting", "forecast", "pipeline review", "opportunity pipeline", "pipeline development"),
    },
    "market_segment": {
        "SMB": ("smb", "smbs", "small business", "small businesses", "small and medium", "startup", "startups", "self-serve"),
        "SME": ("sme customers", "sme clients", "sme accounts", "selling to smes", "small and medium enterprises"),
        "Mid-Market": ("mid-market", "mid market", "midmarket", "commercial", "growth companies", "series b", "series c"),
        "Enterprise": ("enterprise", "fortune 500", "large enterprise", "c-suite", "large organizations", "enterprise accounts", "enterprise customers", "vp-level"),
        "B2C": ("b2c", "consumer", "consumers", "students", "learners", "parents", "individual consumers", "end consumers"),
        "D2C": ("d2c", "direct to consumer", "direct-to-consumer"),
        "B2B2C": ("b2b2c", "b2b and b2c", "b2b & b2c"),
    },
}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Lowercase and collapse whitespace for verbatim / keyword comparison."""
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _contains(text: str, keyword: str) -> bool:
    """Word-boundary substring check (case-insensitive, whitespace-normalized)."""
    return re.search(rf"\b{re.escape(keyword)}\b", text) is not None


def _split_tags(value: Any) -> List[str]:
    """Split a final value (list or '; ' string) into canonical tag strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [s.strip() for s in str(value).split(";") if s.strip()]


def _reasoning_supported(field: str, tag: str, evidence: List[str], reasoning: str) -> Tuple[bool, str]:
    """Deterministic check that the AI's reasoning follows from its evidence.

    Two conditions, both required:
      a. ANCHORED — the reasoning must reference the proposed tag or at least
         one of the tag's supporting keywords (so it argues FOR this tag).
      b. GROUNDED — at least one evidence quote must contain a supporting
         keyword for the tag (so the quoted evidence actually concerns it).

    This is a deliberate keyword proxy for "reasoning supported by evidence".
    It rejects the canonical bad case — Evidence: "Salesforce",
    Reasoning: "SaaS Sales" — because no SaaS-support keyword appears in the
    evidence. It is not a semantic judge; it is a deterministic floor.
    """
    terms = _SUPPORT.get(field, {}).get(tag, ())
    if not terms:
        return False, f"no support vocabulary defined for '{tag}'"

    rn = _normalize(reasoning)
    anchored = _contains(rn, tag) or any(_contains(rn, t) for t in terms)
    if not anchored:
        return (
            False,
            "reasoning does not reference the proposed tag or any of its supporting keywords",
        )

    evidence_text = " ".join(_normalize(q) for q in evidence)
    hits = [t for t in terms if _contains(evidence_text, t)]
    if not hits:
        return (
            False,
            f"evidence quotes contain no keyword supporting '{tag}' "
            f"(support vocabulary: {', '.join(terms[:6])}...)",
        )
    return True, f"supported by evidence keywords: {', '.join(hits[:4])}"


def _field_confidence(
    field: str,
    det_tags: List[str],
    accepted_ai: List[dict],
    all_decisions: List[dict],
) -> str:
    """Compute the per-field confidence label (see module docstring)."""
    if accepted_ai and any(d.get("overlaps_deterministic") for d in accepted_ai):
        return "Very High"  # deterministic + AI agree
    if accepted_ai and det_tags:
        return "High"  # AI added on top of a deterministic baseline
    if accepted_ai:
        return "Medium"  # deterministic was blank; AI filled it
    if det_tags and any(
        d.get("reject_reason", "").startswith("conflicts_with_deterministic")
        for d in all_decisions
    ):
        return "Low"  # disagreement — deterministic kept
    if det_tags:
        return "Deterministic"  # AI rejected for quality reasons; baseline stands
    return "No Match"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def adjudicate(
    deterministic_final: Dict[str, Any],
    classification_audit: Dict[str, Any],
    ai_proposals: Dict[str, Any],
    resume_text: str,
    pass1_data: Dict[str, Any] | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Merge AI context proposals into the deterministic baseline.

    Args:
        deterministic_final: The final_answer dict from
            classify_candidate_audited() — only the three classification
            fields are read or modified; all other keys pass through.
        classification_audit: The audit dict from classify_candidate_audited().
            Mutated in place: each field gains an 'ai' section recording every
            proposal, its decision, and the field confidence.
        ai_proposals: Pass 2 output — {field: [ {tag, confidence, evidence[],
            reasoning}, ... ]}.
        resume_text: The full resume text (authoritative for verbatim checks).
        pass1_data: Optional pass1 dict — used to extract role titles for the
            title-only protection.

    Returns:
        (merged_final_answer, classification_audit). The merged answer keeps
        every deterministic tag; accepted AI tags are appended (deduplicated).
    """
    normalized_resume = _normalize(resume_text)

    role_titles: List[str] = []
    if pass1_data:
        role_titles = [
            str(r.get("role_title")).strip()
            for r in pass1_data.get("role_analysis", [])
            if r.get("role_title")
        ]

    merged: Dict[str, Any] = dict(deterministic_final)

    for field in FIELDS:
        allowed = _ALLOWLISTS[field]
        det_tags = _split_tags(deterministic_final.get(field))
        proposals = ai_proposals.get(field, []) or []
        decisions: List[dict] = []
        accepted: List[str] = list(det_tags)

        for p in proposals:
            tag = str(p.get("tag", "")).strip()
            evidence = [str(q).strip() for q in (p.get("evidence") or []) if str(q).strip()]
            reasoning = str(p.get("reasoning", "")).strip()

            decision: Dict[str, Any] = {
                "tag": tag,
                "confidence": p.get("confidence", "low"),
                "evidence": evidence,
                "reasoning": reasoning,
                "decision": "rejected",
                "reject_reason": None,
                "overlaps_deterministic": tag in det_tags,
            }

            # 1. Allowlist
            if tag not in allowed:
                decision["reject_reason"] = (
                    f"off_allowlist: '{tag}' is not a canonical {_FIELD_LABELS[field]} value"
                )
            # 2. Verbatim quotes (every quote must exist in the resume)
            elif not evidence:
                decision["reject_reason"] = "no_evidence: proposal carries no evidence quotes"
            else:
                missing = [q for q in evidence if _normalize(q) not in normalized_resume]
                if missing:
                    decision["reject_reason"] = (
                        f"quote_not_found: '{missing[0][:80]}' does not appear verbatim in the resume"
                    )
                else:
                    # 3. Title-only protection — checked before reasoning so a
                    # title-only quote is reported as title_only, not as a
                    # reasoning failure.
                    if any(_is_title_only(q, t) for q in evidence for t in role_titles):
                        decision["reject_reason"] = (
                            "title_only: evidence is only the candidate's job title"
                        )
                    else:
                        # 4. Reasoning supported by evidence
                        ok, why = _reasoning_supported(field, tag, evidence, reasoning)
                        if not ok:
                            decision["reject_reason"] = f"reasoning_unsupported: {why}"
                        else:
                            # 5. Segment conflict — deterministic wins for market_segment.
                            # A segment is a single-tier classification; the AI may not
                            # propose a different segment against a deterministic one.
                            # (geography / saas_experience remain additive by design.)
                            if field == "market_segment" and det_tags and tag not in det_tags:
                                decision["reject_reason"] = (
                                    "conflicts_with_deterministic: deterministic already set "
                                    + "; ".join(det_tags)
                                )

            if decision["reject_reason"] is None:
                decision["decision"] = "accepted"
                if tag not in accepted:
                    accepted.append(tag)

            decisions.append(decision)

        # Deterministic order preserved; AI additions appended after.
        merged[field] = accepted or None

        accepted_ai = [d for d in decisions if d["decision"] == "accepted"]
        classification_audit.setdefault("fields", {}).setdefault(field, {})["ai"] = {
            "proposals": decisions,
            "accepted": [d for d in decisions if d["decision"] == "accepted"],
            "rejected": [d for d in decisions if d["decision"] == "rejected"],
            "final_value": merged[field],
            "confidence": _field_confidence(field, det_tags, accepted_ai, decisions),
        }

    logger.info(
        "Adjudication complete — geo=%r saas=%r seg=%r",
        merged.get("geography"),
        merged.get("saas_experience"),
        merged.get("market_segment"),
    )
    return merged, classification_audit
