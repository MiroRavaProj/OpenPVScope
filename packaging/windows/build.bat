@echo off
REM Build OpenPVScope Windows artifacts (run from repo root)
set ROOT=%~dp0..\..
cd /d "%ROOT%\frontend"
call npm ci
call npm run build
cd /d "%ROOT%\backend"
if exist openpvscope\static rmdir /s /q openpvscope\static
mkdir openpvscope\static
xcopy /E /I /Y "%ROOT%\frontend\dist\*" openpvscope\static\
pip install -e ".[desktop]"
pip install pyinstaller
pyinstaller "%ROOT%\packaging\windows\openpvscope.spec" --noconfirm --distpath "%ROOT%\packaging\windows\dist" --workpath "%ROOT%\packaging\windows\build"
echo.
echo Next: compile packaging\windows\OpenPVScope.iss with Inno Setup.
echo Place OpenSfM under engines\opensfm or set OPENPVSCOPE_OPENSFM_ROOT.
