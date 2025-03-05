from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from telebot import types
from weather import fetch_today_forecast, fetch_weekly_forecast
from models import User
from datetime import date, timedelta, datetime

import os
import logging
import importlib

#ВЗАИМОДЕЙСТВИЕ С БД
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)

#ИЗВЛЕЧЕНИЕ ИНФОРМАЦИИ О ПОЛЬЗОВАТЕЛЕ
def get_user(user_id):
    """Возвращает пользователя, но не оставляет сессию открытой."""
    db = SessionLocal()
    user = db.query(User).filter(User.user_id == user_id).first()
    db.close()
    return user

active_sessions = {}

#СОХРАНЕНИЕ ПОЛЬЗОВАТЕЛЯ
def save_user(user_id, username=None, preferred_city=None):
    db = SessionLocal()
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        user = User(user_id=user_id, username=username, preferred_city=preferred_city)
        db.add(user)
        db.commit()
        logging.debug(f"Пользователь с ID {user_id} ({username}) добавлен в базу данных.")
    else:
        if preferred_city:
            user.preferred_city = preferred_city
        if username:
            user.username = username
        db.commit()
        logging.debug(f"Данные пользователя с ID {user_id} ({username}) обновлены.")
    db.close()

#ИЗМЕНЕНИЕ ЕДИНИЦ ИЗМЕРЕНИЯ
def update_user_unit(user_id, unit_type, new_value):
    db = SessionLocal()
    user = db.query(User).filter(User.user_id == user_id).first()

    if user:
        if unit_type == "temp":
            user.temp_unit = new_value
        elif unit_type == "pressure":
            user.pressure_unit = new_value
        elif unit_type == "wind_speed":
            user.wind_speed_unit = new_value
        db.commit()

    db.close()

#ОТОБРАЖЕНИЕ УВЕДОМЛЕНИЙ
def toggle_user_notifications(user_id, new_status):
    """Включает или отключает уведомления и возвращает новый статус."""
    with SessionLocal() as session:
        user = session.query(User).filter(User.user_id == user_id).first()
        if not user:
            return None
        
        user.notifications_enabled = new_status
        session.commit()
        
        return user.notifications_enabled

#ОБНОВЛЕНИЕ ГОРОДА ПОЛЬЗОВАТЕЛЯ
def update_user_city(user_id, city, username=None):
    db = SessionLocal()
    user = db.query(User).filter(User.user_id == user_id).first()

    if user:
        if user.preferred_city == city:
            db.close()
            return False
        user.preferred_city = city
    else:
        user = User(user_id=user_id, username=username, preferred_city=city)
        db.add(user)

    db.commit()
    db.close()
    return True 

#КОНВЕРТАЦИЯ ЕДИНИЦ ИЗМЕРЕНИЯ
def convert_temperature(value, unit):
    if unit == "C":
        return value
    elif unit == "F":
        return value * 9/5 + 32
    elif unit == "K":
        return value + 273.15

def convert_pressure(value, unit):
    conversions = {"mmHg": 1, "mbar": 1.333, "hPa": 1.333, "inHg": 0.03937}
    return value * conversions[unit]

def convert_wind_speed(value, unit):
    conversions = {"m/s": 1, "km/h": 3.6, "mph": 2.23694}
    return value * conversions[unit]

#ЗАЩИТА ОТ КРАША
def safe_execute(func):
    from bot import bot
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            user_id = args[0].from_user.id if args else None
            logging.error(f"Ошибка при выполнении {func.__name__} для пользователя с ID {user_id}: {str(e)}")
            if user_id:
                bot.reply_to(args[0],
                             "Упс.. Похоже произошли небольшие технические шоколадки.\n"
                             "Попробуйте позже ~o~")
    return wrapper

#ЛОКАЛЬНЫЙ ИМПОРТ БОТА
def lazy_import_bot(func):
    def wrapper(*args, **kwargs):
        bot = importlib.import_module("bot") 
        return func(bot, *args, **kwargs) 
    return wrapper

#ЛОГИРОВАНИЕ
def log_action(action, message):
    user = message.from_user
    log_message = (f"{action} | Time: {datetime.now().isoformat()} | "
                   f"User ID: {user.id} | Username: {user.first_name or ''} {user.last_name or ''} | "
                   f"Message: {message.text}")
    logging.debug(log_message)

#ПОЛУЧЕНИЕ ПРОГНОЗА ПОГОДЫ

MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
    7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
}

WEEKDAYS_RU = {
    "Monday": "Понедельник",
    "Tuesday": "Вторник",
    "Wednesday": "Среда",
    "Thursday": "Четверг",
    "Friday": "Пятница",
    "Saturday": "Суббота",
    "Sunday": "Воскресенье",
}


"""НА СЕГОДНЯ"""
def get_today_forecast(city):
    raw_data = fetch_today_forecast(city)
    if not raw_data:
        return None  

    today = date.today()
    day_name = WEEKDAYS_RU[today.strftime("%A")]
    date_formatted = f"{today.day} {MONTHS_RU[today.month]}"  

    today_data = raw_data[0]

    return {
        "date": f"{date_formatted}",
        "day_name": day_name,
        "description": today_data["weather"][0]["description"].capitalize(),
        "precipitation": today_data.get("pop", 0) * 100,
        "temp_min": min(entry["main"]["temp"] for entry in raw_data),
        "temp_max": max(entry["main"]["temp"] for entry in raw_data),
        "pressure": today_data["main"]["pressure"],
        "wind_speed": today_data["wind"]["speed"]
    }

"""НА НЕДЕЛЮ"""
def get_weekly_forecast(city):
    raw_data = fetch_weekly_forecast(city)
    if not raw_data:
        return None  

    daily_data = {}
    today = date.today()
    start_date = today + timedelta(days=1)

    for entry in raw_data:
        timestamp = entry["dt"] 
        date_obj = datetime.fromtimestamp(timestamp).date()
        day_name = WEEKDAYS_RU[date_obj.strftime("%A")]

        if date_obj < start_date or (date_obj - start_date).days >= 5:
            continue

        if date_obj not in daily_data:
            daily_data[date_obj] = {
                "day_name": day_name,
                "temp_min": entry["main"]["temp"],
                "temp_max": entry["main"]["temp"],
                "pressure": entry["main"]["pressure"],
                "wind_speed": entry["wind"]["speed"],
                "description": entry["weather"][0]["description"].capitalize(),
                "precipitation": entry.get("pop", 0) * 100
            }

        daily_data[date_obj]["temp_min"] = min(daily_data[date_obj]["temp_min"], entry["main"]["temp"])
        daily_data[date_obj]["temp_max"] = max(daily_data[date_obj]["temp_max"], entry["main"]["temp"])

    return [
        {
            "date": f"{date.day} {MONTHS_RU[date.month]}",
            "day_name": data["day_name"],
            **data
        }
        for date, data in sorted(daily_data.items())
    ]

#КЛАВИАТУРЫ
"""ПРОГНОЗ ПОГОДЫ"""
def generate_forecast_keyboard():
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("🌤 Сегодня", callback_data="forecast_today"))
    keyboard.add(types.InlineKeyboardButton("📆 Неделя", callback_data="forecast_week"))
    keyboard.add(types.InlineKeyboardButton("↩ Назад", callback_data="back_to_main"))
    return keyboard

"""ВЫБОР ДАННЫХ"""
def generate_format_keyboard():
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("Температура", callback_data="change_temp_unit"))
    keyboard.add(types.InlineKeyboardButton("Давление", callback_data="change_pressure_unit"))
    keyboard.add(types.InlineKeyboardButton("Скорость ветра", callback_data="change_wind_speed_unit"))
    keyboard.add(types.InlineKeyboardButton("↩ Назад", callback_data="back_to_settings"))
    return keyboard

"""ВЫБОР ЕДИНИЦ ИЗМЕРЕНИЯ"""
def generate_unit_selection_keyboard(current_value, unit_type):
    unit_options = {
        "temp": [("°C (Цельсий)", "C"), ("°F (Фаренгейт)", "F"), ("K (Кельвин)", "K")],
        "pressure": [("mmHg", "mmHg"), ("mbar", "mbar"), ("hPa", "hPa"), ("inHg", "inHg")],
        "wind_speed": [("м/с", "m/s"), ("км/ч", "km/h"), ("mph", "mph")]
    }

    keyboard = types.InlineKeyboardMarkup()
    for name, value in unit_options.get(unit_type, []):
        icon = " ✅" if current_value == value else ""
        keyboard.add(types.InlineKeyboardButton(f"{name}{icon}", callback_data=f"set_{unit_type}_unit_{value}"))

    keyboard.add(types.InlineKeyboardButton("↩ Сохранить", callback_data="format_settings"))
    return keyboard

#ФОРМАТИРОВАНИЕ ПОГОДЫ
def format_weather(city_name, temp, description, humidity, wind_speed, pressure, visibility, 
                   temp_unit, pressure_unit, wind_speed_unit):
    return (f"Текущая погода в г.{city_name}:\n"
            f"\n"
            f"▸ Погода: {description}\n"
            f"▸ Температура: {temp:.1f}°{temp_unit}\n"
            f"▸ Влажность: {humidity}%\n"
            f"▸ Скорость ветра: {wind_speed:.1f} {wind_speed_unit}\n"
            f"▸ Давление: {pressure:.1f} {pressure_unit}\n"
            f"▸ Видимость: {visibility} м\n\n"
            f"      ⟪ Deus Weather ⟫")

#ФОРМАТИРОВАНИЕ ЕДИНИЦ ИЗМЕРЕНИЕ
def format_weather_data(data, user):
    temperature = convert_temperature(data["temp"], user.temp_unit)
    pressure = convert_pressure(data["pressure"], user.pressure_unit)
    wind_speed = convert_wind_speed(data["wind_speed"], user.wind_speed_unit)

    return (f"🌡 Температура: {temperature:.1f} {user.temp_unit}\n"
            f"🧭 Давление: {pressure:.1f} {user.pressure_unit}\n"
            f"💨 Ветер: {wind_speed:.1f} {user.wind_speed_unit}")

#КОНВЕРТАЦИЯ ОСАДКОВ В %
def convert_precipitation_to_percent(precipitation_mm):
    if precipitation_mm > 0:
        return min(int(precipitation_mm * 100), 100)  
    return 0

#ОБРАБОТЧИК КОМАНД
def is_valid_command(text):
    valid_commands = ["/start", "/weather", "/changecity", "🌦 Узнать погоду", "📅 Прогноз погоды", "⚙️ Настройки"]
    return text in valid_commands

