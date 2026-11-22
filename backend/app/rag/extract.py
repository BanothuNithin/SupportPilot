import io

from pypdf import PdfReader

ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf"}


class IngestError(Exception):
    """A document could not be turned into text."""


def extract_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(data))
            text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as e:
            raise IngestError(f"Could not read the PDF: {e}") from e
        if not text.strip():
            raise IngestError("No extractable text in the PDF (scanned image?).")
        return text
    return data.decode("utf-8", errors="replace")
