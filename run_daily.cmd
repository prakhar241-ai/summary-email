@echo off
REM Runs the daily inbox summary and emails it to you.
REM Called by the "SummaryEmail_Daily7AM" scheduled task at 07:00 IST.
cd /d "%~dp0"
"C:\Python314\python.exe" "%~dp0summarize_inbox.py" --hours 24 --send >> "%~dp0summaries\run.log" 2>&1
