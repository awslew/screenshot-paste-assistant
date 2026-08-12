@echo off
rem ============================================================
rem  ScreenshotPasteAssistant build script (dev B, task B4)
rem  Output: dist\ScreenshotPasteAssistant\ScreenshotPasteAssistant.exe
rem  Mode:   PyInstaller --noconsole --onedir
rem  Hidden imports cover pystray / PIL / pywin32(win32timezone)
rem  Entry:  main.py (owned by dev A, assembles tray + clipboard engine)
rem  NOTE:   keep this file ASCII-only; cmd parses batch files
rem          with the local OEM codepage (GBK on zh-CN), so any
rem          non-ASCII text here would be mangled.
rem ============================================================
cd /d "%~dp0"

rem --- ensure PyInstaller is installed ---
python -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo [build] PyInstaller not found, installing...
    python -m pip install pyinstaller
    if errorlevel 1 (
        echo [build] failed to install PyInstaller, abort.
        exit /b 1
    )
)

echo [build] building dist\ScreenshotPasteAssistant\ ...

pyinstaller --noconsole --onedir --name ScreenshotPasteAssistant ^
    --hidden-import pystray._win32 ^
    --hidden-import PIL.Image ^
    --hidden-import PIL.ImageGrab ^
    --hidden-import win32timezone ^
    --collect-all pystray ^
    --noconfirm ^
    main.py

if errorlevel 1 (
    echo [build] FAILED, see messages above.
    exit /b 1
)

echo.
echo [build] OK: dist\ScreenshotPasteAssistant\ScreenshotPasteAssistant.exe
exit /b 0
