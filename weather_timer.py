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
from logic import UNIT_TRANSLATIONS, get_all_users, decode_notification_settings, get_wind_direction
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
            # Эмуляция данных для тестов
            current_data = {
                "temp": round(random.uniform(-10, 40), 1),
                "feels_like": round(random.uniform(-10, 40), 1),
                "humidity": random.randint(10, 100),
                "wind_speed": round(random.uniform(0, 10), 1),
                "wind_direction": random.randint(0, 25),
                "wind_gust": round(random.uniform(0, 10), 1),
                "pressure": random.randint(950, 1200),
                "visibility": random.randint(1000, 10000),
                "clouds": random.randint(0, 100),
                "description": random.choice(["Гроза", "Переменная облачность", "Солнечно"])
            }
        
        # Получаем пользователей для города с уведомлениями
        users = db.query(User).filter(User.preferred_city == city).all()
        users_with_notifications = [
            user for user in users if decode_notification_settings(user.notifications_settings).get("weather_threshold_notifications", False)
        ]
        if not users_with_notifications:
            timer_logger.info(f"▸ Нет пользователей с включёнными уведомлениями для города {city}. Проверка завершена.")
            return True

        # Получаем или создаём запись о городе в БД
        city_data = db.query(CheckedCities).filter_by(city_name=city).first()
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
                last_description=current_data["description"]
            )
            db.add(new_entry)
            db.commit()
            timer_logger.info(f"✅ Город {city} добавлен в проверенные города.")
            return True
        
        # Сравнение данных и выявление изменений
        notify_users = False
        changed_params = {}
        for param in current_data:
            if param in ["city_name", "coordinates", "wind_direction", "clouds"]:
                continue

            old_value = getattr(city_data, f"last_{param}", None)
            new_value = current_data[param]

            if param == "description":
                important_descriptions = [
                    "Проливной дождь", "Небольшой проливной дождь", "Снег", "Град",
                    "Гроза", "Шторм", "Буря", "Сильный ветер", "Пыльная буря",
                    "Ливень", "Дождь", "Небольшой дождь", "Небольшой снег"
                ]
                old_desc, new_desc = old_value, new_value

                if old_desc != new_desc and isinstance(new_desc, str):
                    if new_desc.lower() in [desc.lower() for desc in important_descriptions]:
                        changed_params[param] = (old_desc, new_desc)
                        notify_users = True
            else:
                try:
                    old_value = float(old_value) if old_value is not None else None
                    new_value = float(new_value) if new_value is not None else None
                except (ValueError, TypeError):
                    continue

                if old_value is not None and new_value is not None and abs(new_value - old_value) > get_threshold(param):
                    changed_params[param] = (old_value, new_value)
                    notify_users = True

        if notify_users:
            timer_logger.info(f"Изменения для города {city}: {changed_params}")
            send_weather_update(users_with_notifications, city, changed_params, current_data)

        # Обновление данных о погоде в базе
        for param in current_data:
            setattr(city_data, f"last_{param}", getattr(city_data, param, None))
            setattr(city_data, param, current_data[param])

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
        "temperature": 3.0,  # Изменение температуры на 2°C
        "humidity": 15,  # Изменение влажности на 10%
        "wind_speed": 2,  # Изменение скорости ветра на 2 м/с
        "wind_gust": 2,  # Изменение скорости ветра на 2 м/с
        "pressure": 5,  # Изменение давления на 5 мм рт. ст.
        "visibility": 4000,  # Изменение видимости на 500 м
        "feels_like": 3.0,
        "clouds": 20,
        "description": [
                    "Проливной дождь", "Небольшой проливной дождь", "Снег",
                    "Град", "Гроза", "Шторм", "Буря", "Сильный ветер",
                    "Пыльная буря", "Ливень","Дождь","Небольшой дождь", "Небольшой снег"
                    ]
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
        last_menu_id = get_data_field("last_menu_message", chat_id)
        if last_menu_id:
            try:
                bot.delete_message(chat_id, last_menu_id)
                update_data_field("last_menu_message", chat_id, None)
                timer_logger.debug(f"🗑 Удалено старое декоративное сообщение для пользователя {chat_id}.")
            except Exception as e:
                timer_logger.warning(f"⚠ Не удалось удалить декоративное сообщение для {chat_id}: {e}")

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
        
        emoji = get_weather_emoji(current_data, changes)
        header = f"<blockquote>{emoji} Внимание!                              </blockquote>\nПогода в г.{city} изменилась!"
        line = "─" * min(len(header), 21)
        message = f"<b>{header}</b>\n{line}\n"

        params = {
            "description": ("Погода", current_data["description"], ""),
            "temperature": ("Температура", convert_temperature(current_data["temp"], user.temp_unit), UNIT_TRANSLATIONS["temp"].get(user.temp_unit, "°C")),
            "feels_like": ("Ощущается как", convert_temperature(current_data["feels_like"], user.temp_unit), UNIT_TRANSLATIONS["temp"].get(user.temp_unit, "°C")),
            "humidity": ("Влажность", int(current_data["humidity"]), "%"),
            "precipitation": ("Вероятность осадков", int(current_data.get("precipitation", 0)), "%"),
            "pressure": ("Давление", convert_pressure(current_data["pressure"], user.pressure_unit), UNIT_TRANSLATIONS["pressure"].get(user.pressure_unit, " мм рт.")),
            "wind_speed": ("Скорость ветра", convert_wind_speed(current_data["wind_speed"], user.wind_speed_unit), UNIT_TRANSLATIONS["wind_speed"].get(user.wind_speed_unit, " м/с")),
            "wind_direction": ("Направление ветра", f"{get_wind_direction(current_data['wind_direction'])} ({current_data['wind_direction']}°)", ""),
            "wind_gust": ("Порывы ветра", convert_wind_speed(current_data.get("wind_gust", 0), user.wind_speed_unit), UNIT_TRANSLATIONS["wind_speed"].get(user.wind_speed_unit, " м/с")),
            "clouds": ("Облачность", current_data.get("clouds", 0), "%"),
            "visibility": ("Видимость", int(current_data.get("visibility", 0)), " м")
        }

        formatted_params = {} 

        for param, value in current_data.items():
            if param == "temperature":
                translated_unit = UNIT_TRANSLATIONS["temp"].get(user.temp_unit, user.temp_unit)
                formatted_value = round(convert_temperature(value, user.temp_unit), 1)
                formatted_params[param] = f"{formatted_value} {translated_unit}"
            elif param == "feels_like":
                translated_unit = UNIT_TRANSLATIONS["temp"].get(user.temp_unit, user.temp_unit)
                formatted_value = round(convert_temperature(value, user.temp_unit), 1)
                formatted_params[param] = f"{formatted_value} {translated_unit}"
            elif param == "pressure":
                translated_unit = UNIT_TRANSLATIONS["pressure"].get(user.pressure_unit, user.pressure_unit)
                formatted_params[param] = f"{convert_pressure(value, user.pressure_unit)} {translated_unit}"
            elif param == "wind_speed":
                translated_unit = UNIT_TRANSLATIONS["wind_speed"].get(user.wind_speed_unit, user.wind_speed_unit)
                formatted_params[param] = f"{convert_wind_speed(value, user.wind_speed_unit)} {translated_unit}"
            elif param == "wind_gust":
                translated_unit = UNIT_TRANSLATIONS["wind_speed"].get(user.wind_speed_unit, user.wind_speed_unit)
                formatted_params[param] = f"{convert_wind_speed(value, user.wind_speed_unit)} {translated_unit}"
            elif param == "visibility":
                formatted_params[param] = f"{int(value)} м"
            elif param in ("humidity", "precipitation"):
                formatted_params[param] = f"{int(value)}%"
            elif param == "description":
                formatted_params[param] = value.capitalize()

        # Второй проход: обрабатываем изменения
        for param, (label, value, unit) in params.items():
            if not tracked_params.get(param, False):
                continue

            arrow = "▸" 
            value_str = formatted_params.get(param, f"{value}{unit}") 

            if param in changes:
                old, new = changes[param]
                trend_emoji = "⇑" if new > old else "⇓"

                if param == "description":
                    important_descriptions = get_threshold("description")
                    if old != new:
                        if old in important_descriptions or new in important_descriptions:
                            value_str = f"<b>{old.capitalize()} ➝ {new.capitalize()}</b>"
                            arrow = "⇑"
                        else:
                            value_str = f"{old.capitalize()} ➝ {new.capitalize()}"
                        arrow = ""
                elif param == "feels_like":
                    old = round(convert_temperature(old, user.temp_unit), 1)
                    new = round(convert_temperature(new, user.temp_unit), 1)
                    if old != new:
                        value_str = f"<b>{old} ➝ {new}{unit}</b>"
                        arrow = "⇑" if new > old else "⇓"
                    else:
                        arrow = ""
                elif param == "temperature":
                    old = round(convert_temperature(old, user.temp_unit), 1)
                    new = round(convert_temperature(new, user.temp_unit), 1)
                    if old != new:
                        value_str = f"<b>{old} ➝ {new}{unit}</b>"
                        arrow = "⇑" if new > old else "⇓"
                    else:
                        arrow = ""
                elif param == "pressure":
                    old = convert_pressure(old, user.pressure_unit)
                    new = convert_pressure(new, user.pressure_unit)
                    if old != new:
                        value_str = f"<b>{old} ➝ {new} {unit}</b>"

                elif param == "wind_speed":
                    old = convert_wind_speed(old, user.wind_speed_unit)
                    new = convert_wind_speed(new, user.wind_speed_unit)
                    if old != new:
                        value_str = f"<b>{old} ➝ {new} {unit}</b>"

                elif param == "wind_gust":
                    old = convert_wind_speed(old, user.wind_speed_unit)
                    new = convert_wind_speed(new, user.wind_speed_unit)
                    if old != new:
                        value_str = f"<b>{old} ➝ {new} {unit}</b>"

                elif param == "visibility":
                    old, new = int(old), int(new)
                    if old != new:
                        value_str = f"<b>{old} ➝ {new} м</b>"

                elif param in ("humidity", "precipitation"):
                    old, new = int(old), int(new)
                    if old != new:
                        value_str = f"<b>{old} ➝ {new}{unit}</b>"

                arrow = trend_emoji

            message += f"{arrow} {label}: {value_str}\n"

        message += "\n      ⟪ Deus Weather ⟫"

        bot.send_message(user.user_id, message, parse_mode="HTML")
        timer_logger.info(f"▸ Уведомление отправлено пользователю {user.user_id}: {message}\n")

        if get_data_field("last_settings_command", chat_id):
            send_settings_menu(chat_id)
            timer_logger.debug(f"🔄 Повторно отправлено меню настроек для пользователя {chat_id}.")
        else:
            send_main_menu(chat_id)
            timer_logger.debug(f"🔄 Повторно отправлено главное меню для пользователя {chat_id}.")

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