#ИМПОРТЫ
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
# ДОБАВЛЕНЫ: get_user_lang, get_text, get_translation_dict
from logic import (
    safe_execute, convert_pressure, convert_temperature, convert_wind_speed, 
    decode_tracked_params, get_weather_summary_description, 
    get_user_lang, get_text, get_translation_dict, # <-- ВАЖНО
    get_all_users, decode_notification_settings, get_wind_direction, 
    get_today_forecast
)
from weather import get_weather, fetch_today_forecast
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from sqlalchemy.pool import QueuePool    
from threading import Event
from logging.handlers import RotatingFileHandler
from bot import get_data_field, update_data_field, send_main_menu, send_settings_menu, format_forecast
from zoneinfo import ZoneInfo

#ПЕРЕМЕННЫЕ
old_start_time = None
last_start_time = None
test_weather_data = None
last_log_time = time.time()
timer_start_time = time.time()
rounded_time = datetime.fromtimestamp(round(timer_start_time), timezone.utc)

#ОТЛАДКА
TEST = False  #тестовый режим для проверки уведомлений (True - вкл, False - выкл.)

#ПОДКЛЮЧЕНИЕ К БД
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL, poolclass=QueuePool, pool_recycle=280, pool_pre_ping=True, echo=False)
SessionLocal = sessionmaker(bind=engine)

Base.metadata.create_all(engine)


#ШИФРОВАНИЕ
load_dotenv()


#СЛОВАРИ
stop_event = Event()
changed_cities_cache = {}

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
        timer_logger.info(f"📍 Начата проверка изменений погоды для города: {city}")

        if TEST:
            current_data = {
                "temp": round(random.uniform(-10, 40), 1),
                "feels_like": round(random.uniform(-10, 40), 1),
                "humidity": random.randint(10, 100),
                "wind_speed": round(random.uniform(0, 10), 1),
                "wind_direction": random.randint(0, 360),
                "wind_gust": round(random.uniform(0, 10), 1),
                "pressure": random.randint(950, 1050),
                "visibility": random.randint(1000, 10000),
                "clouds": random.randint(0, 100),
                "precipitation": round(random.uniform(0, 100), 1),
                "description": random.choice([
                    # Грозы
                    "Гроза с небольшим дождём", "Гроза с дождём", "Гроза с сильным дождём",
                    "Слабая гроза", "Гроза", "Сильная гроза", "Неустойчивая гроза",
                    "Гроза с лёгкой моросью", "Гроза с моросью", "Гроза с сильной моросью",

                    # Морось
                    "Лёгкая морось", "Морось", "Сильная морось",
                    "Лёгкий моросящий дождь", "Моросящий дождь", "Сильный моросящий дождь",
                    "Ливень и морось", "Сильный ливень и морось", "Моросящий ливень",

                    # Дождь
                    "Небольшой дождь", "Умеренный дождь", "Сильный дождь", "Очень сильный дождь",
                    "Чрезвычайно сильный дождь", "Ледяной дождь",
                    "Лёгкий ливень", "Ливень", "Сильный ливень", "Неустойчивый ливень",

                    # Снег
                    "Небольшой снег", "Снег", "Сильный снег",
                    "Мокрый снег", "Слабый ливень с мокрым снегом", "Ливень с мокрым снегом",
                    "Небольшой дождь со снегом", "Дождь со снегом",
                    "Слабый ливень со снегом", "Ливень со снегом", "Сильный ливень со снегом",
                ])
            }

        users = db.query(User).filter(User.preferred_city == city).all()
        users_with_notifications = [
            user for user in users
            if decode_notification_settings(user.notifications_settings).get("weather_threshold_notifications", False)
        ]
        if not users_with_notifications:
            timer_logger.info(f"▸ Нет пользователей с включёнными уведомлениями для города {city}. Проверка завершена.")
            return True

        city_data = db.query(CheckedCities).filter_by(city_name=city).first()
        precip_current = current_data.get("precipitation", 0.0)

        if not city_data:
            new_entry = CheckedCities(
                city_name=city,
                temperature=current_data["temp"],
                feels_like=current_data["feels_like"],
                humidity=current_data["humidity"],
                wind_speed=current_data["wind_speed"],
                wind_direction=current_data["wind_direction"],
                wind_gust=current_data["wind_gust"],
                pressure=current_data["pressure"],
                visibility=current_data["visibility"],
                clouds=current_data["clouds"],
                precipitation=precip_current,
                description=current_data["description"],
                last_temperature=current_data["temp"],
                last_feels_like=current_data["feels_like"],
                last_humidity=current_data["humidity"],
                last_wind_speed=current_data["wind_speed"],
                last_wind_direction=current_data["wind_direction"],
                last_wind_gust=current_data["wind_gust"],
                last_pressure=current_data["pressure"],
                last_visibility=current_data["visibility"],
                last_clouds=current_data["clouds"],
                last_precipitation=precip_current,
                last_description=current_data["description"]
            )
            db.add(new_entry)
            db.commit()
            timer_logger.info(f"✅ Город {city} добавлен в проверенные города.")
            return True

        # Проверка изменений
        description_changed_critically = False
        changed_params = {}
        important_descriptions = get_threshold("description")

        if city_data.last_temperature != current_data["temp"]:
            changed_params["temperature"] = (city_data.last_temperature, current_data["temp"])
        if city_data.last_feels_like != current_data["feels_like"]:
            changed_params["feels_like"] = (city_data.last_feels_like, current_data["feels_like"])
        if city_data.last_humidity != current_data["humidity"]:
            changed_params["humidity"] = (city_data.last_humidity, current_data["humidity"])
        if city_data.last_wind_speed != current_data["wind_speed"]:
            changed_params["wind_speed"] = (city_data.last_wind_speed, current_data["wind_speed"])
        if city_data.last_wind_direction != current_data["wind_direction"]:
            changed_params["wind_direction"] = (city_data.last_wind_direction, current_data["wind_direction"])
        if city_data.last_wind_gust != current_data["wind_gust"]:
            changed_params["wind_gust"] = (city_data.last_wind_gust, current_data["wind_gust"])
        if city_data.last_pressure != current_data["pressure"]:
            changed_params["pressure"] = (city_data.last_pressure, current_data["pressure"])
        if city_data.last_visibility != current_data["visibility"]:
            changed_params["visibility"] = (city_data.last_visibility, current_data["visibility"])
        if city_data.last_clouds != current_data["clouds"]:
            changed_params["clouds"] = (city_data.last_clouds, current_data["clouds"])
        if city_data.last_precipitation != precip_current:
            changed_params["precipitation"] = (city_data.last_precipitation, precip_current)
        if city_data.last_description != current_data["description"]:
            changed_params["description"] = (city_data.last_description, current_data["description"])
            if isinstance(current_data["description"], str):
                if current_data["description"].lower() in [desc.lower() for desc in important_descriptions]:
                    description_changed_critically = True

        if description_changed_critically:
            full_changed_params = {}
            for key in current_data:
                last_field = f"last_{key}" if key != "temp" else "last_temperature"
                current_value = current_data["temp"] if key == "temp" else current_data.get(key)
                db_value = getattr(city_data, last_field, None)
                if db_value != current_value:
                    full_changed_params[key] = (db_value, current_value)

            timer_logger.info(f"📢 Важное изменение description для города {city}: {changed_params}")

            changed_cities_cache[city] = {
                "current_data": current_data,
                "changed_params": full_changed_params
            }
        else:
            timer_logger.info(f"▸ Нет критических изменений погоды для города {city}")

        # Обновляем last_* и текущие значения
        city_data.last_temperature = city_data.temperature
        city_data.last_feels_like = city_data.feels_like
        city_data.last_humidity = city_data.humidity
        city_data.last_wind_speed = city_data.wind_speed
        city_data.last_wind_direction = city_data.wind_direction
        city_data.last_wind_gust = city_data.wind_gust
        city_data.last_pressure = city_data.pressure
        city_data.last_visibility = city_data.visibility
        city_data.last_clouds = city_data.clouds
        city_data.last_precipitation = city_data.precipitation
        city_data.last_description = city_data.description

        city_data.temperature = current_data["temp"]
        city_data.feels_like = current_data["feels_like"]
        city_data.humidity = current_data["humidity"]
        city_data.wind_speed = current_data["wind_speed"]
        city_data.wind_direction = current_data["wind_direction"]
        city_data.wind_gust = current_data["wind_gust"]
        city_data.pressure = current_data["pressure"]
        city_data.visibility = current_data["visibility"]
        city_data.clouds = current_data["clouds"]
        city_data.precipitation = precip_current
        city_data.description = current_data["description"]

        db.commit()
        timer_logger.info(f"✅ Данные о городе {city} успешно обновлены.")
        return True

    except Exception as e:
        db.rollback()
        timer_logger.error(f"✦ Ошибка при обработке города {city}: {e}")
        return False

    finally:
        db.close()
        timer_logger.info(f"▸ Соединение с БД для города {city} закрыто.")


def get_threshold(param):
    """Возвращает порог изменения для уведомления"""
    # Логика порогов остается без изменений, так как она привязана к системным значениям
    thresholds = {
        "description": [
                        # Грозы
                        "Гроза с небольшим дождём", "Гроза с дождём", "Гроза с сильным дождём",
                        "Слабая гроза", "Гроза", "Сильная гроза", "Неустойчивая гроза",
                        "Гроза с лёгкой моросью", "Гроза с моросью", "Гроза с сильной моросью",

                        # Морось
                        "Лёгкая морось", "Морось", "Сильная морось",
                        "Лёгкий моросящий дождь", "Моросящий дождь", "Сильный моросящий дождь",
                        "Ливень и морось", "Сильный ливень и морось", "Моросящий ливень",

                        # Дождь
                        "Небольшой дождь", "Умеренный дождь", "Сильный дождь", "Очень сильный дождь",
                        "Чрезвычайно сильный дождь", "Ледяной дождь",
                        "Лёгкий ливень", "Ливень", "Сильный ливень", "Неустойчивый ливень",

                        # Снег
                        "Небольшой снег", "Снег", "Сильный снег",
                        "Мокрый снег", "Слабый ливень с мокрым снегом", "Ливень с мокрым снегом",
                        "Небольшой дождь со снегом", "Дождь со снегом",
                        "Слабый ливень со снегом", "Ливень со снегом", "Сильный ливень со снегом",
                    ]
    }
    return thresholds.get(param, 0)

def get_weather_emoji(current_data):
    """Выбирает эмодзи на основе описания погоды (description)."""

    description_emoji_map = {
        "гроза": "⛈️",
        "буря": "⛈️",
        "шторм": "⛈️",
        "сильный ветер": "💨",
        "пыльная буря": "🌪️",
        "проливной дождь": "☔",
        "небольшой проливной дождь": "☔",
        "ливень": "☔",
        "дождь": "🌧️",
        "небольшой дождь": "🌦️",
        "снег": "🌨️",
        "небольшой снег": "🌨️",
        "град": "🌨️",
        # English mappings (simple fallback)
        "thunderstorm": "⛈️",
        "rain": "☔",
        "snow": "🌨️",
        "drizzle": "🌧️",
    }

    description = current_data.get("description", "").lower()

    for key, emoji in description_emoji_map.items():
        if key in description:
            return emoji

    return "🌤️"

def send_weather_update(users, city, changes, current_data):
    """Отправляет уведомления пользователям о погоде, сравнивая все параметры с данными в БД."""
    db = SessionLocal()  # Создаём сессию БД
    city_data = db.query(CheckedCities).filter_by(city_name=city).first()  # Получаем данные о городе
    if not city_data:
        timer_logger.warning(f"⚠ Не найдены данные города {city} в БД.")
        db.close()
        return

    for user in users:
        tracked_params = decode_tracked_params(user.tracked_weather_params)  # Получаем включённые параметры для пользователя

        if not any(tracked_params.values()):  # Если все параметры выключены — пропускаем
            timer_logger.info(f"🚫 Уведомление не отправлено пользователю {user.user_id} — все параметры отключены.")
            continue

        chat_id = user.user_id
        # --- АДАПТАЦИЯ ЯЗЫКА ---
        lang = get_user_lang(user)
        unit_trans = get_translation_dict("unit_translations", lang)
        labels = get_translation_dict("weather_data_labels", lang)
        # -----------------------

        # Удаляем старое меню, если оно было
        last_menu_id = get_data_field("last_menu_message", chat_id)
        if last_menu_id:
            try:
                bot.delete_message(chat_id, last_menu_id)
                update_data_field("last_menu_message", chat_id, None)
            except Exception as e:
                timer_logger.warning(f"⚠ Не удалось удалить декоративное сообщение для {chat_id}: {e}")

        # Заголовок с эмодзи и сообщением об изменении погоды
        emoji = get_weather_emoji(current_data)
        
        # ЛОКАЛИЗАЦИЯ ЗАГОЛОВКА
        header_text = get_text("notification_header", lang).format(emoji=emoji, city=city)
        message = f"{header_text}\n{'─' * 21}\n"

        if "temp" in current_data:
            current_data["temperature"] = current_data["temp"]

        # Конфигурация параметров, использующая локализованные метки
        param_config = {
            "description": (labels.get("description", "Погода"), "", lambda x: str(x).capitalize()),
            "temperature": (
                labels.get("temperature", "Температура"), "", 
                lambda x: f"{round(convert_temperature(x, user.temp_unit))}{unit_trans['temp'].get(user.temp_unit, '')}"
            ),
            "feels_like": (
                labels.get("feels_like", "Ощущается как"), "", 
                lambda x: f"{round(convert_temperature(x, user.temp_unit))}{unit_trans['temp'].get(user.temp_unit, '')}"
            ),
            "humidity": (
                labels.get("humidity", "Влажность"), "%", 
                lambda x: f"{int(x)}%"
            ),
            "precipitation": (
                labels.get("precipitation", "Вероятность осадков"), "%", 
                lambda x: f"{int(x)}%"
            ),
            "pressure": (
                labels.get("pressure", "Давление"), "", 
                lambda x: f"{round(convert_pressure(x, user.pressure_unit))} {unit_trans['pressure'].get(user.pressure_unit, '')}"
            ),
            "wind_speed": (
                labels.get("wind_speed", "Скорость ветра"), "", 
                lambda x: f"{round(convert_wind_speed(x, user.wind_speed_unit))} {unit_trans['wind_speed'].get(user.wind_speed_unit, '')}"
            ),
            "wind_direction": (
                labels.get("wind_direction", "Направление ветра"), "", 
                lambda x: f"{get_wind_direction(float(x), lang)} ({int(float(x))}°)"
            ),
            "wind_gust": (
                labels.get("wind_gust", "Порывы ветра"), "", 
                lambda x: f"{round(convert_wind_speed(x, user.wind_speed_unit))} {unit_trans['wind_speed'].get(user.wind_speed_unit, '')}"
            ),
            "clouds": (
                labels.get("clouds", "Облачность"), "%", 
                lambda x: f"{int(x)}%"
            ),
            "visibility": (
                labels.get("visibility", "Видимость"), "м", 
                lambda x: f"{int(x)} м"
            ),
        }
        
        arrow_up = get_text("notification_trend_up", lang)
        arrow_down = get_text("notification_trend_down", lang)
        arrow_default = get_text("notification_trend_arrow", lang)

        # Проходим по всем параметрам, даже если они не изменились
        for param, (label, _, formatter) in param_config.items():
            if not tracked_params.get(param, False):
                continue

            current = current_data.get(param)
            last = getattr(city_data, f"last_{param}", None)

            if current is None:
                continue

            arrow = arrow_default
            value_str = formatter(current)  # значение по умолчанию — текущее

            if param == "description":
                if last and current and str(last).lower() != str(current).lower():
                    arrow = arrow_up # Для описания просто показываем изменение
                    value_str = f"<b>{str(last).capitalize()} ➝ {str(current).capitalize()}</b>"
            else:
                try:
                    raw_current = float(current)
                    raw_last = float(last) if last is not None else None

                    if raw_last is not None and raw_last != raw_current:
                        # Конвертируем оба значения в пользовательские единицы
                        if param == "temperature":
                            new = round(convert_temperature(raw_current, user.temp_unit))
                            old = round(convert_temperature(raw_last, user.temp_unit))
                            unit = unit_trans["temp"][user.temp_unit]
                        elif param == "feels_like":
                            new = round(convert_temperature(raw_current, user.temp_unit))
                            old = round(convert_temperature(raw_last, user.temp_unit))
                            unit = unit_trans["temp"][user.temp_unit]
                        elif param == "pressure":
                            new = round(convert_pressure(raw_current, user.pressure_unit))
                            old = round(convert_pressure(raw_last, user.pressure_unit))
                            unit = unit_trans["pressure"][user.pressure_unit]
                        elif param in ("wind_speed", "wind_gust"):
                            new = round(convert_wind_speed(raw_current, user.wind_speed_unit))
                            old = round(convert_wind_speed(raw_last, user.wind_speed_unit))
                            unit = unit_trans["wind_speed"][user.wind_speed_unit]
                        elif param == "visibility":
                            new = int(raw_current)
                            old = int(raw_last)
                            unit = "м" # Можно добавить в словарь, если нужно
                        elif param in ("humidity", "precipitation", "clouds"):
                            new = int(raw_current)
                            old = int(raw_last)
                            unit = "%"
                        elif param == "wind_direction":
                            new_direction = get_wind_direction(raw_current, lang)
                            old_direction = get_wind_direction(raw_last, lang)
                            new_str = f"{new_direction} ({int(raw_current)}°)"
                            old_str = f"{old_direction} ({int(raw_last)}°)"
                        else:
                            new, old, unit = raw_current, raw_last, ""

                        trend = arrow_up if new > old else arrow_down
                        
                        if param == "wind_direction":
                             value_str = f"<b>{old_str} ➝ {new_str}</b>"
                        elif param in {"temperature", "feels_like", "pressure", "wind_speed", "wind_gust"}:
                            value_str = f"<b>{old} ➝ {new} {unit}</b>"
                        else:
                            value_str = f"<b>{old} ➝ {new}{unit}</b>"
                            
                        arrow = trend
                except Exception as e:
                    timer_logger.debug(f"⚠ Ошибка при сравнении {param}: {e}")

            # Добавляем строку в сообщение
            message += f"{arrow} {label}: {value_str}\n"

        # Завершающая строка
        message += get_text("notification_footer", lang)

        delete_previous_weather_notification(chat_id)
        sent_msg = bot.send_message(chat_id, message, parse_mode="HTML")
        update_data_field("last_weather_update", chat_id, sent_msg.message_id)
        timer_logger.info(f"▸ Уведомление отправлено пользователю {chat_id}:\n{message}")

        if get_data_field("last_settings_command", chat_id):
            send_settings_menu(chat_id)
        else:
            send_main_menu(chat_id)

    db.close()  # Закрываем соединение с БД


def delete_previous_weather_notification(chat_id):
    """Удаляет предыдущее уведомление об изменении погоды, если оно есть."""
    last_weather_msg_id = get_data_field("last_weather_update", chat_id)
    if last_weather_msg_id:
        try:
            bot.delete_message(chat_id, last_weather_msg_id)
            update_data_field("last_weather_update", chat_id, None)
            timer_logger.info(f"🗑 Предыдущее уведомление удалено у пользователя {chat_id}.")
        except Exception as e:
            timer_logger.warning(f"⚠ Не удалось удалить предыдущее уведомление у {chat_id}: {e}")


@safe_execute
def check_all_cities():
    """Проверяет все города, собирает изменения и рассылает уведомления пользователям."""
    db = SessionLocal()
    users = db.query(User).all()
    cities_to_check = set()

    for user in users:
        if user.preferred_city:
            settings = decode_notification_settings(user.notifications_settings)
            if settings.get("weather_threshold_notifications", False):
                cities_to_check.add(user.preferred_city)

    checked_cities = set()
    max_attempts = 3

    for attempt in range(1, max_attempts + 1):
        remaining = cities_to_check - checked_cities
        if not remaining:
            break
        timer_logger.info(f"🔄 Попытка #{attempt}: осталось проверить {len(remaining)} городов.")
        for city in remaining:
            # Получаем погоду (используем дефолтный RU, так как мы только сравниваем значения)
            weather_data = get_weather(city)
            if weather_data and check_weather_changes(city, weather_data):
                checked_cities.add(city)

    if cities_to_check - checked_cities:
        timer_logger.warning(f"⚠️ Не удалось проверить города: {cities_to_check - checked_cities}")

    # 📬 Рассылаем уведомления пользователям
    for user in users:
        city = user.preferred_city
        if not city or city not in changed_cities_cache:
            continue

        settings = decode_notification_settings(user.notifications_settings)
        if not settings.get("weather_threshold_notifications", False):
            continue

        city_data = db.query(CheckedCities).filter_by(city_name=city).first()

        if city_data and city_data.previous_notify_time:
            previous = city_data.previous_notify_time
            if previous.tzinfo is None:
                previous = previous.replace(tzinfo=timezone.utc)
            time_diff = datetime.now(timezone.utc) - previous
            if time_diff < timedelta(hours=3):
                timer_logger.info(f"⏱ Город {city} пропущен — последнее уведомление было {time_diff} назад.")
                continue

        city_changes = changed_cities_cache[city]
        send_weather_update([user], city, city_changes["changed_params"], city_changes["current_data"])

        if city_data:
            city_data.previous_notify_time = datetime.now(timezone.utc)
            db.commit()

    db.close()
    changed_cities_cache.clear()


#ТАЙМЕР ЧЕКЕРА
@safe_execute
def should_run_check():
    """Проверяет, нужно ли запускать проверку погоды (раз в 30 минут)."""
    global old_start_time

    now = datetime.now(timezone.utc)
    current_minute = now.minute
    current_half_hour = now.replace(minute=0 if current_minute < 30 else 30, second=0, microsecond=0)
    next_half_hour = current_half_hour + timedelta(minutes=30)
    remaining_time = (next_half_hour - now).total_seconds()
    test_interval = 1800  
    if old_start_time is None:
        timer_logger.info("🚀 Первая проверка после запуска.")
        old_start_time = current_half_hour.timestamp()
        return True, 0
    if time.time() - old_start_time < test_interval:
        timer_logger.info(f"⏳ Следующая проверка через {remaining_time:.2f} секунд.")
        return False, min(test_interval, remaining_time)
    timer_logger.info("🕒 Наступило время новой проверки погоды.")
    old_start_time = current_half_hour.timestamp()
    return True, 0


@safe_execute
def send_daily_forecast(test_time=None):
    """Отправляет и закрепляет ежедневный прогноз погоды пользователям."""
    users = get_all_users()
    timer_logger.info(f"▸ Найдено пользователей для прогноза: {len(users)}")

    for user in users:
        settings = decode_notification_settings(user.notifications_settings)
        if not settings.get("forecast_notifications", False):
            timer_logger.debug(f"🚫 Уведомления отключены у {user.user_id}, пропускаем.")
            continue
        
        # ЛОКАЛИЗАЦИЯ
        lang = get_user_lang(user)

        user_tz = ZoneInfo(user.timezone or "Asia/Almaty")
        user_time = test_time.astimezone(user_tz) if test_time else datetime.now(user_tz)
        timer_logger.debug(f"▸ {user.user_id} ({user.preferred_city}): {user_time} (локальное)")

        if user_time.hour == 6 and user_time.minute < 30:
            raw_forecast = get_today_forecast(user.preferred_city, user)
            if not raw_forecast:
                timer_logger.warning(f"⚠ `get_today_forecast` не вернула данные для {user.preferred_city}!")
                continue

            updated_time = user_time.strftime("%H:%M")
            title = get_text("daily_forecast_title", lang)
            
            forecast_message = (
                f"{title}\n"
                # f"[Обновлено в {updated_time}]\n"
                + format_forecast(raw_forecast, user)
                + "\n\n" + get_weather_summary_description(fetch_today_forecast(user.preferred_city, lang=lang), user)
            )

            last_forecast_id = get_data_field("last_daily_forecast", user.user_id)
            if last_forecast_id:
                try:
                    bot.delete_message(chat_id=user.user_id, message_id=last_forecast_id)
                    timer_logger.info(f"🗑 Старое сообщение удалено для пользователя {user.user_id}.")
                except Exception as del_error:
                    timer_logger.warning(f"⚠ Не удалось удалить старое сообщение для {user.user_id}: {del_error}")
            try:
                sent_message = bot.send_message(user.user_id, forecast_message, parse_mode="HTML")
                update_data_field("last_daily_forecast", user.user_id, sent_message.message_id)
                timer_logger.info(f"✅ Новый прогноз отправлен пользователю {user.user_id}.")
                try:
                    bot.pin_chat_message(
                        chat_id=user.user_id,
                        message_id=sent_message.message_id,
                        disable_notification=True,
                    )
                    timer_logger.info(f"📌 Новый прогноз закреплён для пользователя {user.user_id}.")
                except Exception as pin_error:
                    timer_logger.warning(f"⚠ Не удалось закрепить сообщение для {user.user_id}: {pin_error}")
            except Exception as e:
                timer_logger.error(f"❌ Ошибка при отправке прогноза {user.user_id}: {e}")


def update_daily_forecasts():
    """Обновляет закреплённые ежедневные прогнозы."""
    users = get_all_users()
    timer_logger.info(f"▸ Найдено пользователей для прогноза: {len(users)}")

    for user in users:
        lang = get_user_lang(user)
        user_tz = ZoneInfo(user.timezone or "Asia/Almaty")
        user_time = datetime.now(user_tz)

        last_forecast_id = get_data_field("last_daily_forecast", user.user_id)
        if not last_forecast_id:
            timer_logger.debug(f"⚠ Закреплённое сообщение отсутствует для {user.user_id}, пропускаем.")
            continue

        raw_forecast = get_today_forecast(user.preferred_city, user)
        if not raw_forecast:
            timer_logger.warning(f"⚠ `get_today_forecast` не вернула данные для {user.preferred_city}.")
            continue

        updated_time = user_time.strftime("%H:%M")
        title = get_text("daily_forecast_title", lang)
        
        forecast_message = (
            f"{title}\n"
            # f"[Обновлено в {updated_time}]\n"
            + format_forecast(raw_forecast, user)
            + "\n\n" + get_weather_summary_description(fetch_today_forecast(user.preferred_city, lang=lang), user)
        )
        try:
            bot.edit_message_text(
                forecast_message,
                chat_id=user.user_id,
                message_id=last_forecast_id,
                parse_mode="HTML",
            )
            timer_logger.info(f"✅ Прогноз обновлён для пользователя {user.user_id}.")
        except Exception as e:
            timer_logger.info(f"❌ Прогноз не был обновлён для пользователя {user.user_id}")


if __name__ == '__main__':
    while True:
        run_check, wait_time = should_run_check()
        
        if run_check:
            timer_logger.info("Запуск задач проверки.")
            
            check_all_cities()
            # ОТЛАДКА (TEST MODE)
            # user_tz = ZoneInfo("Asia/Almaty")
            # test_time = datetime(2025, 3, 26, 6, 0, 0, tzinfo=user_tz) 
            # send_daily_forecast(test_time)
            send_daily_forecast()
            update_daily_forecasts()
    
        else:
            timer_logger.info(f"⏳ Ждём {wait_time:.2f} секунд до следующей проверки.")
        
        time.sleep(wait_time)