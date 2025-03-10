import glob

from logic import Session, safe_execute, format_weather_data
from weather import get_weather

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
import threading
import random

TEST = False #тестовый режим для проверки уведомлений (True - вкл, False - выкл.)
test_weather_data = None

#ШИФРОВАНИЕ
load_dotenv()

#ЛОГИРОВАНИЕ
LOG_DIR = "logs"
LOG_LIFETIME_DAYS = 7  

def clean_old_logs():
    """Удаляет логи, старше LOG_LIFETIME_DAYS"""
    now = time.time()
    for log_file in glob.glob(os.path.join(LOG_DIR, "*.log")):
        if os.path.isfile(log_file):
            file_time = os.path.getmtime(log_file)
            if now - file_time > LOG_LIFETIME_DAYS * 86400:
                try:
                    os.remove(log_file)
                    logging.info(f"🗑 Удалён старый лог: {log_file}")
                except Exception as e:
                    logging.warning(f"⚠ Ошибка при удалении {log_file}: {e}")

clean_old_logs()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("logs/weather_checker.log", encoding="utf-8"), 
        logging.StreamHandler()  
    ]
)

error_handler = logging.FileHandler("logs/errors.log", encoding="utf-8")
error_handler.setLevel(logging.ERROR)
logging.getLogger().addHandler(error_handler)

#ПОЛУЧЕНИЕ ТОКЕНА БОТА
bot = telebot.TeleBot(os.getenv("BOT_TOKEN"))

#ПОЛУЧЕНИЕ ДАННЫХ ИЗ API
@safe_execute
def get_weather_data(city, user):
    """Получает и форматирует данные о погоде, используя API или тестовые значения."""
    global test_weather_data

    if TEST:
        if test_weather_data is None:
            logging.warning("🧪 ТЕСТОВЫЙ РЕЖИМ АКТИВИРОВАН! Используем случайные данные для всех городов.")
            test_weather_data = {
                "temperature": round(random.uniform(-30, 40), 1),
                "humidity": random.randint(10, 100),
                "wind_speed": round(random.uniform(0, 25), 1),
                "description": random.choice(["ясно", "облачно", "дождь", "снег", "гроза"]),
                "pressure": random.randint(950, 1050),
                "visibility": random.randint(100, 10000)
            }
        return test_weather_data  

    weather_data = get_weather(city)
    if not weather_data:
        logging.error(f"❌ Ошибка получения данных о погоде для {city}")
        return None

    return format_weather_data(weather_data, user)

@safe_execute
def watchdog_timer():
    global last_log_time
    while True:
        time.sleep(3600 + 120)
        if time.time() - last_log_time > 3600 + 110:
            logging.critical("⏳ Чекер завис! Перезапуск...")
            os._exit(1)

@safe_execute
def update_last_log():
    global last_log_time
    last_log_time = time.time()

threading.Thread(target=watchdog_timer, daemon=True).start()

@safe_execute
def check_weather_changes_for_city(city):
    session = Session()
    current_data = get_weather_data(city)
    if not current_data:
        logging.error(f"❌ Не удалось получить данные для города: {city}")
        session.close()
        return
    
    def format_change(label, old_value, new_value, unit=""):
        if old_value is None or old_value != new_value:
            arrow = "📈" if new_value > old_value else "📉"
            return f"**{label}: {new_value}{unit} {arrow}**"
        return f"{label}: {new_value}{unit}"

    
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
        pressure_diff = abs(current_data["pressure"] - (city_record.pressure or 0))
        visibility_diff = abs(current_data["visibility"] - (city_record.visibility or 0))
        logging.info(f"{city} | Температура: {city_record.temperature}°C → {current_data['temperature']}°C (ΔT: {temp_diff}°C)")
        logging.info(f"{city} | Погода: {city_record.description} → {current_data['description']}")
        logging.info(f"{city} | Влажность: {city_record.last_humidity}% → {current_data['humidity']}% (ΔH: {humidity_diff}%)")
        logging.info(f"{city} | Ветер: {city_record.last_wind_speed} м/с → {current_data['wind_speed']} м/с (ΔW: {wind_diff} м/с)")
        logging.info(f"{city} | Давление: {city_record.pressure} мм → {current_data['pressure']} мм (ΔP: {pressure_diff} мм)")
        logging.info(f"{city} | Видимость: {city_record.visibility} м → {current_data['visibility']} м (ΔV: {visibility_diff} м)")

        if temp_diff >= 3 or humidity_diff >= 10 or wind_diff > 2:
            significant_change = True
    else:
        significant_change = False

    if significant_change:
        alert_message = (f"🔔 *Внимание! Погода в г.{city} изменилась!*\n"
                 f"\n"
                 f"▸ Погода: *{current_data['description'].capitalize()}*\n"
                 f"{format_change('▸ Температура', city_record.temperature, current_data['temperature'], '°C')}\n"
                 f"{format_change('▸ Влажность', city_record.last_humidity, current_data['humidity'], '%')}\n"
                 f"{format_change('▸ Скорость ветра', city_record.last_wind_speed, current_data['wind_speed'], ' м/с')}\n"
                 f"{format_change('▸ Давление', city_record.pressure, current_data['pressure'], ' мм')}\n"
                 f"{format_change('▸ Видимость', city_record.visibility, current_data['visibility'], ' м')}")
        users = session.query(User).filter(User.preferred_city == city, User.notifications_enabled == True).all()
        for user in users:
            try: 
                bot.send_message(user.user_id, alert_message, parse_mode="Markdown")
                logging.info(f"📩 Уведомление отправлено: {user.user_id} ({city})\n")
            except Exception as e:
                logging.error(f"❌ Ошибка отправки уведомления {user.user_id}: {e}")

    if city_record:
        city_record.last_humidity = current_data["humidity"]
        city_record.last_wind_speed = current_data["wind_speed"]
        city_record.temperature = current_data["temperature"]
        city_record.weather_info = json.dumps(current_data, ensure_ascii=False)
        city_record.pressure = current_data["pressure"]
        city_record.visibility = current_data["visibility"]
        city_record.description = current_data["description"]
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
    return True

@safe_execute
def check_all_cities():
    session = Session()
    cities = session.query(User.preferred_city).filter(User.notifications_enabled == True).distinct().all()
    session.close()

    cities = {city[0] for city in cities if city[0]} 
    checked_cities = set()  

    attempt = 1
    max_attempts = 3  

    while cities - checked_cities and attempt <= max_attempts:
        remaining_cities = cities - checked_cities 

        logging.info(f"🔄 Попытка #{attempt}: Проверяем {len(remaining_cities)} оставшихся городов...")

        for city in remaining_cities:
            success = check_weather_changes_for_city(city)  

            if success:
                checked_cities.add(city)  
                logging.info(f"✅ {city} добавлен в проверенные города.")

        attempt += 1  

    if cities - checked_cities:
        logging.warning(f"⚠️ Остались непроверенные города: {cities - checked_cities}")

@safe_execute
def notify_admin(message):
    ADMIN_ID = os.getenv("ADMIN_ID")
    if ADMIN_ID:
        try:
            bot.send_message(ADMIN_ID, f"🚨 Внимание! {message}")
        except Exception as e:
            logging.error(f"❌ Ошибка при отправке уведомления админу: {e}")

if __name__ == '__main__':
    while True:
        now = datetime.utcnow()
        next_run = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

        if now - datetime.utcfromtimestamp(last_log_time) < timedelta(hours=1):
            logging.info("⏭ Пропуск проверки, так как последний запуск был менее часа назад.")
        else:
            try:
                logging.info("🔄 Запуск цикла проверки погоды...")
                check_all_cities()
                update_last_log()
                logging.info("✅ Проверка завершена. Ожидание следующего цикла...")
            except Exception as e:
                logging.critical(f"🔥 Критическая ошибка в основном цикле: {e}")
                notify_admin(f"Чекер упал! Ошибка: {e}") 

        sleep_time = max(0, (next_run - datetime.utcnow()).total_seconds())
        logging.info(f"🕒 Следующая проверка через {round(sleep_time)} секунд ({next_run})")
        time.sleep(sleep_time)
