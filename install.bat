@echo off
cd /d "%~dp0"
echo Installiere Pakete aus requirements.txt ...
if exist "brenntag-tag\Scripts\pip.exe" (
    call brenntag-tag\Scripts\pip.exe install -r requirements.txt
) else (
    pip install -r requirements.txt
)
echo.
echo Fertig. Du kannst jetzt run.bat ausfuehren oder in Cursor F5 druecken.
pause
