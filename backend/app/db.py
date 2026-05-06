import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app._compat import get_db_path


def _resolve_db_url() -> str:
    env_path = os.getenv("CHAT_DB_PATH", "").strip()
    if env_path:
        return f"sqlite:///{Path(env_path).expanduser().resolve().as_posix()}"
    # 使用 _compat 解析：打包模式下放在 exe 旁边，开发模式下放在项目根。
    db_path = get_db_path()
    return f"sqlite:///{db_path.as_posix()}"



DATABASE_URL = _resolve_db_url()
engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    from backend.app.models import Base

    Base.metadata.create_all(bind=engine)


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
