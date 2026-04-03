import os
import uuid
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, File, HTTPException, UploadFile

from backend.app.config import get_settings
from backend.app.services.file_parser import parse_text_file, to_base64, ALLOWED_TEXT_EXT


router = APIRouter(prefix="/api/upload", tags=["upload"])


UPLOAD_DIR = Path(__file__).resolve().parents[3] / "uploads" / "backgrounds"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@router.post("/file")
async def upload_file(file: UploadFile = File(...)):
    settings = get_settings()
    ext = Path(file.filename or "").suffix.lower()
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

    return {
        "filename": file.filename,
        "type": "text_context",
        "extracted_text": text[:20000],
        "truncated": len(text) > 20000,
    }


@router.post("/image")
async def upload_image(file: UploadFile = File(...)):
    settings = get_settings()
    mime = (file.content_type or "").lower()
    if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        raise HTTPException(status_code=400, detail=f"Unsupported image type: {mime}")

    raw = await file.read()
    if len(raw) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image too large")

    return {
        "filename": file.filename or "pasted-image",
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

    ext = allowed[mime]
    filename = f"bg-{uuid.uuid4().hex}{ext}"
    target = UPLOAD_DIR / filename
    with open(target, "wb") as f:
        f.write(raw)

    return {
        "filename": filename,
        "url": f"/uploads/backgrounds/{filename}",
        "mime_type": mime,
    }
