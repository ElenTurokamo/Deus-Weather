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
from logic import safe_execute, format_weather_data, format_change, convert_pressure, convert_precipitation_to_percent, convert_temperature, convert_wind_speed, decode_tracked_params, UNIT_TRANSLATIONS
from weather import get_weather
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine    
from threading import Event
from logging.handlers import RotatingFileHandler
from bot import last_menu_message, menu_option, last_settings_command, settings_option

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
        if TEST:
            timer_logger.info(f"⚡ [ТЕСТ] Эмулируем погоду для {city}")
            current_data = {
                "temp": round(random.uniform(-10, 35), 1),
                "humidity": random.randint(10, 100),
                "wind_speed": round(random.uniform(0, 20), 1),
                "pressure": random.randint(950, 1050),
                "visibility": random.randint(1000, 10000),
                "description": random.choice(["Солнечно", "Дождь", "Облачно", "Гроза"])
            }

        users = db.query(User).filter(User.preferred_city == city, User.notifications_enabled == True).all()
        if not users:
            timer_logger.info(f"❌ Уведомления для города {city} отключены. Проверка завершена.")
            return True  

        city_data = db.query(CheckedCities).filter_by(city_name=city).first()

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
            return True 

        notify_users = False
        changed_params = {}

        for user in users:
            if isinstance(user.tracked_weather_params, str):
                try:
                    tracked_params = json.loads(user.tracked_weather_params)
                except json.JSONDecodeError as e:
                    timer_logger.error(f"✦ Ошибка парсинга JSON в tracked_weather_params для пользователя {user.user_id}: {e}")
                    continue
            else:
                tracked_params = user.tracked_weather_params

            for param in tracked_params:
                if param in ["description"]:  
                    continue

                if param in current_data:
                    old_value = getattr(city_data, f"last_{param}", None)
                    new_value = current_data[param]

                    try:
                        old_value = float(old_value) if old_value is not None else None
                        new_value = float(new_value)
                    except ValueError as e:
                        timer_logger.error(f"✦ Невозможно преобразовать параметр {param} в число: {e}")
                        continue

                    if old_value is not None and abs(new_value - old_value) > get_threshold(param):
                        changed_params[param] = (old_value, new_value)
                        notify_users = True

        if notify_users:
            send_weather_update(users, city, changed_params, current_data)

        city_data.last_temperature = current_data["temp"]
        city_data.last_wind_speed = current_data["wind_speed"]
        city_data.last_humidity = current_data["humidity"]
        city_data.last_pressure = current_data["pressure"]
        city_data.last_visibility = current_data["visibility"]
        city_data.last_description = current_data["description"]

        db.commit()
        timer_logger.info(f"✅ Данные о городе {city} обновлены.")
        return True 

    except Exception as e:
        db.rollback()
        timer_logger.error(f"✦ Ошибка при обработке города {city}: {e}")
        return False

    finally:
        db.close()
        timer_logger.debug(f"▸ Соединение с БД для города {city} закрыто.")

def get_threshold(param):
    """Возвращает порог изменения для уведомления"""
    thresholds = {
        "temperature": 2.0,  # Изменение температуры на 2°C
        "humidity": 10,  # Изменение влажности на 10%
        "wind_speed": 2,  # Изменение скорости ветра на 2 м/с
        "pressure": 5,  # Изменение давления на 5 мм рт. ст.
        "visibility": 1500  # Изменение видимости на 500 м
    }
    return thresholds.get(param, 0)

def send_weather_update(users, city, changes, current_data):
    """Отправляет уведомления пользователям о погоде, исключая отключённые параметры, но сохраняя неизменённые."""
    for user in users:
        tracked_params = decode_tracked_params(user.tracked_weather_params)

        if not any(tracked_params.values()):
            timer_logger.info(f"🚫 Уведомление не отправлено пользователю {user.user_id} — все параметры отключены.")
            continue
        chat_id = user.user_id

        # Удаляем предыдущее декоративное сообщение (если оно есть)
        if chat_id in last_menu_message:
            try:
                bot.delete_message(chat_id, last_menu_message[chat_id])
                del last_menu_message[chat_id]
                timer_logger.debug(f"🗑 Удалено старое декоративное сообщение для пользователя {chat_id}.")
            except Exception as e:
                timer_logger.warning(f"⚠ Не удалось удалить декоративное сообщение для {chat_id}: {e}")

        def get_weather_emoji(current_data, changes):
            """Выбирает наиболее важный смайлик в зависимости от изменений погоды."""

            # Таблица приоритетов смайликов (чем выше число, тем важнее)
            priority = {
                "storm": (5, "⛈️"),  # Гроза, буря
                "hurricane_wind": (5, "🌪️"),  # Ураганный ветер (15+ м/с)
                "extreme_heat": (5, "🔥"),  # Очень жарко (30+°C)
                "extreme_cold": (5, "❄️"),  # Очень холодно (-15°C)
                "pressure_drop": (5, "‼️"),  # Резкое падение давления

                "strong_wind": (4, "💨"),  # Сильный ветер (10-15 м/с)
                "heavy_rain": (4, "☔"),  # Ливень
                "big_temp_change": (4, "🌡️"),  # Резкий скачок температуры (±10°C)
                "low_visibility": (4, "🌫️"),  # Сильный туман

                "cloudy": (3, "🌦️"),  # Переменная облачность
                "humidity_increase": (2, "💧"),  # Повышенная влажность (80+%)
                "small_pressure_change": (2, "📉"),  # Незначительное изменение давления
            }

            detected_events = []

            if "wind_speed" in changes:
                old, new = changes["wind_speed"]
                if new >= 15:
                    detected_events.append("hurricane_wind")
                elif new >= 10:
                    detected_events.append("strong_wind")

            if "temp" in changes:
                old, new = changes["temp"]
                diff = abs(new - old)
                if new >= 30:
                    detected_events.append("extreme_heat")
                elif new <= -15:
                    detected_events.append("extreme_cold")
                elif diff >= 10:
                    detected_events.append("big_temp_change")

            if "pressure" in changes:
                old, new = changes["pressure"]
                if abs(new - old) > 15:
                    detected_events.append("pressure_drop")
                elif abs(new - old) > 5:
                    detected_events.append("small_pressure_change")

            if "description" in current_data:
                description = current_data["description"].lower()
                if "гроза" in description or "буря" in description:
                    detected_events.append("storm")
                if "дождь" in description and "ливень" in description:
                    detected_events.append("heavy_rain")

            if "visibility" in changes:
                old, new = changes["visibility"]
                if new < 1000:
                    detected_events.append("low_visibility")

            # Выбираем самый приоритетный смайлик
            if detected_events:
                highest_priority_event = max(detected_events, key=lambda event: priority[event][0])
                return priority[highest_priority_event][1]

            return "🌦️"  # Обычные изменения
        
        # Заголовок
        emoji = get_weather_emoji(current_data, changes)
        header = f"<blockquote>{emoji} Внимание!⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀</blockquote>\nПогода в г.{city} изменилась!"
        line = "─" * min(len(header), 21)
        message = f"<b>{header}</b>\n{line}\n"

        params = {
            "description": ("Погода", current_data["description"], ""),
            "temperature": ("Температура", convert_temperature(current_data["temp"], user.temp_unit), UNIT_TRANSLATIONS["temp"].get(user.temp_unit, "°C")),
            "humidity": ("Влажность", int(current_data["humidity"]), "%"),
            "precipitation": ("Вероятность осадков", int(current_data.get("precipitation", 0)), "%"),
            "pressure": ("Давление", convert_pressure(current_data["pressure"], user.pressure_unit), UNIT_TRANSLATIONS["pressure"].get(user.pressure_unit, " мм рт. ст.")),
            "wind_speed": ("Скорость ветра", convert_wind_speed(current_data["wind_speed"], user.wind_speed_unit), UNIT_TRANSLATIONS["wind_speed"].get(user.wind_speed_unit, " м/с")),
            "visibility": ("Видимость", int(current_data["visibility"]), "м")
        }

        for param, (label, value, unit) in params.items():
            if not tracked_params.get(param, False):
                continue

            arrow = "▸" 
            value_str = f"{value}{unit}" 

            if param in changes:  
                old, new = changes[param]
                trend_emoji = "⇑" if new > old else "⇓"

                if param == "temperature":
                    old = convert_temperature(old, user.temp_unit)
                    new = convert_temperature(new, user.temp_unit)
                elif param == "pressure":
                    old = convert_pressure(old, user.pressure_unit)
                    new = convert_pressure(new, user.pressure_unit)
                elif param == "wind_speed":
                    old = convert_wind_speed(old, user.wind_speed_unit)
                    new = convert_wind_speed(new, user.wind_speed_unit)
                elif param in ["humidity", "visibility"]:
                    old, new = int(old), int(new)

                value_str = f"{old} {unit} ➝ {new} {unit}"
                arrow = trend_emoji

            message += f"{arrow} {label}: {value_str}\n"

        message += "\n      ⟪ Deus Weather ⟫"

        bot.send_message(user.user_id, message, parse_mode="HTML")
        timer_logger.info(f"▸ Уведомление отправлено пользователю {user.user_id}: {message}\n")

        if chat_id in last_settings_command:
            last_menu_message[chat_id] = settings_option(chat_id)
            timer_logger.debug(f"🔄 Повторно отправлено меню настроек для пользователя {chat_id}.")
        else:
            last_menu_message[chat_id] = menu_option(chat_id)
            timer_logger.debug(f"🔄 Повторно отправлено главное меню для пользователя {chat_id}.")

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
                    timer_logger.info(f"✅ {city} добавлен в проверенные города.\n")

        attempt += 1  

    if cities - checked_cities:
        timer_logger.warning(f"⚠️ Остались непроверенные города: {cities - checked_cities}")

    db.close() 

#ТАЙМЕР ЧЕКЕРА
def should_run_check():
    global old_start_time

    now = datetime.now(timezone.utc)
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    next_hour = current_hour + timedelta(hours=1)
    remaining_time = (next_hour - now).total_seconds()

    test_interval = 30 if TEST else 3600  

    if old_start_time is None:
        timer_logger.info("🚀 Первая проверка после запуска.")
        old_start_time = current_hour.timestamp() 
        return True, 0

    if time.time() - old_start_time < test_interval:
        timer_logger.info(f"⏳ Следующая проверка через {remaining_time:.2f} сек.")
        return False, min(test_interval, remaining_time)

    timer_logger.info("🕒 Наступил новый час, запускаем проверку погоды.")
    old_start_time = current_hour.timestamp()  
    return True, 0

if __name__ == '__main__':
    while True:
        run_check, wait_time = should_run_check()
        
        if run_check:
            check_all_cities() 
        else:
            timer_logger.info(f"⏳ Ждём {wait_time:.2f} секунд до следующей проверки.")
        
        time.sleep(wait_time) 