@echo off
REM Führt main.py mit der Venv-Python-Exe aus (ohne Venv aktivieren zu müssen)
set "VENV_PYTHON=%~dp0brenntag-tag\Scripts\python.exe"
set "MAIN=%~dp0main.py"
if not exist "%VENV_PYTHON%" (
  echo Venv nicht gefunden: %VENV_PYTHON%
  echo Bitte zuerst: python -m venv brenntag-tag
  exit /b 1
)
"%VENV_PYTHON%" "%MAIN%" %*
