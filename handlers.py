from bot import bot, send_main_menu, send_settings_menu
from logic import get_user, safe_execute, get_today_forecast, get_weekly_forecast, save_user, log_action, format_weather
from logic import toggle_user_notifications, update_user_city, generate_forecast_keyboard, generate_format_keyboard, generate_unit_selection_keyboard, update_user_unit
from weather import get_weather
from telebot import types

import logging
import re

#ОБРАБОТЧИК ПРОГНОЗОВ ПОГОДЫ (СЕГОДНЯ/НЕДЕЛЯ)
@safe_execute
@bot.callback_query_handler(func=lambda call: call.data in ["forecast_today", "forecast_week"])
def forecast_handler(call):
    chat_id = call.message.chat.id
    user = get_user(call.from_user.id)

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
        f"✦ *{day['date']}*\n"
        f"▸ Погода: {day['description']}\n"
        f"▸ Осадки: {day['precipitation']}%\n"
        f"▸ Температура: от {round(day['temp_min'])}°{user.temp_unit} "
        f"до {round(day['temp_max'])}°{user.temp_unit}\n"
        f"▸ Давление: {round(day['pressure'])} {user.pressure_unit}\n"
        f"▸ Ветер: {round(day['wind_speed'])} {user.wind_speed_unit}\n"
        for day in forecast_data
    ])

    bot.send_message(chat_id, f"📆 *Прогноз погоды:*\n\n{forecast_text}", parse_mode="Markdown")
    send_main_menu(chat_id)

#НАВИГАЦИОННЫЕ ОБРАБОТЧИКИ
@safe_execute
@bot.callback_query_handler(func=lambda call: call.data == "back_to_settings")
def back_to_settings_callback(call):
    chat_id = call.message.chat.id
    bot.delete_message(chat_id, call.message.message_id)
    if call.message.reply_to_message:
        bot.delete_message(chat_id, call.message.reply_to_message.message_id)  
    send_settings_menu(chat_id)

@safe_execute
@bot.callback_query_handler(func=lambda call: call.data == "settings_menu")
def settings_menu_callback(call):
    chat_id = call.message.chat.id
    bot.delete_message(chat_id, call.message.message_id)
    send_settings_menu(chat_id)

@safe_execute
@bot.callback_query_handler(func=lambda call: call.data == "back_to_main")
def back_to_main_callback(call):
    chat_id = call.message.chat.id
    bot.delete_message(chat_id, call.message.message_id)
    send_main_menu(chat_id)

#КОМАНДЫ
@safe_execute
@bot.message_handler(commands=['start'])
def start(message):
    log_action("Получена команда /start", message)
    user_id = message.from_user.id
    user = get_user(user_id)

    if user and user.preferred_city:
        reply_text = (f"С возвращением, {message.from_user.first_name}!\n"
                      f"Ваш основной город — {user.preferred_city}.")
        send_main_menu(message.chat.id)
        logging.debug(f"Пользователь с ID {user_id} уже зарегистрирован.")

    else:
        save_user(user_id, message.from_user.first_name)
        msg = bot.reply_to(message, 
                           f"Привет, {message.from_user.first_name}!\n"
                           "Для того, чтобы начать получать информацию о погоде — укажите свой город.")
        bot.register_next_step_handler(msg, process_new_city)
        logging.debug(f"Новый пользователь {user_id}. Запрошен город.")

@safe_execute
@bot.message_handler(commands=['weather'])
def weather(message):
    user_id = message.from_user.id
    user = get_user(user_id)

    if not user or not user.preferred_city:
        reply = bot.reply_to(message, "Для начала укажите свой город!")
        bot.register_next_step_handler(reply, process_new_city)
        return

    weather_data = get_weather(user.preferred_city)
    if not weather_data:
        bot.reply_to(message, "Не удалось получить данные о погоде.")
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
@bot.message_handler(commands=['changecity'])
def changecity(message):
    log_action("Получена команда /changecity", message)
    user = get_user(message.from_user.id)

    reply_text = (f"Ваш текущий город — {user.preferred_city}. \nВведите новый город для обновления!"
                  if user and user.preferred_city else
                  "Вы ещё не указали свой город! \nУкажите новый город.")

    reply = bot.reply_to(message, reply_text)
    bot.register_next_step_handler(reply, process_new_city, show_menu=True)

@safe_execute
@bot.message_handler(func=lambda message: message.text == "🔔 Уведомления")
def notifications_settings(message):
    log_action("Пользователь открыл настройки уведомлений", message)
    
    user = get_user(message.from_user.id)
    current_status = "Включены" if user and user.notifications_enabled else "Отключены"
    
    status_message = (f"Настройки уведомлений.\n"
                      f"При включении вы будете получать:\n"
                      f"- Предупреждения при изменении погодных условий в вашем городе.\n"
                      f"- Новости при обновлении бота.\n\n"
                      f"Уведомления: {current_status}.")

    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("✅ Включить уведомления", callback_data="enable_notifications"))
    keyboard.add(types.InlineKeyboardButton("❌ Отключить уведомления", callback_data="disable_notifications"))
    keyboard.add(types.InlineKeyboardButton("↪️ Назад", callback_data="back_to_settings"))

    bot.send_message(message.chat.id, status_message, reply_markup=keyboard)

@safe_execute
@bot.callback_query_handler(func=lambda call: call.data in ["enable_notifications", "disable_notifications"])
def toggle_notifications(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id

    new_status = call.data == "enable_notifications"
    updated_status = toggle_user_notifications(user_id, new_status)

    if updated_status is None:
        bot.send_message(chat_id, "Для начала нужно указать город!\nВведите /start.")
        return

    current_status = "Включены" if updated_status else "Отключены"
    log_action(f"Пользователь изменил уведомления: {current_status}", call.message)

    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("✅ Включить уведомления", callback_data="enable_notifications"))
    keyboard.add(types.InlineKeyboardButton("❌ Отключить уведомления", callback_data="disable_notifications"))
    keyboard.add(types.InlineKeyboardButton("↪️ Назад", callback_data="back_to_settings"))

    bot.edit_message_text(f"Настройки уведомлений.\n"
                          f"При включении вы будете получать:\n"
                          f"- Предупреждения при изменении погодных условий в вашем городе.\n"
                          f"- Новости при обновлении бота.\n\n"
                          f"Уведомления: {current_status}.", 
                          chat_id, call.message.message_id, reply_markup=keyboard)

"""ОТКРЫТИЕ МЕНЮ ВЫБОРА ДАННЫХ"""
@safe_execute
@bot.callback_query_handler(func=lambda call: call.data == "format_settings")
def format_settings(call):
    chat_id = call.message.chat.id
    user_id = call.from_user.id
    message_id = call.message.message_id

    user = get_user(user_id)
    if not user:
        bot.send_message(chat_id, "Ошибка: пользователь не найден. Попробуйте /start.")
        return

    text = (f"⚙️ Настройки формата данных\n\n"
            f"▸ Температура: {user.temp_unit}\n"
            f"▸ Давление: {user.pressure_unit}\n"
            f"▸ Скорость ветра: {user.wind_speed_unit}\n\n"
            f"Выберите параметр для изменения:")

    bot.edit_message_text(text, chat_id, message_id, reply_markup=generate_format_keyboard())

@safe_execute
@bot.message_handler(func=lambda message: message.text in menu_actions)
def menu_handler(message):
    menu_actions[message.text](message)

menu_actions = {
    "🌦 Узнать погоду": weather,
    "⚙️ Настройки": lambda msg: send_settings_menu(msg.chat.id),
    "🏙 Изменить город": changecity,
    "🔔 Уведомления": notifications_settings,
    "↪️ Назад": lambda msg: send_main_menu(msg.chat.id),
    "📏 Формат данных": format_settings
}

@safe_execute
@bot.message_handler(commands=['help'])
def help_command(message):
    log_action("Получена команда /help", message)
    
    help_text = (
        "Доступные команды:\n"
        "/start - Запустить бота.\n"
        "/weather - Получить данные о погоде.\n"
        "/changecity - Сменить город.\n"
        "/help - Список доступных команд."
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
        send_main_menu(message.chat.id)

@safe_execute
@bot.message_handler(func=lambda message: message.text == "📅 Прогноз погоды")
def forecast_menu(message):
    bot.send_message(message.chat.id, "Выберите период прогноза:", reply_markup=generate_forecast_keyboard())

from logic import generate_format_keyboard

"""ОТКРЫТИЕ МЕНЮ ИЗМЕНЕНИЯ ФОРМАТА ЕДИНИЦ ИЗМЕРЕНИЯ"""
@safe_execute
@bot.callback_query_handler(func=lambda call: call.data in ["change_temp_unit", "change_pressure_unit", "change_wind_speed_unit"])
def change_unit_menu(call):
    chat_id = call.message.chat.id
    user = get_user(call.from_user.id)

    unit_type = call.data.split("_")[-2]
    current_unit = getattr(user, f"{unit_type}_unit", "N/A")

    bot.edit_message_text(f"Выберите единицу измерения {unit_type}:", 
                          chat_id, call.message.message_id, 
                          reply_markup=generate_unit_selection_keyboard(current_unit, unit_type))

@safe_execute
@bot.callback_query_handler(func=lambda call: call.data.startswith("set_"))
def set_unit(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id

    _, unit_type, _, new_unit = call.data.split("_") 
    update_user_unit(user_id, unit_type, new_unit) 

    change_unit_menu(call)