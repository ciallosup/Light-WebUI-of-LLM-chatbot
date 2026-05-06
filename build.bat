@echo off
REM ============================================================
REM  self-chat 打包脚本
REM  用法：双击运行，或在项目根目录执行 build.bat
REM  前提：已激活 .venv 或系统 Python 已安装 pyinstaller
REM ============================================================

setlocal

REM 优先使用项目 venv 里的 Python
set PYTHON=.venv\Scripts\python.exe
if not exist "%PYTHON%" (
    set PYTHON=python
)

echo [build] 使用 Python: %PYTHON%
%PYTHON% --version

echo.
echo [build] 安装 / 更新 PyInstaller...
%PYTHON% -m pip install pyinstaller --quiet

echo.
echo [build] 开始打包...
%PYTHON% -m PyInstaller build.spec --noconfirm

if errorlevel 1 (
    echo.
    echo [build] 打包失败！请查看上方错误信息。
    pause
    exit /b 1
)

echo.
echo [build] 复制 .env.example 到发布目录...
if exist ".env.example" (
    copy /Y ".env.example" "dist\self-chat\.env.example" >nul
    echo [build] 已复制 .env.example
) else (
    echo [build] 警告：未找到 .env.example，跳过复制。
)

echo.
echo [build] ============================================================
echo [build] 打包完成！发布目录：dist\self-chat\
echo [build]
echo [build] 分发给用户时，请告知：
echo [build]   1. 将 .env.example 重命名为 .env
echo [build]   2. 填写 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
echo [build]   3. 双击 self-chat.exe 启动，浏览器会自动打开
echo [build] ============================================================
echo.

pause
