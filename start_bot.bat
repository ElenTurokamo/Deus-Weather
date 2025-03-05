@echo off
:loop
python bot.py
echo Бот остановлен! Перезапуск через 10 секунд...
timeout /t 10
goto loop