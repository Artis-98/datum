@echo off
rem Launch DATUM using the project's virtual environment.
setlocal
set "HERE=%~dp0"
start "" "%HERE%.venv\Scripts\pythonw.exe" "%HERE%datum.py" %*
endlocal
