from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy import create_engine
from sqlalchemy.sql import func
from sqlalchemy.pool import QueuePool
from telebot import types
from weather import fetch_today_forecast, fetch_weekly_forecast, fetch_tomorrow_forecast, get_city_timezone
from models import User, LocalVars
from datetime import date, timedelta, datetime, timezone
from zoneinfo import ZoneInfo
from texts import TEXTS, get_api_lang_code 
from collections import Counter
from datetime import datetime

import os
import logging
import importlib
import json
import threading

#АДАПТАЦИЯ ЯЗЫКА ПОЛЬЗОВАТЕЛЯ
def get_user_lang(user):
    return getattr(user, 'language', 'ru') or 'ru'

def get_text(key, lang):
    lang = lang or "ru"
    return TEXTS.get(lang, TEXTS["ru"]).get(key, f"MISSING_{key}")

def get_translation_dict(category, lang="ru"):
    lang = lang or "ru"
    return TEXTS.get(lang, TEXTS["ru"]).get(category, {})

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
        "pressure": False,
        "wind_speed": True,
        "visibility": True,
        "wind_direction": False, 
        "wind_gust": False,     
        "clouds": True 
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

def get_wind_direction(degree, lang="ru"):
    degree %= 360
    directions = get_translation_dict("wind_directions", lang)
    for (start, end), direction in directions.items():
        if start <= degree < end:
            return direction
    return get_text("unknown_direction", lang)


#ЗАЩИТА ОТ КРАША
def safe_execute(func):
    bot = get_bot()
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logging.error(f"Ошибка в функции {func.__name__}: {str(e)} | Аргументы: {args}, {kwargs}")

            if args and hasattr(args[0], "chat"):
                user_id = args[0].from_user.id
                user = get_user(user_id)
                lang = get_user_lang(user)
                
                bot.reply_to(args[0], get_text("error_technical_glitch", lang))
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
def generate_forecast_keyboard(chat_id):
    """Создает клавиатуру для сообщения с меню прогноза погоды"""
    user = get_user(chat_id)
    lang = get_user_lang(user)

    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton(get_text("btn_forecast_today", lang), callback_data="forecast_today"))
    keyboard.add(types.InlineKeyboardButton(get_text("btn_forecast_tomorrow", lang), callback_data="forecast_tomorrow"))
    keyboard.add(types.InlineKeyboardButton(get_text("btn_forecast_week", lang), callback_data="forecast_week"))
    keyboard.add(types.InlineKeyboardButton(get_text("btn_back", lang), callback_data="back_from_forecast_menu"))
    return keyboard


def generate_format_keyboard(lang):
    """ЕДИНИЦЫ ИЗМЕРЕНИЯ ДАННЫХ"""
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton(get_text("unit_temp_label", lang), callback_data="change_temp_unit"))
    keyboard.add(types.InlineKeyboardButton(get_text("unit_pressure_label", lang), callback_data="change_pressure_unit"))
    keyboard.add(types.InlineKeyboardButton(get_text("unit_wind_speed_label", lang), callback_data="change_wind_speed_unit"))
    keyboard.add(types.InlineKeyboardButton(get_text("btn_save", lang), callback_data="back_to_settings"))
    return keyboard



def generate_weather_data_keyboard(user):
    """Создаёт клавиатуру для выбора отображаемых данных (2 столбца)"""
    lang = get_user_lang(user)
    labels = get_translation_dict("weather_data_labels", lang)
    # Используем корректное поле из модели пользователя
    tracked_params = decode_tracked_params(getattr(user, 'tracked_weather_params', 0))
    
    keyboard = types.InlineKeyboardMarkup(row_width=2) 
    buttons = [
        types.InlineKeyboardButton(
            f"{'✅' if tracked_params.get(key, False) else '❌'} {label}",
            callback_data=f"toggle_weather_param_{key}"
        )
        for key, label in labels.items()
    ]
    keyboard.add(*buttons)
    keyboard.add(types.InlineKeyboardButton(get_text("btn_back", lang), callback_data="back_to_settings"))
    return keyboard
    
def generate_language_keyboard(user):
    """Создаёт клавиатуру для выбора языка (сетка 3x3)"""
    current_lang = get_user_lang(user)
    
    languages = {
        "ru": "🇷🇺 Русский",
        "en": "🇺🇸 English",
        "kk": "🇰🇿 Қазақша",
        "de": "🇩🇪 Deutsch",
        "fr": "🇫🇷 Français",
        "it": "🇮🇹 Italiano",
        "zh": "🇨🇳 中文",
        "ko": "🇰🇷 한국어",
        "ja": "🇯🇵 日本語"
    }

    keyboard = types.InlineKeyboardMarkup(row_width=3)
    
    buttons = []
    for code, label in languages.items():
        if code == current_lang:
            text = f"✅ {label}"
        else:
            text = label
            
        buttons.append(
            types.InlineKeyboardButton(
                text=text,
                callback_data=f"set_lang_{code}"
            )
        )
    keyboard.add(*buttons)
    
    back_text = get_text("btn_back", current_lang)
    keyboard.add(types.InlineKeyboardButton(back_text, callback_data="back_to_settings"))
    
    return keyboard

def generate_notification_settings_keyboard(user):
    """Создаёт клавиатуру для выбора настроек уведомлений"""
    lang = get_user_lang(user)
    labels = get_translation_dict("notification_labels", lang)
    
    notification_settings = decode_notification_settings(getattr(user, 'notifications_settings', 0))
    keyboard = types.InlineKeyboardMarkup()

    for key, label in labels.items():
        status_emoji = "✅ " if notification_settings.get(key, False) else "❌ "
        keyboard.add(types.InlineKeyboardButton(
            f"{status_emoji}{label}", 
            callback_data=f"toggle_notification_{key}"
        ))

    keyboard.add(types.InlineKeyboardButton(get_text("btn_back", lang), callback_data="back_to_settings"))
    return keyboard

def generate_main_menu_keyboard(user):
    """Создает главную клавиатуру (Reply) с учетом языка"""
    lang = get_user_lang(user)
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
    # Тексты кнопок берем из словаря
    btn_weather = types.KeyboardButton(get_text("basic_keyboard_button_1", lang))
    btn_forecast = types.KeyboardButton(get_text("basic_keyboard_button_2", lang))
    btn_settings = types.KeyboardButton(get_text("basic_keyboard_button_3", lang))
    
    keyboard.add(btn_weather, btn_forecast)
    keyboard.add(btn_settings)
    return keyboard

def generate_help_message(user):
    """Генерирует текст помощи"""
    lang = get_user_lang(user)
    header = get_text("help_header", lang)
    cmds = get_translation_dict("help_cmds", lang)
    footer = get_text("help_footer", lang)
    
    text = f"<b>{header}</b>\n\n"
    for cmd, desc in cmds.items():
        text += f"🔹 <b>{cmd}</b> — {desc}\n"
    
    return text + f"\n{footer}"

"""ВЫБОР ЕДИНИЦ ИЗМЕРЕНИЯ"""
def generate_unit_selection_keyboard(current_value, unit_type, user_id):
    """Создаёт клавиатуру выбора единиц измерения с учетом языка пользователя"""
    user = get_user(user_id)
    lang = get_user_lang(user)
    
    unit_names_dict = get_translation_dict("unit_selection_names", lang)
    unit_names = unit_names_dict.get(unit_type, {})
    
    keyboard = types.InlineKeyboardMarkup()
    for value, name in unit_names.items():
        icon = " ✅" if str(current_value) == str(value) else ""
        keyboard.add(types.InlineKeyboardButton(
            text=f"{name}{icon}", 
            callback_data=f"set_{unit_type}_unit_{value}"
        ))

    keyboard.add(types.InlineKeyboardButton(
        text=get_text("btn_save", lang), 
        callback_data="return_to_format_settings"
    ))
    return keyboard


def format_weather_data(data, user):
    """
    Форматирует погодные данные с учётом единиц измерения и настроек пользователя.
    """
    lang = get_user_lang(user)
    tracked_params = decode_tracked_params(getattr(user, 'tracked_weather_params', 0))
    unit_trans = get_translation_dict("unit_translations", lang)
    labels = get_translation_dict("weather_param_labels", lang)

    temperature = convert_temperature(data["temp"], user.temp_unit)
    pressure = convert_pressure(data["pressure"], user.pressure_unit)
    wind_speed = convert_wind_speed(data["wind_speed"], user.wind_speed_unit)

    header_text = get_text("weather_current_header", lang).format(city=data['city_name'])
    separator = get_text("separator", lang)
    
    weather_text = f"<b>{header_text}</b>\n{separator}\n"

    # Подготовка значений
    val_temp = f"{temperature:.1f}{unit_trans['temp'].get(user.temp_unit, '')}"
    val_feels = f"{convert_temperature(data['feels_like'], user.temp_unit):.1f}{unit_trans['temp'].get(user.temp_unit, '')}"
    val_press = f"{pressure:.1f} {unit_trans['pressure'].get(user.pressure_unit, '')}"
    val_wind = f"{wind_speed:.1f} {unit_trans['wind_speed'].get(user.wind_speed_unit, '')}"
    val_gust = f"{convert_wind_speed(data.get('wind_gust', 0), user.wind_speed_unit):.1f} {unit_trans['wind_speed'].get(user.wind_speed_unit, '')}"
    
    params_map = {
        "description": data["description"].capitalize(),
        "temperature": val_temp,
        "feels_like": val_feels,
        "humidity": f"{data['humidity']}%",
        "precipitation": f"{data.get('precipitation', 0)}%",
        "pressure": val_press,
        "wind_speed": val_wind,
        "wind_direction": f"{get_wind_direction(data['wind_direction'], lang)} ({data['wind_direction']}°)",
        "wind_gust": val_gust,
        "clouds": f"{data['clouds']}%",
        "visibility": f"{data['visibility']} м"
    }

    for param, value in params_map.items():
        if tracked_params.get(param, False):
            label = labels.get(param, param)
            weather_text += f"▸ {label}: {value}\n"

    return weather_text + f"\n{get_text('weather_footer', lang)}"


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
    valid_commands = ["/start", "/weather", "/changecity", "🌤 Узнать погоду", "📅 Прогноз погоды", "⚙️ Настройки"]
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
def get_today_forecast(city, user):
    """
    Получает прогноз на СЕГОДНЯ, агрегируя 3-часовые интервалы.
    """
    lang = get_user_lang(user)
    raw_data = fetch_today_forecast(city, lang)
    if not raw_data: 
        return None
        
    tz = ZoneInfo(user.timezone) if user.timezone else ZoneInfo("UTC")
    now = datetime.now(tz)
    today_str = now.strftime("%Y-%m-%d")
    
    # Фильтруем данные только за сегодня (по времени пользователя)
    today_items = []
    for item in raw_data:
        # OpenWeatherMap дает dt (timestamp в UTC). Конвертируем в время юзера
        dt_obj = datetime.fromtimestamp(item['dt'], tz)
        if dt_obj.strftime("%Y-%m-%d") == today_str:
            today_items.append(item)
            
    if not today_items:
        return None
        
    # Агрегация данных
    temps = [item['main']['temp'] for item in today_items]
    feels_like = [item['main']['feels_like'] for item in today_items]
    humidities = [item['main']['humidity'] for item in today_items]
    wind_speeds = [item['wind']['speed'] for item in today_items]
    # pop = probability of precipitation (0..1)
    pop = [item.get('pop', 0) for item in today_items]
    
    # Собираем все описания погоды
    descriptions = [d['weather'][0]['description'] for d in today_items]
    
    return {
        'date': now.strftime("%d.%m"), # Формат строго DD.MM для format_forecast
        'temp_min': min(temps),
        'temp_max': max(temps),
        'temp': sum(temps) / len(temps),
        'feels_like': sum(feels_like) / len(feels_like),
        'humidity': sum(humidities) / len(humidities),
        'wind_speed': max(wind_speeds),
        'precipitation': int(max(pop) * 100),
        'descriptions': descriptions,
        'pressure': today_items[0]['main']['pressure'], # Берем первое доступное
        'clouds': today_items[0]['clouds']['all'],
        'visibility': today_items[0].get('visibility', 10000),
        'wind_direction': today_items[0]['wind'].get('deg', 0),
        'wind_gust': max([item['wind'].get('gust', 0) for item in today_items])
    }

def get_tomorrow_forecast(city, user):
    """
    Получает прогноз на ЗАВТРА, агрегируя 3-часовые интервалы.
    """
    lang = get_user_lang(user)
    raw_data = fetch_tomorrow_forecast(city, lang) # Обычно это тот же эндпоинт, что и today
    if not raw_data: 
        return None

    tz = ZoneInfo(user.timezone) if user.timezone else ZoneInfo("UTC")
    now = datetime.now(tz)
    tomorrow = now + timedelta(days=1)
    tomorrow_str = tomorrow.strftime("%Y-%m-%d")

    # Фильтруем данные только за завтра
    tomorrow_items = []
    for item in raw_data:
        dt_obj = datetime.fromtimestamp(item['dt'], tz)
        if dt_obj.strftime("%Y-%m-%d") == tomorrow_str:
            tomorrow_items.append(item)

    if not tomorrow_items:
        return None

    # Агрегация данных
    temps = [item['main']['temp'] for item in tomorrow_items]
    feels_like = [item['main']['feels_like'] for item in tomorrow_items]
    humidities = [item['main']['humidity'] for item in tomorrow_items]
    wind_speeds = [item['wind']['speed'] for item in tomorrow_items]
    pop = [item.get('pop', 0) for item in tomorrow_items]
    descriptions = [d['weather'][0]['description'] for d in tomorrow_items]

    return {
        'date': tomorrow.strftime("%d.%m"), # ВАЖНО: Формат DD.MM
        'temp_min': min(temps),
        'temp_max': max(temps),
        'temp': sum(temps) / len(temps),
        'feels_like': sum(feels_like) / len(feels_like),
        'humidity': sum(humidities) / len(humidities),
        'wind_speed': max(wind_speeds),
        'precipitation': int(max(pop) * 100),
        'descriptions': descriptions,
        'pressure': tomorrow_items[0]['main']['pressure'],
        'clouds': tomorrow_items[0]['clouds']['all'],
        'visibility': tomorrow_items[0].get('visibility', 10000),
        'wind_direction': tomorrow_items[0]['wind'].get('deg', 0),
        'wind_gust': max([item['wind'].get('gust', 0) for item in tomorrow_items])
    }

def get_weekly_forecast(city, user):
    """Прогноз погоды на неделю с учётом tracked_weather_params"""
    lang = get_user_lang(user)
    raw_data = fetch_weekly_forecast(city, lang=lang)
    if not raw_data:
        return None  
        
    lang = get_user_lang(user)
    daily_data = {}
    
    # Используем таймзону пользователя для определения "сегодня", если она есть
    try:
        user_tz = ZoneInfo(user.timezone) if user.timezone else timezone.utc
    except:
        user_tz = timezone.utc
        
    today = datetime.now(user_tz).date()
    start_date = today + timedelta(days=1)
    
    months = get_translation_dict("months", lang)
    weekdays = get_translation_dict("weekdays", lang)
    tracked_params = decode_tracked_params(getattr(user, 'tracked_weather_params', 0))

    for entry in raw_data:
        timestamp = entry["dt"] 
        date_obj = datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(user_tz).date()

        if date_obj < start_date or (date_obj - start_date).days >= 5:
            continue
            
        if "main" not in entry or "temp" not in entry["main"]:
            logging.error(f"❌ Ошибка: в данных нет 'main' или 'temp'! {entry}")
            continue
            
        weather_data = extract_weather_data(entry)
        
        if date_obj not in daily_data:
            day_name = weekdays.get(date_obj.strftime("%A"), date_obj.strftime("%A"))
            daily_data[date_obj] = {
                "day_name": day_name,
                "descriptions": [], 
                **{
                    key: value for key, value in weather_data.items() 
                    if tracked_params.get(key, False) and value is not None
                }
            }
        
        current_temp = weather_data["temp"]
        daily_data[date_obj]["temp_min"] = min(daily_data[date_obj].get("temp_min", float("inf")), current_temp)
        daily_data[date_obj]["temp_max"] = max(daily_data[date_obj].get("temp_max", float("-inf")), current_temp)
        
        if "weather" in entry and entry["weather"]:
            daily_data[date_obj]["descriptions"].append(entry["weather"][0]["description"])

    return [
        {
            "date": f"{d.day} {months.get(d.month, '')}",
            "day_name": data["day_name"],
            **data
        }
        for d, data in sorted(daily_data.items())
    ]


def get_forecast_emoji(description, lang="ru"):
    """Возвращает эмодзи на основе описания погоды"""
    description = description.lower()
    # Получаем карту эмодзи из словаря
    emoji_map = get_translation_dict("weather_emoji_map", lang)
    
    for key, emoji in emoji_map.items():
        if key in description:
            return emoji
    return "🌦"


def get_most_severe_description(descriptions, lang="ru"):
    """Выбирает самое 'опасное' или значимое описание из списка"""
    if not descriptions:
        return ""
        
    severity_map = get_translation_dict("severity_map", lang)
    
    def score(desc):
        desc_lower = desc.lower()
        for key, val in severity_map.items():
            if key in desc_lower:
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
    lang = get_user_lang(user)
    try:
        tz = ZoneInfo(user.timezone or "UTC")
    except Exception:
        tz = ZoneInfo("UTC")

    now = datetime.now(tz)
    today = now.date()

    # Загружаем список плохой погоды для сравнения
    bad_descriptions = get_translation_dict("bad_weather_descriptions", lang)

    # Собираем плохую погоду с фильтром по времени
    bad_weather_periods = []
    for entry in forecast_data:
        timestamp = datetime.fromtimestamp(entry["dt"], tz)
        if timestamp.date() != today:
            continue
        if timestamp < now - timedelta(hours=1):
            continue

        # Приводим к формату для сравнения (Capitalize)
        description = entry["weather"][0]["description"].capitalize()
        
        # Проверяем, есть ли описание в списке "плохих"
        if description in bad_descriptions:
            bad_weather_periods.append((timestamp, description))

    if not bad_weather_periods:
        return get_text("weather_summary_clear", lang)

    # Группируем события
    groups = group_bad_weather_periods(bad_weather_periods)

    # Ищем актуальную или ближайшую группу
    for group in groups:
        start_time, _ = group[0]
        end_time, _ = group[-1]

        if now <= end_time:
            main_description = get_most_severe_description([desc for _, desc in group], lang)
            emoji = get_forecast_emoji(main_description, lang)

            if start_time != end_time:
                return get_text("weather_summary_range", lang).format(
                    emoji=emoji,
                    desc=main_description,
                    start=start_time.strftime("%H:%M"),
                    end=end_time.strftime("%H:%M")
                )
            else:
                return get_text("weather_summary_single", lang).format(
                    emoji=emoji,
                    desc=main_description,
                    time=start_time.strftime("%H:%M")
                )

    return get_text("weather_summary_clear", lang)

def format_forecast(weather_data, user, title_text, summary_text=None):
    """
    Универсальная функция форматирования.
    Дата теперь берется из словаря переводов (texts.py).
    """
    lang = get_user_lang(user)
    tracked_params = decode_tracked_params(getattr(user, 'tracked_weather_params', 0))
    
    unit_trans = get_translation_dict("unit_translations", lang)
    labels = get_translation_dict("weather_data_labels", lang) 

    
    # --- 2. ДАТА И ОПИСАНИЕ (Динамический перевод) ---
    tz = ZoneInfo(user.timezone) if user.timezone else ZoneInfo("UTC")

    # Восстанавливаем объект времени (datetime)
    if 'dt' in weather_data:
        dt_obj = datetime.fromtimestamp(weather_data['dt'], tz)
        show_time = True
    elif 'date' in weather_data and len(weather_data['date']) == 5:
        # Пытаемся распарсить формат "ДД.ММ" из прогноза
        try:
            d, m = map(int, weather_data['date'].split('.'))
            now = datetime.now(tz)
            dt_obj = now.replace(month=m, day=d)
            show_time = False 
        except:
            dt_obj = datetime.now(tz)
            show_time = True
    else:
        dt_obj = datetime.now(tz)
        show_time = True

    # Получаем словари перевода из texts.py
    # Ожидается, что в texts.py есть ключи "months" (1..12) и "weekdays" (Monday..Sunday)
    months_map = get_translation_dict("months", lang)
    weekdays_map = get_translation_dict("weekdays", lang)
    
    # Стандартные ключи для дней недели (Python weekday() -> 0..6)
    en_weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    wd_key = en_weekdays[dt_obj.weekday()]
    
    # Достаем перевод
    wd_str = weekdays_map.get(wd_key, wd_key)  # Например "Суббота"
    # Месяц (ключ - int)
    month_str = months_map.get(dt_obj.month, dt_obj.strftime("%B")) # Например "февраля"
    
    day_num = dt_obj.day
    time_str = dt_obj.strftime("%H:%M")

    # Сборка строки даты: "Суббота, 7 февраля 11:31"
    if show_time:
        date_line = f"<b>{wd_str}, {day_num} {month_str} {time_str}</b>"
    else:
        date_line = f"<b>{wd_str}, {day_num} {month_str}</b>"

    # Описание погоды
    desc = ""
    if "descriptions" in weather_data and isinstance(weather_data["descriptions"], list):
        if weather_data["descriptions"]:
            desc = Counter(weather_data["descriptions"]).most_common(1)[0][0].capitalize()
    elif "description" in weather_data:
        desc = str(weather_data['description']).capitalize()
    
    if desc:
        date_line += f"\n▸ {desc}"
    
    info_text = date_line
    
    # --- 3. МЕТЕОДАННЫЕ (Metrics) ---
    metrics_lines = []
    
    # Температура
    if tracked_params.get("temperature", False):
        unit = unit_trans.get("temp", {}).get(user.temp_unit, "°C")
        label = labels.get("temperature", "Температура")
        
        val_str = ""
        if "temp_min" in weather_data and "temp_max" in weather_data:
            t_min = round(convert_temperature(weather_data['temp_min'], user.temp_unit))
            t_max = round(convert_temperature(weather_data['temp_max'], user.temp_unit))
            if t_min == t_max:
                val_str = f"{t_min}{unit}"
            else:
                val_str = f"{t_min}{unit} ~ {t_max}{unit}"
        elif "temp" in weather_data:
            val = round(convert_temperature(weather_data['temp'], user.temp_unit))
            val_str = f"{val}{unit}"
            
        if val_str:
            metrics_lines.append(f"▸ {label}: {val_str}")

    # Ощущается как
    if tracked_params.get("feels_like", False) and "feels_like" in weather_data:
        val = round(convert_temperature(weather_data['feels_like'], user.temp_unit))
        unit = unit_trans.get("temp", {}).get(user.temp_unit, "°C")
        label = labels.get("feels_like", "Ощущается")
        metrics_lines.append(f"▸ {label}: {val}{unit}")

    # Влажность
    if tracked_params.get("humidity", False) and "humidity" in weather_data:
        label = labels.get("humidity", "Влажность")
        metrics_lines.append(f"▸ {label}: {int(weather_data['humidity'])}%")

    # Осадки
    if tracked_params.get("precipitation", False) and "precipitation" in weather_data:
        label = labels.get("precipitation", "Осадки")
        val = weather_data['precipitation']
        metrics_lines.append(f"▸ {label}: {val}%")

    # Давление
    if tracked_params.get("pressure", False) and "pressure" in weather_data:
        val = round(convert_pressure(weather_data['pressure'], user.pressure_unit))
        unit = unit_trans.get("pressure", {}).get(user.pressure_unit, "mmHg")
        label = labels.get("pressure", "Давление")
        metrics_lines.append(f"▸ {label}: {val} {unit}")

    # Ветер
    wind_unit = unit_trans.get("wind_speed", {}).get(user.wind_speed_unit, "m/s")
    if tracked_params.get("wind_speed", False) and "wind_speed" in weather_data:
        val = round(convert_wind_speed(weather_data['wind_speed'], user.wind_speed_unit), 1)
        label = labels.get("wind_speed", "Ветер")
        metrics_lines.append(f"▸ {label}: {val} {wind_unit}")

    # Порывы
    if tracked_params.get("wind_gust", False) and "wind_gust" in weather_data:
        val = round(convert_wind_speed(weather_data['wind_gust'], user.wind_speed_unit), 1)
        label = labels.get("wind_gust", "Порывы")
        metrics_lines.append(f"▸ {label}: {val} {wind_unit}")
        
    # Направление ветра
    if tracked_params.get("wind_direction", False) and "wind_direction" in weather_data:
         label = labels.get("wind_direction", "Направление")
         metrics_lines.append(f"▸ {label}: {weather_data['wind_direction']}°")

    # Облачность
    if tracked_params.get("clouds", False) and "clouds" in weather_data:
        label = labels.get("clouds", "Облачность")
        metrics_lines.append(f"▸ {label}: {int(weather_data['clouds'])}%")
        
    # Видимость
    if tracked_params.get("visibility", False) and "visibility" in weather_data:
        label = labels.get("visibility", "Видимость")
        metrics_lines.append(f"▸ {label}: {int(weather_data['visibility'])} м")

    metrics_text = "\n".join(metrics_lines)

    # --- СБОРКА ИТОГОВОГО СООБЩЕНИЯ ---
    final_message = f"{info_text}"
    
    if metrics_text:
        final_message += f"\n─────────────────────\n<blockquote expandable>{metrics_text}</blockquote>"

    if summary_text:
        final_message += f"\n\n{summary_text}"
        
    return final_message

def get_weekly_forecast_data(city, user):
    """
    Преобразует 3-часовой прогноз (список) в список сводок по дням.
    Аналог get_today_forecast, но для всех дней сразу.
    """
    lang = get_user_lang(user)
    # Используем ту же функцию получения сырых данных
    raw_data = fetch_today_forecast(city, lang) 
    
    if not raw_data:
        return []

    # Словарь для группировки: "2023-10-25" -> [данные, данные...]
    daily_groups = {}
    
    # Получаем таймзону пользователя, если есть, иначе UTC
    tz = ZoneInfo(user.timezone) if user.timezone else timezone.utc
    
    for item in raw_data:
        # Преобразуем timestamp в дату с учетом часового пояса
        dt = datetime.fromtimestamp(item['dt'], tz)
        date_key = dt.strftime('%Y-%m-%d')
        
        if date_key not in daily_groups:
            daily_groups[date_key] = {
                'temps': [],
                'feels_like': [],
                'descriptions': [],
                'wind_speeds': [],
                'humidities': [],
                'pop': [], # Вероятность осадков
                'dt_obj': dt
            }
        
        daily_groups[date_key]['temps'].append(item['main']['temp'])
        daily_groups[date_key]['feels_like'].append(item['main']['feels_like'])
        daily_groups[date_key]['humidities'].append(item['main']['humidity'])
        daily_groups[date_key]['wind_speeds'].append(item['wind']['speed'])
        daily_groups[date_key]['pop'].append(item.get('pop', 0))
        
        if 'weather' in item and item['weather']:
            daily_groups[date_key]['descriptions'].append(item['weather'][0]['description'])

    final_forecast = []
    sorted_days = sorted(daily_groups.keys())
    
    for day in sorted_days:
        data = daily_groups[day]
        dt = data['dt_obj']
        
        # Определение дня недели
        weekdays_ru = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
        weekdays_en = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        wd_idx = dt.weekday()
        day_name = weekdays_ru[wd_idx] if lang == 'ru' else weekdays_en[wd_idx]

        # Агрегация (как в твоем get_today_forecast)
        day_info = {
            'date': dt.strftime("%d.%m"),
            'day_name': day_name,
            'temp_min': min(data['temps']),
            'temp_max': max(data['temps']),
            'temp': sum(data['temps']) / len(data['temps']), # Средняя для совместимости
            'feels_like': sum(data['feels_like']) / len(data['feels_like']),
            'humidity': sum(data['humidities']) / len(data['humidities']),
            'wind_speed': max(data['wind_speeds']), # Берем худший ветер
            'precipitation': int(max(data['pop']) * 100),
            'descriptions': data['descriptions'], # Передаем список, format_forecast сам выберет частое
            
            # Заглушки, чтобы не ломался format_forecast
            'pressure': 1013, 
            'wind_gust': 0,
            'wind_direction': 0,
            'clouds': 0,
            'visibility': 10000 
        }
        final_forecast.append(day_info)
        
    return final_forecast