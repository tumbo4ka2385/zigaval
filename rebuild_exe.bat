
@echo off
title Rebuild VoiceAssistant.exe
cd /d "%~dp0"
 
echo ============================================
echo   Rebuilding VoiceAssistant.exe
echo ============================================
echo.
 
echo [1/4] Closing running VoiceAssistant.exe if any...
taskkill /F /IM VoiceAssistant.exe >nul 2>&1
taskkill /F /IM VoiceAssistant_debug.exe >nul 2>&1
timeout /t 1 /nobreak >nul
 
echo [2/4] Removing old build...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
 
echo [3/4] Building (this may take a minute)...
python -m PyInstaller build_exe.spec
 
if errorlevel 1 (
    echo.
    echo ============================================
    echo   BUILD ERROR. See output above.
    echo ============================================
    pause
    exit /b 1
)
 
echo [4/4] Done!
echo.
echo ============================================
echo   New VoiceAssistant.exe is in the dist folder
echo ============================================
echo.
pause
 