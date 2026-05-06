"""
路径兼容层：统一处理"开发模式"与"PyInstaller 打包模式"下的路径差异。

PyInstaller 打包后：
  - sys.frozen == True
  - sys.executable  → 实际 .exe 路径
  - sys._MEIPASS    → 解压后的临时目录（包含 Python 运行时、依赖、打包进去的数据文件）
  - __file__        → 指向 _MEIPASS 内部，不能用来定位"用户数据目录"

用户数据（chat.db、uploads/、.env）应放在 exe 同级目录，而不是临时目录。
静态前端文件（frontend/）打包进 _MEIPASS，运行时从那里读取。
"""

import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包的可执行文件中。"""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def get_app_dir() -> Path:
    """
    返回"应用根目录"：
      - 打包模式：exe 所在目录（用户数据存放位置）
      - 开发模式：项目根目录（即 backend/ 的上两级）
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    # 本文件在 backend/app/_compat.py，parents[2] 是项目根
    return Path(__file__).resolve().parents[2]


def get_bundle_dir() -> Path:
    """
    返回"打包数据目录"：
      - 打包模式：sys._MEIPASS（PyInstaller 解压的临时目录，含 frontend/ 等只读资源）
      - 开发模式：项目根目录（与 get_app_dir() 相同）
    """
    if is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[2]


def get_frontend_dir() -> Path:
    """返回前端静态文件目录。"""
    return get_bundle_dir() / "frontend"


def get_uploads_dir() -> Path:
    """返回上传文件目录（用户数据，放在 exe 旁边）。"""
    return get_app_dir() / "uploads"


def get_db_path() -> Path:
    """返回 SQLite 数据库文件路径（用户数据，放在 exe 旁边）。"""
    return get_app_dir() / "chat.db"


def get_dotenv_path() -> Path:
    """返回 .env 文件路径（放在 exe 旁边）。"""
    return get_app_dir() / ".env"
