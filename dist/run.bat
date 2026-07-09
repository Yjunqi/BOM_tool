@echo off
chcp 65001 >nul
title BOM-Taobao-Filler 运行器

:BOM_PATH
if "%~1"=="" (
    echo.
    echo ========================================
    echo   BOM-Taobao-Filler
    echo ========================================
    echo.
    echo 请把 BOM 表文件拖到这个窗口上，然后回车
    echo （或者直接回车退出）
    echo.
    set /p "BOM_PATH=路径: "
    if "!BOM_PATH!"=="" exit /b
    set "BOM_PATH=!BOM_PATH:"=!"
) else (
    set "BOM_PATH=%~1"
)

:CHECK_BROWSER
where playwright >nul 2>&1
if errorlevel 1 (
    echo.
    echo ! 首次使用，正在安装浏览器...
    python -m playwright install chromium
)

echo.
echo 正在处理，请勿关闭窗口...
echo.
BOM-Filler.exe "!BOM_PATH!"

echo.
pause