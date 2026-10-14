@echo off
set "GEMINI_CLI_HOME=%~dp0data\gemini-home"
set "TEMP=%~dp0data\tmp"
set "TMP=%~dp0data\tmp"
if not exist "%TEMP%" mkdir "%TEMP%"
cd /d "%~dp0"
node "%~dp0gemini-login.mjs"
pause
