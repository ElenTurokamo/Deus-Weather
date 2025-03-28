#ИМПОРТЫ
from telebot import types
from dotenv import load_dotenv
from logging.handlers import RotatingFileHandler
from logic import get_user, save_user, update_user
from logic import *
from weather import get_weather
from datetime import datetime, timezone

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

#ДЕШИФРОВКА И ИДЕНТИФИКАЦИЯ ТОКЕНА БОТА
BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)


def track_bot_message(message):
    """Запоминает последнее отправленное сообщение от бота."""
    update_data_field("last_bot_message", message.chat.id, message.message_id)


#ОБРАБОТЧИКИ
@bot.message_handler(func=lambda message: not message.text.startswith("/") and message.text not in menu_actions)
def handle_all_messages(message):
    """Обрабатывает все сообщения""" 
    bot_logger.debug(f"Получено сообщение: {message.text}.")
    user_id = message.from_user.id
    chat_id = message.chat.id
    active_sessions[user_id] = chat_id 
    if message.date < bot_start_time:
        bot_logger.debug("🔴 Сообщение проигнорировано (старше времени запуска).")
        return
    if is_valid_command(message.text):  
        if message.text in menu_actions:
            menu_actions[message.text](message)
    else:
        bot.send_message(chat_id, "Я вас не понял. Используйте команды меню!")
        send_main_menu(message.chat.id)


"""ОТПРАВКА МЕНЮ"""
def send_menu(user_id, text, buttons):
    chat_id = active_sessions.get(user_id)
    if not chat_id:
        bot_logger.error(f"Ошибка: нет активного chat_id для user_id {user_id}")
        return
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    for row in buttons:
        keyboard.row(*row)
    bot.send_message(chat_id, text, reply_markup=keyboard)


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


def send_loading_message(chat_id):
    """Отправляет сообщение о загрузке и сразу его удаляет."""
    loading_message = bot.send_message(chat_id, "Загрузка...")
    bot.delete_message(chat_id, loading_message.message_id)


def send_main_menu(chat_id):
    """Отправка главного меню пользователю."""
    delete_last_menu_message(chat_id)
    main_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    main_keyboard.row("🌎 Узнать погоду", "📅 Прогноз погоды")
    main_keyboard.row("👥 Друзья", "🎭 Профиль")
    main_keyboard.row("⚙️ Настройки")
    send_loading_message(chat_id)
    menu_option(chat_id, reply_markup=main_keyboard)


def send_settings_menu(chat_id):
    """Отправка клавиатуры с меню настроек пользователю."""
    delete_last_menu_message(chat_id)
    settings_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    settings_keyboard.row("🏙 Изменить город", "🔔 Уведомления")
    settings_keyboard.row("🌦 Погодные данные", "📏 Единицы измерения")
    settings_keyboard.row("↩ Назад")
    send_loading_message(chat_id)
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


# ОБРАБОТЧИК ПРОГНОЗОВ ПОГОДЫ (СЕГОДНЯ/НЕДЕЛЯ)
@safe_execute
@bot.callback_query_handler(func=lambda call: call.data in ["forecast_today", "forecast_week"])
def forecast_handler(call):
    """Обработчик прогноза погоды на сегодня и неделю с учётом пользовательских настроек"""
    chat_id = call.message.chat.id
    user = get_user(call.from_user.id)
    menu_message_id = call.message.message_id
    if not user or not user.preferred_city:
        bot.send_message(chat_id, "⚠ Сначала укажите ваш город в настройках!")
        return
    forecast_data = (
        [get_today_forecast(user.preferred_city, user)] if call.data == "forecast_today"
        else get_weekly_forecast(user.preferred_city, user)
    )
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
        bot_logger.debug(f"Прогноз погоды успешно отправлен в чат {chat_id}.")
    except Exception as e:
        bot_logger.warning(f"⚠ Не удалось отредактировать сообщение: {str(e)}")
        msg = bot.send_message(chat_id, forecast_text, parse_mode="HTML")
        update_data_field("last_bot_message", chat_id, msg.message_id)
    send_main_menu(chat_id)


# Форматирование прогноза
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
        parts.append(
            f"▸ Температура: от {round(convert_temperature(day['temp_min'], user.temp_unit))}{temp_unit} "
            f"до {round(convert_temperature(day['temp_max'], user.temp_unit))}{temp_unit}"
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
    
    #УБРАЛ ВИДИМОСТЬ ДЛЯ НЕДЕЛЬНОГО ПРОГНОЗА. ПОКА ЧТО КАК БУДТО НЕ ИМЕЕТ СМЫСЛА
    # if tracked_params.get("visibility", False) and "visibility" in day:
    #     parts.append(f"▸ Видимость: {day['visibility']} м")

    return "\n".join(parts)


#НАВИГАЦИОННЫЕ ОБРАБОТЧИКИ
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
            bot_logger.debug(f"Удалено сообщение команды: {last_command_message} для чата {chat_id}")
        except Exception as e:
            bot_logger.warning(f"Ошибка при удалении сообщения команды: {e}")
    delete_last_menu_message(chat_id)
    send_main_menu(chat_id)


#КОМАНДЫ
#ОБРАБОТКА /start
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
        bot_logger.debug(f"Пользователь с ID {user_id} уже зарегистрирован.")
        send_main_menu(message.chat.id)
    else:
        save_user(user_id, message.from_user.first_name)
        new_reply_text = (f"Привет, {message.from_user.first_name}!\n"
                      "Чтобы начать пользоваться ботом — введите свой город.")
        msg = bot.reply_to(message, new_reply_text)
        update_data_field("last_bot_message", chat_id, msg.message_id)
        bot.register_next_step_handler(msg, process_new_city_registration) 
        bot_logger.debug(f"Новый пользователь {user_id}. Запрошен город.")


@safe_execute
@bot.message_handler(commands=['weather'])
def weather(message):
    """Отправка текущей погоды в городе пользователя"""
    (message)
    user_id = message.from_user.id
    user = get_user(user_id)
    bot_logger.debug(f"Получена команда /weather от {user_id}.")
    if not user or not user.preferred_city:
        bot_logger.debug(f"У пользователя с ID {user_id} не выбран город. Запрашиваем город.")
        reply = bot.reply_to(message, "Для начала укажите свой город!")
        bot.register_next_step_handler(reply, process_new_city)
        return
    delete_last_menu_message(message.chat.id)
    weather_data = get_weather(user.preferred_city)
    bot_logger.debug(f"Данные погоды для {user.preferred_city}: {weather_data}")
    if not weather_data:
        bot.reply_to(message, "Не удалось получить данные о погоде.")
        send_main_menu(message.chat.id)
        return
    weather_info = format_weather_data(weather_data, user)
    bot.reply_to(message, weather_info, parse_mode="HTML")
    send_main_menu(message.chat.id)


@safe_execute
@bot.message_handler(regexp=r"^(\/changecity|🏙 Изменить город)$")
def changecity(message):
    (message)
    log_action("Получена команда /changecity", message)
    user_id = message.from_user.id
    user = get_user(user_id)
    chat_id = message.chat.id
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
    """Обработчик кнопки 'Отмена' для команды /changecity"""
    chat_id = call.message.chat.id
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except Exception as e:
        bot_logger.warning(f"Ошибка при удалении сообщения с кнопкой 'Отмена': {e}")
    last_command_message = get_data_field("last_user_command", chat_id)
    if last_command_message:
        try:
            bot.delete_message(chat_id, last_command_message)
            update_data_field("last_user_command", chat_id, None)
        except Exception as e:
            bot_logger.warning(f"Ошибка при удалении сообщения команды /changecity: {e}")
    bot.clear_step_handler_by_chat_id(chat_id)
    send_settings_menu(chat_id)


@safe_execute
@bot.message_handler(func=lambda message: message.text == "🔔 Уведомления")
def notification_settings(message):
    """Обработчик кнопки 'Настройки уведомлений' в настройках"""
    user = get_user(message.from_user.id)
    chat_id = message.chat.id
    delete_last_menu_message(chat_id)
    update_data_field("last_user_command", chat_id, message.message_id)
    bot_logger.debug(f"Сохранён ID команды: {message.message_id} для чата {chat_id}")

    if not user:
        bot.send_message(chat_id, "Ошибка: пользователь не найден.")
        return

    bot_logger.debug(f"Тип user: {type(user)}. Данные: {user}")
    text = "Выберите уведомления, которые вы хотите получать:"
    try:
        keyboard = generate_notification_settings_keyboard(user)
        bot.send_message(chat_id, text, reply_markup=keyboard, reply_to_message_id=message.message_id)
    except Exception as e:
        bot_logger.error(f"Ошибка в notification_settings: {e}")

@safe_execute
@bot.callback_query_handler(func=lambda call: call.data.startswith("toggle_notification_"))
def toggle_notification(call):
    """Обработчик изменения состояния уведомлений"""
    chat_id = call.message.chat.id
    user = get_user(call.from_user.id)
    setting_key = call.data.replace("toggle_notification_", "")

    bot_logger.debug(f"Тип user перед обработкой: {type(user)}. Данные: {user}")

    if not user:
        bot_logger.error(f"Пользователь с ID {call.from_user.id} не найден.")
        return 

    try:
        notification_settings = decode_notification_settings(user.notifications_settings)
        bot_logger.debug(f"Декодированные параметры уведомлений: {notification_settings}")
    except Exception as e:
        bot_logger.error(f"Ошибка декодирования уведомлений пользователя {user.user_id}: {e}")
        notification_settings = {
            "weather_threshold_notifications": True,
            "forecast_notifications": True,
            "bot_notifications": True
        }

    if setting_key in notification_settings:
        notification_settings[setting_key] = not notification_settings[setting_key]
    else:
        bot_logger.warning(f"Неизвестный параметр {setting_key} для пользователя {user.user_id}")
        return

    try:
        update_user(user.user_id, notifications_settings=json.dumps(notification_settings))

        user = get_user(call.from_user.id)
        bot_logger.debug(f"Обновлённые данные пользователя: {user}")

        new_keyboard = generate_notification_settings_keyboard(user)  
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=new_keyboard)
    except Exception as e:
        bot_logger.warning(f"Ошибка при обновлении notifications_settings для пользователя {user.user_id}: {e}")

    bot.answer_callback_query(call.id)


"""ВЫЗОВ МЕНЮ ВЫБОРА ПРОНОЗА ПОГОДЫ"""
@safe_execute
@bot.message_handler(regexp=r"^(\📅 Прогноз погоды|/weatherforecast)$")
def forecast_menu_handler(message):
    """Выводит клавиатуру с выбором прогноза и передаёт ID сообщения дальше."""
    chat_id = message.chat.id
    delete_last_menu_message(chat_id)
    msg = bot.reply_to(message, "Выберите период прогноза:", reply_markup=generate_forecast_keyboard())
    update_data_field("last_user_command", chat_id, {
        "message_id": message.message_id,
        "command": message.text
    })
    update_data_field("last_bot_message", chat_id, msg.message_id)
    return msg.message_id


@bot.callback_query_handler(func=lambda call: call.data == "back_from_forecast_menu")
def back_from_forecast_menu(call):
    """Обработчик кнопки 'Назад' в меню прогноза"""
    chat_id = call.message.chat.id
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


#ОТКРЫТИЕ МЕНЮ ЕДИНИЦ ИЗМЕРЕНИЯ ДАННЫХ
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
        bot_logger.debug(f"Сохранён новый ID команды пользователя: {reply_to} для чата {chat_id}.")
    except Exception as e:
        bot_logger.error(f"Ошибка при сохранении last_user_command для чата {chat_id}: {e}")
    last_menu_id = get_data_field("last_menu_message", chat_id)
    if last_menu_id:
        try:
            bot.delete_message(chat_id, last_menu_id)
            update_data_field("last_menu_message", chat_id, None)
            bot_logger.debug(f"Удалено предыдущее меню для чата {chat_id}.")
        except Exception as e:
            bot_logger.warning(f"Ошибка при удалении старого сообщения: {e}")
    user = get_user(chat_id)
    if not user:
        bot_logger.error(f"Ошибка: пользователь {chat_id} не найден в format_settings()")
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
    menu_message_id = get_data_field("last_bot_message", chat_id)
    try:
        if menu_message_id:
            bot_logger.debug(f"Редактируем сообщение меню: chat_id={chat_id}, message_id={menu_message_id}")
            bot.edit_message_text(text, chat_id, menu_message_id, reply_markup=generate_format_keyboard())
        else:
            raise KeyError("Меню для редактирования отсутствует. Отправляем новое сообщение.")
    except Exception as e:
        bot_logger.warning(f"Ошибка при редактировании сообщения: {e}. Отправляем новое сообщение.")
        try:
            msg = bot.send_message(chat_id, text, reply_markup=generate_format_keyboard(), reply_to_message_id=reply_to)
            update_data_field("last_bot_message", chat_id, msg.message_id)
            bot_logger.debug(f"Новое сообщение меню отправлено: message_id={msg.message_id}")
        except Exception as send_error:
            bot_logger.error(f"Ошибка при отправке нового сообщения: {send_error}")


@safe_execute
@bot.callback_query_handler(func=lambda call: call.data == "return_to_format_settings")
def return_to_format_settings(call):
    """Возвращает пользователя в меню единиц измерения без обновления last_user_command."""
    chat_id = call.message.chat.id
    user = get_user(chat_id)
    if not user:
        bot_logger.error(f"Ошибка: пользователь {chat_id} не найден.")
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
        bot_logger.debug(f"Сообщение с меню единиц измерения успешно обновлено для чата {chat_id}.")
    except Exception as e:
        bot_logger.warning(f"Ошибка при обновлении меню единиц измерения: {e}")


@safe_execute
@bot.callback_query_handler(func=lambda call: call.data == "format_settings")
def format_settings_callback(call):
    """Обработчик кнопки 'Сохранить', возвращает в меню формата данных"""
    format_settings(call.message)


#ФУНКЦИИ В РАЗРАБОТКЕ
@safe_execute
def feature_in_development(message):
    """Временный обработчик для уведомления о разработке"""
    (message)
    chat_id = message.chat.id
    delete_last_menu_message(chat_id)
    feature_name = "профиля" if message.text == "🎭 Профиль" else "друзей"
    bot.reply_to(message, f"‼️ Функция {feature_name} всё ещё в разработке!\n\nСледите за обновлениями!")
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
            bot_logger.debug(f"Удалено сообщение из last_settings_command: {last_settings_message_id} для чата {chat_id}")
        except Exception as e:
            bot_logger.warning(f"Ошибка при удалении сообщения из last_settings_command: {e}")
    try:
        bot.delete_message(chat_id, message.message_id)
    except Exception as e:
        bot_logger.warning(f"Ошибка при удалении сообщения '↩ Назад': {e}")
    send_main_menu(chat_id)


@safe_execute
@bot.message_handler(func=lambda message: message.text == "🌦 Погодные данные")
def weather_data_settings(message):
    """Обработчик кнопки 'Погодные данные' в настройках"""
    user = get_user(message.from_user.id)
    chat_id = message.chat.id
    delete_last_menu_message(chat_id)         
    update_data_field("last_user_command", chat_id, message.message_id)
    bot_logger.debug(f"Сохранён ID команды: {message.message_id} для чата {chat_id}")
    if not user:
        bot.send_message(message.chat.id, "Ошибка: пользователь не найден.")
        return
    bot_logger.debug(f"Тип user: {type(user)}. Данные: {user}")
    text = "Выберите данные, которые вы хотите видеть при получении погоды:"
    try:
        keyboard = generate_weather_data_keyboard(user)
        bot.send_message(chat_id, text, reply_markup=keyboard, reply_to_message_id=message.message_id)
    except Exception as e:
        bot_logger.error(f"Ошибка в weather_data_settings: {e}")


@safe_execute
@bot.callback_query_handler(func=lambda call: call.data.startswith("toggle_weather_param_"))
def toggle_weather_param(call):
    """Обработчик изменения отображаемых данных в прогнозе"""
    chat_id = call.message.chat.id
    user = get_user(call.from_user.id)
    param = call.data.replace("toggle_weather_param_", "")

    # Логирование типа user
    bot_logger.debug(f"Тип user перед обработкой: {type(user)}. Данные: {user}")

    if not user:
        bot_logger.error(f"Пользователь с ID {call.from_user.id} не найден.")
        return 

    try:
        current_params = decode_tracked_params(user.tracked_weather_params)
        bot_logger.debug(f"Декодированные параметры: {current_params}")
    except Exception as e:
        bot_logger.error(f"Ошибка декодирования параметров пользователя {user.user_id}: {e}")
        current_params = {
            "humidity": True,
            "pressure": True,
            "visibility": True,
            "wind_speed": True,
            "description": True,
            "temperature": True,
            "precipitation": True
        }

    if param in current_params:
        current_params[param] = not current_params[param] 
    else:
        bot_logger.warning(f"Неизвестный параметр {param} для пользователя {user.user_id}")
        return

    try:
        update_user(user.user_id, tracked_weather_params=json.dumps(current_params))

        user = get_user(call.from_user.id)
        bot_logger.debug(f"Обновлённые данные пользователя: {user}")

        new_keyboard = generate_weather_data_keyboard(user)  
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=new_keyboard)
    except Exception as e:
        bot_logger.warning(f"Ошибка при обновлении tracked_weather_params для пользователя {user.user_id}: {e}")

@safe_execute
@bot.message_handler(func=lambda message: message.text in menu_actions)
def menu_handler(message):
    menu_actions[message.text](message)

menu_actions = {
    "🌎 Узнать погоду": weather,
    "📅 Прогноз погоды": forecast_menu_handler,
    "⚙️ Настройки": lambda msg: send_settings_menu(msg.chat.id),
    "👥 Друзья": feature_in_development,
    "🎭 Профиль": feature_in_development,
    "🏙 Изменить город": changecity,
    "🔔 Уведомления": notification_settings,
    "↩ Назад": settings_back_to_main_menu,
    "📏 Единицы измерения": lambda msg: format_settings(msg),
    "🌦 Погодные данные": generate_weather_data_keyboard
}

@safe_execute
@bot.message_handler(commands=['help'])
def help_command(message):
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
        start(message)
        return
    if city.startswith("/") or not city:
        error_reply("‼️ Отправьте название города, а не команду!")
        try:
            bot.delete_message(chat_id, message.message_id)
            bot_logger.debug(f"Удалено сообщение пользователя с новым городом: {message.message_id}")
        except Exception as e:
            bot_logger.warning(f"Не удалось удалить сообщение пользователя: {e}")
        return
    if not re.match(r'^[A-Za-zА-Яа-яЁё\s\-]+$', city):
        error_reply("‼️ Название города может содержать только буквы, пробелы и дефисы!")
        try:
            bot.delete_message(chat_id, message.message_id)
            bot_logger.debug(f"Удалено сообщение пользователя с новым городом: {message.message_id}")
        except Exception as e:
            bot_logger.warning(f"Не удалось удалить сообщение пользователя: {e}")
        return
    updated = update_user_city(user_id, city, message.from_user.username)
    success_text = (f"Теперь ваш основной город — {city}!"
                    if updated else f"‼️ Ваш основной город уже установлен: {city}.")
    last_menu_id = get_data_field("last_menu_message", chat_id)
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=last_menu_id,
            text=success_text,
            parse_mode="HTML"
        )
        update_data_field("last_menu_message", chat_id, None)
    except Exception as e:
        bot_logger.warning(f"Не удалось отредактировать сообщение: {e}")
        bot.reply_to(message, success_text)
    try:
        bot.delete_message(chat_id, message.message_id)
        bot_logger.debug(f"Удалено сообщение пользователя с новым городом: {message.message_id}")
    except Exception as e:
        bot_logger.warning(f"Не удалось удалить сообщение пользователя: {e}")
    if show_menu:
        send_settings_menu(chat_id)

@safe_execute
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
            bot_logger.warning(f"Не удалось отредактировать сообщение об ошибке: {e}")
            bot.register_next_step_handler(message, process_new_city_registration)
            return
        bot.register_next_step_handler(message, process_new_city_registration)

    # Проверки на валидность города
    if city == "/start":
        start(message)
        return
    if city.startswith("/") or not city:
        error_reply(f"‼️ Отправьте название города, а не команду!")
        try:
            bot.delete_message(chat_id, message.message_id)
            bot_logger.debug(f"Удалено сообщение пользователя с новым городом: {message.message_id}")
        except Exception as e:
            bot_logger.warning(f"Не удалось удалить сообщение пользователя: {e}")
        return
    if not re.match(r'^[A-Za-zА-Яа-яЁё\s\-]+$', city):
        error_reply(f"‼️ Название города может содержать только буквы, пробелы и дефисы!")
        try:
            bot.delete_message(chat_id, message.message_id)
            bot_logger.debug(f"Удалено сообщение пользователя с новым городом: {message.message_id}")
        except Exception as e:
            bot_logger.warning(f"Не удалось удалить сообщение пользователя: {e}")
        return

    # Обновление города пользователя
    updated = update_user_city(user_id, city, message.from_user.username)
    success_text = f"Добро пожаловать, {message.from_user.first_name}!\n\nТеперь ваш основной город — {city}."
    last_bot_msg_id = get_data_field("last_bot_message", chat_id)
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=last_bot_msg_id,
            text=success_text,
            parse_mode="HTML"
        )
        update_data_field("last_bot_message", chat_id, None)  # Сбрасываем ID после завершения
    except Exception as e:
        bot_logger.warning(f"Не удалось отредактировать сообщение: {e}")

    try:
        bot.delete_message(chat_id, message.message_id)
        bot_logger.debug(f"Удалено сообщение пользователя с новым городом: {message.message_id}")
    except Exception as e:
        bot_logger.warning(f"Не удалось удалить сообщение пользователя: {e}")

    # Переход в главное меню после успешной регистрации
    send_main_menu(chat_id)

@safe_execute
@bot.callback_query_handler(func=lambda call: call.data in ["change_temp_unit", "change_pressure_unit", "change_wind_speed_unit"])
def change_unit_menu(call):
    chat_id = call.message.chat.id
    user = get_user(call.from_user.id)

    unit_type = call.data[len("change_"):-len("_unit")]
    display_names = {
         "temp": "температуры",
         "pressure": "давления",
         "wind_speed": "скорости ветра"
    }
    display_text = display_names.get(unit_type, unit_type)

    current_unit = getattr(user, f"{unit_type}_unit", "N/A")

    try:
        bot.edit_message_text(
            f"Выберите единицу измерения {display_text}:",
            chat_id,
            call.message.message_id,
            reply_markup=generate_unit_selection_keyboard(current_unit, unit_type)
        )
    except Exception as e:
        bot_logger.warning(f"Ошибка при редактировании сообщения для изменения единицы измерения: {e}")

    update_data_field("last_bot_message", chat_id, call.message.message_id)

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


#ПРОПУСК СТАРЫХ СООБЩЕНИЙ
def clear_old_updates():
    """Пропускает старые сообщения, полученные до запуска бота."""
    updates = bot.get_updates(offset=-1)
    if updates:
        last_update_id = updates[-1].update_id
        bot_logger.info(f"Сброшены старые обновления до offset {last_update_id + 1}")

#ЗАПУСК БОТА
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
