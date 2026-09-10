@echo off
rem Scheduled canary: loads NTFY_TOPIC from the repo .env, runs the canary, logs to docs/reliability/runs.jsonl.
rem Registered in Windows Task Scheduler as "PerchData tennis canary" (06:00, 14:00, 22:00 local).
setlocal
cd /d "%~dp0\.."
for /f "usebackq tokens=1,* delims==" %%a in ("..\..\.env") do set "%%a=%%b"
"..\nashville-permits\.venv\Scripts\python.exe" scripts\canary.py --json --log --matches 20 >> storage\canary.log 2>&1
endlocal
