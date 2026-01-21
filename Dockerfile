FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py /app/bot.py
COPY logic.py /app/logic.py
COPY models.py /app/models.py
COPY weather.py /app/weather.py
COPY weather_timer.py /app/weather_timer.py
COPY texts.py /app/texts.py

COPY start.bat /app/start.bat
COPY bot.bat /app/bot.bat
COPY timer.bat /app/timer.bat

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]