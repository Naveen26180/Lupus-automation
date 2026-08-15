"""Build rows for the Classification Audit worksheet.

Read-only explainability layer. This module never influences classification —
it only formats, for human QA, what the classifier, validator, and enrichment
already decided.

For every processed resume, one row per field (Geography, SaaS Experience,
Market Segment) is produced with exactly 13 columns:

    Timestamp | Candidate | Field | Final Value | Evidence | Source Section |
    Rule Matched | Match Type | Why Selected | Why Others Rejected |
    Blank Reason | Enrichment Status | Confidence

The Timestamp column is a placeholder (empty string) — SheetsClient stamps
the real value at write time so one batch of rows shares one timestamp.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Field order and labels — must mirror sheets_client.AUDIT_HEADERS layout.
_FIELDS = ("geography", "saas_experience", "market_segment")
_FIELD_LABELS = {
    "geography": "Geography",
    "saas_experience": "SaaS Experience",
    "market_segment": "Market Segment",
}

# Sanity caps so a single cell never grows unbounded.
_MAX_MATCHES_PER_FIELD = 12
_MAX_REJECTIONS_PER_FIELD = 14
_MAX_TRIGGERS_PER_RULE = 8

_NULL_LIKE = (None, "", "null", "n/a", "not specified", "unknown")


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _is_blank(value: Any) -> bool:
    """True if a value is functionally empty / not provided."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip().lower() in _NULL_LIKE:
        return True
    if isinstance(value, list) and not value:
        return True
    return False


def _split_tags(value: Any) -> list[str]:
    """Split a final value (list or '; ' string) into canonical tag strings."""
    if _is_blank(value):
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [s.strip() for s in str(value).split(";") if s.strip()]


def _source_label(source: str) -> str:
    """Map an internal evidence source label to a human-readable location.

    'document:PROFESSIONAL SUMMARY' → 'Professional Summary'
    'role:Coursera'                → 'Work Experience → Coursera'
    """
    if not source:
        return "Unknown"
    if source.startswith("document:"):
        section = source[len("document:"):].strip()
        return section.title() if section else "Document"
    if source.startswith("role:"):
        employer = source[len("role:"):].strip()
        if employer and employer != "unknown_employer":
            return f"Work Experience → {employer}"
        return "Role Analysis"
    return source


def _triggers_text(triggers: Any) -> str:
    """Join a rule's trigger phrases into a compact, truncated string."""
    if not triggers:
        return ""
    return ", ".join(str(t) for t in triggers[:_MAX_TRIGGERS_PER_RULE])


def _rule_line(match: dict) -> str:
    """One 'Rule Matched' cell line for a single classifier match."""
    line = f'Matched: "{match.get("phrase", "")}" → {match.get("tag", "")}'
    triggers = _triggers_text(match.get("triggers"))
    if triggers:
        line += f"\n  Rule triggers: {triggers}"
    return line


def _why_selected(tag: str, tag_matches: list[dict], enrichment_note: str | None) -> str:
    """Build the 'Why Selected' block for one final tag."""
    lines = [f"Tag: {tag}"]
    if tag_matches:
        m = tag_matches[0]
        lines.append(f'Evidence: "{m.get("evidence", "")}"')
        triggers = _triggers_text(m.get("triggers"))
        matched_rule = triggers or m.get("phrase", "")
        lines.append(f"Matched rule: {matched_rule}")
        lines.append(f"Therefore: {tag}")
    elif enrichment_note:
        lines.append("Original: (blank)")
        lines.append(f"Enrichment source: {enrichment_note}")
        lines.append(f"Therefore: {tag}")
    else:
        lines.append("Value present in final output; provenance not recorded.")
    return "\n".join(lines)


def _blank_reason(
    field: str,
    raw: Any,
    matches: list[dict],
    rejected: list[dict],
    info: dict,
) -> str:
    """Explain why a field ended up blank."""
    raw_tags = _split_tags(raw)
    if raw_tags:
        return (
            "Classifier matched " + ", ".join(raw_tags)
            + " but the validator removed all values (not in canonical allowlist)."
        )
    if any(m.get("title_blocked") for m in matches):
        return (
            "Every evidence match was a job-title-only statement — blocked by "
            "title-only rules. No substantive sales evidence in the resume."
        )
    scraped = info.get("scraped_geo" if field == "geography" else "scraped_seg")
    if not _is_blank(scraped):
        return (
            f'Enrichment research found "{scraped}" but it failed validation. '
            "No resume evidence matched any rule."
        )
    if rejected:
        triggers = _triggers_text(rejected[0].get("triggers"))
        return (
            f"No evidence matched any {_FIELD_LABELS[field]} rule "
            f"(e.g. rule triggers: {triggers}). Field intentionally left blank."
        )
    return "No evidence matched any rule. Field intentionally left blank."


def _enrichment_status(field: str, pre: Any, final: Any, info: dict) -> str:
    """Summarize what enrichment did (or didn't do) for one field."""
    resume_state = "Resume populated" if not _is_blank(pre) else "Resume blank"

    if field == "saas_experience":
        # Enrichment never touches saas_experience — resume-stated role
        # descriptions are not replaced, only geography & segment are filled.
        return f"{resume_state}; Enrichment N/A (field never enriched)"

    ran = info.get("ran", False)
    if not ran:
        reason = info.get("reason") or "not enabled"
        if reason == "no_companies":
            return f"{resume_state}; Enrichment skipped (no companies to research)"
        if reason == "fields_populated":
            return f"{resume_state}; Enrichment skipped (both fields already populated)"
        if reason == "error":
            return f"{resume_state}; Enrichment attempted (failed before completing)"
        return f"{resume_state}; Enrichment skipped"

    if not _is_blank(final) and _is_blank(pre):
        return f"{resume_state}; Enrichment succeeded"

    scraped = info.get("scraped_geo" if field == "geography" else "scraped_seg")
    if not _is_blank(scraped):
        return f"{resume_state}; Enrichment rejected (scraped value failed validation)"

    return f"{resume_state}; Enrichment attempted (no usable value found)"


def _confidence(field: str, final: Any, pre: Any, info: dict, raw: Any, final_tags: list[str]) -> str:
    """Classify confidence: Deterministic / Validated / Enriched / No Match.

    Not an AI confidence score — it records how the value was produced.
    """
    if _is_blank(final):
        return "No Match"

    info = info or {}
    # Value filled in from company research (resume had the field blank)
    if info.get("ran") and _is_blank(pre) and field in ("geography", "market_segment"):
        return "Enriched"

    # Value produced by the deterministic classifier rules
    raw_tags = _split_tags(raw)
    if raw_tags and any(t in final_tags for t in raw_tags):
        return "Deterministic"

    # Value passed the validator but has no recorded rule match (alias mapping)
    return "Validated"


def _build_row(
    field: str,
    field_audit: dict,
    pre_enrichment: dict,
    validated_data: dict,
    enrichment_info: dict,
    candidate: str,
) -> list:
    """Build the 13-column audit row for a single field."""
    raw = field_audit.get("raw_value")
    matches = field_audit.get("matches", []) or []
    rejected = field_audit.get("rejected", []) or []

    pre = pre_enrichment.get(field)
    final = validated_data.get(field)
    final_tags = _split_tags(final)
    raw_tags = _split_tags(raw)
    info = enrichment_info or {}

    # Matches that actually produced a final tag
    producing = [m for m in matches if not m.get("title_blocked") and m.get("tag") in final_tags]
    # Matches that were suppressed because the evidence was just the job title
    blocked = [m for m in matches if m.get("title_blocked")]
    # Matches whose tag did not survive (validator dropped it)
    dropped = [m for m in matches if m.get("tag") not in final_tags and not m.get("title_blocked")]

    # ── Evidence / Source Section / Rule Matched (parallel lines) ──────────
    evidence_lines = [str(m.get("evidence", "")) for m in producing]
    source_lines = [_source_label(str(m.get("source", ""))) for m in producing]
    rule_lines = [_rule_line(m) for m in producing]

    if len(producing) > _MAX_MATCHES_PER_FIELD:
        omitted = len(producing) - _MAX_MATCHES_PER_FIELD
        evidence_lines = evidence_lines[:_MAX_MATCHES_PER_FIELD] + [f"… ({omitted} more matches omitted)"]
        source_lines = source_lines[:_MAX_MATCHES_PER_FIELD] + [""]
        rule_lines = rule_lines[:_MAX_MATCHES_PER_FIELD] + [f"… ({omitted} more matches omitted)"]

    # ── Match Type ─────────────────────────────────────────────────────────
    match_types: list[str] = []
    if producing:
        match_types = list(dict.fromkeys(m.get("match_type", "Explicit") for m in producing))
    elif not _is_blank(final):
        if info.get("ran") and _is_blank(pre) and field in ("geography", "market_segment"):
            match_types = ["Enrichment"]
        elif raw_tags and not any(t in final_tags for t in raw_tags):
            match_types = ["Validator Alias"]
        else:
            match_types = ["None"]
    else:
        match_types = ["None"]

    # ── Why Selected ───────────────────────────────────────────────────────
    why_selected_blocks: list[str] = []
    for tag in final_tags:
        tag_matches = [m for m in producing if m.get("tag") == tag]
        enrichment_note: str | None = None
        if not tag_matches and field in ("geography", "market_segment"):
            scraped = info.get("scraped_geo" if field == "geography" else "scraped_seg")
            if info.get("ran") and _is_blank(pre) and not _is_blank(scraped):
                enrichment_note = f"company research ({scraped})"
        why_selected_blocks.append(_why_selected(tag, tag_matches, enrichment_note))
    why_selected_text = "\n\n".join(why_selected_blocks)

    # ── Why Others Rejected ────────────────────────────────────────────────
    reject_lines: list[str] = []
    for m in blocked:
        reject_lines.append(
            f'Rejected: {m.get("tag", "")} — evidence was only the job title '
            "(title-only rule blocked it)."
        )
    for m in dropped:
        reject_lines.append(
            f'Rejected: {m.get("tag", "")} — matched "{m.get("phrase", "")}" but the '
            "validator dropped it (not in canonical allowlist)."
        )
    # Safety net: raw classifier tags with no recorded match that did not
    # survive into the final value (shouldn't happen in the real flow, but
    # hand-built/edge audit data must still be explained).
    reported_dropped = {m.get("tag") for m in dropped}
    for tag in raw_tags:
        if tag not in final_tags and tag not in reported_dropped:
            reject_lines.append(
                f'Rejected: {tag} — validator dropped it (not in canonical allowlist).'
            )
    for r in rejected:
        if r.get("tag") in final_tags:
            continue
        triggers = _triggers_text(r.get("triggers"))
        reason = f"no evidence matched rule triggers ({triggers})" if triggers else "no evidence matched"
        reject_lines.append(f'Rejected: {r.get("tag", "")} — {reason}.')
    # Enrichment conflict note (resume value vs. scraped value)
    note = validated_data.get("data_source_note") or ""
    if note:
        reject_lines.append(f"Note: {note}")

    if len(reject_lines) > _MAX_REJECTIONS_PER_FIELD:
        omitted = len(reject_lines) - _MAX_REJECTIONS_PER_FIELD
        reject_lines = reject_lines[:_MAX_REJECTIONS_PER_FIELD] + [f"… ({omitted} more items omitted)"]
    why_rejected_text = "\n".join(reject_lines)

    # ── Blank Reason ───────────────────────────────────────────────────────
    blank_reason = ""
    if _is_blank(final):
        blank_reason = _blank_reason(field, raw, matches, rejected, info)

    # ── Enrichment Status / Confidence ─────────────────────────────────────
    enrich_status = _enrichment_status(field, pre, final, info)
    confidence = _confidence(field, final, pre, info, raw, final_tags)

    return [
        "",  # Timestamp — stamped by SheetsClient at write time
        candidate,
        _FIELD_LABELS[field],
        "; ".join(final_tags) if final_tags else "(blank)",
        "\n".join(evidence_lines),
        "\n".join(source_lines),
        "\n".join(rule_lines),
        "; ".join(match_types),
        why_selected_text,
        why_rejected_text,
        blank_reason,
        enrich_status,
        confidence,
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_audit_rows(
    classification_audit: dict,
    pre_enrichment: dict,
    validated_data: dict,
    enrichment_info: dict | None = None,
) -> list[list]:
    """Build the audit rows for one processed resume.

    Args:
        classification_audit: Audit dict returned by
            core.classifier.classify_candidate_audited() (attached to the
            extraction result under '_classification_audit').
        pre_enrichment: Snapshot of the validated geography / saas_experience /
            market_segment values taken BEFORE enrichment ran.
        validated_data: The final (post-enrichment, post-validation) candidate
            dict — only the three classified fields are read from it.
        enrichment_info: Dict recorded by enrichment_pipeline.enrich_candidate()
            under '_enrichment_info' (ran / reason / scraped values).

    Returns:
        List of 3 rows (one per field), each with 13 columns matching
        sheets_client.AUDIT_HEADERS. Returns [] if the audit dict is empty.
    """
    if not classification_audit:
        return []

    candidate = classification_audit.get("candidate_name") or validated_data.get("full_name") or ""
    fields_audit = classification_audit.get("fields", {}) or {}
    info = enrichment_info or {}

    rows: list[list] = []
    for field in _FIELDS:
        field_audit = fields_audit.get(field, {}) or {}
        rows.append(
            _build_row(
                field,
                field_audit,
                pre_enrichment or {},
                validated_data,
                info,
                candidate,
            )
        )
    return rows
