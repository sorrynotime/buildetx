@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   Open Radio Builder - 打包脚本
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 python，请先安装 Python 并勾选 Add Python to PATH。
    if /i not "%~1"=="nopause" pause
    exit /b 1
)

python -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo [1/3] 未安装 PyInstaller，正在安装...
    python -m pip install pyinstaller
    if errorlevel 1 (
        echo [错误] PyInstaller 安装失败，请检查网络。
        if /i not "%~1"=="nopause" pause
        exit /b 1
    )
) else (
    echo [1/3] PyInstaller 已就绪
)

echo [2/3] 正在打包 exe...
python -m PyInstaller OpenRadioBuilder.spec --noconfirm
if errorlevel 1 (
    echo [错误] 打包失败，请查看上方日志。
    if /i not "%~1"=="nopause" pause
    exit /b 1
)

echo [3/3] 复制说明和工具链附件到 dist...
if exist README.md copy /y README.md dist\ >nul
if exist attachment (
    if not exist dist\attachment mkdir dist\attachment
    xcopy /e /i /y attachment dist\attachment >nul
)

echo.
echo 打包完成：dist\OpenRadioBuilder.exe
echo 发布时把整个 dist 文件夹一起拷走即可。
if /i not "%~1"=="nopause" pause
exit /b 0
