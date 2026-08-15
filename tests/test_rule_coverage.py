"""Phase 5 — Rule coverage test.

Every canonical value the validator allows must be producible by at least one
deterministic classifier rule. If a tag exists in the validator allowlist but
no rule can ever produce it, the test fails — this prevents dead taxonomy
values from silently accumulating.

Intentionally-unsupported values (if any) must be listed explicitly here so a
product decision is recorded, not just a gap ignored.
"""

from core.classifier import SAAS_RULES, SEGMENT_RULES, GEO_RULES
from core.validator import _GEO_TAGS, _SAAS_EXP_ALLOWED, _SEGMENT_ALLOWED


def _rule_tags(rules):
    return {rule.tag for rule in rules}


def test_every_saas_tag_has_a_classifier_rule():
    unreachable = sorted(_SAAS_EXP_ALLOWED - _rule_tags(SAAS_RULES))
    assert not unreachable, (
        f"SaaS allowlist tags with NO classifier rule (dead values): {unreachable}"
    )


def test_every_segment_tag_has_a_classifier_rule():
    unreachable = sorted(_SEGMENT_ALLOWED - _rule_tags(SEGMENT_RULES))
    assert not unreachable, (
        f"Market Segment allowlist tags with NO classifier rule (dead values): {unreachable}"
    )


def test_every_geography_tag_has_a_classifier_rule():
    unreachable = sorted(_GEO_TAGS - _rule_tags(GEO_RULES))
    assert not unreachable, (
        f"Geography canonical tags with NO classifier rule (dead values): {unreachable}"
    )


def test_no_duplicate_tags_in_saas_rules():
    """Duplicate tags are allowed for provenance (e.g. two Enterprise rules),
    but a duplicate must still be intentional — every rule must be reachable."""
    tags = [r.tag for r in SAAS_RULES]
    dups = {t for t in tags if tags.count(t) > 1}
    assert not dups, f"Unexpected duplicate SaaS rule tags: {dups}"
