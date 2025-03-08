#ИМПОРТЫ
from telebot import types
from dotenv import load_dotenv
from logging.handlers import RotatingFileHandler
from logic import get_user, save_user
from logic import *
from weather import get_weather

import logging
import time
import os
import requests
import telebot
import re

#ШИФРОВАНИЕ
load_dotenv()
bot_start_time = time.time()

#ЛОГИРОВАНИЕ
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logging.info(f"Бот стартовал в {bot_start_time}.")

if not os.path.exists("logs"):
    os.makedirs("logs")

LOG_FILE = "logs/bot.log"
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"

file_handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
file_handler.setLevel(logging.DEBUG)

console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter(LOG_FORMAT))
console_handler.setLevel(logging.INFO)

logging.basicConfig(level=logging.DEBUG, handlers=[file_handler, console_handler])

#ДЕШИФРОВКА И ИДЕНТИФИКАЦИЯ ТОКЕНА БОТА
BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)

#СЛОВАРИ
last_menu_message = {}
conversation_id = {}

#УДАЛЕНИЕ МЕНЮ ПРОГНОЗА
def delete_forecast_menu(chat_id, menu_message_id):
    """Удаляет сообщение с forecast_menu, если оно есть."""
    try:
        bot.delete_message(chat_id, menu_message_id)
    except Exception as e:
        logging.warning(f"Ошибка при удалении forecast_menu: {e}")

#ОБРАБОТЧИКИ
@bot.message_handler(func=lambda message: not message.text.startswith("/") and message.text not in menu_actions)
def handle_all_messages(message):
    """Обрабатывает все сообщения""" 
    logging.debug(f"Получено сообщение: {message.text}.")
    user_id = message.from_user.id
    chat_id = message.chat.id
    active_sessions[user_id] = chat_id 

    if message.date < bot_start_time:
        logging.debug("🔴 Сообщение проигнорировано (старше времени запуска).")
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
        logging.error(f"Ошибка: нет активного chat_id для user_id {user_id}")
        return

    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    for row in buttons:
        keyboard.row(*row)

    bot.send_message(chat_id, text, reply_markup=keyboard)

def menu_option(chat_id, reply_markup=None):
    """Отправка декоративного сообщения при взаимодействии с главным меню"""
    menu_opt = bot.send_message(chat_id, "Выберите опцию:", reply_markup=reply_markup)
    last_menu_message[chat_id] = menu_opt.message_id
    return menu_opt.message_id

def settings_option(chat_id, reply_markup=None):
    """Отправка декоративного сообщения при взаимодействии с меню настроек"""
    settings_opt = bot.send_message(chat_id, "Выберите настройку:", reply_markup=reply_markup)
    last_menu_message[chat_id] = settings_opt.message_id
    return settings_opt.message_id

def send_main_menu(chat_id):
    main_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    main_keyboard.row("🌦 Узнать погоду", "📅 Прогноз погоды")
    main_keyboard.row("👥 Друзья", "🎭 Профиль")
    main_keyboard.row("⚙️ Настройки")
    loading_message = bot.send_message(chat_id, "Загрузка...")
    bot.delete_message(chat_id, loading_message.message_id)
    menu_option(chat_id, reply_markup=main_keyboard)

def send_settings_menu(chat_id):
    settings_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    settings_keyboard.row("🏙 Изменить город","🔔 Уведомления")
    settings_keyboard.row("📏 Единицы измерения")
    settings_keyboard.row("↩ Назад")
    loading_message = bot.send_message(chat_id, "Загрузка...")
    bot.delete_message(chat_id, loading_message.message_id)
    settings_option(chat_id, reply_markup=settings_keyboard)

#ОБРАБОТЧИК ПРОГНОЗОВ ПОГОДЫ (СЕГОДНЯ/НЕДЕЛЯ)
@safe_execute
@bot.callback_query_handler(func=lambda call: call.data in ["forecast_today", "forecast_week"])
def forecast_handler(call):
    chat_id = call.message.chat.id
    user = get_user(call.from_user.id)
    menu_message_id = call.message.message_id

    if not user or not user.preferred_city:
        bot.send_message(chat_id, "Сначала укажите ваш город в настройках!")
        return

    if call.data == "forecast_today":
        forecast_data = [get_today_forecast(user.preferred_city)]
    else:
        forecast_data = get_weekly_forecast(user.preferred_city) 

    if not forecast_data or None in forecast_data:
        bot.send_message(chat_id, "Не удалось получить прогноз погоды.")
        return

    forecast_text = "\n".join([
        f"📆 *{day['date']}, {day['day_name']}*\n\n"
        f"▸ Погода: {day['description']}\n"
        f"▸ Осадки: {day['precipitation']}%\n"
        f"▸ Температура: от {round(day['temp_min'])}°{user.temp_unit} "
        f"до {round(day['temp_max'])}°{user.temp_unit}\n"
        f"▸ Давление: {round(day['pressure'])} {user.pressure_unit}\n"
        f"▸ Скорость ветра: {round(day['wind_speed'])} {user.wind_speed_unit}\n"
        for day in forecast_data
    ]) if call.data == "forecast_today" else "\n".join([
        f"✦ *{day['date']}, {day['day_name']}*\n"
        f"▸ Погода: {day['description']}\n"
        f"▸ Осадки: {day['precipitation']}%\n"
        f"▸ Температура: от {round(day['temp_min'])}°{user.temp_unit} "
        f"до {round(day['temp_max'])}°{user.temp_unit}\n"
        f"▸ Давление: {round(day['pressure'])} {user.pressure_unit}\n"
        f"▸ Ветер: {round(day['wind_speed'])} {user.wind_speed_unit}\n"
        for day in forecast_data
    ])

    bot.delete_message(chat_id, call.message.message_id)

    bot.send_message(chat_id, forecast_text, parse_mode="Markdown")
    send_main_menu(chat_id)


#НАВИГАЦИОННЫЕ ОБРАБОТЧИКИ
@safe_execute
@bot.callback_query_handler(func=lambda call: call.data == "back_to_settings")
def back_to_settings_callback(call):
    """Обработчик возврата в меню настроек"""
    chat_id = call.message.chat.id
    bot.delete_message(chat_id, call.message.message_id)
    
    if chat_id in last_menu_message:
        try:
            bot.delete_message(chat_id, last_menu_message[chat_id]) 
        except Exception as e:
            logging.warning(f"Ошибка при удалении сообщения: {e}")

    send_settings_menu(chat_id)

@safe_execute
@bot.message_handler(func=lambda message: message.text == "⚙️ Настройки")
def settings_menu_handler(message):
    """Обработчик вызова меню настроек через сообщение"""
    chat_id = message.chat.id

    if chat_id in last_menu_message:
        try:
            bot.delete_message(chat_id, last_menu_message[chat_id])
            del last_menu_message[chat_id] 
        except Exception as e:
            logging.warning(f"Ошибка при удалении старого сообщения: {e}")

    send_settings_menu(chat_id)

@safe_execute
@bot.callback_query_handler(func=lambda call: call.data == "back_to_main")
def back_to_main_callback(call):
    """Обработчик возврата в главное меню"""
    chat_id = call.message.chat.id
    bot.delete_message(chat_id, call.message.message_id)

    # Удаляем "Выберите настройку", если оно осталось
    if chat_id in last_menu_message:
        try:
            bot.delete_message(chat_id, last_menu_message[chat_id])
            del last_menu_message[chat_id]  # Чистим ID из словаря
        except Exception as e:
            logging.warning(f"Ошибка при удалении 'Выберите настройку': {e}")

    # Теперь отправляем главное меню
    send_main_menu(chat_id)

#КОМАНДЫ
#ОБРАБОТКА /start
@bot.message_handler(commands=['start'])
def start(message):
    """Регистрация пользователя/запуск бота"""
    log_action("Получена команда /start", message)
    user_id = message.from_user.id
    user = get_user(user_id)

    if user and user.preferred_city:
        back_reply_text = (f"С возвращением, {message.from_user.first_name}!\n"
                      f"Ваш основной город — {user.preferred_city}.")
        bot.reply_to(message, back_reply_text)
        logging.debug(f"Пользователь с ID {user_id} уже зарегистрирован.")
        send_main_menu(message.chat.id)
    else:
        save_user(user_id, message.from_user.first_name)
        new_reply_text = (f"Привет, {message.from_user.first_name}!\n"
                      "Для того, чтобы начать получать информацию о погоде — укажите свой город.")
        msg = bot.reply_to(message, new_reply_text)
        bot.register_next_step_handler(msg, lambda m: process_new_city(m, show_menu=True)) 
        logging.debug(f"Новый пользователь {user_id}. Запрошен город.")
    

@safe_execute
@bot.message_handler(commands=['weather'])
def weather(message):
    """Отправка текущей погоды в городе пользователя"""
    user_id = message.from_user.id
    user = get_user(user_id)
    logging.debug(f"Получена команда /weather от {user_id}.")
    if not user or not user.preferred_city:
        logging.debug(f"У пользователя с ID {user_id} не выбран город. Запрашиваем город.")
        reply = bot.reply_to(message, "Для начала укажите свой город!")
        bot.register_next_step_handler(reply, process_new_city)
        return
    
    if message.chat.id in last_menu_message:
        try:
            bot.delete_message(message.chat.id, last_menu_message[message.chat.id])
        except Exception as e:
            logging.warning(f"Не удалось удалить сообщение: {e}")

    weather_data = get_weather(user.preferred_city)
    if not weather_data:
        bot.reply_to(message, "Не удалось получить данные о погоде.")
        send_main_menu(message.chat.id)
        return
    
    weather_data = get_weather(user.preferred_city)
    if not weather_data:
        bot.reply_to(message, "Не удалось получить данные о погоде.")
        send_main_menu(message.chat.id)
        return
    

    weather_info = format_weather(
        weather_data["city_name"],
        weather_data["temp"],
        weather_data["description"],
        weather_data["humidity"],
        weather_data["wind_speed"],
        weather_data["pressure"],
        weather_data["visibility"],
        user.temp_unit, user.pressure_unit, user.wind_speed_unit
    )

    bot.reply_to(message, weather_info)
    send_main_menu(message.chat.id)

@safe_execute
@bot.message_handler(regexp=r"^(\/changecity|🏙 Изменить город)$")
def changecity(message):
    log_action("Получена команда /changecity", message)
    user_id = message.from_user.id
    user = get_user(user_id)
    chat_id = message.chat.id
    
    if chat_id in last_menu_message:
        try:
            bot.delete_message(chat_id, last_menu_message[chat_id])
            del last_menu_message[chat_id]
        except Exception as e:
            logging.warning(f"Ошибка при удалении 'Выберите настройку': {e}")

    reply_text = (f"▸ Ваш текущий город — {user.preferred_city}. \n\nВведите название нового города для обновления!"
                  if user and user.preferred_city else
                  "Вы ещё не указали свой город! \nУкажите новый город.")

    keyboard = types.InlineKeyboardMarkup()
    cancel_button = types.InlineKeyboardButton("✖ Отмена", callback_data="cancel_changecity")
    keyboard.add(cancel_button)

    reply = bot.reply_to(message, reply_text, reply_markup=keyboard)

    last_menu_message[chat_id] = reply.message_id

    bot.register_next_step_handler(reply, process_new_city, show_menu=True)

@safe_execute
@bot.callback_query_handler(func=lambda call: call.data == "cancel_changecity")
def cancel_changecity_callback(call):
    """Обработчик отмены смены города"""
    chat_id = call.message.chat.id

    bot.delete_message(chat_id, call.message.message_id)
    bot.clear_step_handler_by_chat_id(chat_id)
    send_settings_menu(chat_id)

@safe_execute
@bot.message_handler(func=lambda message: message.text == "🔔 Уведомления")
def notifications_settings(message):
    log_action("Пользователь открыл настройки уведомлений", message)
    chat_id = message.chat.id
    user_id = message.from_user.id
    user = get_user(user_id)

    if chat_id in last_menu_message:
        try:
            bot.delete_message(chat_id, last_menu_message[chat_id])
            del last_menu_message[chat_id]
        except Exception as e:
            logging.warning(f"Ошибка при удалении 'Выберите настройку': {e}")

    if not user:
        bot.send_message(chat_id, "Вы не зарегистрированы. Пожалуйста, начните с команды /start.")
        return
    
    user = get_user(user_id)
    
    current_status = "Включены ✅" if user.notifications_enabled else "Отключены ❌"
    status_message = (f"▸ Текущий статус: {current_status}.\n\n"
                        f"Какие уведомления вы будете получать?\n"
                        f"• Предупреждения о резких изменениях погоды в вашем городе.\n"
                        f"• Сообщения о статусе работы бота (например, технические работы).\n\n")

    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("✅ Включить уведомления", callback_data="enable_notifications"))
    keyboard.add(types.InlineKeyboardButton("❌ Отключить уведомления", callback_data="disable_notifications"))
    keyboard.add(types.InlineKeyboardButton("↩ Назад", callback_data="back_to_settings"))

    bot.reply_to(message, status_message, reply_markup=keyboard)

"""ВЫЗОВ МЕНЮ ВЫБОРА ПРОНОЗА ПОГОДЫ"""
@safe_execute
@bot.message_handler(regexp=r"^(\📅 Прогноз погоды|/weatherforecast)$")
def forecast_menu(message):
    """Выводит клавиатуру с выбором прогноза и передаёт ID сообщения дальше."""
    if message.chat.id in last_menu_message:
        try:
            bot.delete_message(message.chat.id, last_menu_message[message.chat.id])
        except Exception as e:
            logging.warning(f"Не удалось удалить сообщение: {e}")

    msg = bot.send_message(message.chat.id, "Выберите период прогноза:", reply_markup=generate_forecast_keyboard())
    return msg.message_id

@safe_execute
@bot.callback_query_handler(func=lambda call: call.data in ["enable_notifications", "disable_notifications"])
def toggle_notifications(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id  

    new_status = call.data == "enable_notifications"
    
    updated_status = toggle_user_notifications(user_id, new_status)

    if updated_status is None:
        bot.send_message(chat_id, "Для начала нужно указать город!\nВведите /start.")
        return

    current_status = "Включены ✅" if updated_status else "Отключены ❌" 

    log_action(f"Пользователь изменил уведомления: {current_status}", call.message)

    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("✅ Включить уведомления", callback_data="enable_notifications"))
    keyboard.add(types.InlineKeyboardButton("❌ Отключить уведомления", callback_data="disable_notifications"))
    keyboard.add(types.InlineKeyboardButton("↩ Назад", callback_data="back_to_settings"))

    try:
        bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                              text=f"▸ Текущий статус: {current_status}.\n\n"
                                    f"Какие уведомления вы будете получать?\n"
                                    f"• Предупреждения о резких изменениях погоды в вашем городе.\n"
                                    f"• Сообщения о статусе работы бота (например, технические работы).\n\n",
                              reply_markup=keyboard)
    except Exception as e:
        logging.warning(f"Ошибка при редактировании сообщения: {e}")

        try:
            bot.delete_message(chat_id, message_id)
        except Exception as e:
            logging.warning(f"Ошибка при удалении сообщения: {e}")

        bot.send_message(chat_id,
                         f"▸ Текущий статус: {current_status}.\n\n"
                            f"Какие уведомления вы будете получать?\n"
                            f"• Предупреждения о резких изменениях погоды в вашем городе.\n"
                            f"• Сообщения о статусе работы бота (например, технические работы).\n\n",
                         reply_markup=keyboard)

"""ОТКРЫТИЕ МЕНЮ ЕДИНИЦ ИЗМЕРЕНИЯ ДАННЫХ"""
@safe_execute
def format_settings(param, reply_to=None):
    """
    Редактирует сообщение меню единиц измерения.
    Если param – это объект сообщения, то используется его chat.id и message_id для reply_to.
    Если передан chat_id (int), то пытаемся взять reply_to из last_menu_message.
    """
    if isinstance(param, int):
        chat_id = param
        # Если reply_to не передали, попробуем взять его из last_menu_message
        if reply_to is None:
            reply_to = last_menu_message.get(chat_id)
    else:
        chat_id = param.chat.id
        if reply_to is None:
            reply_to = param.message_id

    logging.debug(f"format_settings вызван с chat_id={chat_id}, reply_to={reply_to}")
    
    user = get_user(chat_id)
    if not user:
        logging.error(f"Ошибка: пользователь {chat_id} не найден в format_settings()")
        bot.send_message(chat_id, "Ошибка: пользователь не найден. Попробуйте /start.")
        return

    text = (
        f"Сейчас ваши значения отображаются так:\n\n"
        f"▸ Температура: {user.temp_unit}\n"
        f"▸ Давление: {user.pressure_unit}\n"
        f"▸ Скорость ветра: {user.wind_speed_unit}\n\n"
        f"Выберите параметр для изменения:"
    )

    try:
        bot.edit_message_text(text, chat_id, reply_to, reply_markup=generate_format_keyboard())
        # Обновляем last_menu_message, чтобы сохранить id отредактированного сообщения
        last_menu_message[chat_id] = reply_to
    except Exception as e:
        logging.warning(f"Не удалось отредактировать сообщение: {e}")
        new_msg = bot.send_message(chat_id, text, reply_to_message_id=reply_to, reply_markup=generate_format_keyboard())
        last_menu_message[chat_id] = new_msg.message_id

@safe_execute
@bot.callback_query_handler(func=lambda call: call.data == "format_settings")
def format_settings_callback(call):
    """Обработчик кнопки 'Сохранить', возвращает в меню формата данных"""
    chat_id = call.message.chat.id
    format_settings(call.message)

@safe_execute
def feature_in_development(message):
    """Временный обработчик для уведомления о разработке"""
    chat_id = message.chat.id

    if chat_id in last_menu_message:
        try:
            bot.delete_message(chat_id, last_menu_message[chat_id])
            del last_menu_message[chat_id] 
        except Exception as e:
            logging.warning(f"Ошибка при удалении старого сообщения: {e}")

    feature_name = "профиля" if message.text == "🎭 Профиль" else "друзей"
    bot.reply_to(message, f"‼️ Функция {feature_name} всё ещё в разработке!\n\nСледите за обновлениями!")
    send_main_menu(chat_id)

@safe_execute
@bot.message_handler(func=lambda message: message.text in menu_actions)
def menu_handler(message):
    menu_actions[message.text](message)

menu_actions = {
    "🌦 Узнать погоду": weather,
    "📅 Прогноз погоды": forecast_menu,
    "⚙️ Настройки": lambda msg: send_settings_menu(msg.chat.id),
    "👥 Друзья": feature_in_development,
    "🎭 Профиль": feature_in_development,
    "🏙 Изменить город": changecity,
    "🔔 Уведомления": notifications_settings,
    "↩ Назад": lambda msg: send_main_menu(msg.chat.id),
    "📏 Единицы измерения": format_settings
}

@safe_execute
@bot.message_handler(commands=['help'])
def help_command(message):
    user_id = message.from_user.id
    user = get_user(user_id)
    logging.debug(f"Получена команда /help от пользователя с ID {user_id}.")
    
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
    user_id = message.from_user.id
    city = message.text.strip()

    def error_reply(text):
        """Отправляет сообщение об ошибке и запрашивает повторный ввод."""
        reply = bot.reply_to(message, text)
        bot.register_next_step_handler(reply, process_new_city, show_menu)

    if city == "/start":
        start(message)
        return
    if city.startswith("/") or not city:
        error_reply("Отправьте название города, а не команду!")
        return
    if not re.match(r'^[A-Za-zА-Яа-яЁё\s\-]+$', city):
        error_reply("Название города может содержать только буквы, пробелы и дефисы!")
        return

    updated = update_user_city(user_id, city, message.from_user.username)
    bot.reply_to(message, f"Теперь ваш основной город — {city}." if updated else f"Ваш основной город уже установлен: {city}.")

    if show_menu:
        send_settings_menu(message.chat.id)

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

    bot.edit_message_text(f"Выберите единицу измерения {display_text}:", 
                          chat_id, call.message.message_id, 
                          reply_markup=generate_unit_selection_keyboard(current_unit, unit_type))


@safe_execute
@bot.callback_query_handler(func=lambda call: call.data.startswith("set_"))
def set_unit(call):
    """Изменяет единицы измерения и обновляет inline-клавиатуру, оставаясь в меню до нажатия 'Сохранить'."""
@safe_execute
@bot.callback_query_handler(func=lambda call: call.data.startswith("set_"))
def set_unit(call):
    """Изменяет единицы измерения и обновляет inline-клавиатуру, оставаясь в меню до нажатия 'Сохранить'."""
    user_id = call.from_user.id
    chat_id = call.message.chat.id

    # Убираем префикс "set_"
    data = call.data[len("set_"):]  # например, для wind_speed: "wind_speed_unit_m/s"
    try:
        # Разбиваем по разделителю "_unit_"
        unit_type, new_unit = data.split("_unit_", 1)
    except Exception as e:
        logging.error(f"Ошибка при разборе callback_data: {call.data}, {e}")
        return

    logging.debug(f"set_unit: call.from_user.id={user_id}, data={call.data}, unit_type={unit_type}, new_unit={new_unit}")

    user = get_user(user_id)
    if not user:
        logging.error(f"Ошибка: пользователь {user_id} не найден в set_unit().")
        bot.send_message(chat_id, "Ошибка: пользователь не найден. Попробуйте /start.")
        return

    update_user_unit(user_id, unit_type, new_unit)
    logging.debug(f"Единицы {unit_type} изменены на {new_unit} для user_id={user_id}")

    # Обновляем клавиатуру с галочкой у выбранной опции
    user = get_user(user_id)
    new_keyboard = generate_unit_selection_keyboard(getattr(user, f"{unit_type}_unit"), unit_type)

    try:
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=new_keyboard)
    except Exception as e:
        logging.error(f"Ошибка при редактировании клавиатуры: {e}")


#ПРОПУСК СТАРЫХ СООБЩЕНИЙ
def clear_old_updates():
    """Пропускает старые сообщения, полученные до запуска бота."""
    updates = bot.get_updates(offset=-1)
    if updates:
        last_update_id = updates[-1].update_id
        logging.info(f"Сброшены старые обновления до offset {last_update_id + 1}")

#ЗАПУСК БОТА
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    logging.info("Бот запущен.")
    clear_old_updates()

    MAX_RETRIES = 10
    attempt = 1  

    while attempt <= MAX_RETRIES:
        try:
            logging.info(f"Попытка #{attempt}: Запускаем polling...")
            bot.polling(timeout=10, long_polling_timeout=10, allowed_updates=["message", "callback_query"])
        except requests.exceptions.ReadTimeout:
            logging.warning(f"Попытка #{attempt}: Read timeout. Перезапуск через 5 секунд...")
        except requests.exceptions.ConnectionError as e:
            logging.error(f"Попытка #{attempt}: Ошибка соединения: {e}. Перезапуск через 5 секунд...")
        except Exception as e:
            logging.critical(f"Попытка #{attempt}: Неизвестная ошибка: {e}. Перезапуск через 5 секунд...")
        finally:
            attempt += 1
            time.sleep(5)

    logging.critical("Достигнуто максимальное количество попыток! Бот остановлен.")
