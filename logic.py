from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy import create_engine
from telebot import types
from weather import fetch_today_forecast, fetch_weekly_forecast
from models import User
from datetime import date, timedelta, datetime

import os
import logging
import importlib

#СЛОВАРИ
UNIT_TRANSLATIONS = {
    "temp": {"C": "°C", "F": "°F", "K": "К"},
    "pressure": {"mmHg": "мм рт. ст.", "mbar": "мбар", "hPa": "гПа", "inHg": "дюйм. рт. ст."},
    "wind_speed": {"m/s": "м/с", "km/h": "км/ч", "mph": "миль/ч"}
}

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

#ВЗАИМОДЕЙСТВИЕ С БД
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)

def update_user(user_id: int, **kwargs):
    """Обновляет данные пользователя в БД. kwargs - любые поля, которые нужно обновить."""
    db: Session = SessionLocal()
    user = db.query(User).filter(User.user_id == user_id).first()
    
    if not user:
        db.close()
        return False 

    for key, value in kwargs.items():
        if hasattr(user, key):  
            setattr(user, key, value)

    db.commit()
    db.close()
    return True

#ИЗВЛЕЧЕНИЕ ИНФОРМАЦИИ О ПОЛЬЗОВАТЕЛЕ
def get_user(user_id):
    """Возвращает пользователя, но не оставляет сессию открытой."""
    db = SessionLocal()
    user = db.query(User).filter(User.user_id == user_id).first()

    logging.debug(f"Вызов get_user() с user_id={user_id} (Тип: {type(user_id)}) - {'Найден' if user else 'Не найден'}")

    db.close()
    return user
active_sessions = {}

#БЕЗОПАСНЫЙ ИМПОРТ БОТА
def get_bot():
    bot_module = importlib.import_module("bot")
    return bot_module.bot

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
    logging.debug(f"update_user_unit вызван с user_id={user_id}, unit_type={unit_type}, new_value={new_value}")
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
        db.close()  # Закрываем сессию сразу после изменений
    else:
        db.close()  # Чтобы не было утечек, закрываем и если user=None

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
    logging.debug(f"Converting {value} to {unit}")
    if unit == "C":
        return value
    elif unit == "F":
        return value * 9/5 + 32
    elif unit == "K":
        return value + 273.15

def convert_pressure(value, unit):
    logging.debug(f"Converting {value} to {unit}")
    conversions = {"mmHg": 1, "mbar": 1.333, "hPa": 1.333, "inHg": 0.03937}
    return value * conversions[unit]

def convert_wind_speed(value, unit):
    logging.debug(f"Converting {value} to {unit}")
    conversions = {"m/s": 1, "km/h": 3.6, "mph": 2.23694}
    return value * conversions[unit]

#ЗАЩИТА ОТ КРАША
def safe_execute(func):
    bot = get_bot()
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

#КЛАВИАТУРЫ
def generate_forecast_keyboard():
    """ПРОГНОЗ ПОГОДЫ"""
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("🌤 Сегодня", callback_data="forecast_today"))
    keyboard.add(types.InlineKeyboardButton("📆 Неделя", callback_data="forecast_week"))
    keyboard.add(types.InlineKeyboardButton("↩ Назад", callback_data="back_to_forecast_menu"))
    return keyboard

def generate_format_keyboard():
    """ВЫБОР ДАННЫХ"""
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("Температура", callback_data="change_temp_unit"))
    keyboard.add(types.InlineKeyboardButton("Давление", callback_data="change_pressure_unit"))
    keyboard.add(types.InlineKeyboardButton("Скорость ветра", callback_data="change_wind_speed_unit"))
    keyboard.add(types.InlineKeyboardButton("↩ Назад", callback_data="back_to_settings"))
    return keyboard

def generate_weather_data_keyboard(user):
    """Создаёт клавиатуру для выбора отображаемых данных"""
    options = {
        "description": "Описание",
        "temperature": "Температура",
        "humidity": "Влажность",
        "precipitation": "Вероятность осадков",
        "pressure": "Давление",
        "wind_speed": "Скорость ветра",
        "visibility": "Видимость"
    }

    selected_params = user.tracked_weather_params.split(",") if user.tracked_weather_params else []
    keyboard = types.InlineKeyboardMarkup()

    for key, label in options.items():
        icon = " ✅" if key in selected_params else ""
        keyboard.add(types.InlineKeyboardButton(f"{label}{icon}", callback_data=f"toggle_weather_param_{key}"))

    keyboard.add(types.InlineKeyboardButton("↩ Назад", callback_data="back_to_settings"))
    return keyboard

"""ВЫБОР ЕДИНИЦ ИЗМЕРЕНИЯ"""
def generate_unit_selection_keyboard(current_value, unit_type):
    unit_options = {
        "temp": [("°C (Цельсий)", "C"), ("°F (Фаренгейт)", "F"), ("K (Кельвин)", "K")],
        "pressure": [("мм рт. ст.", "mmHg"), ("мбар", "mbar"), ("гПа", "hPa"), ("дюйм. рт. ст.", "inHg")],
        "wind_speed": [("м/с", "m/s"), ("км/ч", "km/h"), ("миль/ч", "mph")]
    }

    keyboard = types.InlineKeyboardMarkup()
    for name, value in unit_options.get(unit_type, []):
        icon = " ✅" if current_value == value else ""
        keyboard.add(types.InlineKeyboardButton(f"{name}{icon}", callback_data=f"set_{unit_type}_unit_{value}"))

    keyboard.add(types.InlineKeyboardButton("↩ Сохранить", callback_data="format_settings"))
    return keyboard

def format_weather_data(data, user):
    """Форматирует погодные данные с учётом единиц измерения и настроек пользователя"""
    tracked_params = set(user.tracked_weather_params.split(",")) if user.tracked_weather_params else set()

    temperature = convert_temperature(data["temp"], user.temp_unit)
    pressure = convert_pressure(data["pressure"], user.pressure_unit)
    wind_speed = convert_wind_speed(data["wind_speed"], user.wind_speed_unit)

    logging.debug(f"Конвертация: {data['temp']}° -> {temperature} {user.temp_unit}")
    logging.debug(f"Конвертация: {data['pressure']} -> {pressure} {user.pressure_unit}")
    logging.debug(f"Конвертация: {data['wind_speed']} -> {wind_speed} {user.wind_speed_unit}")

    weather_text = (
        (f"<b>✦ </b>" + f"<u><b>Сейчас в г.{data['city_name']}:</b></u>\n")
    )

    params = {
        "description": ("Погода", data["description"]),
        "temperature": ("Температура", f"{temperature:.1f}{UNIT_TRANSLATIONS['temp'][user.temp_unit]}"),
        "humidity": ("Влажность", f"{data['humidity']}%"),
        "precipitation": ("Вероятность осадков", f"{data.get('precipitation', 0)}%"),
        "pressure": ("Давление", f"{pressure:.1f} {UNIT_TRANSLATIONS['pressure'][user.pressure_unit]}"),
        "wind_speed": ("Скорость ветра", f"{wind_speed:.1f} {UNIT_TRANSLATIONS['wind_speed'][user.wind_speed_unit]}"),
        "visibility": ("Видимость", f"{data['visibility']} м")
    }

    for param, (label, value) in params.items():
        if param in tracked_params:
            logging.debug(f"Добавление параметра: {param} - {label}: {value}")
            weather_text += f"▸ {label}: {value}\n"

    return weather_text + "\n      ⟪ Deus Weather ⟫"

def format_change(label, old_value, new_value, unit=""):
    """Форматирует изменения данных, добавляя стрелки при изменении значений."""
    if old_value is None or old_value != new_value:
        arrow = "📈" if new_value > old_value else "📉"
        return f"<b>{label}: {new_value}{unit} {arrow}</b>"
    return f"{label}: {new_value}{unit}"

#КОНВЕРТАЦИЯ ОСАДКОВ В %
def convert_precipitation_to_percent(precipitation_mm):
    if precipitation_mm > 0:
        return min(int(precipitation_mm * 100), 100)  
    return 0

#ОБРАБОТЧИК КОМАНД
def is_valid_command(text):
    valid_commands = ["/start", "/weather", "/changecity", "🌎 Узнать погоду", "📅 Прогноз погоды", "⚙️ Настройки"]
    return text in valid_commands

#ПОЛУЧЕНИЕ ПОГОДНЫХ ДАННЫХ
def extract_weather_data(entry):
    """Извлекает погодные данные из записи API"""
    temp = entry["main"]["temp"]
    return {
        "temp": temp,
        "temp_min": entry["main"].get("temp_min", temp),
        "temp_max": entry["main"].get("temp_max", temp),
        "humidity": entry["main"].get("humidity", None),
        "visibility": entry.get("visibility", None),
        "pressure": entry["main"]["pressure"],
        "wind_speed": entry["wind"]["speed"],
        "description": entry["weather"][0]["description"].capitalize(),
        "precipitation": round(entry.get("pop", 0) * 100)
    }

#ПОЛУЧЕНИЕ ПРОГНОЗА ПОГОДЫ
def get_today_forecast(city, user):
    """Прогноз погоды на сегодня"""
    raw_data = fetch_today_forecast(city)
    if not raw_data:
        return None  

    today = date.today()
    day_name = WEEKDAYS_RU[today.strftime("%A")]
    date_formatted = f"{today.day} {MONTHS_RU[today.month]}"  

    today_data = raw_data[0]

    if "main" not in today_data or "temp" not in today_data["main"]:
        logging.error(f"❌ Ошибка: в данных нет 'main' или 'temp'! {today_data}")
        return None  

    weather_data = extract_weather_data(today_data)
    weather_data.update({
        "date": date_formatted,
        "day_name": day_name
    })

    return weather_data


def get_weekly_forecast(city, user):
    """Прогноз погоды на неделю"""
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

        if "main" not in entry or "temp" not in entry["main"]:
            logging.error(f"❌ Ошибка: в данных нет 'main' или 'temp'! {entry}")
            continue

        weather_data = extract_weather_data(entry)

        if date_obj not in daily_data:
            daily_data[date_obj] = {
                "day_name": day_name,
                **weather_data
            }

        daily_data[date_obj]["temp_min"] = min(daily_data[date_obj]["temp_min"], weather_data["temp"])
        daily_data[date_obj]["temp_max"] = max(daily_data[date_obj]["temp_max"], weather_data["temp"])

    return [
        {
            "date": f"{date.day} {MONTHS_RU[date.month]}",
            "day_name": data["day_name"],
            **data
        }
        for date, data in sorted(daily_data.items())
    ]