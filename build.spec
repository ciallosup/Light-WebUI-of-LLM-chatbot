# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置。

用法：
    pyinstaller build.spec

产物：
    dist/self-chat/          ← 目录模式（启动快，推荐）
      self-chat.exe
      .env.example           ← 提示用户填写 API Key
      _internal/             ← Python 运行时 + 依赖（PyInstaller 5.8+ 自动生成）

用户需要在 dist/self-chat/ 旁边放一个 .env 文件（参考 .env.example）。
chat.db 和 uploads/ 会在首次运行时自动创建。
"""

import sys
from pathlib import Path

# 项目根目录（build.spec 所在位置）
ROOT = Path(SPECPATH)

block_cipher = None

a = Analysis(
    # 入口脚本
    [str(ROOT / '__main__.py')],
    pathex=[str(ROOT)],
    binaries=[],
    # 打包进去的数据文件：(源路径, 目标目录)
    # frontend/ 放到 _MEIPASS/frontend/，运行时由 _compat.get_frontend_dir() 读取
    datas=[
        (str(ROOT / 'frontend'), 'frontend'),
    ],
    hiddenimports=[
        # uvicorn 动态加载的模块
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.loops.asyncio',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.http.httptools_impl',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.protocols.websockets.websockets_impl',
        'uvicorn.protocols.websockets.wsproto_impl',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'uvicorn.lifespan.off',
        # FastAPI / Starlette 动态加载
        'starlette.routing',
        'starlette.staticfiles',
        'starlette.responses',
        # SQLAlchemy 方言
        'sqlalchemy.dialects.sqlite',
        # python-multipart（FastAPI 文件上传依赖）
        'multipart',
        # httpx 传输层
        'httpx._transports.default',
        'httpx._transports.asgi',
        # 文件解析（按需）
        'docx',
        'pdfminer',
        'pdfminer.high_level',
        'pdfminer.layout',
        # 应用自身模块（确保不被 tree-shaking 掉）
        'backend.app.main',
        'backend.app._compat',
        'backend.app.config',
        'backend.app.db',
        'backend.app.models',
        'backend.app.schemas',
        'backend.app.middleware',
        'backend.app.routers.chat',
        'backend.app.routers.conversations',
        'backend.app.routers.settings',
        'backend.app.routers.upload',
        'backend.app.services.llm_client',
        'backend.app.services.history',
        'backend.app.services.file_parser',
        'backend.app.services.runtime_settings',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 排除不需要的大型包，减小体积
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'PIL',
        'cv2',
        'torch',
        'tensorflow',
        'pytest',
        'IPython',
        'jupyter',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # onedir 模式：二进制文件单独放，启动更快
    name='self-chat',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,                # 启用 UPX 压缩（需要安装 upx，可选）
    console=True,            # 保留控制台窗口，方便查看日志和错误
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon=str(ROOT / 'frontend' / 'favicon.ico'),  # 取消注释可设置图标
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='self-chat',        # 输出目录名：dist/self-chat/
)
