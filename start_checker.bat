@echo off
:loop
python weather_checker.py
echo Чекер остановлен! Перезапуск через 10 секунд...
timeout /t 10
goto loop