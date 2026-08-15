"""DOCX text extraction using python-docx.

Responsibility: extract raw text from a DOCX file — no cleaning,
inferring, normalizing, or classifying. Output is always str.
"""

import logging
from pathlib import Path

from docx import Document
from docx.opc.exceptions import PackageNotFoundError

from core.exceptions import ParsingError, FileValidationError

logger = logging.getLogger(__name__)


def extract_text_from_docx(file_path: Path) -> str:
    """Extract raw text content from a DOCX file.

    Reads all paragraphs and concatenates them. Detects password-
    protected (encrypted) and corrupted DOCX files.

    Args:
        file_path: Path to the DOCX file.

    Returns:
        Concatenated paragraph text from the document.

    Raises:
        FileValidationError: If the DOCX is password-protected or corrupted.
        ParsingError: If text extraction fails for any other reason.
    """
    filename = file_path.name
    logger.info("Extracting text from DOCX: %s", filename)

    try:
        doc = Document(file_path)
        paragraphs_text: list[str] = [
            para.text for para in doc.paragraphs if para.text.strip()
        ]
        full_text = "\n".join(paragraphs_text).strip()

    except PackageNotFoundError as exc:
        logger.error("DOCX file is corrupt or not a valid DOCX: %s", filename)
        raise FileValidationError(
            filename, "Corrupt or invalid DOCX file"
        ) from exc

    except Exception as exc:
        error_msg = str(exc).lower()

        # python-docx / zipfile raise errors for encrypted Office files
        if any(
            keyword in error_msg
            for keyword in ("password", "encrypted", "bad zipfile", "not a zip")
        ):
            logger.warning(
                "Password-protected or corrupt DOCX detected: %s", filename
            )
            raise FileValidationError(
                filename, "Password-protected or corrupt DOCX file"
            ) from exc

        logger.error("DOCX parsing failed for '%s': %s", filename, exc)
        raise ParsingError(filename, str(exc)) from exc

    if not full_text:
        logger.warning("DOCX '%s' produced no extractable text", filename)
        raise ParsingError(
            filename, "No extractable text — file may be empty"
        )

    logger.info(
        "Extracted %d characters from '%s'",
        len(full_text),
        filename,
    )
    return full_text
