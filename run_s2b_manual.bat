@echo off
setlocal
if "%~1"=="" (
  echo Usage: run_s2b_manual.bat "GOOGLE_SHEET_URL" [personal^|school] [dry^|run]
  echo Example: run_s2b_manual.bat "https://docs.google.com/spreadsheets/d/.../edit?gid=0#gid=0" personal dry
  exit /b 2
)
set SHEET_URL=%~1
set ACCOUNT=%~2
set MODE=%~3
if "%ACCOUNT%"=="" set ACCOUNT=personal
if "%MODE%"=="" set MODE=dry

if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe s2b_auto.py --sheet "%SHEET_URL%" --account %ACCOUNT% --mode %MODE% --manual-login-then-headless
) else (
  python s2b_auto.py --sheet "%SHEET_URL%" --account %ACCOUNT% --mode %MODE% --manual-login-then-headless
)
