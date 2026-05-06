"""
可执行程序入口（PyInstaller 打包 / python -m self_chat 两用）。

行为：
  1. 读取 .env（由 config.py 在 import 时完成）
  2. 启动 uvicorn（programmatic 模式，不依赖命令行）
  3. 自动在默认浏览器打开 http://HOST:PORT
"""

import sys
import threading
import time
import webbrowser


def _open_browser(url: str, delay: float = 1.5) -> None:
    """延迟打开浏览器，等 uvicorn 完成绑定端口。"""
    def _open():
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:
            pass
    t = threading.Thread(target=_open, daemon=True)
    t.start()


def main() -> None:
    # 必须在 import uvicorn 之前确保 sys.path 正确（PyInstaller 已处理，开发模式下也 OK）
    import uvicorn

    # 延迟 import，让 _compat / config 先初始化（load_dotenv 在 config 模块级别执行）
    from backend.app.config import get_settings
    settings = get_settings()

    host = settings.host or "127.0.0.1"
    port = settings.port or 8000
    url = f"http://{host}:{port}"

    print(f"[self-chat] 启动服务：{url}")
    print(f"[self-chat] 如果浏览器未自动打开，请手动访问：{url}")

    _open_browser(url)

    uvicorn.run(
        "backend.app.main:app",
        host=host,
        port=port,
        # 打包后不能用 reload（文件监视器依赖源码目录）
        reload=False,
        # 日志格式与开发模式保持一致
        log_level="info",
    )


if __name__ == "__main__":
    main()
