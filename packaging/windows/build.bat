@echo off
REM Build OpenPVScope Windows artifacts (run from repo root)
set ROOT=%~dp0..\..
cd /d "%ROOT%"

echo Fetching ODX_Setup into packaging\windows\vendor ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\fetch_odx_setup.ps1"
if errorlevel 1 (
  echo ERROR: Failed to download ODX_Setup. Release build requires vendor\ODX_Setup_*.exe
  exit /b 1
)
dir /b "%ROOT%\packaging\windows\vendor\ODX_Setup*.exe" >nul 2>&1
if errorlevel 1 (
  echo ERROR: No packaging\windows\vendor\ODX_Setup*.exe after fetch. Aborting.
  exit /b 1
)

cd /d "%ROOT%\frontend"
call npm ci
if errorlevel 1 exit /b 1
call npm run build
if errorlevel 1 exit /b 1
cd /d "%ROOT%\backend"
if exist openpvscope\static rmdir /s /q openpvscope\static
mkdir openpvscope\static
xcopy /E /I /Y "%ROOT%\frontend\dist\*" openpvscope\static\
pip install -e ".[desktop]"
if errorlevel 1 exit /b 1
pip install pyinstaller
if errorlevel 1 exit /b 1
pyinstaller "%ROOT%\packaging\windows\openpvscope.spec" --noconfirm --distpath "%ROOT%\packaging\windows\dist" --workpath "%ROOT%\packaging\windows\build"
if errorlevel 1 exit /b 1
echo.
echo Next: compile packaging\windows\OpenPVScope.iss with Inno Setup.
echo Full Setup will silently install ODX to C:\ODX from vendor\ODX_Setup_*.exe.
