import logging
import re
from typing import Dict, Any, List, Set, Tuple

logger = logging.getLogger(__name__)

# Tags that must NOT fire from a bare title string only.
# allowed to fire from broader responsibility/evidence text.
_TITLE_ONLY_BLOCKED = frozenset([
    "Account Management",
    "Customer Retention",
    "Upsell/Cross-Sell",
    "Full-Cycle Sales",
    "SaaS Sales",
    "Consultative Selling",
    "B2B",
])


def _is_title_only(evidence_text: str, role_title: str | None) -> bool:
    """Return True if evidence quote effectively represents just the job title."""
    if not role_title:
        return False
    
    # Normalize by removing all non-alphanumeric chars
    e_clean = re.sub(r'[^a-z0-9]', '', evidence_text.lower())
    t_clean = re.sub(r'[^a-z0-9]', '', role_title.lower())
    
    if not e_clean or not t_clean:
        return False
    if e_clean == t_clean:
        return True
    
    # Allow title variants by checking if the base title substring exists inside the role title
    # Example: evidence="Account Manager" (accountmanager) inside title="Senior Account Manager"
    if e_clean in t_clean and len(e_clean) >= 6:
        return True
        
    return False


class RuleDef:
    """A deterministic classification rule for pattern matching."""
    def __init__(self, tag: str, pattern: str, match_type: str = "EXPLICIT_PHRASE"):
        self.tag = tag
        self.pattern = pattern
        self.match_type = match_type
        self.regex = re.compile(pattern, re.IGNORECASE)

    def match(self, text: str) -> str:
        m = self.regex.search(text)
        if m:
            return m.group(0)
        return ""


_GEO_VERBS = (
    r"sold into|sold across|sold to|worked across|managed|managed accounts across|"
    r"covered|responsible for|supported|served|handled|prospected into|prospecting into"
)
_GEO_NOUNS = r"accounts?|market|sales territory|territory|region|customers?|clients?|prospects?"


def _build_geo_pattern(terms: List[str]) -> str:
    """Build a contextual regex for explicit sales-territory evidence.

    Fires only when a region appears with sales-territory context:
      verb + region          sold into India, worked across APAC, supported Middle East
      region + noun          US accounts, North America customers, APAC territory
      noun + in/across       customers in Europe, clients across EMEA
      geographies-like       geographies like the US, Canada, ...; regions including GCC

    Candidate-location statements (Based in India, Lives in Bangalore, Located in
    Chennai, Remote from India) never match — no rule fires on location alone.
    """
    term_group = "|".join(terms)
    return (
        rf"\b(?:{_GEO_VERBS})\s+(?:the\s+)?(?:{term_group})\b"
        rf"|\b(?:{term_group})\s+(?:{_GEO_NOUNS})\b"
        rf"|\b(?:{_GEO_NOUNS})\s+(?:across|in|throughout|within)\s+(?:the\s+)?(?:{term_group})\b"
        rf"|\b(?:geographies?|regions?|territories?|markets?)\s+(?:like|such as|including|across|spanning|covering)\s+(?:the\s+)?.*?(?:{term_group})\b"
    )


SAAS_RULES = [
    RuleDef("Outbound/Prospecting", r"\b(?:cold calls?|cold calling|outbound sales|outbound prospecting|outbound pipeline|prospecting accounts|prospecting enterprise accounts|prospecting customers|prospecting clients|prospected into|prospect into|outbound calls|outbound campaigns|outbound bdr|outbound sdr|outbound outreach|prospective customers|prospective clients|lead generation|lead-gen|account-based prospecting|abm|cold outreach|cold email|cold emails|cold emailing|outbound|prospecting|dial(?:ed|ing)?\s+(?:about|around|approximately|over|up to\s+)?\s*\d[\d,+\-– ]*\s*calls?|(?:made|making|placed|placing)\s+(?:about|around|approximately|over|up to\s+)?\s*\d[\d,+\-– ]*\s*calls?)\b"),
    RuleDef("Inside Sales", r"\b(?:inside sales)\b"),
    RuleDef("Consultative Selling", r"\b(?:discovery calls?|sales discovery|consultative discovery|consultative selling|consultative saas-style selling|consultative sales|consultative approach)\b"),
    RuleDef("Full-Cycle Sales", r"\b(?:full-cycle sales|full cycle sales|full-cycle|full cycle|end-to-end sales cycle)\b"),
    RuleDef("Account Management", r"\b(?:account management|managing accounts|managed accounts|manage accounts|client management|managing clients|account manager|managed enterprise accounts?|manage enterprise accounts?|key accounts?|strategic accounts?)\b"),
    RuleDef("Upsell/Cross-Sell", r"\b(?:upsell|upsold|upselling|cross-sell|cross sell|cross-sold|cross selling|additional products|additional modules|additional services)\b"),
    RuleDef("Customer Retention", r"\b(?:customer retention|client retention|customer-retention|retention of customers|retention of clients|retained customers|retained clients|renewed contracts|renewed customer contracts|contract renewals|renewal rate|customer renewals|reduced customer churn|reduced client churn|reduced churn|client churn|customer churn|managed renewals|improved retention|customer retention rate|improved customer retention|reduced refunds?|reduced refund rate|refund rates?|refund reduction|reducing refunds?|decreased refunds?|lowered refunds?)\b"),
    RuleDef("B2B", r"\b(?:b2b|business to business|business-to-business|corporate clients?|corporate customers?|business clients?|business customers?|b2b clients?|b2b customers?|prospective businesses?|enterprise buyers?)\b"),
    RuleDef("SaaS Sales", r"\b(?:saas|saas-style|software-as-a-service|software as a service|software subscription|software subscriptions)\b"),
    RuleDef("Sales Operations", r"\b(?:sales operations|sales ops|revenue operations|revops)\b"),
    # ── Coverage expansion (ported from the pass2 mapping table + allowlist) ──
    RuleDef("Inbound Sales", r"\b(?:inbound sales|inbound leads?|inbound calls?|inbound inquiries?|inbound prospects?|inbound pipeline|inbound demos?|inbound demo requests?|responding to inbound|handling inbound)\b"),
    RuleDef("Field Sales", r"\b(?:field sales|on-?site sales|door-?to-?door sales?|outside sales|in-?person sales)\b"),
    RuleDef("Channel Sales", r"\b(?:channel sales|channel partners?|channel strategy|channel program|via channels?|through channels?|reseller|resellers|distributor|distributors|indirect sales|sales channels?)\b"),
    RuleDef("Partner Sales", r"\b(?:partner sales|partnership sales|alliance sales|partner-?led sales|via partners?|through partners?)\b"),
    RuleDef("Pre-Sales", r"\b(?:pre-?sales|pre-?sales support|rfp|rfps?|rfq|rfqs?|proof of concept|proofs of concept|product demo|product demos|demo calls?|demo requests?|solution architect|solutions architect)\b"),
    RuleDef("Sales Engineering", r"\b(?:sales engineer|sales engineering|solutions engineer|solutions engineering|technical sales|technical pre-?sales)\b"),
    RuleDef("Funnel Management", r"\b(?:pipeline management|sales pipeline|pipeline review|pipeline reviews|funnel management|funnel analysis|funnel metrics?|forecast|forecasting|deal pipeline|opportunity pipeline|pipeline development|pipeline building|build(?:ing)? (?:a |the )?pipeline)\b"),
    RuleDef("Team Lead", r"\b(?:team lead|team leads?|team leader|leading (?:a |the |my )?team|led (?:a |the |my )?team|managing (?:a |the )?team of|managed (?:a |the )?team of|managing sdrs?|lead(?:ing)? (?:a |the )?team|team management)\b"),
    RuleDef("P&L Ownership", r"\b(?:p&l|profit and loss|profit & loss)\b"),
    RuleDef("BANT", r"\bBANT\b"),
    RuleDef("SPIN", r"\bSPIN\s+(?:selling|sales|methodology|framework|questions?)\b"),
    RuleDef("MEDDIC", r"\bMEDDIC\b"),
    RuleDef("MEDDPICC", r"\bMEDDPICC\b"),
    RuleDef("Challenger Sale", r"\bChallenger\s*(?:Sale|Sales|Methodology|Selling)\b"),
    RuleDef("Solution Selling", r"\b(?:Solution Selling|Solutions Selling|Solution-Based Selling|solution-oriented selling)\b"),
    RuleDef("Value Selling", r"\b(?:Value Selling|Value-Based Selling|value-based sales)\b"),
    RuleDef("Sandler", r"\bSandler\b"),
    RuleDef("B2C", r"\b(?:b2c|business to consumer|business-to-consumer|consumer customers?|consumer sales|consumer market|consumer segment|individual consumers?|end consumers?)\b"),
    RuleDef("B2B2C", r"\b(?:b2b2c|b2b and b2c|b2b & b2c|b2b/b2c|business-to-business-to-consumer)\b"),
    RuleDef("Transactional", r"\b(?:transactional sales?|transactional selling|high-?volume sales?|high-?velocity sales?|self-?serve sales?|self-?serve motion|low-?ticket sales?|short sales? cycle)\b"),
    RuleDef("Enterprise Sales Cycle", r"\b(?:enterprise sales cycle|long sales cycle|long sales cycles|complex sales?|complex selling|enterprise-?level deals?|multi-?stakeholder sales?|large-?ticket deals?|six-?figure deals?)\b"),
    RuleDef("PLG", r"\b(?:product-?led growth|plg|self-?serve funnel|bottom-?up adoption|land and expand)\b"),
]

# SME customer-context terms: only fire when followed by business/client words.
# This prevents 'Subject Matter Expert', 'technical SME', 'acted as SME' from matching.
_SME_CUSTOMER_PATTERN = (
    r"\b(?:"
    r"sme(?:\s+(?:customers?|clients?|accounts?|segment|market|businesses|companies|firms|sector|space|landscape))"
    r"|selling to smes?"
    r"|selling into smes?"
    r"|worked with sme businesses"
    r"|sme base"
    r")\b"
)

SEGMENT_RULES = [
    # Fortune 500 first — provides precise provenance log before Enterprise also fires.
    RuleDef("Enterprise", r"\b(?:fortune\s*(?:500|100)(?:\s+(?:customers?|clients?|accounts?|companies?|firms?))?)\b", "EXPLICIT_FORTUNE500"),
    RuleDef("Enterprise", r"\b(?:enterprise accounts?|enterprise customers?|enterprise clients?|enterprise prospects?|enterprise sales|enterprise segment|enterprise market|mid-market and enterprise|smb and enterprise|strategic accounts?|key accounts?|enterprise tier)\b"),
    RuleDef("Mid-Market", r"\b(?:mid-market accounts?|mid-market customers?|mid-market clients?|mid-market|mid market|midmarket)\b"),
    RuleDef("SMB", r"\b(?:smb|smbs|small business|small businesses|small and medium businesses|small and medium-sized businesses|smb customers?|smb accounts?|smb segment)\b"),
    RuleDef("SME", _SME_CUSTOMER_PATTERN),
    RuleDef("B2C", r"\b(?:b2c|business to consumer|business-to-consumer|consumer customers?|consumer sales|consumer segment|consumer app|consumer application|consumer market|individual consumers?|end consumers?|students? and (?:their )?parents?|parents? and (?:their )?students?|potential students?|prospective students?|individual learners?|reach(?:ed|ing)? out to (?:potential |prospective )?students?|engag(?:ing|ed)? with (?:students?|learners?)|selling to (?:individual )?learners?|enroll(?:ing|ed)? (?:students?|learners?))\b"),
    RuleDef("D2C", r"\b(?:d2c|direct-to-consumer|direct to consumer|d2c customers?|d2c brands?|d2c business|d2c sales|d2c segment)\b"),
    RuleDef("B2B2C", r"\b(?:b2b2c|b2b and b2c|b2b & b2c|b2b/b2c|business-to-business-to-consumer)\b"),
]

GEO_RULES = [
    RuleDef("EMEA", _build_geo_pattern(["emea"]), "CONTEXTUAL_PHRASE"),
    RuleDef("APAC", _build_geo_pattern(["apac", "asia pacific", "asia-pacific", "asia", "singapore"]), "CONTEXTUAL_PHRASE"),
    RuleDef("India", _build_geo_pattern(["india", "indian", "domestic", "domestic market"]) + r"|\bdomestic (?:and|&|as well as) international\b", "CONTEXTUAL_PHRASE"),
    # Emit "NA" (canonical tag) so validator accepts it directly without alias lookup.
    RuleDef("NA", _build_geo_pattern(["north america", "usa", "united states", "us market", "us markets", "us"]), "CONTEXTUAL_PHRASE"),
    RuleDef("LATAM", _build_geo_pattern(["latam", "latin america"]), "CONTEXTUAL_PHRASE"),
    RuleDef("UKI", _build_geo_pattern(["uki", "uk", "united kingdom", "london"]), "CONTEXTUAL_PHRASE"),
    RuleDef("DACH", _build_geo_pattern(["dach", "germany", "austria", "switzerland"]), "CONTEXTUAL_PHRASE"),
    # ── Coverage expansion: remaining canonical regions ───────────────────────
    RuleDef("MEA", _build_geo_pattern(["mea", "middle east", "mena", "africa", "egypt", "uae", "saudi arabia", "dubai"]), "CONTEXTUAL_PHRASE"),
    RuleDef("GCC", _build_geo_pattern(["gcc", "gulf", "gulf region", "gcc countries"]), "CONTEXTUAL_PHRASE"),
    RuleDef("SEA", _build_geo_pattern(["southeast asia", "south east asia", "south-east asia", "se asia", "se-asia"]), "CONTEXTUAL_PHRASE"),
    RuleDef("ASEAN", _build_geo_pattern(["asean", "asean region"]), "CONTEXTUAL_PHRASE"),
    RuleDef("EU", _build_geo_pattern(["europe", "eu", "european"]), "CONTEXTUAL_PHRASE"),
    RuleDef("Global", _build_geo_pattern(["global", "worldwide", "international"]) + r"|\bdomestic (?:and|&|as well as) international\b|\bmultiple regions?\b|\bmultiple markets?\b", "CONTEXTUAL_PHRASE"),
    RuleDef("ANZ", _build_geo_pattern(["anz", "australia", "new zealand", "nz"]), "CONTEXTUAL_PHRASE"),
    RuleDef("Nordics", _build_geo_pattern(["nordics", "nordic", "scandinavia", "scandinavian", "denmark", "sweden", "norway", "finland"]), "CONTEXTUAL_PHRASE"),
    RuleDef("Benelux", _build_geo_pattern(["benelux", "belgium", "netherlands", "luxembourg"]), "CONTEXTUAL_PHRASE"),
    RuleDef("CEE", _build_geo_pattern(["cee", "central and eastern europe", "poland", "czech republic", "hungary", "romania"]), "CONTEXTUAL_PHRASE"),
    RuleDef("Iberia", _build_geo_pattern(["iberia", "spain", "portugal"]), "CONTEXTUAL_PHRASE"),
    RuleDef("CIS", _build_geo_pattern(["cis", "russia", "ukraine", "kazakhstan"]), "CONTEXTUAL_PHRASE"),
    RuleDef("APJ", _build_geo_pattern(["apj", "japan", "south korea", "korea"]), "CONTEXTUAL_PHRASE"),
    RuleDef("ROW", _build_geo_pattern(["rest of the world", "rest of world", "row market", "row territory"]), "CONTEXTUAL_PHRASE"),
]


def _build_evidence_stream(pass1_data: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Build a unified list of (evidence_text, source_label) tuples.

    Sources:
      document:<SECTION>  — from document_evidence items
      role:<employer>     — from role_analysis[].evidence_quotes
    """
    stream: List[Tuple[str, str]] = []

    doc_evidence = pass1_data.get("document_evidence", [])
    if isinstance(doc_evidence, list):
        for item in doc_evidence:
            if isinstance(item, dict):
                text = item.get("text", "")
                section = item.get("source_section", "DOCUMENT")
                if text:
                    stream.append((str(text), f"document:{section}"))
            elif isinstance(item, str) and item:
                stream.append((item, "document:UNKNOWN"))

    roles = pass1_data.get("role_analysis", [])
    for role in roles:
        employer = role.get("employer") or "unknown_employer"
        source_label = f"role:{employer}"
        quotes = role.get("evidence_quotes", [])
        if not isinstance(quotes, list):
            continue
        for q in quotes:
            if isinstance(q, str) and q:
                stream.append((q, source_label))
            elif isinstance(q, dict):
                text = q.get("text", "")
                if text:
                    stream.append((str(text), source_label))

    return stream


def _rule_triggers(rule: RuleDef) -> List[str]:
    """Derive human-readable trigger phrases from a rule's regex pattern.

    Used only for the audit trail display — the actual matching still uses the
    raw compiled regex. Approximate by design; perfect regex parsing is not
    the goal here.
    """
    cleaned = rule.pattern
    cleaned = cleaned.replace(r"\b", "")
    cleaned = cleaned.replace(r"(?:", "")
    cleaned = cleaned.replace(")", "")
    cleaned = cleaned.replace(r"\s+", " ")
    cleaned = cleaned.replace(r"\.", ".")
    cleaned = cleaned.replace(".", "")
    cleaned = cleaned.replace(r"\-", "-")
    cleaned = cleaned.replace("?", "")
    cleaned = cleaned.replace("*", "")
    cleaned = cleaned.replace("+", "")
    cleaned = cleaned.replace("^", "")
    cleaned = cleaned.replace("$", "")
    parts = [p.strip() for p in cleaned.split("|") if p.strip()]
    seen: Set[str] = set()
    out: List[str] = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out[:14]


def _ordered_tags(final_tags: Set[str], rules: List[RuleDef]) -> List[str]:
    """Return fired tags in deterministic rule order (deduplicated).

    Sets have arbitrary iteration order, so listing the set directly would make
    multi-tag output non-deterministic across processes (hash randomization).
    Rule order keeps the final values stable and matches the taxonomy layout.
    """
    out: List[str] = []
    for rule in rules:
        if rule.tag in final_tags and rule.tag not in out:
            out.append(rule.tag)
    return out


def classify_candidate_audited(pass1_data: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Classify a candidate and return (final_answer, audit_trail).

    Classification logic is identical to classify_candidate(). The audit trail
    additionally records, per field, every rule that fired (with verbatim
    evidence, source location, matched phrase, and match type) plus every
    rule that was rejected. The audit is read-only debugging data — it never
    influences the classification result.
    """
    final_answer: Dict[str, Any] = {}

    meta = pass1_data.get("candidate_metadata", {})
    final_answer["full_name"] = meta.get("full_name")
    final_answer["email"] = meta.get("email")
    final_answer["phone_number"] = meta.get("phone_number")
    final_answer["linkedin_url"] = meta.get("linkedin_url")
    final_answer["college"] = meta.get("college")

    final_answer["years_of_experience"] = None
    final_answer["current_company"] = None
    final_answer["past_companies"] = None

    saas_tags: Set[str] = set()
    segment_tags: Set[str] = set()
    geo_tags: Set[str] = set()

    audit: Dict[str, Any] = {
        "candidate_name": meta.get("full_name"),
        "fields": {
            "geography": {"matches": [], "rejected": []},
            "saas_experience": {"matches": [], "rejected": []},
            "market_segment": {"matches": [], "rejected": []},
        },
    }

    evidence_stream = _build_evidence_stream(pass1_data)
    roles = pass1_data.get("role_analysis", [])

    def _record(
        field_key: str,
        rule: RuleDef,
        matched_phrase: str,
        evidence_text: str,
        source: str,
        is_title: bool,
    ) -> None:
        """Append one match record to the audit trail for a field."""
        blocked = is_title and rule.tag in _TITLE_ONLY_BLOCKED
        audit["fields"][field_key]["matches"].append({
            "tag": rule.tag,
            "phrase": matched_phrase,
            "evidence": evidence_text,
            "source": source,
            "match_type": (
                "Contextual" if rule.match_type == "CONTEXTUAL_PHRASE" else "Explicit"
            ),
            "title_blocked": blocked,
            "triggers": _rule_triggers(rule),
        })

    for (evidence_text, source) in evidence_stream:
        # Determine whether this item is a bare role title
        is_title = False
        for role in roles:
            if _is_title_only(evidence_text, role.get("role_title")):
                is_title = True
                break

        # ── SaaS experience ──────────────────────────────────────────────
        for rule in SAAS_RULES:
            matched_phrase = rule.match(evidence_text)
            if matched_phrase:
                _record("saas_experience", rule, matched_phrase, evidence_text, source, is_title)
                if is_title and rule.tag in _TITLE_ONLY_BLOCKED:
                    continue
                if rule.tag not in saas_tags:
                    logger.info(
                        "[CLASSIFIER] Field: saas_experience | Assigned: '%s' | "
                        "Rule: '%s' | MatchType: %s | Source: %s | Evidence: '%s'",
                        rule.tag, matched_phrase, rule.match_type, source, evidence_text
                    )
                saas_tags.add(rule.tag)

        # ── Market segment ───────────────────────────────────────────────
        for rule in SEGMENT_RULES:
            matched_phrase = rule.match(evidence_text)
            if matched_phrase:
                _record("market_segment", rule, matched_phrase, evidence_text, source, is_title)
                if rule.tag not in segment_tags:
                    logger.info(
                        "[CLASSIFIER] Field: market_segment | Assigned: '%s' | "
                        "Rule: '%s' | MatchType: %s | Source: %s | Evidence: '%s'",
                        rule.tag, matched_phrase, rule.match_type, source, evidence_text
                    )
                segment_tags.add(rule.tag)

        # ── Geography ────────────────────────────────────────────────────
        for rule in GEO_RULES:
            matched_phrase = rule.match(evidence_text)
            if matched_phrase:
                _record("geography", rule, matched_phrase, evidence_text, source, is_title)
                if rule.tag not in geo_tags:
                    logger.info(
                        "[CLASSIFIER] Field: geography | Assigned: '%s' | "
                        "Rule: '%s' | MatchType: %s | Source: %s | Evidence: '%s'",
                        rule.tag, matched_phrase, rule.match_type, source, evidence_text
                    )
                geo_tags.add(rule.tag)

    # Record which rules did NOT fire — the "why other values were rejected" data
    fired_tags = {
        "saas_experience": saas_tags,
        "market_segment": segment_tags,
        "geography": geo_tags,
    }
    for field_key, rules in (
        ("saas_experience", SAAS_RULES),
        ("market_segment", SEGMENT_RULES),
        ("geography", GEO_RULES),
    ):
        for rule in rules:
            if rule.tag not in fired_tags[field_key]:
                audit["fields"][field_key]["rejected"].append({
                    "tag": rule.tag,
                    "triggers": _rule_triggers(rule),
                })

    final_answer["saas_experience"] = _ordered_tags(saas_tags, SAAS_RULES) or None
    final_answer["market_segment"] = _ordered_tags(segment_tags, SEGMENT_RULES) or None
    final_answer["geography"] = _ordered_tags(geo_tags, GEO_RULES) or None

    for field_key in audit["fields"]:
        audit["fields"][field_key]["raw_value"] = final_answer[field_key]

    return final_answer, audit


def classify_candidate(pass1_data: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate evidence quotes deterministically to assert taxonomy tags.

    Consumes both document_evidence and role_analysis[].evidence_quotes via
    a unified internal evidence stream. Returns only the final_answer dict —
    use classify_candidate_audited() when the audit trail is also needed.
    """
    final_answer, _ = classify_candidate_audited(pass1_data)
    return final_answer
