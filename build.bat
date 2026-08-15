@echo off
rem ============================================================
rem  Civitai 数据导入助手 — 打包为单个 exe (PyInstaller)
rem  产物位于 dist\CivitaiDataAssistant.exe
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"
uv sync --group dev
uv run --group dev pyinstaller --noconfirm --clean --onefile --windowed --name CivitaiDataAssistant civitai_gui.py
pause
