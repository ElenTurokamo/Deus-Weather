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

load_dotenv()

if not os.path.exists("logs"):
    os.makedirs("logs")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("logs/weather_checker.log", encoding="utf-8"),
        logging.StreamHandler()                                                                                                                                 
    ]
)

logging.info("🚀 Чекер запущен и логируется в logs/weather_checker.log")

last_log_time = time.time()

DB_URL = os.getenv("DATABASE_URL")
engine = create_engine(DB_URL, echo=False)
Session = sessionmaker(bind=engine)

bot = telebot.TeleBot(os.getenv("BOT_TOKEN"))

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
        "temperature": round(resp_data["main"]["temp"], 1),
        "humidity": round(resp_data["main"]["humidity"]),
        "wind_speed": round(resp_data["wind"]["speed"], 1),
        "description": resp_data["weather"][0]["description"],
        "pressure": round(resp_data["main"]["pressure"] * 0.75006),
        "visibility": resp_data.get("visibility", "Неизвестно")
    }

@safeexecute
def watchdog_timer():
    global last_log_time
    while True:
        time.sleep(3600 + 60)
        if time.time() - last_log_time > 3600 + 50:
            logging.critical("⏳ Чекер завис! Перезапускаем...")
            os._exit(1)

@safeexecute
def update_last_log():
    global last_log_time
    last_log_time = time.time()

threading.Thread(target=watchdog_timer, daemon=True).start()

@safeexecute
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
        logging.info(f"🌡 {city} | Температура: {city_record.temperature}°C → {current_data['temperature']}°C (ΔT: {temp_diff}°C)")
        logging.info(f"🌥 {city} | Погода: {city_record.description} → {current_data['description']}")
        logging.info(f"💧 {city} | Влажность: {city_record.last_humidity}% → {current_data['humidity']}% (ΔH: {humidity_diff}%)")
        logging.info(f"💨 {city} | Ветер: {city_record.last_wind_speed} м/с → {current_data['wind_speed']} м/с (ΔW: {wind_diff} м/с)")
        logging.info(f"📊 {city} | Давление: {city_record.pressure} мм → {current_data['pressure']} мм (ΔP: {pressure_diff} мм)")
        logging.info(f"👀 {city} | Видимость: {city_record.visibility} м → {current_data['visibility']} м (ΔV: {visibility_diff} м)")

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
                logging.info(f"📩 Уведомление отправлено: {user.user_id} ({city})")
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

@safeexecute
def check_all_cities():
    session = Session()
    cities = session.query(User.preferred_city).filter(User.notifications_enabled == True).distinct().all()
    session.close()

    cities = {city[0] for city in cities if city[0]}  # Убираем пустые значения
    checked_cities = set()  # Храним успешно проверенные города

    attempt = 1
    max_attempts = 3  # Количество повторных проверок, если остались непройденные города

    while cities - checked_cities and attempt <= max_attempts:
        remaining_cities = cities - checked_cities  # Города, которые еще не проверены

        logging.info(f"🔄 Попытка #{attempt}: Проверяем {len(remaining_cities)} оставшихся городов...")

        for city in remaining_cities:
            success = check_weather_changes_for_city(city)  # Проверяем город

            if success:
                checked_cities.add(city)  # Если проверка успешна, добавляем в список проверенных
                logging.info(f"✅ {city} добавлен в проверенные города.")

        attempt += 1  # Переходим к следующей попытке, если не все города проверены

    # Если после всех попыток остались непроверенные города, логируем ошибку
    if cities - checked_cities:
        logging.warning(f"⚠️ Остались непроверенные города: {cities - checked_cities}")

@safeexecute
def notify_admin(message):
    ADMIN_ID = os.getenv("ADMIN_ID")
    if ADMIN_ID:
        try:
            bot.send_message(ADMIN_ID, f"🚨 Внимание! {message}")
        except Exception as e:
            logging.error(f"❌ Ошибка при отправке уведомления админу: {e}")

if __name__ == '__main__':
    while True:
        try:
            logging.info("🔄 Запуск цикла проверки погоды...")
            check_all_cities()
            logging.info("✅ Проверка завершена. Ожидание следующего цикла...")
        except Exception as e:
            logging.critical(f"🔥 Критическая ошибка в основном цикле: {e}")
            notify_admin(f"Чекер упал! Ошибка: {e}") 
        now = datetime.utcnow()
        next_run = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        sleep_time = max(0, (next_run - now).total_seconds())
        logging.info(f"🕒 Следующая проверка через {round(sleep_time)} секунд ({next_run})")
        time.sleep(sleep_time)