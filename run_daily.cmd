@echo off
REM Runs the daily inbox summary and emails it to you.
REM Called by the "SummaryEmail_Daily7AM" scheduled task at 07:00 IST.
cd /d "%~dp0"
REM Uses imap_summary.py (Gmail App Password). The older summarize_inbox.py
REM used OAuth, whose token silently expired every 7 days.
"C:\Python314\python.exe" "%~dp0imap_summary.py" >> "%~dp0summaries\run.log" 2>&1
