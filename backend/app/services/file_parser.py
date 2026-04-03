import base64
from pathlib import Path

from docx import Document
from pypdf import PdfReader


ALLOWED_TEXT_EXT = {".txt", ".md", ".pdf", ".docx"}


def parse_text_file(file_path: str) -> str:
    ext = Path(file_path).suffix.lower()
    if ext in {".txt", ".md"}:
        return Path(file_path).read_text(encoding="utf-8", errors="ignore")

    if ext == ".pdf":
        reader = PdfReader(file_path)
        return "\n".join((page.extract_text() or "") for page in reader.pages)

    if ext == ".docx":
        doc = Document(file_path)
        return "\n".join(p.text for p in doc.paragraphs)

    raise ValueError(f"Unsupported text file type: {ext}")


def to_base64(data: bytes) -> str:
    return base64.b64encode(data).decode("utf-8")
