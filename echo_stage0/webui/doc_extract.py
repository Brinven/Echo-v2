"""
Document extraction for the chat lane (2026-07-18).

A document attached on /chat is turned into PLAIN TEXT here on the web thread (CPU,
touches nothing shared) before it rides the turn — the pipeline and the model only ever
see text. Mirrors remote_audio's philosophy: fail-soft (None, never an exception into
Flask), size-capped, and a bad document degrades to a text-only turn — an attachment
must never cost Michael the question he just typed.

Formats: plain text (.txt/.md/.csv/.log/.json + friends), PDF (pypdf), Word (python-docx).
Both PDF/Word deps are pure-Python installs — torch untouched (verified at install).
"""

import io
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Route-level guard on the upload itself (a minute of prose is ~KB; 8 MB is a runaway).
DOC_MAX_BYTES = 8 * 1024 * 1024
# Cap on the EXTRACTED text that rides the prompt. The 12B's context is large, but a
# whole book in every follow-up turn is prefill nobody asked for. ~24k chars ≈ 6k tokens.
DOC_MAX_CHARS = 24_000

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".log", ".json", ".yaml", ".yml",
    ".ini", ".cfg", ".toml", ".xml", ".html", ".htm", ".py", ".js", ".ts", ".sh",
    ".bat", ".ps1", ".sql",
}


def _truncate(text: str) -> str:
    text = text.strip()
    if len(text) <= DOC_MAX_CHARS:
        return text
    return text[:DOC_MAX_CHARS] + "\n[… document truncated here — the rest was too long to carry]"


def _from_pdf(data: bytes) -> str | None:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        pages = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")
            # Enough text already? Stop reading a 900-page PDF for nothing.
            if sum(len(p) for p in pages) > DOC_MAX_CHARS * 2:
                break
        text = "\n".join(pages).strip()
        return text or None
    except Exception as e:
        logger.warning(f"pdf extract failed: {e}")
        return None


def _from_docx(data: bytes) -> str | None:
    try:
        import docx
        d = docx.Document(io.BytesIO(data))
        text = "\n".join(p.text for p in d.paragraphs).strip()
        return text or None
    except Exception as e:
        logger.warning(f"docx extract failed: {e}")
        return None


def extract_doc(data: bytes, filename: str) -> str | None:
    """Extracted, capped text for a document — or None if it can't be read.

    Format is decided by extension first, magic bytes as the tiebreak (a .txt that is
    secretly a PDF still extracts). Encrypted/scanned/image-only PDFs come back None —
    the route reports that as `doc_dropped: unreadable` rather than guessing.
    """
    if not data:
        return None
    ext = Path(filename or "").suffix.lower()
    is_pdf = data[:5] == b"%PDF-"
    is_zip = data[:2] == b"PK"   # docx is a zip container

    text = None
    if ext == ".pdf" or is_pdf:
        text = _from_pdf(data)
    elif ext == ".docx" or (is_zip and ext in ("", ".doc", ".docx")):
        text = _from_docx(data)
    elif ext in TEXT_EXTENSIONS:
        try:
            decoded = data.decode("utf-8", errors="replace")
            if "\x00" not in decoded:
                text = decoded.strip() or None
        except Exception:
            text = None
    return _truncate(text) if text else None
