import requests
import json
import time
import logging
from datetime import datetime, timedelta
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
import telebot
import os
from dotenv import load_dotenv
from models import CheckedCities, User
from functools import wraps

load_dotenv()

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

DB_URL = os.getenv("DATABASE_URL")
engine = create_engine(DB_URL, echo=False)
Session = sessionmaker(bind=engine)

bot = telebot.TeleBot(os.getenv("BOT_TOKEN"))

# Декоратор для безопасного выполнения функций
def safeexecute(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logging.critical(f"🔥 Ошибка в функции {func.__name__}: {e}")
            return None
    return wrapper

@safeexecute
def get_weather_data(city):
    url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={os.getenv('WEATHER_API_KEY')}&units=metric&lang=ru"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except Exception as e:
        logging.error(f"❌ Ошибка получения данных о погоде для {city}: {e}")
        return None
    resp_data = response.json()
    if resp_data.get("cod") != 200:
        logging.error(f"⚠ Ошибка в ответе API для {city}: {resp_data}")
        return None
    return {
        "temperature": resp_data["main"]["temp"],
        "humidity": resp_data["main"]["humidity"],
        "wind_speed": resp_data["wind"]["speed"],
        "description": resp_data["weather"][0]["description"],
        "pressure": round(resp_data["main"]["pressure"] * 0.75006),
        "visibility": resp_data.get("visibility", "Неизвестно")
    }

@safeexecute
def check_weather_changes_for_city(city):
    session = Session()
    current_data = get_weather_data(city)
    if not current_data:
        logging.error(f"❌ Не удалось получить данные для города: {city}")
        session.close()
        return
    
    now = datetime.utcnow()
    city_record = session.query(CheckedCities).filter_by(city_name=city).first()
    
    if city_record and city_record.last_checked:
        time_diff = now - city_record.last_checked
        logging.info(f"📍 {city} | Проверка: {now} (разница {time_diff})")
        if time_diff < timedelta(minutes=0):
            logging.info(f"⏭ Пропуск проверки для {city}, так как проверка была недавно")
            session.close()
            return

    significant_change = False
    if city_record:
        temp_diff = abs(current_data["temperature"] - city_record.temperature)
        humidity_diff = abs(current_data["humidity"] - (city_record.last_humidity or 0))
        wind_diff = abs(current_data["wind_speed"] - (city_record.last_wind_speed or 0))

        logging.info(f"🌡 {city} | ΔT: {temp_diff}°C | 💧 ΔH: {humidity_diff}% | 💨 ΔW: {wind_diff} м/с")

        if temp_diff >= 5 or humidity_diff >= 15 or wind_diff > 2:
            significant_change = True
    else:
        significant_change = False

    if significant_change:
        alert_message = (f"🔔 Внимание! Погода в городе {city} изменилась!\n"
                         f"\n"
                         f"▸ Температура: {current_data['temperature']}°C, {current_data['description']}\n"
                         f"▸ Влажность: {current_data['humidity']}%\n"
                         f"▸ Скорость ветра: {current_data['wind_speed']} м/с")
        users = session.query(User).filter(User.preferred_city == city, User.notifications_enabled == True).all()
        for user in users:
            try: 
                bot.send_message(user.user_id, alert_message)
                logging.info(f"📩 Уведомление отправлено: {user.user_id} ({city})")
            except Exception as e:
                logging.error(f"❌ Ошибка отправки уведомления {user.user_id}: {e}")

    if city_record:
        city_record.last_humidity = current_data["humidity"]
        city_record.last_wind_speed = current_data["wind_speed"]
        city_record.temperature = current_data["temperature"]
        city_record.weather_info = json.dumps(current_data, ensure_ascii=False)
        city_record.last_checked = now
    else:
        new_record = CheckedCities(
            city_name=city,
            weather_info=json.dumps(current_data, ensure_ascii=False),
            temperature=current_data["temperature"],
            last_checked=now,
            last_humidity=current_data["humidity"],
            last_wind_speed=current_data["wind_speed"]
        )
        session.add(new_record)
    session.commit()
    session.close()

@safeexecute
def check_all_cities():
    session = Session()
    cities = session.query(User.preferred_city).filter(User.notifications_enabled == True).distinct().all()
    session.close()
    for (city,) in cities:
        if city:
            check_weather_changes_for_city(city)

if __name__ == '__main__':
    while True:
        try:
            logging.info("🔄 Запуск цикла проверки погоды...")
            check_all_cities()
            logging.info("✅ Проверка завершена. Ожидание следующего цикла...")
        except Exception as e:
            logging.critical(f"🔥 Критическая ошибка в основном цикле: {e}")
        time.sleep(900)
