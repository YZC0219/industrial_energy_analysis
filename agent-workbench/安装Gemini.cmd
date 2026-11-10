@echo off
setlocal
if exist "%~dp0tools\agy.exe" (
  echo Antigravity is already installed. Run login Gemini next.
  pause
  exit /b 0
)
if /i not "%~1"=="--legacy" (
  echo Preferred client: Google Antigravity CLI. See README.md, Google client setup.
  echo Install its official Windows client as "%~dp0tools\agy.exe", then run login Gemini.
  echo This script does not download Antigravity or change Windows settings.
  echo Optional legacy Gemini CLI: run this script with --legacy.
  echo Legacy client was rejected with UNSUPPORTED_CLIENT in the recorded verification.
  pause
  exit /b 1
)
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
