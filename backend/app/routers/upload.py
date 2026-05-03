import logging
import os
import uuid
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, File, HTTPException, UploadFile

from backend.app.config import get_settings
from backend.app.services.file_parser import ALLOWED_TEXT_EXT, parse_text_file, to_base64

router = APIRouter(prefix="/api/upload", tags=["upload"])
logger = logging.getLogger(__name__)

UPLOAD_DIR = Path(__file__).resolve().parents[3] / "uploads" / "backgrounds"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

_ALLOWED_IMAGE_MIME = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

def _safe_filename(filename: str | None) -> str:
    base = Path(filename or "").name
    return base[:200] if base else "upload"

def _validate_image_signature(mime: str, raw: bytes) -> bool:
    if mime == "image/png":
        return raw.startswith(bytes.fromhex("89504E470D0A1A0A"))
    if mime == "image/jpeg":
        return raw[:3] == bytes.fromhex("FFD8FF")
    if mime == "image/gif":
        return raw.startswith(b"GIF87a") or raw.startswith(b"GIF89a")
    if mime == "image/webp":
        return len(raw) >= 12 and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"
    return False

@router.post("/file")
async def upload_file(file: UploadFile = File(...)):
    settings = get_settings()
    filename = _safe_filename(file.filename)
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_TEXT_EXT:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")

    raw = await file.read()
    if len(raw) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large")

    with NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(raw)
        temp_path = tmp.name

    try:
        text = parse_text_file(temp_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    logger.info("upload_file_ok filename=%s bytes=%s ext=%s", filename, len(raw), ext)
    return {
        "filename": filename,
        "type": "text_context",
        "extracted_text": text[:20000],
        "truncated": len(text) > 20000,
    }

@router.post("/image")
async def upload_image(file: UploadFile = File(...)):
    settings = get_settings()
    filename = _safe_filename(file.filename)
    mime = (file.content_type or "").lower()
    ext = Path(filename).suffix.lower()

    if mime not in _ALLOWED_IMAGE_MIME:
        raise HTTPException(status_code=400, detail=f"Unsupported image type: {mime}")
    if ext and ext not in _ALLOWED_IMAGE_EXT:
        raise HTTPException(status_code=400, detail=f"Unsupported image extension: {ext}")

    raw = await file.read()
    if len(raw) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image too large")
    if not _validate_image_signature(mime, raw):
        raise HTTPException(status_code=400, detail="Invalid image binary signature")

    logger.info("upload_image_ok filename=%s bytes=%s mime=%s", filename, len(raw), mime)
    return {
        "filename": filename or "pasted-image",
        "mime_type": mime,
        "data_base64": to_base64(raw),
    }

@router.post("/background")
async def upload_background(file: UploadFile = File(...)):
    settings = get_settings()
    mime = (file.content_type or "").lower()
    allowed = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    if mime not in allowed:
        raise HTTPException(status_code=400, detail=f"Unsupported image type: {mime}")

    raw = await file.read()
    if len(raw) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image too large")
    if not _validate_image_signature(mime, raw):
        raise HTTPException(status_code=400, detail="Invalid image binary signature")

    ext = allowed[mime]
    filename = f"bg-{uuid.uuid4().hex}{ext}"
    target = UPLOAD_DIR / filename
    with open(target, "wb") as f:
        f.write(raw)

    logger.info("upload_background_ok filename=%s bytes=%s mime=%s", filename, len(raw), mime)
    return {
        "filename": filename,
        "url": f"/uploads/backgrounds/{filename}",
        "mime_type": mime,
    }