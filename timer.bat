@echo off
:timerLoop
echo Starting weather_timer.py
python weather_timer.py
echo weather_timer.py finished. Restarting in 5 seconds...
timeout /t 5 >nul
goto timerLoop