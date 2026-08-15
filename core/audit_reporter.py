"""Forensic audit reporter — one JSON + one Markdown file per processed resume.

Replaces the verbose Classification Audit sheet rows. Google Sheets stays
clean (production data + an optional 'Audit File' reference); every decision
is captured in machine-readable JSON and a human-readable Markdown report
under audit/.

File naming: audit/<YYYYMMDD_HHMMSS>_<Candidate_Name>.json / .md

The report is a complete forensic trace:
  - resume metadata (timestamp, source, filename, candidate)
  - pass 1 evidence output (full)
  - deterministic classifier output (matched rules, rejected rules)
  - AI (pass 2) proposals with evidence, reasoning, confidence
  - adjudicator decisions (accepted / rejected + exact reason)
  - validator decisions (tags dropped / normalized)
  - enrichment status
  - final output (what actually reached Google Sheets)

Writing these files must NEVER break the pipeline — callers wrap this in a
try/except and treat any failure as non-fatal.
"""

import json
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_AUDIT_DIR = Path(__file__).resolve().parent.parent / "audit"

_FIELDS = ("geography", "saas_experience", "market_segment")
_FIELD_LABELS = {
    "geography": "Geography",
    "saas_experience": "SaaS Experience",
    "market_segment": "Market Segment",
}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _jsonable(obj):
    """Recursively convert a structure into JSON-serializable primitives.

    recompute_derived_fields() mutates the role_analysis entries in place
    (adding datetime.date objects for parsed start/end dates), so the pass1
    evidence dict can contain non-JSON types by the time we write the report.
    """
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def _safe_name(name: str) -> str:
    """Sanitize a candidate name for use in a filename."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", name or "Candidate").strip("_")
    return (cleaned or "Candidate")[:60]


def _split_tags(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [s.strip() for s in str(value).split(";") if s.strip()]


def _unique_path(stem: str) -> Path:
    """Return a non-colliding path for the given stem (append _2, _3, ...)."""
    path = _AUDIT_DIR / f"{stem}.json"
    counter = 2
    while path.exists():
        path = _AUDIT_DIR / f"{stem}_{counter}.json"
        counter += 1
    return path


def _build_report(
    pass1_data: dict,
    classification_audit: dict,
    pre_enrichment: dict,
    validated_data: dict,
    enrichment_info: dict,
    resume_text: str,
    filename: str,
    source: str,
    timestamp: datetime,
) -> dict:
    """Assemble the full forensic report dict (JSON-serializable)."""
    candidate = (
        classification_audit.get("candidate_name")
        or validated_data.get("full_name")
        or Path(filename).stem
    )

    deterministic_output = {}
    ai_output = {}
    validator_decisions = {}
    for field in _FIELDS:
        fa = classification_audit.get("fields", {}).get(field, {}) or {}
        deterministic_output[field] = {
            "raw_value": fa.get("raw_value"),
            "matches": fa.get("matches", []) or [],
            "rejected": fa.get("rejected", []) or [],
        }

        ai = fa.get("ai") or {}
        ai_output[field] = {
            "proposals": ai.get("proposals", []) or [],
            "confidence": ai.get("confidence"),
        }

        # Validator comparison: merged (pre-validator) vs validated (pre-enrichment)
        merged_tags = _split_tags(ai.get("final_value"))
        validated_tags = _split_tags(pre_enrichment.get(field))
        dropped = [t for t in merged_tags if t not in validated_tags]
        validator_decisions[field] = {
            "pre_validator": ai.get("final_value"),
            "validated": pre_enrichment.get(field),
            "dropped_by_validator": dropped,
        }

    final_output = {
        k: v
        for k, v in validated_data.items()
        if not k.startswith("_")
    }

    return {
        "audit_version": 2,
        "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "candidate": candidate,
        "source": source,
        "filename": filename,
        "resume_text": resume_text,
        "pass1": pass1_data or {},
        "deterministic_output": deterministic_output,
        "ai_output": ai_output,
        "adjudicator_decisions": {
            field: {
                "final_value": (classification_audit.get("fields", {}).get(field, {}) or {}).get("ai", {}).get("final_value"),
                "proposals": (classification_audit.get("fields", {}).get(field, {}) or {}).get("ai", {}).get("proposals", []) or [],
            }
            for field in _FIELDS
        },
        "validator_decisions": validator_decisions,
        "enrichment": enrichment_info or {},
        "final_output": final_output,
    }


def _md_for_field(field: str, report: dict) -> str:
    """Human-readable forensic section for one field."""
    label = _FIELD_LABELS[field]
    det = report["deterministic_output"].get(field, {})
    ai = report["ai_output"].get(field, {})
    adj = report["adjudicator_decisions"].get(field, {})
    val = report["validator_decisions"].get(field, {})
    final = report["final_output"].get(field) or "(blank)"

    lines = [f"# {label}", "", f"**Final:** {final}", ""]

    conf = ai.get("confidence")
    if conf:
        lines.append(f"**Confidence:** {conf}")

    # Enrichment note
    enrich = report.get("enrichment", {}) or {}
    if enrich.get("ran"):
        lines.append(f"**Enrichment:** ran (fills blanks only)")
    else:
        reason = enrich.get("reason") or "not enabled"
        lines.append(f"**Enrichment:** {reason}")

    # Validator
    dropped = val.get("dropped_by_validator") or []
    if dropped:
        lines.append(f"**Validator dropped:** {', '.join(dropped)}")

    lines.append("")

    # Deterministic matches
    matches = det.get("matches", []) or []
    if matches:
        lines.append("## Deterministic rules matched")
        for m in matches[:12]:
            lines.append(f"- `{m.get('tag')}` — \"{m.get('evidence', '')}\"")
            lines.append(f"  - phrase: `{m.get('phrase', '')}` · source: {m.get('source', '')} · match_type: {m.get('match_type', '')}")
            if m.get("title_blocked"):
                lines.append("  - ⚠ blocked: title-only evidence")
        lines.append("")

    # Deterministic rejected rules
    rejected = det.get("rejected", []) or []
    if rejected:
        lines.append("## Deterministic rules that did NOT fire")
        for r in rejected[:14]:
            triggers = ", ".join(str(t) for t in (r.get("triggers") or [])[:4])
            lines.append(f"- {r.get('tag')} — no evidence matched (triggers: {triggers})")
        lines.append("")

    # AI proposals
    proposals = ai.get("proposals", []) or []
    if proposals:
        lines.append("## AI (Pass 2) proposals")
        for p in proposals:
            status = "✅ ACCEPTED" if p.get("decision") == "accepted" else "❌ REJECTED"
            lines.append(f"### {p.get('tag')} — {status} (AI confidence: {p.get('confidence')})")
            for q in p.get("evidence", []):
                lines.append(f"> \"{q}\"")
            lines.append(f"**Reasoning:** {p.get('reasoning', '')}")
            if p.get("reject_reason"):
                lines.append(f"**Rejection reason:** {p.get('reject_reason')}")
            lines.append("")
    else:
        lines.append("## AI (Pass 2) proposals")
        lines.append("None — AI classification disabled, failed, or nothing proposed.")
        lines.append("")

    # Blank explanation
    if not report["final_output"].get(field):
        lines.append("## Why blank")
        if dropped:
            lines.append("All candidate tags were dropped by the validator (not canonical).")
        elif not matches and not proposals:
            lines.append("No evidence matched any deterministic rule and the AI proposed nothing.")
        else:
            lines.append("See the rejected items above — no evidence-backed value survived.")

    lines.append("--------------------------------")
    lines.append("")
    return "\n".join(lines)


def _build_markdown(report: dict) -> str:
    """Render the full Markdown forensic report."""
    lines = [
        "# Forensic Classification Report",
        "",
        f"**Candidate:** {report['candidate']}",
        f"**Timestamp:** {report['timestamp']}",
        f"**Source:** {report['source']}",
        f"**Filename:** {report['filename']}",
        "",
        "---",
        "",
    ]
    for field in _FIELDS:
        lines.append(_md_for_field(field, report))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_audit_report(
    pass1_data: dict,
    classification_audit: dict,
    pre_enrichment: dict,
    validated_data: dict,
    enrichment_info: dict,
    resume_text: str,
    filename: str,
    source: str = "telegram",
) -> str:
    """Write the JSON + Markdown forensic report for one resume.

    Args:
        pass1_data: Raw pass1 evidence dict (attached by extract_fields under
            '_pass1_data').
        classification_audit: Audit dict from classify_candidate_audited()
            (possibly extended by the adjudicator), popped from the result.
        pre_enrichment: Snapshot of the validated three fields BEFORE
            enrichment (resume values).
        validated_data: Final post-enrichment, post-validation candidate dict.
        enrichment_info: _enrichment_info marker from the enrichment pipeline.
        resume_text: Full extracted resume text.
        filename: The original resume filename.
        source: Intake channel (telegram / api / ...).

    Returns:
        Relative audit file path, e.g. "audit/20260815_181221_Snehasish_Das.json",
        or "" if writing failed.
    """
    try:
        timestamp = datetime.now(timezone.utc)
        candidate = (
            classification_audit.get("candidate_name")
            or validated_data.get("full_name")
            or Path(filename).stem
        )
        stem = f"{timestamp.strftime('%Y%m%d_%H%M%S')}_{_safe_name(candidate)}"

        _AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        json_path = _unique_path(stem)
        md_path = json_path.with_suffix(".md")

        report = _build_report(
            pass1_data=pass1_data,
            classification_audit=classification_audit,
            pre_enrichment=pre_enrichment,
            validated_data=validated_data,
            enrichment_info=enrichment_info,
            resume_text=resume_text,
            filename=filename,
            source=source,
            timestamp=timestamp,
        )

        json_path.write_text(
            json.dumps(_jsonable(report), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        md_path.write_text(_build_markdown(report), encoding="utf-8")

        relative = f"audit/{json_path.name}"
        logger.info(
            "Forensic audit written: %s (candidate='%s', %d bytes json)",
            relative,
            candidate,
            json_path.stat().st_size,
        )
        return relative

    except Exception as exc:  # noqa: BLE001 — audit must never break the pipeline
        logger.error("Failed to write forensic audit report (non-fatal): %s", exc)
        return ""
