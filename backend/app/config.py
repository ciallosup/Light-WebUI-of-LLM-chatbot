from functools import lru_cache
from pydantic import BaseModel
from dotenv import load_dotenv
import os


load_dotenv()


class Settings(BaseModel):
    llm_base_url: str = os.getenv("LLM_BASE_URL", "")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "")
    llm_models: str = os.getenv("LLM_MODELS", "")
    llm_chat_path: str = os.getenv("LLM_CHAT_PATH", "/v1/chat/completions")
    llm_timeout_sec: float = float(os.getenv("LLM_TIMEOUT_SEC", "180"))
    host: str = os.getenv("HOST", "127.0.0.1")
    port: int = int(os.getenv("PORT", "8000"))
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "10"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
