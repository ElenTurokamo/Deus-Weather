from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy import create_engine
from sqlalchemy.sql import func
from sqlalchemy.pool import QueuePool
from telebot import types
from weather import fetch_today_forecast, fetch_weekly_forecast, fetch_tomorrow_forecast, get_city_timezone
from models import User, LocalVars
from datetime import date, timedelta, datetime, timezone
from zoneinfo import ZoneInfo

import os
import logging
import importlib
import json
import threading

#СЛОВАРИ
UNIT_TRANSLATIONS = {
    "temp": {"C": "°C", "F": "°F", "K": "К", "ICE": "🍦"},
    "pressure": {"mmHg": "мм рт.", "mbar": "мбар", "hPa": "гПа", "inHg": "дюйм. рт."},
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

WIND_DIRECTIONS = {
    (337.5, 360): "Север",
    (0, 22.5): "Север",
    (22.5, 67.5): "Северо-Восток",
    (67.5, 112.5): "Восток",
    (112.5, 157.5): "Юго-Восток",
    (157.5, 202.5): "Юг",
    (202.5, 247.5): "Юго-Запад",
    (247.5, 292.5): "Запад",
    (292.5, 337.5): "Северо-Запад"
}

BAD_WEATHER_DESCRIPTIONS = [
    "Гроза с небольшим дождём", "Гроза с дождём", "Гроза с сильным дождём",
    "Слабая гроза", "Гроза", "Сильная гроза", "Неустойчивая гроза",
    "Гроза с лёгкой моросью", "Гроза с моросью", "Гроза с сильной моросью",

    "Лёгкая морось", "Морось", "Сильная морось",
    "Лёгкий моросящий дождь", "Моросящий дождь", "Сильный моросящий дождь",
    "Ливень и морось", "Сильный ливень и морось", "Моросящий ливень",

    "Небольшой дождь", "Умеренный дождь", "Сильный дождь", "Очень сильный дождь",
    "Чрезвычайно сильный дождь", "Ледяной дождь",
    "Лёгкий ливень", "Ливень", "Сильный ливень", "Неустойчивый ливень",

    "Небольшой снег", "Снег", "Сильный снег",
    "Мокрый снег", "Слабый ливень с мокрым снегом", "Ливень с мокрым снегом",
    "Небольшой дождь со снегом", "Дождь со снегом",
    "Слабый ливень со снегом", "Ливень со снегом", "Сильный ливень со снегом",
]

WEATHER_EMOJI_MAP = {
    "гроза": "🌩",
    "морось": "🌫",
    "дождь": "☔️",
    "ливень": "🌧",
    "снег": "❄️",
    "туман": "🌁",
}

SEVERITY_MAP = {
    "гроза": 5,
    "сильный ливень": 4,
    "сильный снег": 4,
    "ливень": 3,
    "дождь": 2,
    "морось": 1,
}


#ВЗАИМОДЕЙСТВИЕ С БД
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL, poolclass=QueuePool, pool_recycle=280, pool_pre_ping=True, echo=False)
SessionLocal = sessionmaker(bind=engine)

def update_user(user_id: int, **kwargs):
    """Обновляет данные пользователя в БД. kwargs - любые поля, которые нужно обновить."""
    logging.debug(f"Вызов update_user с user_id={user_id} и kwargs={kwargs}")  # Логирование входящих аргументов
    db: Session = SessionLocal()
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        logging.error(f"Пользователь {user_id} не найден при попытке обновления.")
        db.close()
        return False
    for key, value in kwargs.items():
        if hasattr(user, key):
            logging.debug(f"Обновление поля {key} для пользователя {user_id}: {value}")
            setattr(user, key, value)
    try:
        db.commit()
        logging.debug(f"Обновление пользователя {user_id} успешно завершено с параметрами {kwargs}.")  # Логирование успешного обновления
    except Exception as e:
        logging.error(f"Ошибка при обновлении пользователя {user_id}: {e}")
        db.rollback()
    finally:
        db.close()
    return True


def initialize_json_from_db():
    """Инициализирует JSON-файл данными всех пользователей из БД."""
    db = SessionLocal()
    all_vars = db.query(LocalVars).all()
    db.close()

    # Заготовка под структуру данных
    data = {
        "last_menu_message": {},
        "last_settings_command": {},
        "last_user_command": {},
        "last_format_settings_menu": {},
        "last_bot_message": {},
        "last_daily_forecast": {},
        "last_weather_update": {},
        "stop_event": False
    }

    for vars_row in all_vars:
        uid = str(vars_row.user_id)
        data["last_menu_message"][uid] = vars_row.last_menu_message
        data["last_settings_command"][uid] = vars_row.last_settings_command
        data["last_user_command"][uid] = vars_row.last_user_command
        data["last_format_settings_menu"][uid] = vars_row.last_format_settings_menu
        data["last_bot_message"][uid] = vars_row.last_bot_message
        data["last_daily_forecast"][uid] = vars_row.last_daily_forecast
        data["last_weather_update"][uid] = vars_row.last_weather_update

    save_data(data)


def sync_json_to_db(user_id):
    """Сохраняет данные конкретного пользователя в БД"""
    db = SessionLocal()
    data = load_data()

    local_vars = db.query(LocalVars).filter(LocalVars.user_id == user_id).first()
    if not local_vars:
        local_vars = LocalVars(user_id=user_id)

    local_vars.last_menu_message = data.get("last_menu_message", {}).get(str(user_id))
    local_vars.last_settings_command = data.get("last_settings_command", {}).get(str(user_id))
    local_vars.last_user_command = data.get("last_user_command", {}).get(str(user_id))
    local_vars.last_format_settings_menu = data.get("last_format_settings_menu", {}).get(str(user_id))
    local_vars.last_bot_message = data.get("last_bot_message", {}).get(str(user_id))
    local_vars.last_daily_forecast = data.get("last_daily_forecast", {}).get(str(user_id))
    local_vars.last_weather_update = data.get("last_weather_update", {}).get(str(user_id))

    db.add(local_vars)
    db.commit()
    db.close()


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
    """Добавляет пользователя в базу данных или обновляет его данные."""
    db = SessionLocal()
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        last_unique_id = db.query(func.max(User.unique_id)).scalar() or 100000000
        new_unique_id = last_unique_id + 1
        timezone = get_city_timezone(preferred_city) if preferred_city else "UTC"
        user = User(
            user_id=user_id,
            unique_id=new_unique_id,
            username=username,
            preferred_city=preferred_city,
            timezone=timezone
        )
        db.add(user)
        db.commit()
        logging.debug(f"Пользователь с ID {user_id} ({username}) добавлен с unique_id {new_unique_id}.")
    else:
        if preferred_city:
            user.preferred_city = preferred_city
            user.timezone = get_city_timezone(preferred_city) or user.timezone 
        if username:
            user.username = username
        db.commit()
        logging.debug(f"Данные пользователя с ID {user_id} ({username}) обновлены.")
    db.close()


#ДЕКОДЕРЫ БД
def decode_tracked_params(tracked_params):
    """Декодирует JSON-строку или возвращает словарь, иначе — значение по умолчанию."""
    default_params = {
        "description": True,
        "temperature": True,
        "feels_like": True,
        "humidity": True,
        "precipitation": True,
        "pressure": True,
        "wind_speed": True,
        "visibility": True,
        "wind_direction": False, 
        "wind_gust": False,     
        "clouds": False 
    }
    if isinstance(tracked_params, str):
        try:
            return json.loads(tracked_params)
        except json.JSONDecodeError:
            logging.warning("❌ Ошибка декодирования JSON. Используем значения по умолчанию.")
            return default_params
    elif isinstance(tracked_params, dict):
        return tracked_params
    else:
        logging.warning("❌ Некорректный формат tracked_params. Используем значения по умолчанию.")
        return default_params
    

def decode_notification_settings(notification_settings):
    """Декодирует JSON-строку или возвращает словарь, иначе — значение по умолчанию."""
    default_settings = {
        "bot_notifications": True,
        "forecast_notifications": True,
        "weather_threshold_notifications": False
    }
    if isinstance(notification_settings, str):
        try:
            return json.loads(notification_settings)
        except json.JSONDecodeError:
            logging.warning("❌ Ошибка декодирования JSON настроек уведомлений. Используем значения по умолчанию.")
            return default_settings
    elif isinstance(notification_settings, dict):
        return notification_settings
    else:
        logging.warning("❌ Некорректный формат notification_settings. Используем значения по умолчанию.")
        return default_settings


def load_data():
    """Загружает данные из JSON-файла."""
    with _lock:
        with open(DATA_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
        

def save_data(data):
    """Сохраняет данные в JSON-файл."""
    with _lock:
        with open(DATA_FILE, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=4)


#ОБЩЕЕ ХРАНИЛИЩЕ СЛОВАРЕЙ
DATA_FILE = "data_store.json"
_lock = threading.Lock()
if not os.path.exists(DATA_FILE):
    initialize_json_from_db()
    if not os.path.exists(DATA_FILE): 
        with open(DATA_FILE, "w", encoding="utf-8") as file:
            json.dump({
                "last_menu_message": {},
                "last_settings_command": {},
                "last_bot_message": {},
                "last_user_command": {},
                "last_daily_forecast": {},
                "last_format_settings_menu": {},
                "last_weather_update": {},
                "stop_event": False
            }, file, ensure_ascii=False, indent=4)


def get_data(key):
    """Получает данные из хранилища по ключу."""
    data = load_data()
    return data.get(key, {})


def set_data(key, value, user_id=None):
    """Устанавливает значение и сохраняет для указанного пользователя."""
    data = load_data()
    if user_id is not None:
        if key not in data:
            data[key] = {}
        data[key][str(user_id)] = value
    else:
        data[key] = value
    save_data(data)
    if user_id is not None:
        sync_json_to_db(user_id)

def update_data_field(dict_key, sub_key, value):
    """Обновляет поле внутри словаря и синхронизирует с БД"""
    data = load_data()
    if dict_key not in data:
        data[dict_key] = {}
    data[dict_key][str(sub_key)] = value
    save_data(data)
    sync_json_to_db(int(sub_key))  


def get_data_field(dict_key, sub_key):
    """Получает значение конкретного поля из словаря в хранилище."""
    data = load_data()
    return data.get(dict_key, {}).get(str(sub_key))


def is_stop_event_set():
    """Проверяет, установлен ли stop_event."""
    return get_data("stop_event")


def set_stop_event(value):
    """Устанавливает значение stop_event."""
    set_data("stop_event", value)


#ПОЛУЧЕНИЕ СПИСКА ПОЛЬЗОВАТЕЛЕЙ ИЗ БД
def get_all_users(filter_notifications=True):
    """Возвращает список всех пользователей из базы данных."""
    db = SessionLocal()
    users = db.query(User).all()
    db.close()

    if filter_notifications:
        users = [
            user for user in users 
            if decode_notification_settings(user.notifications_settings).get("forecast_notifications", False)
        ]

    return users

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
        db.close()  
    else:
        db.close() 

#ОТОБРАЖЕНИЕ УВЕДОМЛЕНИЙ
def toggle_user_notifications(user_id, new_status):
    """Включает или отключает уведомления и возвращает новый статус."""
    with SessionLocal() as session:
        user = session.query(User).filter(User.user_id == user_id).first()
        if not user:
            return None
        
        settings = decode_notification_settings(user.notifications_settings)
        settings["forecast_notifications"] = new_status
        user.notifications_settings = json.dumps(settings)
        session.commit()
        
        return settings["forecast_notifications"]

#ОБНОВЛЕНИЕ ГОРОДА ПОЛЬЗОВАТЕЛЯ
def update_user_city(user_id, city, username=None):
    """Обновляет город и часовой пояс пользователя в БД."""
    with SessionLocal() as db:  # Используем контекстный менеджер для автоматического закрытия сессии
        user = db.query(User).filter(User.user_id == user_id).first()
        if user:
            if user.preferred_city == city:
                return False
            user.preferred_city = city
            user.timezone = get_city_timezone(city) or "UTC"
        else:
            user = User(
                user_id=user_id,
                username=username,
                preferred_city=city,
                timezone=get_city_timezone(city) or "UTC"
            )
            db.add(user)
        db.commit()
        logging.info(f"Пользователь {user_id}: город обновлён на {city}, часовой пояс — {user.timezone}.")
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
    elif unit == "ICE":
        return round(-value / 18, 1)

def convert_pressure(value, unit):
    logging.debug(f"Converting {value} to {unit}")
    conversions = {"mmHg": 0.75006, "mbar": 1, "hPa": 1, "inHg": 0.02953}
    return round(value * conversions[unit], 1)

def convert_wind_speed(value, unit):
    logging.debug(f"Converting {value} to {unit}")
    conversions = {"m/s": 1, "km/h": 3.6, "mph": 2.23694}
    return round(value * conversions[unit], 1)

def get_wind_direction(degree):
    for (start, end), direction in WIND_DIRECTIONS.items():
        if start <= degree < end:
            return direction
    return "Неопределено"


#ЗАЩИТА ОТ КРАША
def safe_execute(func):
    bot = get_bot()
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logging.error(f"Ошибка в функции {func.__name__}: {str(e)} | Аргументы: {args}, {kwargs}")

            if args and hasattr(args[0], "chat"):
                bot.reply_to(args[0],
                             "Упс... Похоже, произошли небольшие технические шоколадки!\n"
                             "Отправьте повторный запрос немного позже ~o~")
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
    """Создает клавиатуру для сообщения с меню прогноза погоды"""
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("🌤 Сегодня", callback_data="forecast_today"))
    keyboard.add(types.InlineKeyboardButton("🌧 Завтра", callback_data="forecast_tomorrow"))
    keyboard.add(types.InlineKeyboardButton("📆 Неделя", callback_data="forecast_week"))
    keyboard.add(types.InlineKeyboardButton("↩ Назад", callback_data="back_from_forecast_menu"))
    return keyboard

def generate_format_keyboard():
    """ЕДИНИЦЫ ИЗМЕРЕНИЯ ДАННЫХ"""
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("Температура", callback_data="change_temp_unit"))
    keyboard.add(types.InlineKeyboardButton("Давление", callback_data="change_pressure_unit"))
    keyboard.add(types.InlineKeyboardButton("Скорость ветра", callback_data="change_wind_speed_unit"))
    keyboard.add(types.InlineKeyboardButton("↩ Назад", callback_data="back_to_settings"))
    return keyboard


def generate_weather_data_keyboard(user):
    """Создаёт клавиатуру для выбора отображаемых данных (2 столбца)"""
    options = {
        "description": "Описание",
        "temperature": "Температура",
        "humidity": "Влажность",
        "precipitation": "Шанс осадков",
        "pressure": "Давление",
        "wind_speed": "Скорость ветра",
        "visibility": "Видимость",
        "feels_like": "Мнимая тем-ра",
        "clouds": "Облачность",
        "wind_direction": "Курс ветра",
        "wind_gust": "Порывы ветра"
    }

    tracked_params = decode_tracked_params(user.tracked_weather_params)
    keyboard = types.InlineKeyboardMarkup(row_width=2) 
    buttons = [
        types.InlineKeyboardButton(
            f"{'✅' if tracked_params.get(key, False) else ''} {label}",
            callback_data=f"toggle_weather_param_{key}"
        )
        for key, label in options.items()
    ]
    keyboard.add(*buttons)
    keyboard.add(types.InlineKeyboardButton("↩ Назад", callback_data="back_to_settings"))
    return keyboard


def generate_notification_settings_keyboard(user):
    """Создаёт клавиатуру для выбора настроек уведомлений"""
    options = {
        "weather_threshold_notifications": "Оповещения об изменении погоды",
        "forecast_notifications": "Ежедневный прогноз",
        "bot_notifications": "Новости бота"
    }

    notification_settings = decode_notification_settings(user.notifications_settings)
    keyboard = types.InlineKeyboardMarkup()

    for key, label in options.items():
        icon = "✅" if notification_settings.get(key, False) else ""
        keyboard.add(types.InlineKeyboardButton(f"{icon} {label}", callback_data=f"toggle_notification_{key}"))

    keyboard.add(types.InlineKeyboardButton("↩ Назад", callback_data="back_to_settings"))
    return keyboard


"""ВЫБОР ЕДИНИЦ ИЗМЕРЕНИЯ"""
def generate_unit_selection_keyboard(current_value, unit_type):
    unit_options = {
        "temp": [("°C (Цельсий)", "C"), ("°F (Фаренгейт)", "F"), ("K (Кельвин)", "K"), ("Мороженки (🍦)", "ICE")],
        "pressure": [("мм рт. ст.", "mmHg"), ("мбар", "mbar"), ("гПа", "hPa"), ("дюйм. рт. ст.", "inHg")],
        "wind_speed": [("м/с", "m/s"), ("км/ч", "km/h"), ("миль/ч", "mph")]
    }

    keyboard = types.InlineKeyboardMarkup()
    for name, value in unit_options.get(unit_type, []):
        icon = " ✅" if current_value == value else ""
        keyboard.add(types.InlineKeyboardButton(f"{name}{icon}", callback_data=f"set_{unit_type}_unit_{value}"))

    keyboard.add(types.InlineKeyboardButton("↩ Сохранить", callback_data="return_to_format_settings"))
    return keyboard


def format_weather_data(data, user):
    """
    Форматирует погодные данные с учётом единиц измерения и настроек пользователя.
    Автоматически декодирует JSON из tracked_weather_params.
    """
    tracked_params = decode_tracked_params(user.tracked_weather_params)

    temperature = convert_temperature(data["temp"], user.temp_unit)
    pressure = convert_pressure(data["pressure"], user.pressure_unit)
    wind_speed = convert_wind_speed(data["wind_speed"], user.wind_speed_unit)

    logging.debug(f"Конвертация: {data['temp']}° -> {temperature} {user.temp_unit}")
    logging.debug(f"Конвертация: {data['pressure']} -> {pressure} {user.pressure_unit}")
    logging.debug(f"Конвертация: {data['wind_speed']} -> {wind_speed} {user.wind_speed_unit}")

    header = f"Сейчас в г.{data['city_name']}:"
    max_line_length = 21
    line = "─" * min(len(header), max_line_length)
    
    weather_text = (
        f"<b>{header}</b>\n"
        f"{line}\n"
    )

    params = {
        "description": ("Погода", data["description"].capitalize()),
        "temperature": ("Температура", f"{temperature:.1f}{UNIT_TRANSLATIONS['temp'][user.temp_unit]}"),
        "feels_like": ("Ощущается как", f"{convert_temperature(data['feels_like'], user.temp_unit):.1f}{UNIT_TRANSLATIONS['temp'][user.temp_unit]}"),
        "humidity": ("Влажность", f"{data['humidity']}%"),
        "precipitation": ("Вероятность осадков", f"{data.get('precipitation', 0)}%"),
        "pressure": ("Давление", f"{pressure:.1f} {UNIT_TRANSLATIONS['pressure'][user.pressure_unit]}"),
        "wind_speed": ("Скорость ветра", f"{wind_speed:.1f} {UNIT_TRANSLATIONS['wind_speed'][user.wind_speed_unit]}"),
        "wind_direction": ("Направление ветра", f"{get_wind_direction(data['wind_direction'])} ({data['wind_direction']}°)"),
        "wind_gust": ("Порывы ветра", f"{convert_wind_speed(data['wind_gust'], user.wind_speed_unit):.1f} {UNIT_TRANSLATIONS['wind_speed'][user.wind_speed_unit]}"),
        "clouds": ("Облачность", f"{data['clouds']}%"),
        "visibility": ("Видимость", f"{data['visibility']} м")
    }

    for param, (label, value) in params.items():
        if tracked_params.get(param, False): 
            weather_text += f"▸ {label}: {value}\n"

    return weather_text + "\n🍉 Какой бы ни была погода — этот день прекрасен!"


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
    valid_commands = ["/start", "/weather", "/changecity", "🔅 Погода сейчас", "📅 Прогноз погоды", "⚙️ Настройки"]
    return text in valid_commands


#ПОЛУЧЕНИЕ ПОГОДНЫХ ДАННЫХ
def extract_weather_data(entry):
    """Извлекает погодные данные из записи API"""
    temp = entry["main"]["temp"]
    feels_like = entry["main"].get("feels_like", temp)
    temp_min = entry["main"].get("temp_min", temp)
    temp_max = entry["main"].get("temp_max", temp)
    feels_like = entry["main"].get("feels_like", None)
    humidity = entry["main"].get("humidity", None)
    visibility = entry.get("visibility", None)
    pressure = entry["main"].get("pressure", None)
    wind_speed = entry["wind"].get("speed", None)
    wind_direction = entry["wind"].get("deg", None)
    wind_gust = entry["wind"].get("gust", None)
    clouds = entry["clouds"].get("all", None)
    description = entry["weather"][0]["description"].capitalize()
    precipitation = entry.get("pop", None)

    weather_data = {
        "temp": temp,
        "feels_like": feels_like,
        "temp_min": temp_min,
        "temp_max": temp_max,
        "feels_like": feels_like,
        "humidity": humidity,
        "visibility": visibility,
        "pressure": pressure,
        "wind_speed": wind_speed,
        "wind_direction": wind_direction,
        "wind_gust": wind_gust,
        "clouds": clouds,
        "description": description,
        "precipitation": round(precipitation * 100) if precipitation is not None else None
    }

    logging.debug(f"Извлечённые погодные данные: {weather_data}")
    return weather_data


#ПОЛУЧЕНИЕ ПРОГНОЗА ПОГОДЫ
def get_today_forecast(city, user, target_date=None):
    """Прогноз погоды на определённую дату с учётом предпочтений пользователя."""
    raw_data = fetch_today_forecast(city)
    if not raw_data:
        return None

    tz = ZoneInfo(user.timezone or "UTC")
    now = datetime.now(tz)
    today = target_date or now.date()

    # Фильтруем только те данные, которые соответствуют нужной дате
    today_entries = [
        entry for entry in raw_data
        if datetime.fromtimestamp(entry["dt"], tz).date() == today
    ]

    if not today_entries:
        logging.warning(f"⚠ Нет прогноза на дату {today} для города {city}")
        return None
    
    descriptions = []
    for entry in today_entries:
        if "weather" in entry and entry["weather"]:
            descriptions.append(entry["weather"][0]["description"])

    # Берём ближайшую точку к текущему времени (или просто первую)
    today_data = min(today_entries, key=lambda entry: abs(datetime.fromtimestamp(entry["dt"], tz) - now))

    if "main" not in today_data or "temp" not in today_data["main"]:
        logging.error(f"❌ Ошибка: в данных нет 'main' или 'temp'! {today_data}")
        return None

    weather_data = extract_weather_data(today_data)
    tracked_params = decode_tracked_params(user.tracked_weather_params)
    filtered_weather_data = {}

    for key, value in weather_data.items():
        if tracked_params.get(key, False) and value is not None:
            filtered_weather_data[key] = value
        else:
            logging.debug(f"Ключ {key} исключён из данных прогноза: {value}")

    temp_min = weather_data.get("temp_min", weather_data["temp"])
    temp_max = weather_data.get("temp_max", weather_data["temp"])
    filtered_weather_data["temp_min"] = min(filtered_weather_data.get("temp_min", float("inf")), temp_min)
    filtered_weather_data["temp_max"] = max(filtered_weather_data.get("temp_max", float("-inf")), temp_max)

    filtered_weather_data["descriptions"] = descriptions

    day_name = WEEKDAYS_RU[today.strftime("%A")]
    date_formatted = f"{today.day} {MONTHS_RU[today.month]}"
    filtered_weather_data.update({
        "date": date_formatted,
        "day_name": day_name
    })

    return filtered_weather_data



def get_tomorrow_forecast(city, user):
    """Прогноз погоды на завтра с учётом локального времени из timezone пользователя"""
    raw_data = fetch_tomorrow_forecast(city)
    if not raw_data or not user.timezone:
        return None
    try:
        user_tz = ZoneInfo(user.timezone)
    except Exception as e:
        logging.error(f"❌ Ошибка при загрузке таймзоны: {e}")
        return None
    now_local = datetime.now(user_tz)
    tomorrow_date = (now_local + timedelta(days=1)).date()
    tomorrow_entries = []
    descriptions = [] 
    for entry in raw_data:
        entry_dt_utc = datetime.fromtimestamp(entry["dt"], tz=timezone.utc)
        entry_dt_local = entry_dt_utc.astimezone(user_tz)
        if entry_dt_local.date() == tomorrow_date:
            tomorrow_entries.append(entry)
            if "weather" in entry and entry["weather"]:
                descriptions.append(entry["weather"][0]["description"])
    if not tomorrow_entries:
        return None
    day_name = WEEKDAYS_RU[tomorrow_date.strftime("%A")]
    date_formatted = f"{tomorrow_date.day} {MONTHS_RU[tomorrow_date.month]}"
    tracked_params = decode_tracked_params(user.tracked_weather_params)
    filtered_weather_data = {}
    temp_min = float("inf")
    temp_max = float("-inf")
    for entry in tomorrow_entries:
        if "main" not in entry or "temp" not in entry["main"]:
            logging.error(f"❌ Ошибка: в данных нет 'main' или 'temp'! {entry}")
            continue
        weather_data = extract_weather_data(entry)
        for key, value in weather_data.items():
            if tracked_params.get(key, False) and value is not None:
                filtered_weather_data[key] = value
        temp = weather_data.get("temp")
        if temp is not None:
            temp_min = min(temp_min, temp)
            temp_max = max(temp_max, temp)
    if temp_min == float("inf"):
        temp_min = None
    if temp_max == float("-inf"):
        temp_max = None
    filtered_weather_data.update({
        "temp_min": temp_min,
        "temp_max": temp_max,
        "date": date_formatted,
        "day_name": day_name,
        "descriptions": descriptions  
    })
    return filtered_weather_data


def get_weekly_forecast(city, user):
    """Прогноз погоды на неделю с учётом tracked_weather_params"""
    raw_data = fetch_weekly_forecast(city)
    if not raw_data:
        return None  
    daily_data = {}
    today = date.today()
    start_date = today + timedelta(days=1)
    tracked_params = decode_tracked_params(user.tracked_weather_params)
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
                "descriptions": [], 
                **{
                    key: value for key, value in weather_data.items() if key in tracked_params and tracked_params[key]
                }
            }
        daily_data[date_obj]["temp_min"] = min(daily_data[date_obj].get("temp_min", float("inf")), weather_data["temp"])
        daily_data[date_obj]["temp_max"] = max(daily_data[date_obj].get("temp_max", float("-inf")), weather_data["temp"])
        if "weather" in entry and entry["weather"]:
            daily_data[date_obj]["descriptions"].append(entry["weather"][0]["description"])
    return [
        {
            "date": f"{date.day} {MONTHS_RU[date.month]}",
            "day_name": data["day_name"],
            **data
        }
        for date, data in sorted(daily_data.items())
    ]


def get_forecast_emoji(description):
    description = description.lower()
    for key, emoji in WEATHER_EMOJI_MAP.items():
        if key in description:
            return emoji
    return "🌦" 


def get_most_severe_description(descriptions):
    def score(desc):
        for key, val in SEVERITY_MAP.items():
            if key in desc.lower():
                return val
        return 0
    return max(descriptions, key=score)


MAX_GAP_HOURS = 3


def group_bad_weather_periods(bad_weather_periods):
    """Группирует подряд идущие плохие погодные прогнозы."""
    if not bad_weather_periods:
        return []

    groups = []
    current_group = [bad_weather_periods[0]]

    for i in range(1, len(bad_weather_periods)):
        prev_time, _ = bad_weather_periods[i-1]
        curr_time, _ = bad_weather_periods[i]
        if (curr_time - prev_time) <= timedelta(hours=MAX_GAP_HOURS):
            current_group.append(bad_weather_periods[i])
        else:
            groups.append(current_group)
            current_group = [bad_weather_periods[i]]

    groups.append(current_group)
    return groups



def get_weather_summary_description(forecast_data, user):
    """Анализирует прогноз и выдает краткое, но честное резюме погоды."""
    tz = ZoneInfo(user.timezone or "UTC")
    now = datetime.now(tz)
    today = now.date()

    # Собираем плохую погоду с фильтром по времени
    bad_weather_periods = []
    for entry in forecast_data:
        timestamp = datetime.fromtimestamp(entry["dt"], tz)
        if timestamp.date() != today:
            continue
        if timestamp < now - timedelta(hours=1):  # Не трогаем старьё
            continue

        description = entry["weather"][0]["description"].capitalize()
        if description in BAD_WEATHER_DESCRIPTIONS:
            bad_weather_periods.append((timestamp, description))

    if not bad_weather_periods:
        return "🌤 Сегодня осадков не ожидается!"

    # Группируем события
    groups = group_bad_weather_periods(bad_weather_periods)

    # Ищем группу, которая либо актуальна прямо сейчас, либо ближайшая
    for group in groups:
        start_time, start_desc = group[0]
        end_time, end_desc = group[-1]

        if now <= end_time:
            main_description = get_most_severe_description([desc for _, desc in group])
            emoji = get_forecast_emoji(main_description)

            # Если событие длится больше одного таймслота
            if start_time != end_time:
                start_str = start_time.strftime("%H:%M")
                end_str = end_time.strftime("%H:%M")
                return f"{emoji} {main_description} ожидается с {start_str} до {end_str}."
            else:
                start_str = start_time.strftime("%H:%M")
                return f"{emoji} {main_description} ожидается в {start_str}."

    return "🌤 Сегодня осадков не ожидается!"