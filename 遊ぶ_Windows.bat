@echo off
rem SANGOKU-FUJIN launcher (keep this file ASCII-safe for cmd.exe)
cd /d "%~dp0"
echo ============================================
echo   SANGOKU-FUJIN  http://localhost:8035
echo   (close this window to stop the game)
echo ============================================
start "" http://localhost:8035
py -3 -m sim.web
if %errorlevel%==0 goto end
echo.
echo [py launcher failed, trying "python" ...]
python -m sim.web
if %errorlevel%==0 goto end
echo.
echo --------------------------------------------
echo  FAILED. If you see 'py' / 'python' is not
echo  recognized, or the Microsoft Store opened:
echo  install Python from
echo     https://www.python.org/downloads/
echo  and CHECK "Add python.exe to PATH".
echo  Then double-click this file again.
echo --------------------------------------------
:end
echo.
pause
