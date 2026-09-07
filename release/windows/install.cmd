@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-KoreanPatch.ps1" %*
set "PATCH_EXIT=%ERRORLEVEL%"
if not "%AETERNA_KO_NO_PAUSE%"=="1" pause
exit /b %PATCH_EXIT%
