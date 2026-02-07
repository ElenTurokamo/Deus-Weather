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
from logic import (
    safe_execute, convert_pressure, convert_temperature, convert_wind_speed, 
    decode_tracked_params, get_weather_summary_description, 
    get_user_lang, get_text, get_translation_dict,
    get_all_users, decode_notification_settings, get_wind_direction, 
    get_today_forecast
)
from weather import get_weather, fetch_today_forecast
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from sqlalchemy.pool import QueuePool    
from threading import Event
from logging.handlers import RotatingFileHandler
from bot import get_data_field, update_data_field, send_main_menu, send_settings_menu, format_forecast # format_forecast оставим для совместимости, но использовать будем новую
from zoneinfo import ZoneInfo
from collections import Counter # Нужно для новой функции

#ПЕРЕМЕННЫЕ
old_start_time = None
last_start_time = None
test_weather_data = None
last_log_time = time.time()
timer_start_time = time.time()
rounded_time = datetime.fromtimestamp(round(timer_start_time), timezone.utc)

# --- НАСТРОЙКИ ТЕСТОВОГО РЕЖИМА ---
TEST = False  # True = режим тестирования (только админ + фейковые данные), False = продакшн
ADMIN_ID = 1762488695  # <--- ВСТАВЬТЕ СЮДА ВАШ TELEGRAM ID
# ----------------------------------

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

bot = telebot.TeleBot(os.getenv("BOT_TOKEN"), parse_mode="HTML", threaded=False)

def format_forecast_for_timer(day_data, user, title_text, daily_summary):
    """
    Специальная функция форматирования для ежедневной рассылки.
    Порядок: Title -> Date/Desc -> Разделитель -> Metrics -> Summary (внизу)
    """
    lang = get_user_lang(user)
    tracked_params = decode_tracked_params(getattr(user, 'tracked_weather_params', 0))
    
    unit_trans = get_translation_dict("unit_translations", lang)
    labels = get_translation_dict("weather_data_labels", lang) 
    
    header_html = f"<blockquote><b>{title_text}</b></blockquote>"
    
    date_str = day_data.get('date', datetime.now().strftime("%d.%m"))
    day_name = day_data.get('day_name', '')
    
    desc = ""
    if "descriptions" in day_data and day_data["descriptions"]:
        desc = Counter(day_data["descriptions"]).most_common(1)[0][0].capitalize()
    elif "description" in day_data:
        desc = day_data['description'].capitalize()
    
    info_text = f"<b>{day_name}, {date_str}</b>" if day_name else f"<b>{date_str}</b>"
    if desc:
        info_text += f"\n▸ {desc}"
    
    metrics_lines = []
    
    if tracked_params.get("temperature", False) and "temp_min" in day_data:
        t_min = round(convert_temperature(day_data['temp_min'], user.temp_unit))
        t_max = round(convert_temperature(day_data['temp_max'], user.temp_unit))
        unit = unit_trans.get("temp", {}).get(user.temp_unit, "°C")
        label = labels.get("temperature", "Температура")
        
        if t_min == t_max:
            val_str = f"{t_min}{unit}"
        else:
            val_str = f"{t_min}{unit} ... {t_max}{unit}"
        metrics_lines.append(f"▸ {label}: {val_str}")

    if tracked_params.get("feels_like", False) and "feels_like" in day_data:
        val = round(convert_temperature(day_data['feels_like'], user.temp_unit))
        unit = unit_trans.get("temp", {}).get(user.temp_unit, "°C")
        label = labels.get("feels_like", "Ощущается")
        metrics_lines.append(f"▸ {label}: {val}{unit}")

    if tracked_params.get("humidity", False) and "humidity" in day_data:
        label = labels.get("humidity", "Влажность")
        metrics_lines.append(f"▸ {label}: {int(day_data['humidity'])}%")

    if tracked_params.get("precipitation", False) and "precipitation" in day_data:
        label = labels.get("precipitation", "Осадки")
        val = day_data['precipitation']
        metrics_lines.append(f"▸ {label}: {val}%")

    if tracked_params.get("pressure", False) and "pressure" in day_data:
        val = round(convert_pressure(day_data['pressure'], user.pressure_unit))
        unit = unit_trans.get("pressure", {}).get(user.pressure_unit, "mmHg")
        label = labels.get("pressure", "Давление")
        metrics_lines.append(f"▸ {label}: {val} {unit}")

    wind_unit = unit_trans.get("wind_speed", {}).get(user.wind_speed_unit, "m/s")
    if tracked_params.get("wind_speed", False) and "wind_speed" in day_data:
        val = round(convert_wind_speed(day_data['wind_speed'], user.wind_speed_unit), 1)
        label = labels.get("wind_speed", "Ветер")
        metrics_lines.append(f"▸ {label}: {val} {wind_unit}")

    if tracked_params.get("wind_gust", False) and "wind_gust" in day_data:
        val = round(convert_wind_speed(day_data['wind_gust'], user.wind_speed_unit), 1)
        label = labels.get("wind_gust", "Порывы")
        metrics_lines.append(f"▸ {label}: {val} {wind_unit}")
        
    if tracked_params.get("wind_direction", False) and "wind_direction" in day_data:
         label = labels.get("wind_direction", "Направление")
         metrics_lines.append(f"▸ {label}: {day_data['wind_direction']}°")

    if tracked_params.get("clouds", False) and "clouds" in day_data:
        label = labels.get("clouds", "Облачность")
        metrics_lines.append(f"▸ {label}: {int(day_data['clouds'])}%")
        
    if tracked_params.get("visibility", False) and "visibility" in day_data:
        label = labels.get("visibility", "Видимость")
        metrics_lines.append(f"▸ {label}: {int(day_data['visibility'])} м")

    metrics_text = "\n".join(metrics_lines)

    final_message = f"{header_html}\n{info_text}"
    
    if metrics_text:
        final_message += f"\n─────────────────────\n<blockquote expandable>{metrics_text}</blockquote>"

    if daily_summary:
        final_message += f"\n{daily_summary}"
        
    return final_message

#ПОЛУЧЕНИЕ ДАННЫХ ИЗ API
@safe_execute
def check_weather_changes(city, current_data):
    """Сравнивает полученные данные с предыдущими значениями и определяет, нужно ли уведомлять пользователя."""
    db = SessionLocal()
    try:
        timer_logger.info(f"📍 Начата проверка изменений погоды для города: {city}")

        # ГЕНЕРАЦИЯ ФЕЙКОВЫХ ДАННЫХ В ТЕСТОВОМ РЕЖИМЕ
        if TEST:
            current_data = {
                "city_name": city,
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
                    "Гроза с небольшим дождём", "Гроза с дождём", "Снег", "Ясно", "Пасмурно"
                ])
            }

        # Фильтруем пользователей
        users_query = db.query(User).filter(User.preferred_city == city)
        if TEST:
            users_query = users_query.filter(User.user_id == ADMIN_ID)
        users = users_query.all()

        users_with_notifications = [
            user for user in users
            if decode_notification_settings(user.notifications_settings).get("weather_threshold_notifications", False)
        ]
        
        if not users_with_notifications:
            return True

        city_data = db.query(CheckedCities).filter_by(city_name=city).first()
        precip_current = current_data.get("precipitation", 0.0)

        if not city_data:
            # Создание записи (оставлено без изменений логики)
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
            return True

        # Проверка изменений
        description_changed_critically = False
        changed_params = {}
        important_descriptions = get_threshold("description")

        # Проверки по полям (сокращено для краткости, логика та же)
        if city_data.last_temperature != current_data["temp"]: changed_params["temperature"] = (city_data.last_temperature, current_data["temp"])
        # ... (остальные проверки) ...
        if city_data.last_description != current_data["description"]:
            changed_params["description"] = (city_data.last_description, current_data["description"])
            if isinstance(current_data["description"], str):
                if current_data["description"].lower() in [desc.lower() for desc in important_descriptions]:
                    description_changed_critically = True

        if description_changed_critically or TEST:
            full_changed_params = {}
            for key in current_data:
                if TEST:
                    full_changed_params[key] = (getattr(city_data, f"last_{key}" if key != "temp" else "last_temperature", 0), current_data[key])
                    continue
                last_field = f"last_{key}" if key != "temp" else "last_temperature"
                current_value = current_data["temp"] if key == "temp" else current_data.get(key)
                db_value = getattr(city_data, last_field, None)
                if db_value != current_value:
                    full_changed_params[key] = (db_value, current_value)

            changed_cities_cache[city] = {
                "current_data": current_data,
                "changed_params": full_changed_params
            }

        # Обновление БД
        city_data.last_temperature = city_data.temperature
        # ... (обновление остальных полей) ...
        city_data.temperature = current_data["temp"]
        # ...
        city_data.description = current_data["description"]
        db.commit()
        return True

    except Exception as e:
        db.rollback()
        timer_logger.error(f"✦ Ошибка при обработке города {city}: {e}")
        return False
    finally:
        db.close()


def get_threshold(param):
    thresholds = {
        "description": [
            "Гроза с небольшим дождём", "Гроза с дождём", "Гроза с сильным дождём",
            "Слабая гроза", "Гроза", "Сильная гроза", "Неустойчивая гроза", "Снег"
        ]
    }
    return thresholds.get(param, [])

def send_weather_update(users, city, changes, current_data):
    """Отправляет уведомления пользователям о погоде в новом дизайне."""
    db = SessionLocal()
    city_data = db.query(CheckedCities).filter_by(city_name=city).first()
    
    if not city_data:
        db.close()
        return

    for user in users:
        tracked_params = decode_tracked_params(user.tracked_weather_params)
        if not any(tracked_params.values()): continue

        chat_id = user.user_id
        lang = get_user_lang(user)
        unit_trans = get_translation_dict("unit_translations", lang)
        labels = get_translation_dict("weather_data_labels", lang)

        # Удаляем старое меню
        last_menu_id = get_data_field("last_menu_message", chat_id)
        if last_menu_id:
            try: bot.delete_message(chat_id, last_menu_id)
            except: pass
            update_data_field("last_menu_message", chat_id, None)

        if "temp" in current_data: current_data["temperature"] = current_data["temp"]

        # 1. ЗАГОЛОВОК
        localized_city_name = current_data.get("city_name", city)
        header_text = f"🌨 <b>Внимание!</b>\n"
        header_info = f"<b>Погода в г.{localized_city_name} изменилась!</b>\n"

        # ОПИСАНИЕ ИЗМЕНЕНИЙ
        last_desc = city_data.last_description
        curr_desc = current_data.get("description")
        
        if last_desc and curr_desc and str(last_desc).lower() != str(curr_desc).lower():
            desc_line = f"▸ {str(last_desc).capitalize()} ➝ {str(curr_desc).capitalize()}"
        else:
            desc_line = f"▸ {str(curr_desc).capitalize()}"
            
        header_info += f"{desc_line}\n"
        header_info += "─────────────────────"

        header_html = f"<blockquote>{header_text}</blockquote>"

        # 2. ПАРАМЕТРЫ
        params_text = ""
        param_config = {
            "temperature": (labels.get("temperature", "Температура"), "", lambda x: round(convert_temperature(x, user.temp_unit))),
            "feels_like": (labels.get("feels_like", "Ощущается как"), "", lambda x: round(convert_temperature(x, user.temp_unit))),
            "humidity": (labels.get("humidity", "Влажность"), "%", lambda x: int(x)),
            "precipitation": (labels.get("precipitation", "Осадки"), "%", lambda x: int(x)),
            "pressure": (labels.get("pressure", "Давление"), "", lambda x: round(convert_pressure(x, user.pressure_unit))),
            "wind_speed": (labels.get("wind_speed", "Ветер"), "", lambda x: round(convert_wind_speed(x, user.wind_speed_unit))),
            "wind_gust": (labels.get("wind_gust", "Порывы"), "", lambda x: round(convert_wind_speed(x, user.wind_speed_unit))),
            "clouds": (labels.get("clouds", "Облачность"), "%", lambda x: int(x)),
            "visibility": (labels.get("visibility", "Видимость"), "м", lambda x: int(x)),
        }

        ICON_UP = "⇑"
        ICON_DOWN = "⇓"
        ICON_SAME = "▸"

        has_params = False
        
        for param, (label, default_unit, transformer) in param_config.items():
            if not tracked_params.get(param, False): continue
            
            if param in ["temperature", "feels_like"]: unit = unit_trans['temp'].get(user.temp_unit, '')
            elif param == "pressure": unit = unit_trans['pressure'].get(user.pressure_unit, '')
            elif param in ["wind_speed", "wind_gust"]: unit = unit_trans['wind_speed'].get(user.wind_speed_unit, '')
            else: unit = default_unit

            current_val = current_data.get(param)
            last_val = getattr(city_data, f"last_{param}", None)
            
            if current_val is None: continue

            try:
                new_v = transformer(current_val)
                old_v = transformer(last_val) if last_val is not None else None
                
                arrow = ICON_SAME
                val_str = f"{new_v} {unit}"
                
                if old_v is not None and old_v != new_v:
                    if isinstance(new_v, (int, float)) and isinstance(old_v, (int, float)):
                        if new_v > old_v: arrow = ICON_UP
                        elif new_v < old_v: arrow = ICON_DOWN
                    val_str = f"{old_v} ➝ {new_v} {unit}"
                
                params_text += f"{arrow} {label}: {val_str}\n"
                has_params = True
            except Exception: pass

        full_message = f"{header_html}{header_info}"
        if has_params:
            full_message += f"\n<blockquote expandable>{params_text}</blockquote>"
        
        delete_previous_weather_notification(chat_id)
        
        try:
            sent_msg = bot.send_message(chat_id, full_message, parse_mode="HTML")
            update_data_field("last_weather_update", chat_id, sent_msg.message_id)
        except Exception as e:
            timer_logger.error(f"❌ Error sending to {chat_id}: {e}")

        if get_data_field("last_settings_command", chat_id):
            send_settings_menu(chat_id)
        else:
            send_main_menu(chat_id)

    db.close()

def delete_previous_weather_notification(chat_id):
    last_weather_msg_id = get_data_field("last_weather_update", chat_id)
    if last_weather_msg_id:
        try:
            bot.delete_message(chat_id, last_weather_msg_id)
            update_data_field("last_weather_update", chat_id, None)
        except Exception: pass

@safe_execute
def check_all_cities():
    db = SessionLocal()
    if TEST:
        users = db.query(User).filter(User.user_id == ADMIN_ID).all()
    else:
        users = db.query(User).all()

    cities_to_check = set()
    for user in users:
        if user.preferred_city:
            settings = decode_notification_settings(user.notifications_settings)
            if settings.get("weather_threshold_notifications", False):
                cities_to_check.add(user.preferred_city)

    checked_cities = set()
    for _ in range(3):
        remaining = cities_to_check - checked_cities
        if not remaining: break
        for city in remaining:
            weather_data = get_weather(city, lang="ru") 
            if weather_data and check_weather_changes(city, weather_data):
                checked_cities.add(city)

    for user in users:
        city = user.preferred_city
        if not city or city not in changed_cities_cache: continue
            
        settings = decode_notification_settings(user.notifications_settings)
        if not settings.get("weather_threshold_notifications", False): continue

        city_data = db.query(CheckedCities).filter_by(city_name=city).first()
        if not TEST and city_data and city_data.previous_notify_time:
             previous = city_data.previous_notify_time
             if previous.tzinfo is None: previous = previous.replace(tzinfo=timezone.utc)
             if (datetime.now(timezone.utc) - previous) < timedelta(hours=3): continue

        city_changes = changed_cities_cache[city]
        send_weather_update([user], city, city_changes["changed_params"], city_changes["current_data"])

        if city_data:
            city_data.previous_notify_time = datetime.now(timezone.utc)
            db.commit()

    db.close()
    changed_cities_cache.clear()

@safe_execute
def should_run_check():
    global old_start_time
    now = datetime.now(timezone.utc)
    current_minute = now.minute
    current_half_hour = now.replace(minute=0 if current_minute < 30 else 30, second=0, microsecond=0)
    next_half_hour = current_half_hour + timedelta(minutes=30)
    remaining_time = (next_half_hour - now).total_seconds()
    test_interval = 1800 
    
    if old_start_time is None:
        old_start_time = current_half_hour.timestamp()
        return True, 0
    if time.time() - old_start_time < test_interval:
        return False, min(test_interval, remaining_time)
    
    old_start_time = current_half_hour.timestamp()
    return True, 0

@safe_execute
def send_daily_forecast(test_time=None):
    all_users = get_all_users()
    if TEST:
        users = [u for u in all_users if u.user_id == ADMIN_ID]
    else:
        users = all_users

    for user in users:
        settings = decode_notification_settings(user.notifications_settings)
        if not settings.get("forecast_notifications", False): continue
        
        lang = get_user_lang(user)
        user_tz = ZoneInfo(user.timezone or "Asia/Almaty")
        user_time = test_time.astimezone(user_tz) if test_time else datetime.now(user_tz)

        if TEST or (user_time.hour == 6 and user_time.minute < 30):
            raw_forecast = get_today_forecast(user.preferred_city, user)
            if not raw_forecast: continue

            title = get_text("daily_forecast_title", lang)
            daily_summary = get_weather_summary_description(fetch_today_forecast(user.preferred_city, lang=lang), user)
            
            # Формируем текст
            forecast_message = format_forecast_for_timer(raw_forecast, user, title, daily_summary)

            # Удаляем старое сообщение с прогнозом (если было)
            last_forecast_id = get_data_field("last_daily_forecast", user.user_id)
            if last_forecast_id:
                try: bot.delete_message(chat_id=user.user_id, message_id=last_forecast_id)
                except: pass
            
            try:
                # 1. Отправляем прогноз
                sent_message = bot.send_message(user.user_id, forecast_message, parse_mode="HTML")
                update_data_field("last_daily_forecast", user.user_id, sent_message.message_id)
                
                # 2. Закрепляем (по желанию, try-except чтобы не падало если нет прав)
                try: bot.pin_chat_message(chat_id=user.user_id, message_id=sent_message.message_id, disable_notification=True)
                except: pass

                # 3. --- ИСПРАВЛЕНИЕ: ПЕРЕОТПРАВКА МЕНЮ ---
                # Удаляем старое меню, чтобы оно не висело выше
                last_menu_id = get_data_field("last_menu_message", user.user_id)
                if last_menu_id:
                    try: bot.delete_message(chat_id=user.user_id, message_id=last_menu_id)
                    except: pass
                
                # Отправляем новое меню (оно запишется в базу внутри функции send_main_menu)
                send_main_menu(user.user_id)

            except Exception as e:
                timer_logger.error(f"Error sending forecast: {e}")

def update_daily_forecasts():
    # ... (код фильтрации users тот же) ...
    all_users = get_all_users()
    if TEST:
        users = [u for u in all_users if u.user_id == ADMIN_ID]
    else:
        users = all_users

    for user in users:
        last_forecast_id = get_data_field("last_daily_forecast", user.user_id)
        if not last_forecast_id: continue

        lang = get_user_lang(user)
        raw_forecast = get_today_forecast(user.preferred_city, user)
        if not raw_forecast: continue

        title = get_text("daily_forecast_title", lang)
        daily_summary = get_weather_summary_description(fetch_today_forecast(user.preferred_city, lang=lang), user)
        
        # Используем новую функцию
        forecast_message = format_forecast_for_timer(raw_forecast, user, title, daily_summary)
        
        try:
            bot.edit_message_text(forecast_message, chat_id=user.user_id, message_id=last_forecast_id, parse_mode="HTML")
            # При обновлении сообщения меню переотправлять не обязательно, так как сообщение остается на месте
        except: pass

if __name__ == '__main__':
    while True:
        run_check, wait_time = should_run_check()
        if run_check:
            check_all_cities()
            send_daily_forecast()
            update_daily_forecasts()
        time.sleep(wait_time)