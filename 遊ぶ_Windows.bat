@echo off
rem 三国布陣 — Windows用ランチャー。ダブルクリックで起動します。
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  start "" http://localhost:8035
  py -m sim.web
) else (
  where python >nul 2>nul
  if %errorlevel%==0 (
    start "" http://localhost:8035
    python -m sim.web
  ) else (
    echo Python が見つかりません。https://www.python.org/downloads/ から
    echo インストールしてください（Add python.exe to PATH に必ずチェック）。
    pause
  )
)
