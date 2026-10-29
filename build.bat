@echo off
chcp 437 >nul
echo [BUILD] Installing PyInstaller...
python -m pip install pyinstaller --quiet

echo [BUILD] Building EXE...
python -m PyInstaller --noconfirm build_exe.spec

echo.
if exist "dist\VoiceAssistant.exe" (
    echo [OK] EXE built: dist\VoiceAssistant.exe
    echo [BUILD] Copying project files into dist\ ...
    copy /Y launcher.py dist\ >nul
    copy /Y main.py dist\ >nul
    copy /Y config.py dist\ >nul
    copy /Y llm.py dist\ >nul
    copy /Y stt.py dist\ >nul
    copy /Y tts.py dist\ >nul
    copy /Y recorder.py dist\ >nul
    copy /Y vision.py dist\ >nul
    copy /Y theme.py dist\ >nul
    copy /Y cost_tracker.py dist\ >nul
    copy /Y model_profiles.py dist\ >nul
    if exist context.txt copy /Y context.txt dist\ >nul
    if exist history_context.txt copy /Y history_context.txt dist\ >nul
    if exist icon.ico copy /Y icon.ico dist\ >nul
    if exist launcher_settings.json copy /Y launcher_settings.json dist\ >nul
    if exist cost_settings.json copy /Y cost_settings.json dist\ >nul
    if exist models.json copy /Y models.json dist\ >nul
    if exist avatars (
        if not exist dist\avatars mkdir dist\avatars
        copy /Y avatars\*.png dist\avatars\ >nul 2>nul
    )
    echo [OK] Project files copied to dist\
    echo.
    echo NOTE: from now on you can edit launcher.py and other .py files
    echo directly inside dist\ -- no rebuild needed, just relaunch the exe.
) else (
    echo [ERR] Something went wrong, check output above.
)
pause
