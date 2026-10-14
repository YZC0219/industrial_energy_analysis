@echo off
set "npm_config_cache=%~dp0.cache\npm"
set "TEMP=%~dp0data\tmp"
set "TMP=%~dp0data\tmp"
if not exist "%TEMP%" mkdir "%TEMP%"
call npm.cmd install --prefix "%~dp0tools" @google/gemini-cli --no-audit --no-fund
if errorlevel 1 (
  echo Installation failed. Check network access to registry.npmjs.org.
  pause
  exit /b 1
)
echo Installed. Run login Gemini next.
pause
