"""PDF text extraction using pdfplumber.

Responsibility: extract raw text from a PDF file — no cleaning,
inferring, normalizing, or classifying. Output is always str.

Includes a post-extraction reassembly pass for "drop-cap" PDF templates
where decorative first-letters are stored as separate text runs, causing
pdfplumber to split e.g. "Afifa Farheen" into two lines:
    A F
    FIFA ARHEEN
The _reassemble_dropcap_lines() helper detects and merges these.
"""

import logging
import re
from pathlib import Path

import pdfplumber

from core.exceptions import ParsingError, FileValidationError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Drop-cap reassembly helper
# ---------------------------------------------------------------------------

def _reassemble_dropcap_lines(raw_text: str) -> str:
    """Detect and merge drop-cap header lines in extracted PDF text.

    Some PDF templates style the first letter of each word as a large
    decorative capital, which pdfplumber extracts as a separate line of
    single letters:

        A F                      →  Afifa Farheen
        FIFA ARHEEN

    Detection rules (all must be true for a merge to happen):
      1. The candidate "cap line" contains only single uppercase ASCII
         letters separated by spaces, with 1–8 letters total.
      2. A non-empty "body line" immediately follows.
      3. The number of cap letters equals the number of words on the
         body line.
      4. Each body-line word starts with an uppercase letter (consistent
         with the pattern where the remainder is uppercase-initial).

    If any check fails, both lines are left untouched. This ensures we
    never accidentally merge two unrelated short lines.

    Args:
        raw_text: The raw extracted text from pdfplumber.

    Returns:
        Text with drop-cap lines merged. Unaffected lines are unchanged.
    """
    lines = raw_text.split("\n")
    result: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # --- Check if this line looks like a drop-cap line ---
        # Must be: 1-8 single uppercase ASCII letters separated by spaces
        # e.g. "A F", "S D R", "P S", "E"
        stripped = line.strip()
        if stripped and i + 1 < len(lines):
            tokens = stripped.split()
            is_dropcap_candidate = (
                1 <= len(tokens) <= 8
                and all(len(t) == 1 and t.isascii() and t.isupper() for t in tokens)
            )

            if is_dropcap_candidate:
                body_line = lines[i + 1].strip()
                body_words = body_line.split() if body_line else []

                # Confirmation: word counts must match, and each body word
                # must start with an uppercase letter (the "remainder" after
                # the drop cap was removed by the PDF renderer).
                if (
                    body_words
                    and len(tokens) == len(body_words)
                    and all(w[0].isupper() for w in body_words)
                ):
                    # Merge: prepend each cap letter to its corresponding
                    # body word. The remainder is typically ALL-CAPS because
                    # the PDF template renders the full word in uppercase
                    # decorative styling. Title-casing produces the correct
                    # human-readable form: "A" + "FIFA" → "Afifa".
                    merged_words = []
                    for cap, remainder in zip(tokens, body_words):
                        merged_words.append(cap + remainder.lower())

                    merged_line = " ".join(merged_words)
                    logger.debug(
                        "Drop-cap reassembly: '%s' + '%s' → '%s'",
                        stripped, body_line, merged_line,
                    )
                    result.append(merged_line)
                    i += 2  # skip both the cap line and the body line
                    continue

        result.append(line)
        i += 1

    return "\n".join(result)


def extract_text_from_pdf(file_path: Path) -> str:
    """Extract raw text content from a PDF file.

    Iterates over every page and concatenates the text. Detects
    password-protected and corrupted PDFs and raises appropriate
    exceptions.

    After extraction, runs a drop-cap reassembly pass to fix
    decorative-header templates that split first letters onto
    separate lines.

    Args:
        file_path: Path to the PDF file.

    Returns:
        Concatenated text from all pages. May contain extra whitespace
        — that's fine, the AI prompt handles messy formatting.

    Raises:
        FileValidationError: If the PDF is password-protected.
        ParsingError: If text extraction fails for any other reason.
    """
    filename = file_path.name
    logger.info("Extracting text from PDF: %s", filename)

    try:
        with pdfplumber.open(file_path) as pdf:
            pages_text: list[str] = []

            for i, page in enumerate(pdf.pages):
                text = page.extract_text()
                if text:
                    pages_text.append(text)
                else:
                    logger.debug(
                        "Page %d of '%s' returned no text (may be image-based)",
                        i + 1,
                        filename,
                    )

            full_text = "\n".join(pages_text).strip()

    except Exception as exc:
        error_msg = str(exc).lower()

        # pdfplumber / pdfminer raise various exceptions for encrypted PDFs
        if "password" in error_msg or "encrypted" in error_msg:
            logger.warning("Password-protected PDF detected: %s", filename)
            raise FileValidationError(
                filename, "Password-protected PDF"
            ) from exc

        logger.error("PDF parsing failed for '%s': %s", filename, exc)
        raise ParsingError(filename, str(exc)) from exc

    if not full_text:
        logger.warning("PDF '%s' produced no extractable text", filename)
        raise ParsingError(
            filename,
            "No extractable text — file may be image-based or empty",
        )

    # --- Drop-cap reassembly pass ---
    full_text = _reassemble_dropcap_lines(full_text)

    logger.info(
        "Extracted %d characters from %d pages of '%s'",
        len(full_text),
        len(pages_text),
        filename,
    )
    return full_text
