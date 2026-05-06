import os
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import BaseModel

from backend.app._compat import get_dotenv_path

# 优先加载 exe 旁边的 .env（打包模式），开发模式下也会找到项目根的 .env。
load_dotenv(dotenv_path=get_dotenv_path(), override=False)
# 兜底：再尝试默认搜索（开发模式下 python-dotenv 会向上查找 .env）
load_dotenv(override=False)



def _to_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class Settings(BaseModel):
    llm_base_url: str = os.getenv("LLM_BASE_URL", "")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "")
    llm_models: str = os.getenv("LLM_MODELS", "")
    llm_chat_path: str = os.getenv("LLM_CHAT_PATH", "/v1/chat/completions")
    llm_timeout_sec: float = float(os.getenv("LLM_TIMEOUT_SEC", "180"))

    # 流式空回复时回退到非流式的最小预算时间（秒）。
    # 由于流式失败常发生在主预算接近耗尽时刻，需要一个独立的下限保证 fallback 真正有机会执行。
    llm_fallback_min_sec: float = float(os.getenv("LLM_FALLBACK_MIN_SEC", "60"))

    # 标题生成专用模型（轻量便宜的模型，例如 deepseek-chat / gemini-flash）。
    # 若为空则复用主对话模型。
    llm_title_model: str = os.getenv("LLM_TITLE_MODEL", "")
    # 标题生成单次请求超时（秒）。标题应该很快返回，避免拖累主对话体验。
    llm_title_timeout_sec: float = float(os.getenv("LLM_TITLE_TIMEOUT_SEC", "20"))

    # ---- 历史上下文（context window）控制 ----
    # 每次发送给上游模型的"历史消息" token 预算（不含当前消息与 system prompt）。
    # 估算口径：中文按 1.5 字符/token，英文按 4 字符/token，混合按字符长度 / 2 近似。
    llm_history_token_budget: int = int(os.getenv("LLM_HISTORY_TOKEN_BUDGET", "8000"))
    # 历史消息条数硬上限（双重保险，避免极端情况下的预算误差）。
    llm_history_max_messages: int = int(os.getenv("LLM_HISTORY_MAX_MESSAGES", "40"))
    # 是否始终保留首条 user 消息（任务初始描述），即使在 token 预算之外。
    llm_history_keep_first_user: bool = _to_bool(os.getenv("LLM_HISTORY_KEEP_FIRST_USER"), True)


    host: str = os.getenv("HOST", "127.0.0.1")

    port: int = int(os.getenv("PORT", "8000"))
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "10"))

    cors_allow_origins: str = os.getenv("CORS_ALLOW_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000")
    rate_limit_per_minute: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "120"))
    sse_heartbeat_sec: float = float(os.getenv("SSE_HEARTBEAT_SEC", "15"))
    max_message_len: int = int(os.getenv("MAX_MESSAGE_LEN", "12000"))
    secure_headers_enabled: bool = _to_bool(os.getenv("SECURE_HEADERS_ENABLED"), True)

    def cors_origins(self) -> list[str]:
        raw = self.cors_allow_origins.strip()
        if not raw:
            return []
        if raw == "*":
            return ["*"]
        return [x.strip() for x in raw.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()