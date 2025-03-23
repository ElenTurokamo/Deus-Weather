#ИМОПРТЫ
import glob
import json
import time
import logging
import telebot
import os
import random

from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from functools import wraps
from models import CheckedCities, User, Base
from logic import safe_execute, format_weather_data, format_change, convert_pressure, convert_precipitation_to_percent, convert_temperature, convert_wind_speed
from weather import get_weather
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine    
from threading import Event
from logging.handlers import RotatingFileHandler

#ПЕРЕМЕННЫЕ
old_start_time = None
last_start_time = None
test_weather_data = None
last_log_time = time.time()
timer_start_time = time.time()
rounded_time = datetime.fromtimestamp(round(timer_start_time), timezone.utc)

#ОТЛАДКА
TEST = False #тестовый режим для проверки уведомлений (True - вкл, False - выкл.)

#ПОДКЛЮЧЕНИЕ К БД
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)

Base.metadata.create_all(engine)

#ШИФРОВАНИЕ
load_dotenv()

#СЛОВАРИ
stop_event = Event()

#ЛОГИРОВАНИЕ
LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "timer.log")

if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"

timer_logger = logging.getLogger("timer_logger")
timer_logger.setLevel(logging.DEBUG)
timer_logger.propagate = False 

if timer_logger.hasHandlers():
    timer_logger.handlers.clear()

file_handler = RotatingFileHandler(LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
file_handler.setLevel(logging.DEBUG)

console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter(LOG_FORMAT))
console_handler.setLevel(logging.DEBUG)

error_handler = logging.FileHandler(os.path.join(LOG_DIR, "errors_timer.log"), encoding="utf-8")
error_handler.setLevel(logging.ERROR)
error_handler.setFormatter(logging.Formatter(LOG_FORMAT))

timer_logger.addHandler(file_handler)
timer_logger.addHandler(console_handler)
timer_logger.addHandler(error_handler)

timer_logger.debug("🔍 DEBUG-логгер для таймера инициализирован.")
timer_logger.info("✅ Логирование для таймера настроено!")

#ПОЛУЧЕНИЕ ТОКЕНА БОТА
bot = telebot.TeleBot(os.getenv("BOT_TOKEN"), parse_mode="HTML", threaded=False)

#ПОЛУЧЕНИЕ ДАННЫХ ИЗ API
@safe_execute
def check_weather_changes(city, current_data):
    """Сравнивает полученные данные с предыдущими значениями и определяет, нужно ли уведомлять пользователя."""
    
    db = SessionLocal()
    try:
        # Получаем всех пользователей, у которых включены уведомления для данного города
        users = db.query(User).filter(User.preferred_city == city, User.notifications_enabled == True).all()
        if not users:
            timer_logger.info(f"❌ Уведомления для города {city} отключены. Проверка завершена.")
            return True  # Возвращаем True, чтобы город считался проверенным

        # Получаем данные о городе из БД
        city_data = db.query(CheckedCities).filter_by(city_name=city).first()

        # Если данных о городе нет, создаём новую запись
        if not city_data:
            timer_logger.warning(f"⚠️ В базе нет данных о {city}, записываем первые значения.")
            timer_logger.debug(f"📊 Данные, которые будем записывать в БД для {city}: {current_data}")
            new_entry = CheckedCities(
                city_name=city,
                temperature=current_data["temp"],
                humidity=current_data["humidity"],
                wind_speed=current_data["wind_speed"],
                pressure=current_data["pressure"],
                visibility=current_data["visibility"],
                description=current_data["description"],
                last_temperature=current_data["temp"],
                last_wind_speed=current_data["wind_speed"],
                last_humidity=current_data["humidity"],
                last_pressure=current_data["pressure"],
                last_visibility=current_data["visibility"],
                last_description=current_data["description"]
            )

            db.add(new_entry)
            db.commit()
            timer_logger.info(f"✅ Данные о городе {city} успешно записаны в БД.")
            return True  # Город проверен, данные добавлены

        # Сравниваем текущие данные с последними сохранёнными
        notify_users = False
        changed_params = {}

        for user in users:
            # Загружаем параметры, которые пользователь отслеживает
            if isinstance(user.tracked_weather_params, str):
                try:
                    tracked_params = json.loads(user.tracked_weather_params)
                except json.JSONDecodeError as e:
                    timer_logger.error(f"❌ Ошибка парсинга JSON в tracked_weather_params для пользователя {user.user_id}: {e}")
                    continue
            else:
                tracked_params = user.tracked_weather_params

            # Сравниваем параметры
            for param in tracked_params:
                if param in ["description"]:  # Пропускаем строковые параметры
                    continue

                if param in current_data:
                    old_value = getattr(city_data, f"last_{param}", None)
                    new_value = current_data[param]

                    # Преобразуем значения в числа
                    try:
                        old_value = float(old_value) if old_value is not None else None
                        new_value = float(new_value)
                    except ValueError as e:
                        timer_logger.error(f"❌ Невозможно преобразовать параметр {param} в число: {e}")
                        continue

                    # Проверяем, превысило ли изменение порог
                    if old_value is not None and abs(new_value - old_value) > get_threshold(param):
                        changed_params[param] = (old_value, new_value)
                        notify_users = True

        # Если есть изменения, отправляем уведомления
        if notify_users:
            send_weather_update(users, city, changed_params)

        # Обновляем данные о городе в БД
        city_data.last_temperature = current_data["temp"]
        city_data.last_wind_speed = current_data["wind_speed"]
        city_data.last_humidity = current_data["humidity"]
        city_data.last_pressure = current_data["pressure"]
        city_data.last_visibility = current_data["visibility"]
        city_data.last_description = current_data["description"]

        db.commit()
        timer_logger.info(f"✅ Данные о городе {city} обновлены.")
        return True  # Проверка завершена успешно

    except Exception as e:
        db.rollback()
        timer_logger.error(f"❌ Ошибка при обработке города {city}: {e}")
        return False  # Возвращаем False в случае ошибки

    finally:
        db.close()
        timer_logger.debug(f"📌 Соединение с БД для города {city} закрыто.")

def get_threshold(param):
    """Возвращает порог изменения для уведомления"""
    thresholds = {
        "temperature": 2.0,  # Изменение температуры на 2°C
        "humidity": 10,  # Изменение влажности на 10%
        "wind_speed": 2,  # Изменение скорости ветра на 2 м/с
        "pressure": 5,  # Изменение давления на 5 мм рт. ст.
        "visibility": 500  # Изменение видимости на 500 м
    }
    return thresholds.get(param, 0)

def send_weather_update(users, city, changes):
    """Отправляет уведомления пользователям о значительных изменениях в погоде"""
    for user in users:
        message = f"🌤 Погода в {city} изменилась:\n"
        
        for param, (old, new) in changes.items():
            if param == "temperature":
                converted_old = convert_temperature(old, user.temp_unit)
                converted_new = convert_temperature(new, user.temp_unit)
                unit = user.temp_unit
            elif param == "pressure":
                converted_old = convert_pressure(old, user.pressure_unit)
                converted_new = convert_pressure(new, user.pressure_unit)
                unit = user.pressure_unit
            elif param == "wind_speed":
                converted_old = convert_wind_speed(old, user.wind_speed_unit)
                converted_new = convert_wind_speed(new, user.wind_speed_unit)
                unit = user.wind_speed_unit
            else:
                converted_old, converted_new, unit = old, new, ""

            message += f"🔹 {param}: {converted_old} → {converted_new} {unit}\n"
        
        bot.send_message(user.user_id, message)
        timer_logger.info(f"📩 Уведомление отправлено пользователю {user.user_id}: {message}")

@safe_execute
def check_all_cities():
    """Проверяет все города, для которых включены уведомления."""
    db = SessionLocal()
    cities = db.query(User.preferred_city).filter(User.notifications_enabled == True).distinct().all()
    
    cities = {city[0] for city in cities if city[0]} 
    checked_cities = set()  

    attempt = 1
    max_attempts = 3  

    while cities - checked_cities and attempt <= max_attempts:
        remaining_cities = cities - checked_cities 
        timer_logger.info(f"🔄 Попытка #{attempt}: Проверяем {len(remaining_cities)} оставшихся городов...")

        for city in remaining_cities:
            weather_data = get_weather(city)
            if weather_data:
                success = check_weather_changes(city, weather_data)

                if success:
                    checked_cities.add(city)  
                    timer_logger.info(f"✅ {city} добавлен в проверенные города.")

        attempt += 1  

    if cities - checked_cities:
        timer_logger.warning(f"⚠️ Остались непроверенные города: {cities - checked_cities}")

    db.close() 

#ТАЙМЕР ЧЕКЕРА
def should_run_check():
    global old_start_time, last_start_time
    last_start_time = time.time()

    first_run = old_start_time is None

    if first_run:
        timer_logger.info("🚀 Первая проверка после запуска.")
        old_start_time = last_start_time  
        return True, 0  

    now = datetime.now(timezone.utc)
    next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    remaining_time = (next_hour - now).total_seconds()

    if remaining_time <= 0:  
        timer_logger.info("🕒 Наступил новый час, запускаем проверку погоды.")
        old_start_time = last_start_time
        return True, 0  
    else:
        timer_logger.info(f"⏳ Следующая проверка в {next_hour.strftime('%H:%M:%S UTC')}, через {remaining_time:.2f} сек.")
        return False, remaining_time

if __name__ == '__main__':
    while True:
        run_check, wait_time = should_run_check()
        
        if run_check:
            check_all_cities() 
        else:
            timer_logger.info(f"⏳ Ждём {wait_time:.2f} секунд до следующей проверки.")
        
        time.sleep(wait_time) 