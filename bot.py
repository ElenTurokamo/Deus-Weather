#ИМПОРТЫ
from telebot import types
from dotenv import load_dotenv
from logging.handlers import RotatingFileHandler
from logic import get_user, save_user, update_user 
from logic import *
from logic import (
    decode_tracked_params, convert_temperature, convert_pressure, 
    convert_wind_speed, get_wind_direction, get_text, get_translation_dict
)
from weather import get_weather, resolve_city_from_coords
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from collections import Counter
from texts import TEXTS

import logging
import time
import os
import requests
import telebot
import re
import json


#ШИФРОВАНИЕ
load_dotenv()


#ПЕРЕМЕННЫЕ
bot_start_time = time.time()
rounded_time = datetime.fromtimestamp(round(bot_start_time), timezone.utc)


#ЛОГИРОВАНИЕ
LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "bot.log")

if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"

bot_logger = logging.getLogger("bot_logger")
bot_logger.setLevel(logging.DEBUG)
bot_logger.propagate = False 

if bot_logger.hasHandlers():
    bot_logger.handlers.clear()

file_handler = RotatingFileHandler(LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
file_handler.setLevel(logging.DEBUG)

console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter(LOG_FORMAT))
console_handler.setLevel(logging.DEBUG)

error_handler = logging.FileHandler(os.path.join(LOG_DIR, "errors_bot.log"), encoding="utf-8")
error_handler.setLevel(logging.ERROR)
error_handler.setFormatter(logging.Formatter(LOG_FORMAT))

bot_logger.addHandler(file_handler)
bot_logger.addHandler(console_handler)
bot_logger.addHandler(error_handler)

bot_logger.debug("🔍 DEBUG-логгер для бота инициализирован.")
bot_logger.info("✅ Логирование для бота настроено!")


#ТОКЕН БОТА
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
bot = telebot.TeleBot(BOT_TOKEN)


#ФУНКЦИИ
def track_bot_message(message):
    """Запоминает последнее отправленное сообщение от бота."""
    update_data_field("last_bot_message", message.chat.id, message.message_id)


@bot.message_handler(func=lambda message: not message.text.startswith("/"))
def handle_all_messages(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    user = get_user(user_id)
    lang = get_user_lang(user) if user else "ru"
    current_menu_actions = get_menu_actions(lang)

    if message.date < bot_start_time:
        return
    if message.text in current_menu_actions:
        current_menu_actions[message.text](message)
        return

    bot_logger.info(f"▸ Пользователь {user_id} отправил неизвестное сообщение: {message.text}")
    bot.send_message(chat_id, get_text("unknown_command", lang))
    send_main_menu(chat_id)

"""ОТПРАВКА МЕНЮ"""
def menu_option(chat_id, reply_markup=None):
    user = get_user(chat_id)
    lang = get_user_lang(user)

    menu_message = bot.send_message(
        chat_id,
        get_text("decorative_message_menu", lang),
        reply_markup=reply_markup
    )
    update_data_field("last_menu_message", chat_id, menu_message.message_id)
    return menu_message.message_id



def settings_option(chat_id, reply_markup=None):
    user = get_user(chat_id)
    lang = get_user_lang(user)

    settings_opt = bot.send_message(
        chat_id,
        get_text("decorative_message_settings", lang),
        reply_markup=reply_markup
    )
    update_data_field("last_menu_message", chat_id, settings_opt.message_id)
    return settings_opt.message_id



def send_main_menu(chat_id):
    """Отправка главного меню пользователю с учетом его языка."""
    delete_last_menu_message(chat_id)
    
    # Получаем пользователя и его язык
    user = get_user(chat_id)
    lang = get_user_lang(user)

    main_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    
    # Передаем переменную lang вместо строки "lang"
    main_keyboard.row(
        get_text("basic_keyboard_button_1", lang),
        get_text("basic_keyboard_button_2", lang)
    )
    main_keyboard.row(get_text("basic_keyboard_button_3", lang))
    
    menu_option(chat_id, reply_markup=main_keyboard)



def send_settings_menu(chat_id):
    """Отправка клавиатуры с меню настроек пользователю."""
    delete_last_menu_message(chat_id)
    user = get_user(chat_id)
    lang = get_user_lang(user)

    settings_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    
    settings_keyboard.row(
        get_text("settings_keyboard_button_1", lang),
        get_text("settings_keyboard_button_2", lang)
    )
    settings_keyboard.row(
        get_text("settings_keyboard_button_3", lang),
        get_text("settings_keyboard_button_4", lang)
    )
    settings_keyboard.row(
        get_text("settings_keyboard_button_language", lang),
        get_text("settings_keyboard_button_5", lang)
    )
    
    settings_option(chat_id, reply_markup=settings_keyboard)



def delete_last_menu_message(chat_id):
    """Удаляет последнее декоративное сообщение для чата."""
    message_id = get_data_field("last_menu_message", chat_id)
    if message_id:
        try:
            bot.delete_message(chat_id, message_id)
            update_data_field("last_menu_message", chat_id, None)
        except telebot.apihelper.ApiTelegramException as e:
            if "message to delete not found" in str(e):
                bot_logger.debug(f"Сообщение {message_id} уже удалено.")
            else:
                bot_logger.warning(f"Ошибка при удалении меню-сообщения {message_id}: {e}")
        except Exception as e:
            bot_logger.warning(f"Общая ошибка при удалении: {e}")


@safe_execute
@bot.callback_query_handler(func=lambda call: call.data in ["forecast_today", "forecast_tomorrow", "forecast_week"])
def forecast_handler(call):
    chat_id = call.message.chat.id
    user = get_user(call.from_user.id)
    menu_message_id = call.message.message_id

    if not user or not user.preferred_city:
        bot.send_message(chat_id, "⚠ Сначала укажите ваш город в настройках!")
        return

    lang = get_user_lang(user)  

    if call.data == "forecast_today":
        forecast_data = [get_today_forecast(user.preferred_city, user)]
    elif call.data == "forecast_tomorrow":
        forecast_data = [get_tomorrow_forecast(user.preferred_city, user)]
    else:
        forecast_data = get_weekly_forecast(user.preferred_city, user)

    if not forecast_data or any(d is None for d in forecast_data):
        bot.send_message(chat_id, "⚠ Не удалось получить прогноз погоды.")
        return

    try:
        forecast_text = (
            "\n\n".join([format_forecast(day, user) for day in forecast_data])
            + "\n\n"
            + get_text("forecast_footer", lang)
        )
    except KeyError as e:
        bot_logger.error(f"Ключ отсутствует в данных прогноза: {e}")
        bot.send_message(chat_id, "⚠ Произошла ошибка при обработке прогноза.")
        send_main_menu(chat_id)
        return

    try:
        bot.edit_message_text(
            forecast_text,
            chat_id,
            menu_message_id,
            parse_mode="HTML",
            reply_markup=None
        )
        update_data_field("last_bot_message", chat_id, None)
    except Exception as e:
        bot_logger.warning(f"⚠ Не удалось отредактировать сообщение: {str(e)}")
        msg = bot.send_message(chat_id, forecast_text, parse_mode="HTML")
        update_data_field("last_bot_message", chat_id, msg.message_id)

    bot_logger.info(f"✅ Прогноз погоды отправлен в чат {chat_id}.")
    send_main_menu(chat_id)


def format_forecast(day, user):
    """
    Полностью рабочая версия форматирования прогноза.
    Исправлено: получение единиц измерения, вложенность словарей и локализация меток.
    """
    lang = get_user_lang(user)
    # Декодируем параметры, которые пользователь хочет видеть
    tracked_params = decode_tracked_params(getattr(user, 'tracked_weather_params', 0))
    
    # Получаем словари переводов
    unit_trans = get_translation_dict("unit_translations", lang)
    labels = get_translation_dict("weather_param_labels", lang)
    
    # Шапка прогноза
    parts = [
        get_text("forecast_header", lang).format(day_name=day['day_name'], date=day['date']),
        get_text("separator", lang)
    ]

    # 1. Описание (Weather/Погода)
    if tracked_params.get("description", False):
        desc = ""
        if isinstance(day.get("descriptions"), list) and day["descriptions"]:
            # Берем самое частое описание за день
            desc = Counter(day["descriptions"]).most_common(1)[0][0].capitalize()
        elif "description" in day:
            desc = day['description'].capitalize()
        
        if desc:
            label = labels.get("description", "Weather")
            parts.append(f"▸ {label}: {desc}")

    # 2. Температура (Temperature)
    if tracked_params.get("temperature", False) and "temp_min" in day:
        t_min = round(convert_temperature(day['temp_min'], user.temp_unit))
        t_max = round(convert_temperature(day['temp_max'], user.temp_unit))
        unit = unit_trans.get("temp", {}).get(user.temp_unit, "°C")
        label = labels.get("temperature", "Temp")
        
        if t_min == t_max:
            parts.append(f"▸ {label}: {t_min}{unit}")
        else:
            parts.append(f"▸ {label}: {t_min}{unit} to {t_max}{unit}")

    # 3. Ощущается как (Feels like)
    if tracked_params.get("feels_like", False) and "feels_like" in day:
        val = round(convert_temperature(day['feels_like'], user.temp_unit))
        unit = unit_trans.get("temp", {}).get(user.temp_unit, "°C")
        label = labels.get("feels_like", "Feels like")
        parts.append(f"▸ {label}: {val}{unit}")

    # 4. Влажность (Humidity)
    if tracked_params.get("humidity", False) and "humidity" in day:
        label = labels.get("humidity", "Humidity")
        parts.append(f"▸ {label}: {day['humidity']}%")
    
    # 5. Осадки (Precipitation)
    if tracked_params.get("precipitation", False) and "precipitation" in day:
        label = labels.get("precipitation", "Precipitation")
        parts.append(f"▸ {label}: {day['precipitation']}%")

    # 6. Давление (Pressure) - Исправлено получение юнитов
    if tracked_params.get("pressure", False) and "pressure" in day:
        val = round(convert_pressure(day['pressure'], user.pressure_unit))
        unit = unit_trans.get("pressure", {}).get(user.pressure_unit, "mmHg")
        label = labels.get("pressure", "Pressure")
        parts.append(f"▸ {label}: {val} {unit}")

    # 7. Ветер (Wind Speed) - Исправлено получение юнитов
    wind_unit = unit_trans.get("wind_speed", {}).get(user.wind_speed_unit, "m/s")
    if tracked_params.get("wind_speed", False) and "wind_speed" in day:
        val = round(convert_wind_speed(day['wind_speed'], user.wind_speed_unit), 1)
        label = labels.get("wind_speed", "Wind")
        parts.append(f"▸ {label}: {val} {wind_unit}")

    # 8. Направление ветра (Wind Direction)
    if tracked_params.get("wind_direction", False) and "wind_direction" in day:
        direction = get_wind_direction(day['wind_direction'], lang)
        label = labels.get("wind_direction", "Wind Dir")
        parts.append(f"▸ {label}: {direction} ({day['wind_direction']}°)")

    # 9. Порывы ветра (Wind Gust)
    if tracked_params.get("wind_gust", False) and "wind_gust" in day:
        val = round(convert_wind_speed(day['wind_gust'], user.wind_speed_unit), 1)
        label = labels.get("wind_gust", "Wind Gust")
        parts.append(f"▸ {label}: {val} {wind_unit}")

    # 10. Облачность (Clouds)
    if tracked_params.get("clouds", False) and "clouds" in day:
        label = labels.get("clouds", "Clouds")
        parts.append(f"▸ {label}: {day['clouds']}%")

    # 11. Видимость (Visibility)
    if tracked_params.get("visibility", False) and "visibility" in day:
        label = labels.get("visibility", "Visibility")
        parts.append(f"▸ {label}: {int(day['visibility'])} m")

    return "\n".join(parts)

@safe_execute
@bot.callback_query_handler(func=lambda call: call.data == "back_to_settings")
def back_to_settings_callback(call):
    """Обработчик возврата в меню настроек"""
    chat_id = call.message.chat.id
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except Exception as e:
        bot_logger.warning(f"Ошибка при удалении сообщения с кнопкой 'Назад': {e}")
    last_command_message = get_data_field("last_user_command", chat_id)
    if last_command_message:
        try:
            bot.delete_message(chat_id, last_command_message)
            update_data_field("last_user_command", chat_id, None)
            bot_logger.debug(f"Удалено сообщение команды: {last_command_message}")
        except Exception as e:
            bot_logger.warning(f"Ошибка при удалении сообщения команды: {e}")
    delete_last_menu_message(chat_id)
    send_settings_menu(chat_id)


@safe_execute
@bot.message_handler(func=lambda message: message.text == "⚙️ Настройки")
def settings_menu_handler(message):
    """Обработчик вызова меню настроек через сообщение."""
    chat_id = message.chat.id
    update_data_field("last_settings_command", chat_id, message.message_id)
    bot_logger.debug(f"Сохранён ID команды 'Настройки': {message.message_id} для чата {chat_id}")
    delete_last_menu_message(chat_id)
    send_settings_menu(chat_id)


@safe_execute
@bot.callback_query_handler(func=lambda call: call.data == "back_to_main")
def back_to_main_callback(call):
    """Обработчик возврата в главное меню"""
    chat_id = call.message.chat.id
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except Exception as e:
        bot_logger.warning(f"Ошибка при удалении сообщения с кнопкой 'Назад': {e}")
    last_command_message = get_data_field("last_user_command", chat_id)
    if last_command_message:
        try:
            bot.delete_message(chat_id, last_command_message)
            update_data_field("last_user_command", chat_id, None)
        except Exception as e:
            bot_logger.warning(f"Ошибка при удалении сообщения команды: {e}")
    delete_last_menu_message(chat_id)
    send_main_menu(chat_id)


@bot.message_handler(commands=['start'])
def start(message):
    log_action("Получена команда /start", message)
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Создаем или получаем пользователя (по дефолту язык ru)
    save_user(user_id, message.from_user.first_name)
    user = get_user(user_id)
    lang = get_user_lang(user)
    
    delete_last_menu_message(chat_id)

    # СЦЕНАРИЙ 1: Старый пользователь (город уже есть)
    if user and user.preferred_city:
        text = get_text("greet_returning", lang).format(
            name=message.from_user.first_name,
            city=user.preferred_city
        )
        msg = bot.reply_to(message, text)  
        update_data_field("last_bot_message", chat_id, msg.message_id)
        send_main_menu(chat_id)
        
    # СЦЕНАРИЙ 2: Новый пользователь (города нет)
    else:
        # Отправляем приветствие и СРАЗУ клавиатуру выбора языка
        keyboard = generate_language_keyboard(user)
        text = f"Привет/Hello, {message.from_user.first_name}!\n\n🇷🇺 Выберите язык / 🇺🇸 Choose language:"
        
        msg = bot.send_message(chat_id, text, reply_markup=keyboard)
        update_data_field("last_bot_message", chat_id, msg.message_id)


def ask_for_city_initial(chat_id, user_id, lang, user_name):
    """Вспомогательная функция, вызывается ПОСЛЕ выбора языка"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    btn_text = get_text("button_geo", lang)
    keyboard.add(types.KeyboardButton(text=btn_text, request_location=True))
    
    text = get_text("greet_new", lang).format(name=user_name)
    
    msg = bot.send_message(chat_id, text, reply_markup=keyboard)
    update_data_field("last_bot_message", chat_id, msg.message_id)
    
    # Теперь регистрируем ожидание ввода города
    bot.register_next_step_handler(msg, process_new_city_registration)


@safe_execute
@bot.message_handler(commands=['weather'])
def weather(message):
    """Отправка текущей погоды в городе пользователя"""
    user_id = message.from_user.id
    user = get_user(user_id)
    lang = get_user_lang(user)
    
    bot_logger.info(f"▸ Получена команда /weather от {user_id}.")
    
    if not user or not user.preferred_city:
        bot_logger.info(f"▸ У пользователя {user_id} не выбран город. Запрашиваем ввод.")
        text = get_text("error_no_city", lang)
        reply = bot.reply_to(message, text)
        bot.register_next_step_handler(reply, process_new_city)
        return

    delete_last_menu_message(message.chat.id)
    
    weather_data = get_weather(user.preferred_city, lang=lang)
    
    if not weather_data:
        bot_logger.error(f"▸ Ошибка получения погоды для {user.preferred_city}")
        text = get_text("error_weather_fetch", lang)
        bot.reply_to(message, text)
        send_main_menu(message.chat.id)
        return

    bot_logger.info(f"▸ Погода в {user.preferred_city} успешно получена.")
    
    weather_info = format_weather_data(weather_data, user)
    
    bot.reply_to(message, weather_info, parse_mode="HTML")
    send_main_menu(message.chat.id)


@safe_execute
@bot.message_handler(regexp=r"^(\/changecity|🏙 Изменить город|🏙 Change city|🏙 Қаланы өзгерту)$")
def changecity(message):
    user_id = message.from_user.id
    user = get_user(user_id)
    chat_id = message.chat.id
    lang = get_user_lang(user)
    
    bot_logger.info(f"▸ Получена команда /changecity от {user_id}.")
    delete_last_menu_message(chat_id)
    
    if user and user.preferred_city:
        reply_text = get_text("changecity_current", lang).format(city=user.preferred_city)
    else:
        reply_text = get_text("changecity_none", lang)
        
    keyboard = types.InlineKeyboardMarkup()
    cancel_text = get_text("btn_cancel", lang)
    cancel_button = types.InlineKeyboardButton(cancel_text, callback_data="cancel_changecity")
    keyboard.add(cancel_button)
    
    reply = bot.reply_to(message, reply_text, reply_markup=keyboard)
    
    update_data_field("last_menu_message", chat_id, reply.message_id)
    update_data_field("last_user_command", chat_id, message.message_id)
    
    bot.register_next_step_handler(reply, process_new_city, show_menu=True)


@safe_execute
@bot.callback_query_handler(func=lambda call: call.data == "cancel_changecity")
def cancel_changecity_callback(call):
    """Отмена изменения города и возврат в настройки"""
    chat_id = call.message.chat.id
    bot_logger.info(f"▸ Отмена изменения города для чата {chat_id}.")
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except Exception as e:
        bot_logger.warning(f"▸ Ошибка при удалении сообщения с кнопкой 'Отмена': {e}")
    last_command_message = get_data_field("last_user_command", chat_id)
    if last_command_message:
        try:
            bot.delete_message(chat_id, last_command_message)
            update_data_field("last_user_command", chat_id, None)
        except Exception as e:
            bot_logger.warning(f"▸ Ошибка при удалении сообщения команды /changecity: {e}")
    bot.clear_step_handler_by_chat_id(chat_id)
    send_settings_menu(chat_id)


@safe_execute
@bot.message_handler(func=lambda message: message.text in [
    get_text("notifications_menu_btn", "ru"),
    get_text("notifications_menu_btn", "en"),
    get_text("notifications_menu_btn", "kk")
])
def notification_settings(message):
    user = get_user(message.from_user.id)
    chat_id = message.chat.id
    lang = get_user_lang(user)
    
    bot_logger.info(f"▸ Открыто меню уведомлений для чата {chat_id}.")
    delete_last_menu_message(chat_id)
    update_data_field("last_user_command", chat_id, message.message_id)
    
    if not user:
        bot.send_message(chat_id, get_text("error_user_not_found", lang))
        return
        
    try:
        # ИСПРАВЛЕНИЕ: убрали лишний аргумент lang, функция принимает только user
        keyboard = generate_notification_settings_keyboard(user)
        
        text = get_text("notifications_menu_text", lang)
        bot.send_message(
            chat_id, 
            text, 
            reply_markup=keyboard, 
            reply_to_message_id=message.message_id
        )
    except Exception as e:
        bot_logger.error(f"▸ Ошибка в notification_settings: {e}")


@safe_execute
@bot.callback_query_handler(func=lambda call: call.data.startswith("toggle_notification_"))
def toggle_notification(call):
    """Изменяет состояние уведомлений пользователя"""
    chat_id = call.message.chat.id
    user = get_user(call.from_user.id)
    setting_key = call.data.replace("toggle_notification_", "")
    bot_logger.info(f"▸ Изменение уведомлений ({setting_key}) для пользователя {call.from_user.id}.")
    if not user:
        bot_logger.error(f"▸ Пользователь с ID {call.from_user.id} не найден.")
        return 
    try:
        notification_settings = decode_notification_settings(user.notifications_settings)
    except Exception as e:
        bot_logger.error(f"▸ Ошибка декодирования уведомлений пользователя {user.user_id}: {e}")
        notification_settings = {
            "weather_threshold_notifications": True,
            "forecast_notifications": True,
            "bot_notifications": True
        }
    if setting_key in notification_settings:
        notification_settings[setting_key] = not notification_settings[setting_key]
    else:
        bot_logger.warning(f"▸ Неизвестный параметр {setting_key} для пользователя {user.user_id}")
        return
    try:
        update_user(user.user_id, notifications_settings=json.dumps(notification_settings))
        new_keyboard = generate_notification_settings_keyboard(get_user(call.from_user.id))  
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=new_keyboard)
    except Exception as e:
        bot_logger.warning(f"▸ Ошибка при обновлении notifications_settings для пользователя {user.user_id}: {e}")
    bot.answer_callback_query(call.id)


@safe_execute
@bot.message_handler(commands=['stop'])
def stop_notifications(message):
    user = get_user(message.from_user.id)
    chat_id = message.chat.id
    lang = get_user_lang(user)
    
    if not user:
        bot.send_message(chat_id, get_text("error_user_not_found", lang))
        bot_logger.warning(f"▸ Команда /stop: пользователь {message.from_user.id} не найден.")
        return

    delete_last_menu_message(chat_id)
    
    try:
        new_settings = {
            "weather_threshold_notifications": False,
            "forecast_notifications": False,
            "bot_notifications": False
        }
        update_user(user.user_id, notifications_settings=json.dumps(new_settings))
        
        bot.send_message(chat_id, get_text("stop_success", lang))
        bot_logger.info(f"▸ Пользователь {user.user_id} отключил уведомления через /stop.")
    except Exception as e:
        bot_logger.error(f"▸ Ошибка /stop для {user.user_id}: {e}")
        bot.send_message(chat_id, get_text("stop_error", lang))
    
    send_main_menu(chat_id)


@safe_execute
@bot.message_handler(regexp=r"^(\📅 Прогноз погоды|/weatherforecast)$")
def forecast_menu_handler(message):
    chat_id = message.chat.id
    user = get_user(message.from_user.id)

    if not user:
        bot.send_message(chat_id, "⚠ Пользователь не найден.")
        return

    lang = get_user_lang(user)

    bot_logger.info(f"▸ Пользователь {message.from_user.id} открыл меню прогноза погоды.")
    delete_last_menu_message(chat_id)

    msg = bot.reply_to(
        message,
        get_text("forecast_menu_title", lang),  
        reply_markup=generate_forecast_keyboard(chat_id)
    )

    update_data_field("last_user_command", chat_id, {
        "message_id": message.message_id,
        "command": message.text
    })
    update_data_field("last_bot_message", chat_id, msg.message_id)

    return msg.message_id



@bot.callback_query_handler(func=lambda call: call.data == "back_from_forecast_menu")
def back_from_forecast_menu(call):
    """Закрывает меню прогноза и возвращает в главное меню"""
    chat_id = call.message.chat.id
    bot_logger.info(f"▸ Пользователь {call.from_user.id} вернулся из меню прогноза.")
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except Exception as e:
        bot_logger.warning(f"Ошибка при удалении сообщения с меню прогнозов: {e}")
    last_command_data = get_data_field("last_user_command", chat_id)
    bot_logger.debug(f"Последняя команда перед удалением: {last_command_data}")
    if last_command_data:
        last_command = last_command_data.get("command")
        if last_command in ["📅 Прогноз погоды", "/weatherforecast"]:
            try:
                bot.delete_message(chat_id, last_command_data["message_id"])
                update_data_field("last_user_command", chat_id, None)
            except Exception as e:
                bot_logger.warning(f"Ошибка при удалении сообщения команды: {e}")
    send_main_menu(chat_id)


@safe_execute
def refresh_daily_forecast(user_id):
    """Удаляет старый прогноз, отправляет новый и закрепляет его в чате."""
    last_forecast_id = get_data_field("last_daily_forecast", user_id)
    chat_id = user_id # chat_id равен user_id в личных сообщениях
    if last_forecast_id:
        try:
            bot.delete_message(chat_id=user_id, message_id=last_forecast_id)
            bot_logger.info(f"▸ Старое сообщение удалено для пользователя {user_id}.")
        except Exception as del_error:
            bot_logger.warning(f"▸ Не удалось удалить старое сообщение для {user_id}: {del_error}")
            
    user = get_user(user_id)
    if not user or not user.preferred_city:
        bot_logger.error(f"▸ Ошибка: не найден пользователь {user_id} или его город.")
        return

    lang = get_user_lang(user)
    
    raw_forecast = get_today_forecast(user.preferred_city, user)
    if not raw_forecast:
        bot_logger.warning(f"▸ `get_today_forecast` не вернула данные для {user.preferred_city}!")
        return

    title = get_text("daily_forecast_title", lang)
    
    summary = get_weather_summary_description(fetch_today_forecast(user.preferred_city, lang=lang), user)
    
    forecast_message = (
        f"{title}\n"
        + format_forecast(raw_forecast, user)
        + f"\n\n{summary}"
    )

    try:
        sent_message = bot.send_message(user_id, forecast_message, parse_mode="HTML")
        update_data_field("last_daily_forecast", user_id, sent_message.message_id)
        bot_logger.info(f"▸ Новый прогноз отправлен пользователю {user_id}.")
        
        try:
            bot.pin_chat_message(
                chat_id=user_id,
                message_id=sent_message.message_id,
                disable_notification=True,
            )
            bot_logger.info(f"▸ Новый прогноз закреплён для пользователя {user_id}.")
        except Exception as pin_error:
            bot_logger.warning(f"▸ Не удалось закрепить сообщение для {user_id}: {pin_error}")
    except Exception as e:
        bot_logger.error(f"▸ Ошибка при отправке прогноза {user_id}: {e}")


@safe_execute
def update_existing_forecast(user_id):
    last_forecast_id = get_data_field("last_daily_forecast", user_id)
    user = get_user(user_id)
    if not user or not user.preferred_city:
        bot_logger.error(f"▸ Ошибка: не найден пользователь {user_id} или его город.")
        return

    lang = get_user_lang(user)

    raw_forecast = get_today_forecast(user.preferred_city, user)
    if not raw_forecast:
        bot_logger.warning(f"▸ `get_today_forecast` не вернула данные для {user.preferred_city}!")
        return
    
    # Защита от отсутствия таймзоны
    try:
        user_tz = ZoneInfo(user.timezone or "UTC")
    except Exception:
        user_tz = ZoneInfo("UTC")
        
    updated_time = datetime.now(user_tz).strftime("%H:%M")

    title = get_text("daily_forecast_title", lang)
    updated_label = get_text("daily_forecast_updated", lang).format(time=updated_time)

    forecast_message = (
        f"{title}\n"
        # f"{updated_label}\n"
        + format_forecast(raw_forecast, user)
        + "\n\n"
        + get_weather_summary_description(
            fetch_today_forecast(user.preferred_city, lang=lang),
            user
        )
    )

    if last_forecast_id:
        try:
            bot.edit_message_text(
                chat_id=user_id,
                message_id=last_forecast_id,
                text=forecast_message,
                parse_mode="HTML"
            )
            bot_logger.info(f"▸ Прогноз обновлён для пользователя {user_id}.")
            return
        except Exception as edit_error:
            bot_logger.warning(f"▸ Не удалось обновить сообщение, отправляем новый прогноз: {edit_error}")

        try:
            bot.delete_message(chat_id=user_id, message_id=last_forecast_id)
            bot_logger.info(f"▸ Старый прогноз удалён для пользователя {user_id}.")
        except Exception as del_error:
            bot_logger.warning(f"▸ Не удалось удалить старый прогноз: {del_error}")

    try:
        sent_message = bot.send_message(
            user_id,
            forecast_message,
            parse_mode="HTML"
        )
        update_data_field("last_daily_forecast", user_id, sent_message.message_id)
        bot_logger.info(f"▸ Новый прогноз отправлен пользователю {user_id}.")

        try:
            bot.pin_chat_message(
                chat_id=user_id,
                message_id=sent_message.message_id,
                disable_notification=True,
            )
            bot_logger.info(f"▸ Новый прогноз закреплён для пользователя {user_id}.")
        except Exception as pin_error:
            bot_logger.warning(f"▸ Не удалось закрепить сообщение: {pin_error}")

    except Exception as e:
        bot_logger.error(f"▸ Ошибка при отправке прогноза: {e}")



@safe_execute
def format_settings(param, reply_to=None):
    if isinstance(param, int):
        chat_id = param
    else:
        chat_id = param.chat.id
        reply_to = param.message_id if reply_to is None else reply_to

    try:
        update_data_field("last_user_command", chat_id, reply_to)
    except Exception as e:
        bot_logger.error(f"▸ Ошибка при сохранении last_user_command для чата {chat_id}: {e}")

    last_menu_id = get_data_field("last_menu_message", chat_id)
    if last_menu_id:
        try:
            bot.delete_message(chat_id, last_menu_id)
            update_data_field("last_menu_message", chat_id, None)
        except Exception as e:
            bot_logger.warning(f"▸ Ошибка при удалении старого сообщения: {e}")

    user = get_user(chat_id)
    if not user:
        bot_logger.error(f"▸ Ошибка: пользователь {chat_id} не найден в format_settings()")
        bot.send_message(chat_id, get_text("error_user_not_found_start"))
        return
    
    user = get_user(chat_id)
    lang = get_user_lang(user)
    # Получаем переводы единиц измерения для текущего языка
    unit_trans = get_translation_dict("unit_translations", lang)

    header = get_text("settings_units_header", lang)
    temp = get_text("settings_units_temp", lang).format(
        val=unit_trans["temp"].get(user.temp_unit, user.temp_unit)
    )
    pressure = get_text("settings_units_pressure", lang).format(
        val=unit_trans["pressure"].get(user.pressure_unit, user.pressure_unit)
    )
    wind = get_text("settings_units_wind", lang).format(
        val=unit_trans["wind_speed"].get(user.wind_speed_unit, user.wind_speed_unit)
    )
    choose = get_text("settings_units_choose", lang)

    text = (
        f"<b>{header}</b>\n"
        f"<blockquote>"
        f"{temp}\n"
        f"{pressure}\n"
        f"{wind}"
        f"</blockquote>\n"
        f"{choose}"
    )

    menu_message_id = get_data_field("last_format_settings_menu", chat_id)
    # Генерируем клавиатуру с учетом языка
    keyboard = generate_format_keyboard(lang)
    
    try:
        if menu_message_id:
            bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=menu_message_id,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            bot_logger.info(f"▸ Меню единиц измерения обновлено для чата {chat_id}.")
        else:
            raise KeyError
    except Exception as e:
        bot_logger.warning(f"▸ Ошибка при редактировании сообщения: {e}. Отправляем новое сообщение.")
        try:
            msg = bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
                reply_to_message_id=reply_to,
                parse_mode="HTML"
            )
            update_data_field("last_format_settings_menu", chat_id, msg.message_id)
            bot_logger.info(f"▸ Новое меню единиц измерения отправлено в чат {chat_id}.")
        except Exception as send_error:
            bot_logger.error(f"▸ Ошибка при отправке нового сообщения: {send_error}")


@safe_execute
@bot.callback_query_handler(func=lambda call: call.data == "return_to_format_settings")
def return_to_format_settings(call):
    chat_id = call.message.chat.id
    user = get_user(chat_id)
    if not user:
        bot_logger.error(f"▸ Ошибка: пользователь {chat_id} не найден.")
        bot.send_message(chat_id, get_text("error_user_not_found_start"))
        return

    lang = get_user_lang(user)
    unit_trans = get_translation_dict("unit_translations", lang)

    header = get_text("settings_units_header", lang)
    temp = get_text("settings_units_temp", lang).format(
        val=unit_trans["temp"].get(user.temp_unit, user.temp_unit)
    )
    pressure = get_text("settings_units_pressure", lang).format(
        val=unit_trans["pressure"].get(user.pressure_unit, user.pressure_unit)
    )
    wind = get_text("settings_units_wind", lang).format(
        val=unit_trans["wind_speed"].get(user.wind_speed_unit, user.wind_speed_unit)
    )
    choose = get_text("settings_units_choose", lang)

    text = (
        f"<b>{header}</b>\n"
        f"<blockquote>"
        f"{temp}\n"
        f"{pressure}\n"
        f"{wind}"
        f"</blockquote>\n"
        f"{choose}"
    )

    try:
        bot.edit_message_text(
            text=text,
            chat_id=chat_id,
            message_id=call.message.message_id,
            reply_markup=generate_format_keyboard(lang),
            parse_mode="HTML"
        )
        bot_logger.info(f"▸ Меню единиц измерения обновлено для чата {chat_id}.")
    except Exception as e:
        bot_logger.warning(f"▸ Ошибка при обновлении меню единиц измерения: {e}")



@safe_execute
@bot.callback_query_handler(func=lambda call: call.data == "format_settings")
def format_settings_callback(call):
    """Обработчик кнопки 'Сохранить', возвращает в меню формата данных"""
    format_settings(call.message)


@safe_execute
@bot.message_handler(func=lambda message: True)
def settings_back_to_main_menu(message):
    chat_id = message.chat.id
    user = get_user(chat_id)
    lang = get_user_lang(user) if user else "ru"

    if message.text != get_text("btn_back", lang):
        return

    delete_last_menu_message(chat_id)

    last_settings_message_id = get_data_field("last_settings_command", chat_id)
    if last_settings_message_id:
        try:
            bot.delete_message(chat_id, last_settings_message_id)
            update_data_field("last_settings_command", chat_id, None)
            bot_logger.info(
                f"▸ Удалено сообщение настроек {last_settings_message_id} для чата {chat_id}."
            )
        except Exception as e:
            bot_logger.warning(f"▸ Ошибка при удалении сообщения настроек: {e}")

    try:
        bot.delete_message(chat_id, message.message_id)
    except Exception as e:
        bot_logger.warning(f"▸ Ошибка при удалении сообщения кнопки назад: {e}")

    send_main_menu(chat_id)



@safe_execute
@bot.message_handler(func=lambda message: True)
def weather_data_settings(message):
    chat_id = message.chat.id
    user = get_user(message.from_user.id)
    lang = get_user_lang(user) if user else "ru"

    if message.text != get_text("settings_weather_data_btn", lang):
        return

    delete_last_menu_message(chat_id)
    update_data_field("last_user_command", chat_id, message.message_id)
    bot_logger.info(
        f"▸ Сохранён ID последней команды: {message.message_id} для чата {chat_id}."
    )

    if not user:
        bot_logger.error(f"❌ Ошибка: пользователь {chat_id} не найден.")
        bot.send_message(chat_id, get_text("error_user_not_found", lang))
        return

    text = get_text("settings_weather_data_text", lang)

    try:
        keyboard = generate_weather_data_keyboard(user)
        bot.send_message(
            chat_id,
            text,
            reply_markup=keyboard,
            reply_to_message_id=message.message_id
        )
        bot_logger.info(
            f"▸ Меню настроек погодных данных отправлено пользователю {chat_id}."
        )
    except Exception as e:
        bot_logger.error(f"❌ Ошибка при отправке меню погодных данных: {e}")



@safe_execute
@bot.callback_query_handler(func=lambda call: call.data.startswith("toggle_weather_param_"))
def toggle_weather_param(call):
    """Обработчик изменения отображаемых данных в прогнозе"""
    chat_id = call.message.chat.id
    user = get_user(call.from_user.id)
    param = call.data.replace("toggle_weather_param_", "")
    if not user:
        bot_logger.error(f"❌ Ошибка: пользователь {call.from_user.id} не найден.")
        return
    try:
        current_params = decode_tracked_params(user.tracked_weather_params)
    except Exception as e:
        bot_logger.error(f"❌ Ошибка декодирования параметров пользователя {user.user_id}: {e}")
        current_params = {key: True for key in [
            "description", "temperature", "humidity", "precipitation",
            "pressure", "wind_speed", "visibility", "feels_like",
            "clouds", "wind_direction", "wind_gust"
        ]}
    if param not in current_params:
        bot_logger.warning(f"⚠ Неизвестный параметр {param} для пользователя {user.user_id}")
        return
    current_params[param] = not current_params[param]
    bot_logger.info(f"▸ Параметр {param} переключён на {current_params[param]} для пользователя {user.user_id}")
    try:
        update_user(user.user_id, tracked_weather_params=json.dumps(current_params))
        updated_user = get_user(call.from_user.id)  
        new_keyboard = generate_weather_data_keyboard(updated_user)
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=new_keyboard)
        bot_logger.info(f"✅ UI обновлён для пользователя {user.user_id}.")
    except Exception as e:
        bot_logger.error(f"❌ Ошибка при обновлении tracked_weather_params для пользователя {user.user_id}: {e}")


def get_menu_actions(lang="lang"):
    return {
        get_text("menu_weather_now", lang): weather,
        get_text("menu_forecast", lang): forecast_menu_handler,
        get_text("menu_settings", lang): lambda msg: send_settings_menu(msg.chat.id),
        get_text("menu_change_city", lang): changecity,
        get_text("menu_notifications", lang): notification_settings,
        get_text("menu_back", lang): settings_back_to_main_menu,
        get_text("menu_units", lang): lambda msg: format_settings(msg),
        get_text("menu_weather_data", lang): weather_data_settings,
        get_text("menu_language", lang): language_settings,
    }

@safe_execute
@bot.message_handler(func=lambda message: True)
def menu_handler(message):
    user = get_user(message.chat.id)
    lang = get_user_lang(user) if user else "ru"

    menu_actions = get_menu_actions(lang)
    action = menu_actions.get(message.text)
    if not action:
        return

    action(message)

@safe_execute
@bot.message_handler(func=lambda message: message.text in [
    get_text("menu_language", "ru"),
    get_text("menu_language", "en"),
    get_text("menu_language", "kk")
])
def language_settings(message):
    chat_id = message.chat.id
    user = get_user(message.from_user.id)
    lang = get_user_lang(user)

    bot_logger.info(f"▸ Открыто меню языков для чата {chat_id}.")
    delete_last_menu_message(chat_id)
    update_data_field("last_user_command", chat_id, message.message_id)

    text = get_text("language_select_prompt", lang)
    keyboard = generate_language_keyboard(user)

    try:
        bot.send_message(
            chat_id,
            text,
            reply_markup=keyboard,
            reply_to_message_id=message.message_id
        )
    except Exception as e:
        bot_logger.error(f"▸ Ошибка в language_settings: {e}")

@safe_execute
@bot.callback_query_handler(func=lambda call: call.data.startswith("set_lang_"))
def set_language_callback(call):
    chat_id = call.message.chat.id
    user_id = call.from_user.id
    
    # Парсим код языка (ru, en, kk)
    new_lang_code = call.data.replace("set_lang_", "")
    
    # 1. Обновляем пользователя в БД
    update_user(user_id, language=new_lang_code)
    
    # 2. Получаем обновленного пользователя
    user = get_user(user_id)
    
    # 3. Обновляем клавиатуру (чтобы галочка встала на новый язык)
    try:
        new_keyboard = generate_language_keyboard(user)
        # Меняем текст самого сообщения на новый язык
        new_text = get_text("language_select_prompt", new_lang_code)
        
        bot.edit_message_text(
            text=new_text,
            chat_id=chat_id,
            message_id=call.message.message_id,
            reply_markup=new_keyboard
        )
    except Exception as e:
        bot_logger.warning(f"Ошибка при обновлении сообщения языка: {e}")

    # 4. Показываем всплывающее уведомление
    success_text = get_text("language_set_success", new_lang_code)
    bot.answer_callback_query(call.id, success_text)
    
    # 5. ЛОГИКА ПЕРВОГО ЗАПУСКА
    # Если у пользователя еще не установлен город, запускаем запрос города на новом языке
    if not user.preferred_city:
        bot_logger.info(f"▸ Язык установлен ({new_lang_code}). Переход к выбору города для {user_id}.")
        # Удаляем сообщение с выбором языка, чтобы не мешало (опционально)
        try:
            bot.delete_message(chat_id, call.message.message_id)
        except:
            pass
            
        ask_for_city_initial(chat_id, user_id, new_lang_code, call.from_user.first_name)
    else:
        # Если город уже есть, просто обновляем главное меню (на случай если мы в настройках)
        pass

@safe_execute
@bot.message_handler(commands=["help"])
def help_command(message):
    user_id = message.from_user.id
    user = get_user(user_id)
    lang = get_user_lang(user) if user else "ru"

    bot_logger.debug(f"Получена команда /help от пользователя с ID {user_id}.")

    help_text = (
        f"{get_text('help_title', lang)}\n\n"
        f"{get_text('help_start', lang)}\n"
        f"{get_text('help_stop', lang)}\n"
        f"{get_text('help_weather', lang)}\n"
        f"{get_text('help_changecity', lang)}\n"
        f"{get_text('help_forecast', lang)}\n"
        f"{get_text('help_help', lang)}"
    )

    bot_logger.info(f"▸ Пользователь {user_id} запросил список команд.")
    bot.reply_to(message, help_text)



@safe_execute
def process_new_city(message, show_menu=False):
    user_id = message.from_user.id
    chat_id = message.chat.id
    city = message.text.strip()

    def error_reply(text):
        keyboard = types.InlineKeyboardMarkup()
        cancel_button = types.InlineKeyboardButton(
            get_text("btn_cancel", get_user_lang(get_user(user_id))),
            callback_data="cancel_changecity"
        )
        keyboard.add(cancel_button)
        last_menu_id = get_data_field("last_menu_message", chat_id)
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=last_menu_id,
                text=f"{text}\n\n{get_text('changecity_prompt_retry', get_user_lang(get_user(user_id)))}",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception as e:
            bot_logger.warning(f"Не удалось отредактировать сообщение об ошибке: {e}")
            reply = bot.reply_to(message, text, reply_markup=keyboard)
            bot.register_next_step_handler(reply, process_new_city, show_menu)
            return
        bot.register_next_step_handler(message, process_new_city, show_menu)

    if city == "/start":
        bot_logger.info(f"Пользователь {user_id} отправил /start вместо города.")
        start(message)
        return

    if city.startswith("/") or not city:
        bot_logger.info(f"Пользователь {user_id} отправил некорректное название города: {city}.")
        error_reply(get_text("changecity_error_command", get_user_lang(get_user(user_id))))
        try:
            bot.delete_message(chat_id, message.message_id)
        except Exception as e:
            bot_logger.warning(f"Не удалось удалить сообщение пользователя {user_id}: {e}")
        return

    if not re.match(r'^[A-Za-zА-Яа-яЁё\s\-]+$', city):
        bot_logger.info(f"Пользователь {user_id} отправил название города с недопустимыми символами: {city}.")
        error_reply(get_text("changecity_error_invalid", get_user_lang(get_user(user_id))))
        try:
            bot.delete_message(chat_id, message.message_id)
        except Exception as e:
            bot_logger.warning(f"Не удалось удалить сообщение пользователя {user_id}: {e}")
        return

    updated = update_user_city(user_id, city, message.from_user.username)
    if updated:
        bot_logger.info(f"Пользователь {user_id} успешно сменил город на {city}.")
        success_text = get_text("changecity_success_update", get_user_lang(get_user(user_id))).format(city=city)
    else:
        bot_logger.info(f"Пользователь {user_id} попытался установить уже установленный город: {city}.")
        success_text = get_text("changecity_success_same", get_user_lang(get_user(user_id))).format(city=city)

    last_menu_id = get_data_field("last_menu_message", chat_id)
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=last_menu_id,
            text=success_text,
            parse_mode="HTML"
        )
        update_data_field("last_menu_message", chat_id, None)
        update_existing_forecast(user_id)
    except Exception as e:
        bot_logger.warning(f"Не удалось отредактировать сообщение для пользователя {user_id}: {e}")
        bot.reply_to(message, success_text)

    try:
        bot.delete_message(chat_id, message.message_id)
    except Exception as e:
        bot_logger.warning(f"Не удалось удалить сообщение пользователя {user_id}: {e}")

    if show_menu:
        send_settings_menu(chat_id)



@safe_execute
def process_new_city_registration(message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    def error_reply(comment):
        base_text = get_text("greet_new", "lang").format(name=message.from_user.first_name)
        full_text = f"{base_text}\n\n{comment}"

        last_bot_msg_id = get_data_field("last_bot_message", chat_id)
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        button_geo = types.KeyboardButton(text=get_text("button_geo", "lang"), request_location=True)
        keyboard.add(button_geo)

        try:
            if last_bot_msg_id:
                bot.delete_message(chat_id, last_bot_msg_id)
        except Exception as e:
            bot_logger.warning(f"Не удалось удалить сообщение {last_bot_msg_id} для пользователя {user_id}: {e}")

        msg = bot.send_message(
            chat_id,
            full_text,
            reply_markup=keyboard
        )
        update_data_field("last_bot_message", chat_id, msg.message_id)
        bot.register_next_step_handler(msg, process_new_city_registration)

    # --- Обработка геолокации ---
    if message.location:
        latitude = message.location.latitude
        longitude = message.location.longitude
        city = resolve_city_from_coords(latitude, longitude)
        if not city:
            bot_logger.warning(f"Не удалось определить город по координатам ({latitude}, {longitude}) от пользователя {user_id}.")
            error_reply(get_text("error_city_not_found_coords", "lang"))
            return
    # --- Обработка текстового ввода ---
    elif message.text:
        city = message.text.strip()
        if city == "/start":
            bot_logger.info(f"Пользователь {user_id} отправил /start вместо города при регистрации.")
            start(message)
            return
        if city.startswith("/") or not city:
            bot_logger.info(f"Пользователь {user_id} отправил некорректное название города: {city}.")
            error_reply(get_text("error_invalid_city_command", "lang"))
            try:
                bot.delete_message(chat_id, message.message_id)
            except Exception as e:
                bot_logger.warning(f"Не удалось удалить сообщение пользователя {user_id}: {e}")
            return
        if not re.match(r'^[A-Za-zА-Яа-яЁё\s\-]+$', city):
            bot_logger.info(f"Пользователь {user_id} отправил название города с недопустимыми символами: {city}.")
            error_reply(get_text("error_invalid_city_chars", "lang"))
            try:
                bot.delete_message(chat_id, message.message_id)
            except Exception as e:
                bot_logger.warning(f"Не удалось удалить сообщение пользователя {user_id}: {e}")
            return
    else:
        bot_logger.warning(f"Сообщение от пользователя {user_id} не содержит текста или локации.")
        error_reply(get_text("error_no_input", "lang"))
        return

    # --- Сохраняем город ---
    updated = update_user_city(user_id, city, message.from_user.username)
    if updated:
        bot_logger.info(f"Пользователь {user_id} успешно сменил город на {city}.")
        success_text = get_text("changecity_success_update", "lang").format(city=city)
    else:
        bot_logger.info(f"Пользователь {user_id} повторно установил город: {city}.")
        success_text = get_text("changecity_success_update", "lang").format(city=city)

    base_text = f"Привет, {message.from_user.first_name}!\n{success_text}\n\n{get_text('greet_success_end', 'ru')}"
    full_text = base_text

    last_bot_msg_id = get_data_field("last_bot_message", chat_id)
    try:
        if last_bot_msg_id:
            bot.delete_message(chat_id, last_bot_msg_id)
    except Exception as e:
        bot_logger.warning(f"Не удалось удалить сообщение {last_bot_msg_id} для пользователя {user_id}: {e}")

    msg = bot.send_message(
        chat_id,
        full_text,
        reply_markup=types.ReplyKeyboardRemove()
    )
    update_data_field("last_bot_message", chat_id, msg.message_id)
    refresh_daily_forecast(user_id)
    send_main_menu(chat_id)



@safe_execute
@bot.callback_query_handler(func=lambda call: call.data in ["change_temp_unit", "change_pressure_unit", "change_wind_speed_unit"])
def change_unit_menu(call):
    chat_id = call.message.chat.id
    user_id = call.from_user.id
    user = get_user(user_id)

    if not user:
        bot_logger.error(f"▸ Ошибка: пользователь {user_id} не найден.")
        bot.send_message(chat_id, get_text("error_user_not_found_start", "ru"))
        return

    lang = get_user_lang(user)

    unit_type = call.data[len("change_"):-len("_unit")]
    display_names = {
        "temp": get_text("unit_temp_label_alt", lang),
        "pressure": get_text("unit_pressure_label_alt", lang),
        "wind_speed": get_text("unit_wind_speed_label_alt", lang)
    }

    display_text = display_names.get(unit_type, unit_type)
    current_unit = getattr(user, f"{unit_type}_unit", "N/A")

    bot_logger.info(
        f"Пользователь {user_id} открывает меню изменения единицы измерения: {display_text} (текущая: {current_unit})."
    )

    try:
        bot.edit_message_text(
            text=get_text("settings_unit_select_prompt", lang).format(param=display_text),
            chat_id=chat_id,
            message_id=call.message.message_id,
            reply_markup=generate_unit_selection_keyboard(current_unit, unit_type, user_id)
        )
        update_data_field("last_bot_message", chat_id, call.message.message_id)

    except Exception as e:
        bot_logger.warning(
            f"Ошибка при редактировании сообщения для изменения единицы измерения у пользователя {user_id}: {e}"
        )




@safe_execute
@bot.callback_query_handler(func=lambda call: call.data.startswith("set_"))
def set_unit(call):
    """Изменяет единицы измерения и обновляет inline-клавиатуру."""
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    data = call.data[len("set_"):] 
    
    try:
        unit_type, new_unit = data.split("_unit_", 1)
    except Exception as e:
        bot_logger.error(f"Ошибка при разборе callback_data: {call.data}, {e}")
        return

    db_field_prefix = "wind_speed" if unit_type == "wind" else unit_type
    
    update_user_unit(user_id, unit_type, new_unit) 

    user = get_user(user_id)
    if not user: return

    current_val = getattr(user, f"{db_field_prefix}_unit")
    new_keyboard = generate_unit_selection_keyboard(current_val, unit_type, user_id)
    
    try:   
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=new_keyboard)
        
        lang = get_user_lang(user)
        bot.answer_callback_query(call.id, get_text("unit_updated_success", lang))
        
    except telebot.apihelper.ApiTelegramException as e:
        if "message is not modified" in str(e):
            bot.answer_callback_query(call.id, get_text("unit_already_selected", get_user_lang(user)))
        else:
            bot_logger.error(f"Ошибка при редактировании клавиатуры: {e}")


def clear_old_updates():
    """Пропускает старые сообщения, полученные до запуска бота."""
    updates = bot.get_updates(offset=-1)
    if updates:
        last_update_id = updates[-1].update_id
        bot_logger.info(f"Сброшены старые обновления до [offset {last_update_id + 1}]")


if __name__ == '__main__':
    bot_logger.info("Бот запущен.")
    clear_old_updates()

    MAX_RETRIES = 10
    attempt = 1  

    while attempt <= MAX_RETRIES:
        try:
            bot_logger.info(f"Попытка #{attempt}: Запускаем polling...")
            bot.polling(timeout=10, long_polling_timeout=10, allowed_updates=["message", "callback_query"])
        except requests.exceptions.ReadTimeout:
            bot_logger.warning(f"Попытка #{attempt}: Read timeout. Перезапуск через 5 секунд...")
        except requests.exceptions.ConnectionError as e:
            bot_logger.error(f"Попытка #{attempt}: Ошибка соединения: {e}. Перезапуск через 5 секунд...")
        except Exception as e:
            bot_logger.critical(f"Попытка #{attempt}: Неизвестная ошибка: {e}. Перезапуск через 5 секунд...")
        finally:
            attempt += 1
            time.sleep(5)

    bot_logger.critical("Достигнуто максимальное количество попыток! Бот остановлен.")