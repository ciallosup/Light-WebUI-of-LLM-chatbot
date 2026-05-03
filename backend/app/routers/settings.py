from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.db import get_session
from backend.app.schemas import RuntimeModelUpdate, SystemPromptUpdate
from backend.app.services.runtime_settings import (
    get_runtime_model,
    get_system_prompt,
    set_runtime_model,
    set_system_prompt,
)


router = APIRouter(prefix="/api/settings", tags=["settings"])


def _parse_models() -> list[str]:
    settings = get_settings()
    values = [m.strip() for m in settings.llm_models.split(",") if m.strip()]
    if settings.llm_model and settings.llm_model.strip() and settings.llm_model.strip() not in values:
        values.append(settings.llm_model.strip())
    return values


@router.get("/model")
def get_current_model(session: Session = Depends(get_session)):
    return {"model": get_runtime_model(session)}


@router.put("/model")
def update_current_model(payload: RuntimeModelUpdate, session: Session = Depends(get_session)):
    models = _parse_models()
    model = payload.model.strip()
    if models and model not in models:
        raise HTTPException(status_code=400, detail="Model not in allowed list")
    saved = set_runtime_model(session, model)
    return {"model": saved}


@router.get("/models")
def list_models(session: Session = Depends(get_session)):
    models = _parse_models()
    current = get_runtime_model(session)
    if current and current not in models:
        models.append(current)
    settings = get_settings()
    return {"models": models, "current": current, "timeout_sec": settings.llm_timeout_sec}


@router.get("/system-prompt")
def get_current_system_prompt(session: Session = Depends(get_session)):
    return {"content": get_system_prompt(session)}


@router.put("/system-prompt")
def update_current_system_prompt(payload: SystemPromptUpdate, session: Session = Depends(get_session)):
    content = set_system_prompt(session, payload.content)
    return {"content": content}
