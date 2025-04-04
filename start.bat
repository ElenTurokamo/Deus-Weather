@echo off
chcp 65001 >nul
start "Timer Script" cmd /c "timer.bat"

start "Bot Script" cmd /c "bot.bat"
echo Scripts running, terminating.
exit