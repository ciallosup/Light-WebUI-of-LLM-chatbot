import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles


from backend.app.config import get_settings
from backend.app.db import init_db
from backend.app.middleware import InMemoryRateLimiter, build_rate_limit_middleware, configure_logging
from backend.app.routers.chat import router as chat_router
from backend.app.routers.conversations import router as conversations_router
from backend.app.routers.settings import router as settings_router
from backend.app.routers.upload import router as upload_router


app = FastAPI(title="Local LLM Web Chat")
settings = get_settings()

configure_logging()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins() or ["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

rate_limiter = InMemoryRateLimiter(max_requests=settings.rate_limit_per_minute, window_sec=60)
app.middleware("http")(build_rate_limit_middleware(rate_limiter, settings))

app.include_router(conversations_router)
app.include_router(chat_router)
app.include_router(upload_router)
app.include_router(settings_router)


@app.on_event("startup")
def on_startup():
    init_db()


frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
if frontend_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_dir)), name="assets")

uploads_dir = Path(__file__).resolve().parents[2] / "uploads"
uploads_dir.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(uploads_dir)), name="uploads")


def _asset_mtime(name: str) -> int:
    """返回静态资源的最近修改时间（秒）。用于做版本号，文件变更后立即让浏览器拉新。"""
    p = frontend_dir / name
    try:
        return int(p.stat().st_mtime)
    except OSError:
        return 0


_CACHE_BUST_PATTERNS = [
    (re.compile(r'(href="/assets/style\.css)(\?v=[^"]*)?"'), "style.css"),
    (re.compile(r'(src="/assets/app\.js)(\?v=[^"]*)?"'), "app.js"),
]


@app.get("/")
def index():
    """返回首页 HTML，并自动给本地静态脚本/样式追加 ?v=<mtime> 版本戳。

    背景：浏览器对 /assets 下的静态文件会按 Cache-Control 缓存，
    更新前端代码（如修复流式渲染逻辑）后用户需手动 Ctrl+F5 才能加载新版本，
    经常导致"修了但用户感觉没修"。这里通过 mtime 做强缓存破坏。
    """
    index_file = frontend_dir / "index.html"
    if not index_file.exists():
        return {"status": "ok", "message": "Frontend not found. Please create frontend/index.html"}

    try:
        html = index_file.read_text(encoding="utf-8")
    except OSError:
        return FileResponse(str(index_file))

    for pattern, asset in _CACHE_BUST_PATTERNS:
        version = _asset_mtime(asset)
        if version <= 0:
            continue
        html = pattern.sub(rf'\g<1>?v={version}"', html)

    # 同时让 index.html 自身不被缓存，否则版本戳替换效果会被中间缓存吃掉。
    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }
    return HTMLResponse(content=html, headers=headers)



@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    favicon_file = frontend_dir / "favicon.ico"
    if favicon_file.exists():
        return FileResponse(str(favicon_file))
    return Response(status_code=204)
