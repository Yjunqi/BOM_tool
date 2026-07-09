@echo off
chcp 65001 >nul
title BOM-Taobao-Filler 打包工具

echo ========================================
echo   BOM-Taobao-Filler 一键打包
echo ========================================
echo.

:: 检查是否在 venv 中
if "%VIRTUAL_ENV%"=="" (
    echo [警告] 建议先在虚拟环境中执行，避免打包体积过大
    echo         python -m venv venv ^&^& venv\Scripts\activate
    echo.
)

:: 1. 安装 PyInstaller
echo [1/4] 安装 PyInstaller...
pip install pyinstaller -q

:: 2. 清理旧构建
echo [2/4] 清理临时文件...
rmdir /s /q build dist 2>nul
del /f /q *.spec 2>nul

:: 3. 打包（--onefile 单文件模式）
echo [3/4] 打包中（可能需要 1-2 分钟）...
pyinstaller --onefile ^
    --name BOM-Filler ^
    --add-data "config.yaml;." ^
    --add-data "src;src" ^
    --hidden-import openpyxl.cell._writer ^
    main.py

:: 4. 创建分发目录
echo [4/4] 创建分发目录...
if exist dist\BOM-Filler (
    rmdir /s /q dist\BOM-Filler
)
mkdir dist\BOM-Filler 2>nul
copy dist\BOM-Filler.exe dist\BOM-Filler\ >nul
copy config.yaml dist\BOM-Filler\ >nul
copy README.md dist\BOM-Filler\ >nul

:: 创建 run.bat
(
echo @echo off
echo chcp 65001 ^>nul
echo.
echo :check_playwright
echo where playwright ^>nul 2^>^&1
echo if errorlevel 1 (
echo     echo [首次运行] 检测到未安装浏览器组件，正在下载...
echo     python -m playwright install chromium
echo )
echo.
echo echo.
echo echo ========================================
echo echo   BOM-Taobao-Filler
echo echo ========================================
echo echo.
echo echo 请把 BOM 表拖到这个窗口上，然后按回车
echo echo.
echo set /p BOM_PATH=^>^>
echo.
echo if not exist "%%BOM_PATH%%" (
echo     echo [错误] 文件不存在
echo     pause
echo     exit /b 1
echo )
echo.
echo BOM-Filler.exe "%%BOM_PATH%%"
echo.
echo pause
) > dist\BOM-Filler\run.bat

:: 创建首个运行提示
(
echo 使用说明：
echo.
echo 1. 首次使用前先确认已安装 Python 3.10+
echo 2. 在 dist\BOM-Filler\ 目录下打开终端，执行：
echo       python -m playwright install chromium
echo 3. 然后双击 run.bat，拖入 BOM 表即可
echo.
echo 如果淘宝页面结构变化导致抓取失败，请更新 taobao_client.py
echo 中的 JS 解析逻辑。
) > dist\BOM-Filler\README.txt

echo.
echo ========================================
echo   ✅ 打包完成！
echo ========================================
echo.
echo 输出目录：dist\BOM-Filler\
echo.
echo 目录结构：
dir /b dist\BOM-Filler\
echo.
echo 首次使用需安装浏览器：
echo   cd dist\BOM-Filler
echo   python -m playwright install chromium
echo.
pause