import io

from app.core.errors import ParseFailed
from app.models.fulltext import FullTextSection
from app.utils.text import clean_text


def parse_pdf(content: bytes) -> tuple[dict, list[FullTextSection]]:
    if not content.startswith(b"%PDF"):
        raise ParseFailed("Content does not have a PDF header")
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ParseFailed("pypdf is required for PDF parsing") from exc
    try:
        reader = PdfReader(io.BytesIO(content))
        text = clean_text("\n".join(page.extract_text() or "" for page in reader.pages)) or ""
    except Exception as exc:
        raise ParseFailed(f"Could not parse PDF: {exc}") from exc
    if not text:
        raise ParseFailed("PDF contained no extractable text")
    return {}, [FullTextSection(section_type="other", heading="Full text", text=text)]
