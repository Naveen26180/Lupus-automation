"""Unit tests for pdf_parser._reassemble_dropcap_lines().

Covers the drop-cap reassembly logic added in July 2026 to fix
stylized PDF templates that split decorative first-letters into
separate text lines (e.g. "A F" + "FIFA ARHEEN" → "Afifa Farheen").
"""

import sys
from pathlib import Path

# Ensure the project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from integrations.parsers.pdf_parser import _reassemble_dropcap_lines


# ---------------------------------------------------------------------------
# Positive cases — should merge
# ---------------------------------------------------------------------------

def test_two_word_dropcap_basic():
    """Classic case: 'A F' + 'FIFA ARHEEN' → 'Afifa Farheen'."""
    raw = "A F\nFIFA ARHEEN\nSome other content"
    result = _reassemble_dropcap_lines(raw)
    assert "Afifa Farheen" in result
    assert "A F" not in result
    assert "FIFA ARHEEN" not in result


def test_single_letter_dropcap():
    """Single drop-cap letter: 'R' + 'AHUL' → 'Rahul'."""
    raw = "R\nAHUL\nSoftware Engineer"
    result = _reassemble_dropcap_lines(raw)
    assert "Rahul" in result
    assert result.count("R\n") == 0


def test_three_word_dropcap():
    """Three-word name: 'S K M' + 'ANJEET UMAR EHTA' → 'Sanjeet Kumar Mehta'."""
    raw = "S K M\nANJEET UMAR EHTA\nExperience"
    result = _reassemble_dropcap_lines(raw)
    assert "Sanjeet Kumar Mehta" in result


def test_dropcap_at_start_of_document():
    """Drop-cap is the very first line of the extracted text."""
    raw = "P S\nRIYA HARMA\nBangalore | priya@email.com"
    result = _reassemble_dropcap_lines(raw)
    assert "Priya Sharma" in result
    lines = result.split("\n")
    assert lines[0] == "Priya Sharma"


def test_surrounding_content_preserved():
    """Lines before and after the drop-cap block are untouched."""
    raw = "Resume\nA F\nFIFA ARHEEN\nBangalore"
    result = _reassemble_dropcap_lines(raw)
    lines = result.split("\n")
    assert lines[0] == "Resume"
    assert lines[1] == "Afifa Farheen"
    assert lines[2] == "Bangalore"


# ---------------------------------------------------------------------------
# Negative cases — should NOT merge (leave lines untouched)
# ---------------------------------------------------------------------------

def test_word_count_mismatch_no_merge():
    """Cap line has 2 letters but body has 3 words — no merge."""
    raw = "A B\nFIRST SECOND THIRD\nContent"
    result = _reassemble_dropcap_lines(raw)
    assert "A B" in result
    assert "FIRST SECOND THIRD" in result


def test_lowercase_body_no_merge():
    """Body line words start with lowercase — not a drop-cap pattern."""
    raw = "A B\nfirst second\nContent"
    result = _reassemble_dropcap_lines(raw)
    assert "A B" in result
    assert "first second" in result


def test_multi_char_tokens_not_dropcap():
    """Line contains multi-character tokens — not a drop-cap line."""
    raw = "AB CD\nSOME THING\nContent"
    result = _reassemble_dropcap_lines(raw)
    assert "AB CD" in result
    assert "SOME THING" in result


def test_lowercase_caps_not_dropcap():
    """Lowercase single letters are not drop-caps (must be uppercase)."""
    raw = "a b\nFIRST SECOND\nContent"
    result = _reassemble_dropcap_lines(raw)
    assert "a b" in result


def test_nine_tokens_exceeds_limit_no_merge():
    """Cap line with 9 tokens exceeds the 1-8 limit — no merge."""
    caps = " ".join("ABCDEFGHI")  # 9 single uppercase letters
    raw = f"{caps}\n{'WORD ' * 9}\nContent"
    result = _reassemble_dropcap_lines(raw)
    assert caps in result


def test_empty_body_line_no_merge():
    """Drop-cap candidate followed by empty line — no merge."""
    raw = "A B\n\nSome content"
    result = _reassemble_dropcap_lines(raw)
    assert "A B" in result


def test_last_line_is_dropcap_candidate_no_crash():
    """Drop-cap candidate at the very last line — no next line to merge with, no crash."""
    raw = "Some content\nA B"
    result = _reassemble_dropcap_lines(raw)
    assert "A B" in result   # untouched
    assert "Some content" in result


def test_normal_resume_text_unchanged():
    """Typical resume body text must pass through completely unchanged."""
    raw = (
        "John Smith\n"
        "john.smith@email.com | +91 9876543210\n"
        "Senior Sales Executive\n"
        "Salesforce | Jan 2022 - Present\n"
        "- Managed enterprise accounts across APAC\n"
        "Education\n"
        "B.Com | Mumbai University | 2018-2021"
    )
    result = _reassemble_dropcap_lines(raw)
    assert result == raw


def test_empty_string_no_crash():
    """Empty input returns empty string without error."""
    assert _reassemble_dropcap_lines("") == ""


def test_single_line_no_crash():
    """Single-line input is returned unchanged."""
    assert _reassemble_dropcap_lines("Hello") == "Hello"
