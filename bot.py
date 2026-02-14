# ИМПОРТЫ
import json
import logging
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from zoneinfo import ZoneInfo

import requests
import telebot
from dotenv import load_dotenv
from telebot import types

from texts import TEXTS
from weather import get_weather, resolve_city_from_coords, fetch_today_forecast, fetch_tomorrow_forecast
from logic import (
    # users / storage
    get_user, save_user, update_user, update_user_city, update_user_unit,

    # texts / i18n
    get_text, get_translation_dict, get_user_lang,

    # forecast / formatting
    format_forecast, get_today_forecast, get_tomorrow_forecast, get_weekly_forecast_data,
    get_weather_summary_description, 

    # units / decoding
    decode_tracked_params, decode_notification_settings,
    convert_temperature, convert_pressure, convert_wind_speed, get_wind_direction,

    # ui keyboards (генераторы)
    generate_language_keyboard, generate_forecast_keyboard, generate_format_keyboard,
    generate_notification_settings_keyboard, generate_unit_selection_keyboard,
    generate_weather_data_keyboard, generate_language_keyboard,

    # json-store helpers
    get_data_field, update_data_field,

    # misc
    safe_execute, log_action,
)


#ШИФРОВАНИЕ
load_dotenv()


#ПЕРЕМЕННЫЕ
bot_start_time = time.time()
rounded_time = datetime.fromtimestamp(round(bot_start_time), timezone.utc)

#КОНСТАНТЫ
COUNTRY_CODES = ["KZ", "RU", "US", "DE", "FR", "IT", "CN", "KR", "JP"]

CITY_QUERY_BY_COUNTRY = {
    "KZ": ["Almaty", "Astana", "Shymkent", "Karaganda", "Aktobe"],
    "RU": ["Moscow", "Saint Petersburg", "Kazan", "Novosibirsk", "Yekaterinburg"],
    "US": ["New York", "Los Angeles", "Chicago", "Miami", "San Francisco"],
    "DE": ["Berlin", "Munich", "Hamburg", "Frankfurt", "Cologne"],
    "FR": ["Paris", "Marseille", "Lyon", "Toulouse", "Nice"],
    "IT": ["Rome", "Milan", "Naples", "Turin", "Florence"],
    "CN": ["Beijing", "Shanghai", "Guangzhou", "Shenzhen", "Chengdu"],
    "KR": ["Seoul", "Busan", "Incheon", "Daegu", "Daejeon"],
    "JP": ["Tokyo", "Osaka", "Kyoto", "Yokohama", "Sapporo"],
}

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
@bot.message_handler(func=lambda m: getattr(m, "pinned_message", None) is not None)
def _delete_pin_service_message(message):
    try:
        # чистим только в личных чатах
        if getattr(message.chat, "type", None) != "private":
            return
        bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except Exception as e:
        bot_logger.debug(f"Не удалось удалить системное сообщение о закреплении: {e}")


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("citypick_")
    and (get_data_field("citypick_flow", call.message.chat.id) not in ("reg", "chg"))
)
def legacy_citypick_guard(call):
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id, "Меню устарело. Откройте /start или Настройки → Изменить город.")
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except Exception:
        pass


def track_bot_message(message):
    """Запоминает последнее отправленное сообщение от бота."""
    update_data_field("last_bot_message", message.chat.id, message.message_id)

# def start_city_picker(chat_id: int, lang: str, flow: str):
#     """
#     Запускает выбор города и ОБЯЗАТЕЛЬНО сохраняет ID сообщения,
#     чтобы потом его можно было удалить.
#     """
#     last_msg_id = get_data_field("last_bot_message", chat_id)
#     safe_delete(chat_id, last_msg_id)

#     update_data_field("citypick_flow", chat_id, flow)

#     text = get_text("citypick_select_city", lang)  
    
#     kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    
#     btn_manual = types.KeyboardButton(get_text("citypick_btn_manual", lang)) 
#     btn_geo = types.KeyboardButton(get_text("citypick_btn_geo", lang), request_location=True)
    
#     kb.add(btn_geo, btn_manual)
    
#     msg = bot.send_message(chat_id, text, reply_markup=kb)
    
#     update_data_field("last_bot_message", chat_id, msg.message_id)
    
#     bot.register_next_step_handler(msg, process_new_city_registration)

def start_city_picker(chat_id: int, lang: str, flow: str):
    """
    Запускает выбор города, удаляя предыдущее сообщение (выбор cязыка).
    """
    # 1. Удаляем предыдущее сообщение (это было "Выберите язык")
    last_msg_id = get_data_field("last_bot_message", chat_id)
    safe_delete(chat_id, last_msg_id)

    update_data_field("citypick_flow", chat_id, flow)

    # Формируем новое сообщение
    text = get_text("citypick_select_country", lang)
    # Предполагаем, что у тебя есть функция build_country_kb
    kb = build_country_kb(lang, flow=flow) 

    # Отправляем "Выберите страну/город"
    msg = bot.send_message(chat_id, text, reply_markup=kb)
    
    # ЗАПОМИНАЕМ ID сообщения "Выберите город"
    update_data_field("last_bot_message", chat_id, msg.message_id)


def build_country_kb(lang: str, flow: str = "reg") -> types.InlineKeyboardMarkup:
    countries_map = get_translation_dict("countries", lang)
    kb = types.InlineKeyboardMarkup(row_width=2)

    buttons = []
    for code in COUNTRY_CODES:
        label = countries_map.get(code, code)
        buttons.append(types.InlineKeyboardButton(label, callback_data=f"citypick_country_{code}"))
    kb.add(*buttons)

    kb.add(
        types.InlineKeyboardButton(get_text("citypick_btn_manual", lang), callback_data="citypick_manual"),
        types.InlineKeyboardButton(get_text("citypick_btn_geo", lang), callback_data="citypick_geo"),
    )

    # ✅ Кнопка отмены — только при смене города
    if flow == "chg":
        kb.add(types.InlineKeyboardButton(get_text("btn_cancel", lang), callback_data="cancel_changecity"))

    return kb

def build_city_kb(lang: str, country_code: str, flow: str = "reg") -> types.InlineKeyboardMarkup:
    cities_tr = get_translation_dict("cities_by_country", lang)
    cities = cities_tr.get(country_code) or CITY_QUERY_BY_COUNTRY.get(country_code, [])

    kb = types.InlineKeyboardMarkup(row_width=2)

    for i, city_name in enumerate(cities):
        kb.add(types.InlineKeyboardButton(city_name, callback_data=f"citypick_city_{country_code}_{i}"))

    # нижний ряд: назад + (опционально) отмена
    kb.add(types.InlineKeyboardButton(get_text("citypick_btn_back", lang), callback_data="citypick_back"))

    if flow == "chg":
        kb.add(types.InlineKeyboardButton(get_text("btn_cancel", lang), callback_data="cancel_changecity"))

    return kb

@bot.callback_query_handler(func=lambda call: call.data.startswith("citypick_country_"))
def citypick_country(call):
    chat_id = call.message.chat.id
    user_id = call.from_user.id
    user = require_registered_user(user_id, chat_id, "ru")
    if not user:
        bot.answer_callback_query(call.id)
        return
    lang = get_user_lang(user)

    country_code = call.data.replace("citypick_country_", "").strip().upper()
    flow = get_data_field("citypick_flow", chat_id) or "chg"
    kb = build_city_kb(lang, country_code, flow=flow)

    bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=get_text("citypick_select_city", lang),
        reply_markup=kb
    )
    bot.answer_callback_query(call.id)



@bot.callback_query_handler(func=lambda call: call.data.startswith("citypick_city_"))
def citypick_city(call):
    chat_id = call.message.chat.id
    user_id = call.from_user.id
    user = require_registered_user(user_id, chat_id, "ru")
    if not user:
        bot.answer_callback_query(call.id)
        return
    lang = get_user_lang(user)

    # citypick_city_KZ_0
    parts = call.data.split("_")
    country_code = parts[2].upper()
    idx = int(parts[3])

    cities_tr = get_translation_dict("cities_by_country", lang)
    cities = cities_tr.get(country_code) or CITY_QUERY_BY_COUNTRY.get(country_code, [])

    if not cities or idx < 0 or idx >= len(cities):
        bot.answer_callback_query(call.id, "⚠ City list is empty / index error")
        return

    city_name = cities[idx]

    update_user_city(user_id, city_name, call.from_user.username)

    flow = get_data_field("citypick_flow", chat_id) or ("reg" if not user.preferred_city else "chg")

    # ✅ при смене города: "эхо" города -> сразу удалить
    if flow == "chg":
        try:
            echo_msg = bot.send_message(chat_id, city_name)
            bot.delete_message(chat_id, echo_msg.message_id)
        except Exception:
            pass

    # финальный текст
    if flow == "reg":
        text = get_text("greet_success_end", lang).format(name=call.from_user.first_name, city=city_name)
    else:
        text = get_text("changecity_success_update", lang).format(city=city_name)

    # удалить inline-сообщение с выбором города
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except Exception:
        pass

    bot.send_message(chat_id, text, parse_mode="HTML", disable_web_page_preview=True)
    refresh_daily_forecast(user_id)

    send_main_menu(chat_id)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "citypick_back")
def citypick_back(call):
    chat_id = call.message.chat.id
    user_id = call.from_user.id
    user = require_registered_user(user_id, chat_id, "ru")
    if not user:
        bot.answer_callback_query(call.id)
        return
    lang = get_user_lang(user)

    flow = get_data_field("citypick_flow", chat_id) or "chg"
    kb = build_country_kb(lang, flow=flow)
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=get_text("citypick_select_country", lang),
        reply_markup=kb
    )
    bot.answer_callback_query(call.id)

def require_registered_user(user_id: int, chat_id: int, lang_fallback: str = "ru"):
    """
    Возвращает user, если он есть в БД. Если пользователя нет — просит пройти /start и возвращает None.
    """
    user = get_user(user_id)
    if not user:
        bot.send_message(chat_id, get_text("error_user_not_found_start", lang_fallback))
        return None
    return user

@bot.callback_query_handler(func=lambda call: call.data == "citypick_manual")
def citypick_manual(call):
    chat_id = call.message.chat.id
    user_id = call.from_user.id
    user = require_registered_user(user_id, chat_id, "ru")
    if not user:
        bot.answer_callback_query(call.id)
        return
    lang = get_user_lang(user)

    # важно: не отправляем “Привет… отправьте координаты…” — это неуместно на manual
    prompt = get_text("changecity_prompt", lang) if user and user.preferred_city else get_text("greet_new_manual_prompt", lang)
    msg = bot.send_message(chat_id, prompt)

    bot.register_next_step_handler(msg, process_city_manual_input)
    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda m: bool(m.text) and not m.text.startswith("/"))
def handle_all_messages(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    user = require_registered_user(user_id, chat_id, "ru")
    if not user:
        return
    lang = get_user_lang(user)
    current_menu_actions = get_menu_actions(lang)

    if message.date < bot_start_time:
        return
    if message.text in current_menu_actions:
        current_menu_actions[message.text](message)
        return

    bot_logger.info(f"▸ Пользователь {user_id} отправил неизвестное сообщение: {message.text}")
    bot.send_message(chat_id, get_text("unknown_command", lang))
    send_main_menu(chat_id)

@safe_execute
def process_city_manual_input(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    user = get_user(user_id)
    lang = get_user_lang(user) if user else "ru"

    if not message.text:
        bot.send_message(chat_id, get_text("error_no_input", lang))
        return

    city = message.text.strip()
    if city.startswith("/"):
        bot.send_message(chat_id, get_text("error_invalid_city_command", lang))
        return

    updated = update_user_city(user_id, city, message.from_user.username)

    flow = get_data_field("citypick_flow", chat_id) or ("reg" if not user or not user.preferred_city else "chg")
    if flow == "reg":
        text = get_text("greet_success_end", lang).format(name=message.from_user.first_name, city=city)
    else:
        text = get_text("changecity_success_update", lang).format(city=city)

    bot.send_message(chat_id, text, parse_mode="HTML", disable_web_page_preview=True)
    refresh_daily_forecast(user_id)
    send_main_menu(chat_id)

"""ОТПРАВКА МЕНЮ"""
def menu_option(user_id, reply_markup=None):
    user = get_user(user_id)
    lang = get_user_lang(user)

    menu_message = bot.send_message(
        user_id,
        get_text("decorative_message_menu", lang),
        reply_markup=reply_markup
    )
    update_data_field("last_menu_message", user_id, menu_message.message_id)
    return menu_message.message_id



def settings_option(user_id, reply_markup=None):
    user = get_user(user_id)
    lang = get_user_lang(user)

    settings_opt = bot.send_message(
        user_id,
        get_text("decorative_message_settings", lang),
        reply_markup=reply_markup
    )
    update_data_field("last_menu_message", user_id, settings_opt.message_id)
    return settings_opt.message_id



def send_main_menu(user_id):
    """Отправка главного меню пользователю с учетом его языка."""
    delete_last_menu_message(user_id)
    
    # Получаем пользователя и его язык
    user = get_user(user_id)
    lang = get_user_lang(user)

    main_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    
    # Передаем переменную lang вместо строки "lang"
    main_keyboard.row(
        get_text("basic_keyboard_button_1", lang),
        get_text("basic_keyboard_button_2", lang)
    )
    main_keyboard.row(get_text("basic_keyboard_button_3", lang))
    
    menu_option(user_id, reply_markup=main_keyboard)



def send_settings_menu(user_id):
    """Отправка клавиатуры с меню настроек пользователю."""
    delete_last_menu_message(user_id)
    user = get_user(user_id)
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
    
    settings_option(user_id, reply_markup=settings_keyboard)



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

def safe_delete(chat_id, message_id):
    """Безопасное удаление сообщения без краша бота."""
    if not message_id:
        return
    try:
        bot.delete_message(chat_id, message_id)
    except Exception:
        pass


@safe_execute
@bot.callback_query_handler(func=lambda call: call.data in ["forecast_today", "forecast_tomorrow", "forecast_week"])
def forecast_handler(call):
    chat_id = call.message.chat.id
    user = get_user(call.from_user.id)
    menu_message_id = call.message.message_id

    if not user:
        bot.send_message(chat_id, get_text("error_user_not_found_start", "ru"))
        return

    lang = get_user_lang(user)

    if not user.preferred_city:
        bot.send_message(chat_id, get_text("city_not_set", lang))
        return

    lang = get_user_lang(user)  

    # 1. Получение данных и определение заголовков/описаний
    # Мы сразу определяем, какой заголовок и какую функцию для raw-данных использовать
    forecast_data = []
    title_text = ""
    summary_raw_data = None # Данные для текстового описания (дождь в 14:00 и т.д.)

    if call.data == "forecast_today":
        # Данные
        day_data = get_today_forecast(user.preferred_city, user)
        if day_data: forecast_data = [day_data]
        
        # Тексты
        title_text = get_text("daily_forecast_title", lang)
        summary_raw_data = fetch_today_forecast(user.preferred_city, lang=lang)

    elif call.data == "forecast_tomorrow":
        # Данные
        day_data = get_tomorrow_forecast(user.preferred_city, user) # Убедись, что эта функция импортирована
        if day_data: forecast_data = [day_data]
        
        # Тексты
        title_text = get_text("tomorrow_forecast_title", lang) or "Прогноз на завтра"
        summary_raw_data = fetch_tomorrow_forecast(user.preferred_city, lang=lang)

    else: # forecast_week
        # Данные
        forecast_data = get_weekly_forecast_data(user.preferred_city, user) # Используем get_weekly_forecast_data из logic
        
        # Тексты
        title_text = get_text("weekly_forecast_title", lang) or "Прогноз на неделю"
        summary_raw_data = None # Для недели детальное описание по часам не генерируем, чтобы не спамить

    # 2. Проверка на пустоту
    if not forecast_data or any(d is None for d in forecast_data):
        bot.send_message(chat_id, "⚠ Не удалось получить прогноз погоды.")
        return

    # 3. Генерация текста сообщения
    try:
        formatted_pages = []
        
        for day in forecast_data:
            # Если есть сырые данные для саммари (только для сегодня/завтра), генерируем текст
            current_summary = None
            if summary_raw_data:
                current_summary = get_weather_summary_description(summary_raw_data, user)
            
            # Вызываем НОВУЮ функцию форматирования
            # Она сама добавит заголовок, разделители и спойлеры
            text = format_forecast(day, user, title_text, summary_text=current_summary)
            formatted_pages.append(text)

        # Склеиваем всё через двойной отступ
        forecast_text = "\n\n".join(formatted_pages)
    
    except KeyError as e:
        bot_logger.error(f"Ключ отсутствует в данных прогноза: {e}")
        bot.send_message(chat_id, "⚠ Произошла ошибка при обработке прогноза.")
        send_main_menu(chat_id)
        return

    # 4. Отправка / Редактирование (как в старом коде)
    try:
        bot.edit_message_text(
            forecast_text,
            chat_id,
            menu_message_id,
            parse_mode="HTML",
            reply_markup=None # Или вернуть клавиатуру, если нужно
        )
        update_data_field("last_bot_message", chat_id, None)
    except Exception as e:
        bot_logger.warning(f"⚠ Не удалось отредактировать сообщение: {str(e)}")
        # Если не удалось отредактировать (например, слишком старое), шлем новое
        msg = bot.send_message(chat_id, forecast_text, parse_mode="HTML")
        update_data_field("last_bot_message", chat_id, msg.message_id)

    bot_logger.info(f"✅ Прогноз погоды ({call.data}) отправлен в чат {chat_id}.")
    
    # Переотправляем меню, чтобы оно было внизу
    send_main_menu(chat_id)


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
@safe_execute
def start(message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    # 2. Удаляем старое висящее меню бота (если было)
    last_msg_id = get_data_field("last_bot_message", chat_id)
    safe_delete(chat_id, last_msg_id)

    save_user(user_id, message.from_user.first_name)
    user = get_user(user_id)
    lang = get_user_lang(user)
    
    # Проверка на старого пользователя
    preferred_city = getattr(user, 'preferred_city', None)

    if preferred_city:
        # Старый пользователь
        text = get_text("greet_returning", lang).format(
            name=message.from_user.first_name,
            city=preferred_city
        )
        msg = bot.send_message(chat_id, text)
        update_data_field("last_bot_message", chat_id, msg.message_id)
        send_main_menu(chat_id)
    else:
        # Новый пользователь
        # is_registration=True убирает кнопку "Назад" и галочки
        keyboard = generate_language_keyboard(user, is_registration=True)
        text = f"Привет/Hello, {message.from_user.first_name}!\n\n🇷🇺 Выберите язык / 🇺🇸 Choose language:"
        
        msg = bot.send_message(chat_id, text, reply_markup=keyboard)
        # ЗАПОМИНАЕМ ID сообщения "Выберите язык"
        update_data_field("last_bot_message", chat_id, msg.message_id)


@bot.message_handler(commands=['weather'])
def handle_weather_command(message):
    chat_id = message.chat.id
    user = get_user(message.from_user.id)

    if not user:
        bot.reply_to(message, get_text("error_user_not_found_start", "ru"))
        return

    lang = get_user_lang(user)
    if not user.preferred_city:
        bot.reply_to(message, get_text("city_not_set", lang))
        return

    weather_data = get_weather(user.preferred_city, lang=lang)
    
    if weather_data:
        title = get_text("current_weather_title", lang) or "Текущая погода"
        
        msg = format_forecast(weather_data, user, title, summary_text=None)
        
        # 1. Удаляем старое меню (чтобы оно не висело выше)
        last_menu_id = get_data_field("last_menu_message", chat_id)
        if last_menu_id:
            try:
                bot.delete_message(chat_id, last_menu_id)
            except Exception:
                pass # Игнорируем, если сообщение уже удалено или слишком старое
            update_data_field("last_menu_message", chat_id, None)

        # 2. Отвечаем на команду
        bot.reply_to(message, msg, parse_mode="HTML")

        # 3. Отправляем меню заново вниз
        send_main_menu(chat_id)
        
    else:
        bot.reply_to(message, get_text("error_getting_weather", lang))


@safe_execute
@bot.message_handler(regexp=r"^(\/changecity|🏙 Изменить город|🏙 Change city|🏙 Қаланы өзгерту)$")
def cmd_changecity(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    user = require_registered_user(user_id, chat_id, "ru")
    if not user:
        return
    lang = get_user_lang(user)
    start_city_picker(chat_id, lang, flow="chg")


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
    update_data_field("citypick_flow", chat_id, None)
    send_settings_menu(chat_id)


@safe_execute
@bot.message_handler(func=lambda message: message.text in [
    get_text("notifications_menu_btn", "ru"),
    get_text("notifications_menu_btn", "en"),
    get_text("notifications_menu_btn", "kk")
])
def notification_settings(message):
    chat_id = message.chat.id
    user = get_user(message.from_user.id)

    if not user:
        bot.send_message(chat_id, get_text("error_user_not_found_start", "ru"))
        return

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
    chat_id = message.chat.id
    user = get_user(message.from_user.id)

    if not user:
        bot.send_message(chat_id, get_text("error_user_not_found_start", "ru"))
        return

    lang = get_user_lang(user)

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
    summary = get_weather_summary_description(
        fetch_today_forecast(user.preferred_city, lang=lang),
        user
    )

    forecast_message = format_forecast(
        raw_forecast,
        user,
        title,
        summary_text=summary, 
        is_daily_forecast=True  
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
    """
    Обновляет уже существующий ежедневный прогноз:
    - если есть last_daily_forecast -> редактирует его (это и есть "закреплённое")
    - если нет -> создаёт новый через refresh_daily_forecast()
    """
    last_forecast_id = get_data_field("last_daily_forecast", user_id)
    user = get_user(user_id)

    if not user or not user.preferred_city:
        bot_logger.error(f"▸ Ошибка: не найден пользователь {user_id} или его город.")
        return

    # если ещё не было прогноза — создаём и закрепляем
    if not last_forecast_id:
        refresh_daily_forecast(user_id)
        return

    lang = get_user_lang(user)

    raw_forecast = get_today_forecast(user.preferred_city, user)
    if not raw_forecast:
        bot_logger.warning(f"▸ `get_today_forecast` не вернула данные для {user.preferred_city}!")
        return

    title = get_text("daily_forecast_title", lang)

    summary = get_weather_summary_description(
        fetch_today_forecast(user.preferred_city, lang=lang),
        user
    )

    # ✅ ВАЖНО: format_forecast требует title_text
    forecast_message = format_forecast(
        raw_forecast,
        user,
        title,
        summary_text=summary, 
        is_daily_forecast=True
    )

    try:
        bot.edit_message_text(
            chat_id=user_id,
            message_id=last_forecast_id,
            text=forecast_message,
            parse_mode="HTML"
        )
        bot_logger.info(f"▸ Прогноз обновлён (edit) для пользователя {user_id}.")
        return
    except Exception as edit_error:
        # Если Telegram не дал редактировать (сообщение удалено/не найдено/и т.п.) —
        # тогда вынужденно пересоздаём и закрепляем заново.
        bot_logger.warning(f"▸ Не удалось отредактировать прогноз для {user_id}: {edit_error}")
        refresh_daily_forecast(user_id)


@safe_execute
def format_settings(param, reply_to=None):
    if isinstance(param, int):
        chat_id = param
    else:
        chat_id = param.chat.id
        reply_to = param.message_id if reply_to is None else reply_to

    update_data_field("last_user_command", chat_id, reply_to)

    last_menu_id = get_data_field("last_menu_message", chat_id)
    if last_menu_id:
        try:
            bot.delete_message(chat_id, last_menu_id)
        except Exception:
            pass
        update_data_field("last_menu_message", chat_id, None)

    user = get_user(chat_id)
    if not user:
        bot_logger.error(f"▸ Ошибка: пользователь {chat_id} не найден в format_settings()")
        bot.send_message(chat_id, get_text("error_user_not_found_start"))
        return

    lang = get_user_lang(user)
    unit_trans = get_translation_dict("unit_translations", lang)

    header = get_text("settings_units_header", lang)
    temp = get_text("settings_units_temp", lang).format(val=unit_trans["temp"].get(user.temp_unit, user.temp_unit))
    pressure = get_text("settings_units_pressure", lang).format(val=unit_trans["pressure"].get(user.pressure_unit, user.pressure_unit))
    wind = get_text("settings_units_wind", lang).format(val=unit_trans["wind_speed"].get(user.wind_speed_unit, user.wind_speed_unit))
    choose = get_text("settings_units_choose", lang)

    text = f"<b>{header}</b>\n<blockquote>{temp}\n{pressure}\n{wind}</blockquote>\n{choose}"

    menu_message_id = get_data_field("last_format_settings_menu", chat_id)
    keyboard = generate_format_keyboard(lang)

    try:
        if menu_message_id:
            bot.edit_message_text(text=text, chat_id=chat_id, message_id=menu_message_id, reply_markup=keyboard, parse_mode="HTML")
        else:
            raise KeyError
    except Exception:
        msg = bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, reply_to_message_id=reply_to, parse_mode="HTML")
        update_data_field("last_format_settings_menu", chat_id, msg.message_id)


@safe_execute
@bot.callback_query_handler(func=lambda call: call.data == "return_to_format_settings")
def return_to_format_settings(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id

    user = get_user(user_id)
    if not user:
        bot_logger.error(f"▸ Ошибка: пользователь {user_id} не найден.")
        bot.send_message(chat_id, get_text("error_user_not_found_start", "ru"))
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
        f"<blockquote>{temp}\n{pressure}\n{wind}</blockquote>\n"
        f"{choose}"
    )

    keyboard = generate_format_keyboard(lang)

    # Редактируем то сообщение, где сейчас находится inline-меню
    bot.edit_message_text(
        text=text,
        chat_id=chat_id,
        message_id=call.message.message_id,
        reply_markup=keyboard,
        parse_mode="HTML"
    )



@safe_execute
@bot.callback_query_handler(func=lambda call: call.data == "format_settings")
def format_settings_callback(call):
    """Обработчик кнопки 'Сохранить', возвращает в меню формата данных"""
    format_settings(call.message)


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
@bot.message_handler(func=lambda message: message.text in [
    get_text("menu_language", "ru"),
    get_text("menu_language", "en"),
    get_text("menu_language", "kk")
])
def language_settings(message):
    chat_id = message.chat.id
    user = get_user(message.from_user.id)

    if not user:
        bot.send_message(chat_id, get_text("error_user_not_found_start", "ru"))
        return

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
            
        start_city_picker(chat_id, new_lang_code, flow="reg")
    else:
        try:
            update_existing_forecast(user_id)
        except Exception:
            update_existing_forecast(user_id)
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
    user = get_user(user_id)
    lang = get_user_lang(user)

    # 1. Удаляем ВВОД ПОЛЬЗОВАТЕЛЯ ("Алматы")
    # Делаем паузу 0.5 сек, чтобы глаз успел заметить отправку, потом удаляем
    try:
        time.sleep(0.5) # Маленькая задержка для визуального подтверждения
        bot.delete_message(chat_id, message.message_id)
    except Exception:
        pass

    # 2. Удаляем сообщение бота "Выберите город" (которое мы сохранили в start_city_picker)
    last_bot_msg_id = get_data_field("last_bot_message", chat_id)
    safe_delete(chat_id, last_bot_msg_id)

    flow = get_data_field("citypick_flow", chat_id) or "reg"

    # Функция отправки ошибки (если ввели бред)
    def error_reply(error_key):
        if flow == "chg":
            prompt = get_text("citypick_manual_prompt_chg", lang)
        else:
            prompt = get_text("greet_new_manual_prompt", lang) # Тот самый ключ

        full_text = f"{get_text(error_key, lang)}\n\n{prompt}"
        
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        kb.add(types.KeyboardButton(text=get_text("citypick_btn_geo", lang), request_location=True))
        
        msg = bot.send_message(chat_id, full_text, reply_markup=kb)
        # Запоминаем сообщение с ошибкой, чтобы потом и его удалить
        update_data_field("last_bot_message", chat_id, msg.message_id)
        bot.register_next_step_handler(msg, process_new_city_registration)

    # --- Обработка ввода ---
    if message.location:
        city = resolve_city_from_coords(message.location.latitude, message.location.longitude)
        if not city:
            error_reply("error_city_not_found_coords")
            return
    elif message.text:
        city = message.text.strip()
        if city == "/start": 
            start(message)
            return
        if city.startswith("/") or not city:
            error_reply("error_invalid_city_command")
            return
        if not re.match(r'^[A-Za-zА-Яа-яЁё\s\-]+$', city):
            error_reply("error_invalid_city_chars")
            return
    else:
        error_reply("error_no_input")
        return

    # --- УСПЕХ ---
    update_user_city(user_id, city, message.from_user.username)
    
    if flow == "chg":
        success_text = get_text("citypick_success_chg", lang).format(city=city)
    else:
        success_text = get_text("citypick_success_reg", lang).format(city=city)

    # Отправляем сообщение успеха. Кнопок нет (Remove)
    msg = bot.send_message(
        chat_id, 
        success_text, 
        reply_markup=types.ReplyKeyboardRemove()
    )
    
    # Обнуляем last_bot_message, так как "цепочка меню" закончилась.
    # Это сообщение останется висеть как итог, пока новое меню не придет.
    update_data_field("last_bot_message", chat_id, None)

    refresh_daily_forecast(user_id)
    send_main_menu(chat_id)

@safe_execute
@bot.callback_query_handler(func=lambda call: call.data in ("open_settings", "back_to_settings"))
def open_settings_callback(call):
    chat_id = call.message.chat.id

    # закрываем "часики" сразу
    bot.answer_callback_query(call.id)

    # можно удалить текущее inline-сообщение (не обязательно, но аккуратно)
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except Exception:
        pass

    send_settings_menu(chat_id)

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

@safe_execute
def settings_back_to_main_menu(message):
    """Кнопка 'Назад/Выход' из настроек -> главное меню + удалить сообщение пользователя сразу."""
    chat_id = message.chat.id
    user_id = message.from_user.id

    user = require_registered_user(user_id, chat_id, "ru")
    if not user:
        return

    # ✅ удалить сообщение пользователя с нажатием кнопки
    try:
        bot.delete_message(chat_id, message.message_id)
    except Exception:
        pass

    # удалить декоративное меню настроек
    delete_last_menu_message(chat_id)

    # вернуть главное меню
    send_main_menu(chat_id)


@safe_execute
def weather_data_settings(message):
    """Открывает меню выбора отображаемых погодных параметров (inline)."""
    chat_id = message.chat.id
    user_id = message.from_user.id

    user = require_registered_user(user_id, chat_id, "ru")
    if not user:
        return

    lang = get_user_lang(user)

    delete_last_menu_message(chat_id)
    update_data_field("last_user_command", chat_id, message.message_id)

    keyboard = generate_weather_data_keyboard(user)
    text = get_text("weather_data_settings_text", lang) if "weather_data_settings_text" else "Выберите, какие параметры показывать:"

    bot.send_message(
        chat_id,
        text,
        reply_markup=keyboard,
        reply_to_message_id=message.message_id
    )

def get_menu_actions(lang="ru"):
    return {
        get_text("menu_weather_now", lang): handle_weather_command,
        get_text("menu_forecast", lang): forecast_menu_handler,
        get_text("menu_settings", lang): lambda msg: send_settings_menu(msg.chat.id),
        get_text("menu_change_city", lang): cmd_changecity,
        get_text("menu_notifications", lang): notification_settings,
        get_text("menu_back", lang): settings_back_to_main_menu,
        get_text("menu_units", lang): lambda msg: format_settings(msg),
        get_text("menu_weather_data", lang): weather_data_settings,
        get_text("menu_language", lang): language_settings,
    }

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