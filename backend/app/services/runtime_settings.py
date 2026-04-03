from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.models import AppSetting


RUNTIME_MODEL_KEY = "runtime_model"
SYSTEM_PROMPT_KEY = "system_prompt"


def get_runtime_model(session: Session) -> str:
    row = session.get(AppSetting, RUNTIME_MODEL_KEY)
    if row and row.value.strip():
        return row.value.strip()
    return get_settings().llm_model.strip()


def set_runtime_model(session: Session, model: str) -> str:
    value = (model or "").strip()
    if not value:
        raise ValueError("model is required")

    row = session.get(AppSetting, RUNTIME_MODEL_KEY)
    if row:
        row.value = value
    else:
        row = AppSetting(key=RUNTIME_MODEL_KEY, value=value)
        session.add(row)

    session.commit()
    return value


def get_system_prompt(session: Session) -> str:
    row = session.get(AppSetting, SYSTEM_PROMPT_KEY)
    if row:
        return row.value or ""
    return ""


def set_system_prompt(session: Session, content: str) -> str:
    value = content or ""
    row = session.get(AppSetting, SYSTEM_PROMPT_KEY)
    if row:
        row.value = value
    else:
        row = AppSetting(key=SYSTEM_PROMPT_KEY, value=value)
        session.add(row)

    session.commit()
    return value
