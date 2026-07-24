@echo off
REM Build OpenPVScope Windows artifacts (run from repo root)
set ROOT=%~dp0..\..
cd /d "%ROOT%"

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
echo ODX is not bundled; the app installs it on demand from the Photogrammetry UI.
