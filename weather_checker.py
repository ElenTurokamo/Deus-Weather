#ИМОПРТЫ
import glob
import json
import time
import logging
import telebot
import os
import threading
import random

from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from functools import wraps
from models import CheckedCities, User
from logic import safe_execute, format_weather_data, format_change
from weather import get_weather
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine    

#ПЕРЕМЕННЫЕ
old_start_time = None
last_start_time = None
test_weather_data = None
last_log_time = time.time()

#ОТЛАДКА
TEST = True #тестовый режим для проверки уведомлений (True - вкл, False - выкл.)

#ПОДКЛЮЧЕНИЕ К БД
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)

#ШИФРОВАНИЕ
load_dotenv()

#ЛОГИРОВАНИЕ
LOG_DIR = "logs"
LOG_LIFETIME_DAYS = 7  

@safe_execute
def clean_old_logs():
    """Удаляет логи, старше LOG_LIFETIME_DAYS, кроме занятых файлов."""
    now = time.time()
    for log_file in glob.glob(os.path.join(LOG_DIR, "*.log")):
        if os.path.isfile(log_file):
            try:
                with open(log_file, "a"):  # Пробуем открыть в режиме дозаписи
                    file_time = os.path.getmtime(log_file)
                    if now - file_time > LOG_LIFETIME_DAYS * 86400:
                        os.remove(log_file)
                        logging.info(f"🗑 Удалён старый лог: {log_file}")
            except PermissionError:
                logging.warning(f"⚠ Файл {log_file} занят другим процессом, пропускаем.")
            except Exception as e:
                logging.warning(f"⚠ Ошибка при удалении {log_file}: {e}")

clean_old_logs()

logging.basicConfig(
    level=logging.DEBUG,
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
        logging.info(f"🧪 Тестовые данные для {city}: {test_weather_data}")
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
def check_weather_changes_for_city(city, SessionLocal):
    """Проверяет изменения погоды, отправляет уведомления пользователям и обновляет базу."""
    
    current_data = get_weather(city)
    if not current_data:
        logging.error(f"❌ Не удалось получить данные для города: {city}")
        return False

    now = datetime.now(timezone.utc)
    city_record = SessionLocal.query(CheckedCities).filter_by(city_name=city).first()

    if city_record and city_record.last_checked:
        time_diff = now - city_record.last_checked
        logging.info(f"📍 {city} | Проверка: {now} (разница {time_diff})")

        if time_diff < timedelta(minutes=30): 
            logging.info(f"⏭ Пропуск проверки для {city}, так как проверка была недавно")
            SessionLocal.close()
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

        if temp_diff >= 3 or humidity_diff >= 10 or wind_diff > 2 or pressure_diff > 5 or visibility_diff > 500:
            significant_change = True

    if significant_change:
        alert_message = (f"🔔 <b>Внимание! Погода в {city} изменилась!</b>\n"
                         f"\n"
                         f"▸ Погода: <b>{current_data['description'].capitalize()}</b>\n"
                         f"{format_change('▸ Температура', city_record.temperature, current_data['temperature'], '°C')}\n"
                         f"{format_change('▸ Влажность', city_record.last_humidity, current_data['humidity'], '%')}\n"
                         f"{format_change('▸ Скорость ветра', city_record.last_wind_speed, current_data['wind_speed'], ' м/с')}\n"
                         f"{format_change('▸ Давление', city_record.pressure, current_data['pressure'], ' мм')}\n"
                         f"{format_change('▸ Видимость', city_record.visibility, current_data['visibility'], ' м')}")
        
        users = SessionLocal.query(User).filter(User.preferred_city == city, User.notifications_enabled == True).all()
        for user in users:
            try:
                bot.send_message(user.user_id, alert_message, parse_mode="HTML") 
                logging.info(f"📩 Уведомление отправлено: {user.user_id} ({city})")
            except Exception as e:
                logging.error(f"❌ Ошибка отправки уведомления {user.user_id}: {e}")

    try:
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
            SessionLocal.add(new_record)

        SessionLocal.commit()
    except Exception as e:
        logging.error(f"❌ Ошибка при обновлении данных в БД: {e}")
        SessionLocal.rollback()
    finally:
        SessionLocal.close() 

    return True


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
        logging.info(f"🔄 Попытка #{attempt}: Проверяем {len(remaining_cities)} оставшихся городов...")

        for city in remaining_cities:
            success = check_weather_changes_for_city(city, db) 

            if success:
                checked_cities.add(city)  
                logging.info(f"✅ {city} добавлен в проверенные города.")

        attempt += 1  

    if cities - checked_cities:
        logging.warning(f"⚠️ Остались непроверенные города: {cities - checked_cities}")

    db.close() 

#ТАЙМЕР ЧЕКЕРА
def should_run_check():
    global old_start_time, last_start_time
    last_start_time = time.time() 

    if old_start_time is None:
        logging.info("🚀 Первая проверка, так как old_start_time отсутствует.")
        old_start_time = last_start_time
        return True, 0 

    elapsed_time = last_start_time - old_start_time
    remaining_time = max(0, 3600 - elapsed_time)

    logging.info(f"⏳ Прошло с последней проверки: {elapsed_time:.2f} сек. "
                 f"Осталось до следующей проверки: {remaining_time:.2f} сек.")

    if elapsed_time >= 3600:
        logging.info("🕒 Прошёл час, запускаем проверку погоды.")
        old_start_time = last_start_time
        return True, 0  
    else:
        return False, remaining_time 


if __name__ == '__main__':
    while True:
        run_check, wait_time = should_run_check()
        
        if run_check:
            check_all_cities() 
        else:
            logging.info(f"⏳ Ждём {wait_time:.2f} секунд до следующей проверки.")
        
        time.sleep(wait_time) 