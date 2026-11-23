@echo off
setlocal
set "OFFICE_CONTEXT=8192"
cd /d "%~dp0"
set "PYRUN=py -3"
where py >nul 2>nul
if not errorlevel 1 goto setup
where python >nul 2>nul
if errorlevel 1 goto missing
set "PYRUN=python"
:setup
if exist ".qa-env\Scripts\python.exe" set "PYRUN=.qa-env\Scripts\python.exe"
%PYRUN% -c "import playwright.sync_api" >nul 2>nul
if not errorlevel 1 goto browser
if exist ".qa-env\Scripts\python.exe" goto install
%PYRUN% -m venv .qa-env
if errorlevel 1 goto qa_failed
:install
set "PYRUN=.qa-env\Scripts\python.exe"
%PYRUN% -m pip install -r requirements-qa.txt
if errorlevel 1 goto qa_failed
:browser
%PYRUN% -c "from office.browser_qa import browser_path; from pathlib import Path; from playwright.sync_api import sync_playwright; p=sync_playwright().start(); ok=Path(browser_path() or p.chromium.executable_path).is_file(); p.stop(); raise SystemExit(int(not ok))"
if not errorlevel 1 goto run
%PYRUN% -m playwright install chromium
if not errorlevel 1 goto run
:qa_failed
echo Browser QA setup incomplete. Check Internet and rerun this launcher for browser testing.
:run
%PYRUN% server.py %*
goto end
:missing
echo Install Python 3.10 or newer from python.org and run this file again.
:end
pause
