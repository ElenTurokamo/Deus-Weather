#ИМПОРТЫ
from telebot import types
from dotenv import load_dotenv
from logging.handlers import RotatingFileHandler
from logic import get_user, save_user, update_user
from logic import *
from weather import get_weather
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


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


@bot.message_handler(func=lambda message: not message.text.startswith("/") and message.text not in menu_actions)
def handle_all_messages(message):
    """Обрабатывает все сообщения""" 
    bot_logger.debug(f"Получено сообщение: {message.text}.")
    user_id = message.from_user.id
    chat_id = message.chat.id
    active_sessions[user_id] = chat_id 
    if message.date < bot_start_time:
        return
    if is_valid_command(message.text):  
        if message.text in menu_actions:
            menu_actions[message.text](message)
    else:
        bot_logger.info(f"▸ Пользователь {user_id} отправил дичь. Вежливо просим его попробовать снова.")
        bot.send_message(chat_id, "Я вас не понял. Используйте команды меню!")
        send_main_menu(message.chat.id)


"""ОТПРАВКА МЕНЮ"""
def menu_option(chat_id, reply_markup=None):
    """Отправка декоративного сообщения при взаимодействии с главным меню."""
    menu_message = bot.send_message(chat_id, "Выберите опцию:", reply_markup=reply_markup)
    update_data_field("last_menu_message", chat_id, menu_message.message_id)
    return menu_message.message_id


def settings_option(chat_id, reply_markup=None):
    """Отправка декоративного сообщения при взаимодействии с меню настроек."""
    settings_opt = bot.send_message(chat_id, "Выберите настройку:", reply_markup=reply_markup)
    update_data_field("last_menu_message", chat_id, settings_opt.message_id)
    return settings_opt.message_id


def send_main_menu(chat_id):
    """Отправка главного меню пользователю."""
    delete_last_menu_message(chat_id)
    main_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    main_keyboard.row("🌎 Погода сегодня", "📅 Прогноз погоды")
    main_keyboard.row("👥 Друзья", "🎭 Профиль")
    main_keyboard.row("🌤 Deus Pass", "⚙️ Настройки")
    menu_option(chat_id, reply_markup=main_keyboard)


def send_settings_menu(chat_id):
    """Отправка клавиатуры с меню настроек пользователю."""
    delete_last_menu_message(chat_id)
    settings_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    settings_keyboard.row("🏙 Изменить город", "🔔 Уведомления")
    settings_keyboard.row("🌦 Погодные данные", "📏 Единицы измерения")
    settings_keyboard.row("↩ Назад")
    settings_option(chat_id, reply_markup=settings_keyboard)


def delete_last_menu_message(chat_id):
    """Удаляет последнее декоративное сообщение для чата."""
    message_id = get_data_field("last_menu_message", chat_id)
    if message_id:
        try:
            bot.delete_message(chat_id, message_id)
            update_data_field("last_menu_message", chat_id, None)
        except Exception as e:
            bot_logger.warning(f"Ошибка при удалении меню-сообщения {message_id} для чата {chat_id}: {e}")


@safe_execute
@bot.callback_query_handler(func=lambda call: call.data in ["forecast_today", "forecast_tomorrow", "forecast_week"])
def forecast_handler(call):
    """Обработчик прогноза погоды на сегодня, завтра и неделю с учётом пользовательских настроек"""
    chat_id = call.message.chat.id
    user = get_user(call.from_user.id)
    menu_message_id = call.message.message_id
    if not user or not user.preferred_city:
        bot.send_message(chat_id, "⚠ Сначала укажите ваш город в настройках!")
        return
    if call.data == "forecast_today":
        forecast_data = [get_today_forecast(user.preferred_city, user)]
    elif call.data == "forecast_tomorrow":
        forecast_data = [get_tomorrow_forecast(user.preferred_city, user)]
    else:
        forecast_data = get_weekly_forecast(user.preferred_city, user)
    if not forecast_data or None in forecast_data:
        bot.send_message(chat_id, "⚠ Не удалось получить прогноз погоды.")
        return
    try:
        forecast_text = "\n\n".join([format_forecast(day, user) for day in forecast_data]) + "\n\n      ⟪ Deus Weather ⟫"
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
    """Форматирует прогноз с учётом настроек пользователя"""
    tracked_params = decode_tracked_params(user.tracked_weather_params)
    parts = [
        f"<b>{day['day_name']}, {day['date']}</b>",
        "─" * min(len(f"{day['day_name']}, {day['date']}"), 21)
    ]
    if tracked_params.get("description", False) and "description" in day:
        parts.append(f"▸ Погода: {day['description']}")
    if tracked_params.get("temperature", False) and "temp_min" in day and "temp_max" in day:
        temp_unit = UNIT_TRANSLATIONS['temp'][user.temp_unit]
        temp_min = round(convert_temperature(day['temp_min'], user.temp_unit))
        temp_max = round(convert_temperature(day['temp_max'], user.temp_unit))
        if temp_min == temp_max:
            parts.append(f"▸ Температура: {temp_min}{temp_unit}")
        else:
            parts.append(f"▸ Температура: от {temp_min}{temp_unit} до {temp_max}{temp_unit}")
    if tracked_params.get("feels_like", False) and "feels_like" in day:
        temp_unit = UNIT_TRANSLATIONS['temp'][user.temp_unit]
        parts.append(
            f"▸ Ощущается как: {round(convert_temperature(day['feels_like'], user.temp_unit))}{temp_unit}"
        )
    if tracked_params.get("humidity", False) and "humidity" in day:
        parts.append(f"▸ Влажность: {day['humidity']}%")
    if tracked_params.get("precipitation", False) and "precipitation" in day:
        parts.append(f"▸ Вероятность осадков: {day['precipitation']}%")
    if tracked_params.get("pressure", False) and "pressure" in day:
        parts.append(
            f"▸ Давление: {round(convert_pressure(day['pressure'], user.pressure_unit))} "
            f"{UNIT_TRANSLATIONS['pressure'][user.pressure_unit]}"
        )
    if tracked_params.get("wind_speed", False) and "wind_speed" in day:
        parts.append(
            f"▸ Скорость ветра: {round(convert_wind_speed(day['wind_speed'], user.wind_speed_unit))} "
            f"{UNIT_TRANSLATIONS['wind_speed'][user.wind_speed_unit]}"
        )
    if tracked_params.get("wind_direction", False) and "wind_direction" in day:
        direction = get_wind_direction(day['wind_direction'])
        parts.append(f"▸ Направление ветра: {direction} ({day['wind_direction']}°)")
    if tracked_params.get("wind_gust", False) and "wind_gust" in day:
        parts.append(
            f"▸ Порывы ветра: {round(convert_wind_speed(day['wind_gust'], user.wind_speed_unit))} "
            f"{UNIT_TRANSLATIONS['wind_speed'][user.wind_speed_unit]}"
        )
    if tracked_params.get("clouds", False) and "clouds" in day:
        parts.append(f"▸ Облачность: {day['clouds']}%")
    if tracked_params.get("visibility", False) and "visibility" in day:
        parts.append(f"▸ Видимость: {int(day['visibility'])} м")
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
    """Регистрация пользователя/запуск бота"""
    log_action("Получена команда /start", message)
    user_id = message.from_user.id
    user = get_user(user_id)
    chat_id = message.chat.id
    delete_last_menu_message(chat_id)
    if user and user.preferred_city:
        back_reply_text = (f"С возвращением, {message.from_user.first_name}!\n"
                      f"Ваш основной город — {user.preferred_city}.")
        msg = bot.reply_to(message, back_reply_text)  
        update_data_field("last_bot_message", chat_id, msg.message_id)
        send_main_menu(message.chat.id)
    else:
        save_user(user_id, message.from_user.first_name)
        new_reply_text = (f"Привет, {message.from_user.first_name}!\n"
                      "Чтобы начать пользоваться ботом — введите свой город.")
        msg = bot.reply_to(message, new_reply_text)
        update_data_field("last_bot_message", chat_id, msg.message_id)
        bot.register_next_step_handler(msg, process_new_city_registration) 
    bot_logger.info(f"▸ Команда /start обработана для пользователя {user_id}.")


@safe_execute
@bot.message_handler(commands=['weather'])
def weather(message):
    """Отправка текущей погоды в городе пользователя"""
    (message)
    user_id = message.from_user.id
    user = get_user(user_id)
    bot_logger.info(f"▸ Получена команда /weather от {user_id}.")
    if not user or not user.preferred_city:
        bot_logger.info(f"▸ У пользователя {user_id} не выбран город. Запрашиваем ввод.")
        reply = bot.reply_to(message, "Для начала укажите свой город!")
        bot.register_next_step_handler(reply, process_new_city)
        return
    delete_last_menu_message(message.chat.id)
    weather_data = get_weather(user.preferred_city)
    if not weather_data:
        bot.reply_to(message, "Не удалось получить данные о погоде.")
        send_main_menu(message.chat.id)
        return
    bot_logger.info(f"▸ Погода в {user.preferred_city} успешно получена.")
    weather_info = format_weather_data(weather_data, user)
    bot.reply_to(message, weather_info, parse_mode="HTML")
    send_main_menu(message.chat.id)


@safe_execute
@bot.message_handler(regexp=r"^(\/changecity|🏙 Изменить город)$")
def changecity(message):
    """Запрос на изменение города у пользователя"""
    user_id = message.from_user.id
    user = get_user(user_id)
    chat_id = message.chat.id
    bot_logger.info(f"▸ Получена команда /changecity от {user_id}.")
    delete_last_menu_message(chat_id)
    reply_text = (f"▸ Ваш текущий город — {user.preferred_city}. \n\nВведите название нового города для обновления!"
                  if user and user.preferred_city else
                  "Вы ещё не указали свой город! \nУкажите новый город.")
    keyboard = types.InlineKeyboardMarkup()
    cancel_button = types.InlineKeyboardButton("✖ Отмена", callback_data="cancel_changecity")
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
@bot.message_handler(func=lambda message: message.text == "🔔 Уведомления")
def notification_settings(message):
    """Открывает меню настроек уведомлений"""
    user = get_user(message.from_user.id)
    chat_id = message.chat.id
    bot_logger.info(f"▸ Открыто меню уведомлений для чата {chat_id}.")
    delete_last_menu_message(chat_id)
    update_data_field("last_user_command", chat_id, message.message_id)
    if not user:
        bot.send_message(chat_id, "Ошибка: пользователь не найден.")
        return
    try:
        keyboard = generate_notification_settings_keyboard(user)
        bot.send_message(chat_id, "Выберите уведомления, которые вы хотите получать:", reply_markup=keyboard, reply_to_message_id=message.message_id)
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
@bot.message_handler(regexp=r"^(\📅 Прогноз погоды|/weatherforecast)$")
def forecast_menu_handler(message):
    """Отправляет пользователю меню с выбором прогноза"""
    chat_id = message.chat.id
    bot_logger.info(f"▸ Пользователь {message.from_user.id} открыл меню прогноза погоды.")
    delete_last_menu_message(chat_id)
    msg = bot.reply_to(message, "Выберите период прогноза:", reply_markup=generate_forecast_keyboard())
    update_data_field("last_user_command", chat_id, {"message_id": message.message_id, "command": message.text})
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
    raw_forecast = get_today_forecast(user.preferred_city, user)
    if not raw_forecast:
        bot_logger.warning(f"▸ `get_today_forecast` не вернула данные для {user.preferred_city}!")
        return
    user_tz = ZoneInfo(user.timezone) if user.timezone else ZoneInfo("UTC")
    user_time = datetime.now().astimezone(user_tz)
    updated_time = user_time.strftime("%H:%M")
    forecast_message = (
        "<blockquote>📅 Ежедневный прогноз погоды</blockquote>\n"
        f"[Обновлено в {updated_time}]\n"
        + format_forecast(raw_forecast, user)
        + "\n\n      ⟪ Deus Weather ⟫"
    )
    try:
        sent_message = bot.send_message(
            user_id, forecast_message, parse_mode="HTML"
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
            bot_logger.warning(f"▸ Не удалось закрепить сообщение для {user_id}: {pin_error}")
    except Exception as e:
        bot_logger.error(f"▸ Ошибка при отправке прогноза {user_id}: {e}")


@safe_execute
def update_existing_forecast(user_id):
    """Обновляет существующий прогноз, если он есть, иначе отправляет новый и закрепляет его."""
    last_forecast_id = get_data_field("last_daily_forecast", user_id)
    user = get_user(user_id)
    if not user or not user.preferred_city:
        bot_logger.error(f"▸ Ошибка: не найден пользователь {user_id} или его город.")
        return
    raw_forecast = get_today_forecast(user.preferred_city, user)
    if not raw_forecast:
        bot_logger.warning(f"▸ `get_today_forecast` не вернула данные для {user.preferred_city}!")
        return
    user_tz = ZoneInfo(user.timezone) if user.timezone else ZoneInfo("UTC")
    user_time = datetime.now().astimezone(user_tz)
    updated_time = user_time.strftime("%H:%M")
    forecast_message = (
        "<blockquote>📅 Ежедневный прогноз погоды</blockquote>\n"
        f"[Обновлено в {updated_time}]\n"
        + format_forecast(raw_forecast, user)
        + "\n\n      ⟪ Deus Weather ⟫"
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
            user_id, forecast_message, parse_mode="HTML"
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
    """Редактирует сообщение меню единиц измерения."""
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
        bot.send_message(chat_id, "Ошибка: пользователь не найден. Попробуйте /start.")
        return
    header = f"Сейчас ваши данные измеряются в следующих величинах:"
    separator = "─" * min(len(header), 21)
    text = (
        f"{header}\n"
        f"{separator}\n"
        f"▸ Температура: {UNIT_TRANSLATIONS['temp'][user.temp_unit]}\n"
        f"▸ Давление: {UNIT_TRANSLATIONS['pressure'][user.pressure_unit]}\n"
        f"▸ Скорость ветра: {UNIT_TRANSLATIONS['wind_speed'][user.wind_speed_unit]}\n"
        f"{separator}\n"
        f"Выберите параметр для изменения единиц измерения:"
    )
    menu_message_id = get_data_field("last_format_settings_menu", chat_id)
    try:
        if menu_message_id:
            bot.edit_message_text(text, chat_id, menu_message_id, reply_markup=generate_format_keyboard())
            bot_logger.info(f"▸ Меню единиц измерения обновлено для чата {chat_id}.")
        else:
            raise KeyError("Меню отсутствует, отправляем новое сообщение.")
    except Exception as e:
        bot_logger.warning(f"▸ Ошибка при редактировании сообщения: {e}. Отправляем новое сообщение.")
        try:
            msg = bot.send_message(chat_id, text, reply_markup=generate_format_keyboard(), reply_to_message_id=reply_to)
            update_data_field("last_format_settings_menu", chat_id, msg.message_id)
            bot_logger.info(f"▸ Новое меню единиц измерения отправлено в чат {chat_id}.")
        except Exception as send_error:
            bot_logger.error(f"▸ Ошибка при отправке нового сообщения: {send_error}")


@safe_execute
@bot.callback_query_handler(func=lambda call: call.data == "return_to_format_settings")
def return_to_format_settings(call):
    """Возвращает пользователя в меню единиц измерения без обновления last_user_command."""
    chat_id = call.message.chat.id
    user = get_user(chat_id)
    if not user:
        bot_logger.error(f"▸ Ошибка: пользователь {chat_id} не найден.")
        bot.send_message(chat_id, "Ошибка: пользователь не найден. Попробуйте /start.")
        return
    header = f"Сейчас ваши данные измеряются в следующих величинах:"
    separator = "─" * min(len(header), 21)
    text = (
        f"{header}\n"
        f"{separator}\n"
        f"▸ Температура: {UNIT_TRANSLATIONS['temp'][user.temp_unit]}\n"
        f"▸ Давление: {UNIT_TRANSLATIONS['pressure'][user.pressure_unit]}\n"
        f"▸ Скорость ветра: {UNIT_TRANSLATIONS['wind_speed'][user.wind_speed_unit]}\n"
        f"{separator}\n"
        f"Выберите параметр для изменения единиц измерения:"
    )
    try:
        bot.edit_message_text(
            text,
            chat_id,
            call.message.message_id,
            reply_markup=generate_format_keyboard(),
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
def feature_in_development(message):
    """Временный обработчик для уведомления о разработке"""
    chat_id = message.chat.id
    delete_last_menu_message(chat_id)
    if message.text == "🎭 Профиль": 
        feature_name = "профиля"
    elif message.text == "🌤 Deus Pass":
        feature_name = "подписки"
    else:
        feature_name = "друзей"
    bot.reply_to(message, f"‼️ Функция {feature_name} всё ещё в разработке!\n\nСледите за обновлениями!")
    bot_logger.info(f"▸ Пользователь {chat_id} запросил {feature_name}, но функция в разработке.")
    send_main_menu(chat_id)


@safe_execute
@bot.message_handler(func=lambda message: message.text == "↩ Назад")
def settings_back_to_main_menu(message):
    """Обработчик кнопки '↩ Назад' в главном меню"""
    chat_id = message.chat.id
    delete_last_menu_message(chat_id)
    last_settings_message_id = get_data_field("last_settings_command", chat_id)
    if last_settings_message_id:
        try:
            bot.delete_message(chat_id, last_settings_message_id)
            update_data_field("last_settings_command", chat_id, None)
            bot_logger.info(f"▸ Удалено сообщение настроек {last_settings_message_id} для чата {chat_id}.")
        except Exception as e:
            bot_logger.warning(f"▸ Ошибка при удалении сообщения настроек: {e}")
    try:
        bot.delete_message(chat_id, message.message_id)
    except Exception as e:
        bot_logger.warning(f"▸ Ошибка при удалении сообщения '↩ Назад': {e}")
    send_main_menu(chat_id)


@safe_execute
@bot.message_handler(func=lambda message: message.text == "🌦 Погодные данные")
def weather_data_settings(message):
    """Обработчик кнопки 'Погодные данные' в настройках"""
    chat_id = message.chat.id
    user = get_user(message.from_user.id)
    delete_last_menu_message(chat_id)         
    update_data_field("last_user_command", chat_id, message.message_id)
    bot_logger.info(f"▸ Сохранён ID последней команды: {message.message_id} для чата {chat_id}.")
    if not user:
        bot_logger.error(f"❌ Ошибка: пользователь {chat_id} не найден.")
        bot.send_message(chat_id, "Ошибка: пользователь не найден.")
        return
    text = "Выберите данные, которые вы хотите видеть при получении погоды:"
    try:
        keyboard = generate_weather_data_keyboard(user)
        bot.send_message(chat_id, text, reply_markup=keyboard, reply_to_message_id=message.message_id)
        bot_logger.info(f"▸ Меню настроек погодных данных отправлено пользователю {chat_id}.")
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


@safe_execute
@bot.message_handler(func=lambda message: message.text in menu_actions)
def menu_handler(message):
    """Универсальный обработчик для всех команд бота"""
    menu_actions[message.text](message)
menu_actions = {
    "🌎 Погода сегодня": weather,
    "📅 Прогноз погоды": forecast_menu_handler,
    "⚙️ Настройки": lambda msg: send_settings_menu(msg.chat.id),
    "👥 Друзья": feature_in_development,
    "🎭 Профиль": feature_in_development,
    "🌤 Deus Pass": feature_in_development,
    "🏙 Изменить город": changecity,
    "🔔 Уведомления": notification_settings,
    "↩ Назад": settings_back_to_main_menu,
    "📏 Единицы измерения": lambda msg: format_settings(msg),
    "🌦 Погодные данные": generate_weather_data_keyboard
}


@safe_execute
@bot.message_handler(commands=['help'])
def help_command(message):
    """Обработчик команды /help. Отправляет сообщения с имеющимися у бота командами."""
    user_id = message.from_user.id
    user = get_user(user_id)
    bot_logger.debug(f"Получена команда /help от пользователя с ID {user_id}.")
    help_text = (
        "Основные команды бота:\n\n"
        "▸ /start — Запустить бота.\n"
        "▸ /weather — Узнать текущую погоду.\n"
        "▸ /changecity — Сменить город.\n"
        "▸ /weatherforecast — Получить прогноз погоды.\n"
        "▸ /help — Получить список доступных команд."
    )
    bot_logger.info(f"▸ Пользователь {user_id} запросил список команд.")
    bot.reply_to(message, help_text)


@safe_execute
def process_new_city(message, show_menu=False):
    """Обрабатывает ввод нового города пользователем и редактирует сообщение с текущим городом."""
    user_id = message.from_user.id
    chat_id = message.chat.id
    city = message.text.strip()
    def error_reply(text):
        """Редактирует сообщение с ошибкой, добавляет кнопку отмены и запрашивает повторный ввод."""
        keyboard = types.InlineKeyboardMarkup()
        cancel_button = types.InlineKeyboardButton("✖ Отмена", callback_data="cancel_changecity")
        keyboard.add(cancel_button)
        last_menu_id = get_data_field("last_menu_message", chat_id)
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=last_menu_id,
                text=f"{text}\n\nВведите название нового города для обновления!",
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
        error_reply("‼️ Отправьте название города, а не команду!")
        try:
            bot.delete_message(chat_id, message.message_id)
        except Exception as e:
            bot_logger.warning(f"Не удалось удалить сообщение пользователя {user_id}: {e}")
        return
    if not re.match(r'^[A-Za-zА-Яа-яЁё\s\-]+$', city):
        bot_logger.info(f"Пользователь {user_id} отправил название города с недопустимыми символами: {city}.")
        error_reply("‼️ Название города может содержать только буквы, пробелы и дефисы!")
        try:
            bot.delete_message(chat_id, message.message_id)
        except Exception as e:
            bot_logger.warning(f"Не удалось удалить сообщение пользователя {user_id}: {e}")
        return
    updated = update_user_city(user_id, city, message.from_user.username)
    if updated:
        bot_logger.info(f"Пользователь {user_id} успешно сменил город на {city}.")
        success_text = f"Теперь ваш основной город — {city}!"
    else:
        bot_logger.info(f"Пользователь {user_id} попытался установить уже установленный город: {city}.")
        success_text = f"г.{city} остался вашим основным городом."
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
    """Обрабатывает ввод нового города для регистрации пользователя."""
    user_id = message.from_user.id
    chat_id = message.chat.id
    city = message.text.strip()
    def error_reply(text):
        """Редактирует сообщение с ошибкой, запрашивает повторный ввод без кнопки отмены."""
        last_bot_msg_id = get_data_field("last_bot_message", chat_id)
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=last_bot_msg_id,
                text=f"{text}\n\nВведите название своего города для завершения регистрации!",
                parse_mode="HTML"
            )
        except Exception as e:
            bot_logger.warning(f"Не удалось отредактировать сообщение об ошибке для пользователя {user_id}: {e}")
            bot.register_next_step_handler(message, process_new_city_registration)
            return
        bot.register_next_step_handler(message, process_new_city_registration)
    if city == "/start":
        bot_logger.info(f"Пользователь {user_id} отправил /start вместо города при регистрации.")
        start(message)
        return
    if city.startswith("/") or not city:
        bot_logger.info(f"Пользователь {user_id} отправил некорректное название города: {city}.")
        error_reply("‼️ Отправьте название города, а не команду!")
        try:
            bot.delete_message(chat_id, message.message_id)
        except Exception as e:
            bot_logger.warning(f"Не удалось удалить сообщение пользователя {user_id}: {e}")
        return
    if not re.match(r'^[A-Za-zА-Яа-яЁё\s\-]+$', city):
        bot_logger.info(f"Пользователь {user_id} отправил название города с недопустимыми символами: {city}.")
        error_reply("‼️ Название города может содержать только буквы, пробелы и дефисы!")
        try:
            bot.delete_message(chat_id, message.message_id)
        except Exception as e:
            bot_logger.warning(f"Не удалось удалить сообщение пользователя {user_id}: {e}")
        return
    updated = update_user_city(user_id, city, message.from_user.username)
    bot_logger.info(f"Пользователь {user_id} зарегистрировал город: {city}.")

    success_text = f"Добро пожаловать, {message.from_user.first_name}!\n\nТеперь ваш основной город — {city}."
    last_bot_msg_id = get_data_field("last_bot_message", chat_id)
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=last_bot_msg_id,
            text=success_text,
            parse_mode="HTML"
        )
        update_data_field("last_bot_message", chat_id, None)
        refresh_daily_forecast(chat_id)
    except Exception as e:
        bot_logger.warning(f"Не удалось отредактировать сообщение для пользователя {user_id}: {e}")
    try:
        bot.delete_message(chat_id, message.message_id)
    except Exception as e:
        bot_logger.warning(f"Не удалось удалить сообщение пользователя {user_id}: {e}")
    send_main_menu(chat_id)


@safe_execute
@bot.callback_query_handler(func=lambda call: call.data in ["change_temp_unit", "change_pressure_unit", "change_wind_speed_unit"])
def change_unit_menu(call):
    """Промежуточное меню для выбора параметра, для которого необходимо изменить единицы измерения"""
    chat_id = call.message.chat.id
    user_id = call.from_user.id
    user = get_user(user_id)
    unit_type = call.data[len("change_"):-len("_unit")]
    display_names = {
        "temp": "температуры",
        "pressure": "давления",
        "wind_speed": "скорости ветра"
    }
    display_text = display_names.get(unit_type, unit_type)
    current_unit = getattr(user, f"{unit_type}_unit", "N/A")
    bot_logger.info(f"Пользователь {user_id} открывает меню изменения единицы измерения: {display_text} (текущая: {current_unit}).")
    try:
        bot.edit_message_text(
            f"Выберите единицу измерения {display_text}:",
            chat_id,
            call.message.message_id,
            reply_markup=generate_unit_selection_keyboard(current_unit, unit_type)
        )
        update_data_field("last_bot_message", chat_id, call.message.message_id)
    except Exception as e:
        bot_logger.warning(f"Ошибка при редактировании сообщения для изменения единицы измерения у пользователя {user_id}: {e}")


@safe_execute
@bot.callback_query_handler(func=lambda call: call.data.startswith("set_"))
def set_unit(call):
    """Изменяет единицы измерения и обновляет inline-клавиатуру, оставаясь в меню до нажатия 'Сохранить'."""
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    data = call.data[len("set_"):] 
    try:
        unit_type, new_unit = data.split("_unit_", 1)
    except Exception as e:
        bot_logger.error(f"Ошибка при разборе callback_data: {call.data}, {e}")
        return
    bot_logger.debug(f"set_unit: call.from_user.id={user_id}, data={call.data}, unit_type={unit_type}, new_unit={new_unit}")
    user = get_user(user_id)
    if not user:
        bot_logger.error(f"Ошибка: пользователь {user_id} не найден в set_unit().")
        bot.send_message(chat_id, "Ошибка: пользователь не найден. Попробуйте /start.")
        return
    update_user_unit(user_id, unit_type, new_unit)
    bot_logger.debug(f"Единицы {unit_type} изменены на {new_unit} для user_id={user_id}")

    user = get_user(user_id)
    new_keyboard = generate_unit_selection_keyboard(getattr(user, f"{unit_type}_unit"), unit_type)
    try:   
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=new_keyboard)
    except Exception as e:
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
