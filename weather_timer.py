#ИМОПРТЫ
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
from logic import safe_execute, convert_pressure, convert_temperature, convert_wind_speed, decode_tracked_params
from logic import UNIT_TRANSLATIONS, get_all_users, decode_notification_settings, get_wind_direction, get_wind_direction
from weather import get_weather
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine    
from threading import Event
from logging.handlers import RotatingFileHandler
from bot import get_data_field, update_data_field, send_main_menu, send_settings_menu, get_today_forecast, format_forecast
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
                    "Проливной дождь", "Небольшой проливной дождь", "Снег",
                    "Град", "Гроза", "Шторм", "Буря", "Сильный ветер",
                    "Пыльная буря", "Ливень", "Дождь", "Небольшой дождь", "Небольшой снег"
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
            send_weather_update(users_with_notifications, city, full_changed_params, current_data)
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
    thresholds = {
        "description": [
                    "Проливной дождь", "Небольшой проливной дождь", "Снег",
                    "Град", "Гроза", "Шторм", "Буря", "Сильный ветер",
                    "Пыльная буря", "Ливень","Дождь","Небольшой дождь", "Небольшой снег"
                    ]
    }
    return thresholds.get(param, 0)

def get_weather_emoji(current_data, changes):
    """Выбирает наиболее важный смайлик в зависимости от изменений погоды."""

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

    if detected_events:
        highest_priority_event = max(detected_events, key=lambda event: priority[event][0])
        return priority[highest_priority_event][1]

    return "🌦️" 

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

        # Удаляем старое меню, если оно было
        last_menu_id = get_data_field("last_menu_message", chat_id)
        if last_menu_id:
            try:
                bot.delete_message(chat_id, last_menu_id)
                update_data_field("last_menu_message", chat_id, None)
            except Exception as e:
                timer_logger.warning(f"⚠ Не удалось удалить декоративное сообщение для {chat_id}: {e}")

        # Заголовок с эмодзи и сообщением об изменении погоды
        emoji = get_weather_emoji(current_data, changes)
        header = f"<blockquote>{emoji} Внимание!</blockquote>\nПогода в г.{city} изменилась!"
        message = f"<b>{header}</b>\n{'─' * min(len(header), 21)}\n"

        if "temp" in current_data:
            current_data["temperature"] = current_data["temp"]

        param_config = {
            "description": ("Погода", "", lambda x: str(x).capitalize()),
            "temperature": (
                "Температура", "", 
                lambda x: f"{round(convert_temperature(x, user.temp_unit))}{UNIT_TRANSLATIONS['temp'][user.temp_unit]}"
            ),
            "feels_like": (
                "Ощущается как", "", 
                lambda x: f"{round(convert_temperature(x, user.temp_unit))}{UNIT_TRANSLATIONS['temp'][user.temp_unit]}"
            ),
            "humidity": (
                "Влажность", "%", 
                lambda x: f"{int(x)}%"
            ),
            "precipitation": (
                "Вероятность осадков", "%", 
                lambda x: f"{int(x)}%"
            ),
            "pressure": (
                "Давление", "", 
                lambda x: f"{round(convert_pressure(x, user.pressure_unit))} {UNIT_TRANSLATIONS['pressure'][user.pressure_unit]}"
            ),
            "wind_speed": (
                "Скорость ветра", "", 
                lambda x: f"{round(convert_wind_speed(x, user.wind_speed_unit))} {UNIT_TRANSLATIONS['wind_speed'][user.wind_speed_unit]}"
            ),
            "wind_direction": (
                "Направление ветра", "", 
                lambda x: f"{get_wind_direction(float(x))} ({int(float(x))}°)"
            ),
            "wind_gust": (
                "Порывы ветра", "", 
                lambda x: f"{round(convert_wind_speed(x, user.wind_speed_unit))} {UNIT_TRANSLATIONS['wind_speed'][user.wind_speed_unit]}"
            ),
            "clouds": (
                "Облачность", "%", 
                lambda x: f"{int(x)}%"
            ),
            "visibility": (
                "Видимость", "м", 
                lambda x: f"{int(x)} м"
            ),
        }
        # Проходим по всем параметрам, даже если они не изменились
        for param, (label, _, formatter) in param_config.items():
            if not tracked_params.get(param, False):
                continue

            current = current_data.get(param)
            last = getattr(city_data, f"last_{param}", None)

            if current is None:
                continue

            arrow = "▸"
            value_str = formatter(current)  # значение по умолчанию — текущее, уже с нужной единицей

            if param == "description":
                if last and current and str(last).lower() != str(current).lower():
                    arrow = "⇑"
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
                            unit = UNIT_TRANSLATIONS["temp"][user.temp_unit]
                        elif param == "feels_like":
                            new = round(convert_temperature(raw_current, user.temp_unit))
                            old = round(convert_temperature(raw_last, user.temp_unit))
                            unit = UNIT_TRANSLATIONS["temp"][user.temp_unit]
                        elif param == "pressure":
                            new = round(convert_pressure(raw_current, user.pressure_unit))
                            old = round(convert_pressure(raw_last, user.pressure_unit))
                            unit = UNIT_TRANSLATIONS["pressure"][user.pressure_unit]
                        elif param in ("wind_speed", "wind_gust"):
                            new = round(convert_wind_speed(raw_current, user.wind_speed_unit))
                            old = round(convert_wind_speed(raw_last, user.wind_speed_unit))
                            unit = UNIT_TRANSLATIONS["wind_speed"][user.wind_speed_unit]
                        elif param == "visibility":
                            new = int(raw_current)
                            old = int(raw_last)
                            unit = "м"
                        elif param in ("humidity", "precipitation", "clouds"):
                            new = int(raw_current)
                            old = int(raw_last)
                            unit = "%"
                        elif param == "wind_direction":
                            new_direction = get_wind_direction(raw_current)
                            old_direction = get_wind_direction(raw_last)
                            new_str = f"{new_direction} ({int(raw_current)}°)"
                            old_str = f"{old_direction} ({int(raw_last)}°)"
                        else:
                            new, old, unit = raw_current, raw_last, ""

                        trend = "⇑" if new > old else "⇓"
                        if param in {"temperature", "feels_like", "precipitation", "clouds", "humidity"}:
                            value_str = f"<b>{old} ➝ {new}{unit}</b>"
                        elif param in {"wind_direction"}:
                            value_str = f"<b>{old_str} ➝ {new_str}</b>"
                        else:
                            value_str = f"<b>{old} ➝ {new} {unit}</b>"
                        arrow = trend
                except Exception as e:
                    timer_logger.debug(f"⚠ Ошибка при сравнении {param}: {e}")

            # Добавляем строку в сообщение
            message += f"{arrow} {label}: {value_str}\n"

        # Завершающая строка
        message += "\n      ⟪ Deus Weather ⟫"

        # Отправляем сообщение
        bot.send_message(chat_id, message, parse_mode="HTML")
        timer_logger.info(f"▸ Уведомление отправлено пользователю {chat_id}:\n{message}")

        # Отправляем повторно меню (в зависимости от последней команды)
        if get_data_field("last_settings_command", chat_id):
            send_settings_menu(chat_id)
        else:
            send_main_menu(chat_id)

    db.close()  # Закрываем соединение с БД

@safe_execute
def check_all_cities():
    """Проверяет все города, для которых включены уведомления."""
    db = SessionLocal()
    users = db.query(User.preferred_city, User.notifications_settings).distinct().all()
    cities = set()
    for city, settings in users:
        if city:
            decoded_settings = decode_notification_settings(settings)
            if decoded_settings.get("weather_threshold_notifications", False):
                cities.add(city)
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
    now = test_time or datetime.now()
    for user in users:
        settings = decode_notification_settings(user.notifications_settings)
        if not settings.get("forecast_notifications", False):
            timer_logger.debug(f"🚫 Уведомления отключены у {user.user_id}, пропускаем.")
            continue
        user_tz = ZoneInfo(user.timezone) if user.timezone else ZoneInfo("UTC")
        user_time = now.astimezone(user_tz)
        timer_logger.debug(f"▸ {user.user_id} ({user.preferred_city}): {user_time} (локальное)")
        if user_time.hour == 6 and user_time.minute < 10:
            raw_forecast = get_today_forecast(user.preferred_city, user)         
            if not raw_forecast:
                timer_logger.warning(f"⚠ `get_today_forecast` не вернула данные для {user.preferred_city}!")
                continue
            updated_time = user_time.strftime("%H:%M")
            forecast_message = (
                "<blockquote>📅 Ежедневный прогноз погоды</blockquote>\n"
                f"[Обновлено в {updated_time}]\n"
                + format_forecast(raw_forecast, user)
                + "\n\n      ⟪ Deus Weather ⟫"
            )
            last_forecast_id = get_data_field("last_daily_forecast", user.user_id)
            if last_forecast_id:
                try:
                    bot.delete_message(chat_id=user.user_id, message_id=last_forecast_id)
                    timer_logger.info(f"🗑 Старое сообщение удалено для пользователя {user.user_id}.")
                except Exception as del_error:
                    timer_logger.warning(f"⚠ Не удалось удалить старое сообщение для {user.user_id}: {del_error}")
            try:
                sent_message = bot.send_message(
                    user.user_id, forecast_message, parse_mode="HTML"
                )
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
    now = datetime.now()
    timer_logger.info(f"▸ Найдено пользователей для прогноза: {len(users)}")

    for user in users:
        user_tz = ZoneInfo(user.timezone) if user.timezone else ZoneInfo("UTC")
        user_time = now.astimezone(user_tz)

        last_forecast_id = get_data_field("last_daily_forecast", user.user_id)
        if not last_forecast_id:
            timer_logger.debug(f"⚠ Закреплённое сообщение отсутствует для {user.user_id}, пропускаем.")
            continue

        raw_forecast = get_today_forecast(user.preferred_city, user)
        if not raw_forecast:
            timer_logger.warning(f"⚠ `get_today_forecast` не вернула данные для {user.preferred_city}.")
            continue

        updated_time = user_time.strftime("%H:%M")
        forecast_message = (
            "<blockquote>📅 Ежедневный прогноз погоды</blockquote>\n"
            f"[Обновлено в {updated_time}]\n"
            + format_forecast(raw_forecast, user)
            + "\n\n      ⟪ Deus Weather ⟫"
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
            timer_logger.error(f"❌ Ошибка при обновлении прогноза {user.user_id}: {e}")


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