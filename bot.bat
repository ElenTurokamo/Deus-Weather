@echo off
:botLoop
echo Starting bot.py
python bot.py
echo bot.py finished. Restarting in 5 seconds...
timeout /t 5 >nul
goto botLoop