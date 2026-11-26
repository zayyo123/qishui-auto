@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 汽水音乐自动看广告

echo ========================================
echo   汽水音乐自动看广告
echo ========================================
echo.

REM Prefer local venv, then system python
if exist "%~dp0.venv\Scripts\python.exe" (
  set "PY=%~dp0.venv\Scripts\python.exe"
) else (
  where python >nul 2>&1
  if errorlevel 1 (
    echo [错误] 未找到 Python。请先安装 Python 3.10~3.12，并执行：
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
  )
  set "PY=python"
)

REM Optional: sibling platform-tools next to this project
if exist "%~dp0..\platform-tools\adb.exe" (
  set "PATH=%~dp0..\platform-tools;%PATH%"
)
if exist "%~dp0platform-tools\adb.exe" (
  set "PATH=%~dp0platform-tools;%PATH%"
)

where adb >nul 2>&1
if errorlevel 1 (
  echo [错误] 未找到 adb。请安装 Android platform-tools 并加入 PATH
  echo 下载: https://developer.android.com/tools/releases/platform-tools
  pause
  exit /b 1
)

echo [检查] ADB 设备...
adb devices
echo.

"%PY%" -c "import cv2, numpy, rapidocr_onnxruntime" >nul 2>&1
if errorlevel 1 (
  echo [提示] OCR 依赖未安装，正在尝试安装...
  if exist "%~dp0.venv\Scripts\pip.exe" (
    "%~dp0.venv\Scripts\pip.exe" install -r "%~dp0requirements.txt"
  ) else (
    "%PY%" -m pip install -r "%~dp0requirements.txt"
  )
  echo.
)

echo [启动] qishui_auto.py
echo 停止请按 Ctrl + C
echo.
"%PY%" "%~dp0qishui_auto.py"

echo.
echo 脚本已退出。
pause
