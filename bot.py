import telebot
from telebot import types
from weather import get_weather
import data
import logging
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import User, Base
import re

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

DATABASE_URL = "mysql+pymysql://user:password123@localhost/weather_bot"
engine = create_engine(DATABASE_URL, echo=True)
SessionLocal = sessionmaker(bind=engine)

bot = telebot.TeleBot(data.BOT_TOKEN)

conversation_id = {}

def safe_execute(func):
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

def get_user(user_id):
    db = SessionLocal()
    user = db.query(User).filter(User.user_id == user_id).first()
    db.close()
    return user

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

def log_action(action, message):
    user = message.from_user
    log_message = f"{action} | Time: {datetime.now().isoformat()} | User ID: {user.id} | Username: {user.first_name or ''} {user.last_name or ''} | Message: {message.text}"
    logging.debug(log_message)

def update_conversation(user_id):
    conversation_id[user_id] = conversation_id.get(user_id, 0) + 1
    logging.debug(f"Обновлён conversation_id для пользователя с ID {user_id}: {conversation_id[user_id]}")
    return conversation_id[user_id]

@safe_execute
def process_new_city(message, show_menu=False):
    user_id = message.from_user.id
    city = message.text.strip()
    
    if city.startswith("/") or not city:
        reply = bot.reply_to(message, "Отправьте название города, а не команду!")
        logging.debug(f"Пользователь {user_id} ввёл некорректную команду или пустой город.")
        bot.register_next_step_handler(reply, lambda m: process_new_city(m, show_menu))
        return
    
    if not re.match(r'^[A-Za-zА-Яа-яЁё\s\-]+$', city):
        reply = bot.reply_to(message, "Название города может содержать только буквы, пробелы и дефисы!")
        logging.debug(f"Пользователь {user_id} ввёл недопустимые символы в названии города: {city}")
        bot.register_next_step_handler(reply, lambda m: process_new_city(m, show_menu))
        return
    
    user = get_user(user_id)
    if user:
        if user.preferred_city == city:
            reply = bot.reply_to(message, f"Теперь ваш основной город - это {city}!")
            logging.debug(f"Пользователь {user_id} уже указал город: {city}")
        else:
            save_user(user_id, preferred_city=city)
            reply = bot.reply_to(message, f"Данные обновлены!\n"
                                        f"Теперь ваш основной город — {city}.")
            logging.debug(f"Город для пользователя {user_id} был успешно обновлён на {city}")
    else:
        save_user(user_id, username=message.from_user.username, preferred_city=city)
        reply = bot.reply_to(message, f"Данные сохранены!\n"
                                        f"Теперь ваш основной город — {city}.")
        logging.debug(f"Новый пользователь {user_id} указал город: {city}")
    

    if show_menu:
        send_main_menu(message.chat.id)

def send_main_menu(chat_id):
    main_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    main_keyboard.row("🌦 Узнать погоду", "⚙️ Настройки")
    
    loading_message = bot.send_message(chat_id, "Загрузка...")
    bot.delete_message(chat_id, loading_message.message_id) 
    bot.send_message(chat_id, "Выберите опцию:", reply_markup=main_keyboard)

def send_settings_menu(chat_id):
    settings_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    settings_keyboard.row("🏙 Изменить город", "🔔 Уведомления")
    settings_keyboard.row("↪️ Назад")
    
    loading_message = bot.send_message(chat_id, "Загрузка...")
    bot.delete_message(chat_id, loading_message.message_id) 
    bot.send_message(chat_id, "Выберите настройку:", reply_markup=settings_keyboard)


@safe_execute
@bot.message_handler(commands=['start'])
def start(message):
    log_action("Получена команда /start", message)
    user_id = message.from_user.id
    user = get_user(user_id)
    
    if user:
        reply_text = (f"С возвращением, {message.from_user.first_name}! "
                      f"Ваш основной город — {user.preferred_city or 'не указан.'}.")
        bot.reply_to(message, reply_text)
        logging.debug(f"Пользователь с ID {user_id} уже зарегистрирован. Приветственное сообщение обновлено.")
    else:
        save_user(user_id, message.from_user.first_name)
        reply_text = (f"Привет, {message.from_user.first_name}! "
                      "Для того, чтобы начать получать информацию о погоде — укажите свой город.")
        msg = bot.reply_to(message, reply_text)
        bot.register_next_step_handler(msg, lambda m: process_new_city(m))
        logging.debug(f"Новый пользователь с ID {user_id} зарегистрирован. Запрошен город.")
    
    send_main_menu(message.chat.id)

@safe_execute
@bot.message_handler(commands=['weather'])
def weather(message):
    user_id = message.from_user.id
    logging.debug(f"Получена команда /weather от пользователя с ID {user_id}.")
    user = get_user(user_id)
    if not user or not user.preferred_city:
        logging.debug(f"У пользователя с ID {user_id} не выбран город. Запрашиваем город.")
        reply = bot.reply_to(message, "Для начала — укажите свой город!")
        bot.register_next_step_handler(reply, process_new_city)
    else:
        city = user.preferred_city
        logging.debug(f"Пользователь с ID {user_id} имеет сохранённый город: '{city}'.")
        weather_info = get_weather(city)
        if weather_info:
            bot.reply_to(message, weather_info)
            logging.debug(f"Информация о погоде для города '{city}' отправлена пользователю с ID {user_id}.")
        else:
            bot.reply_to(message, f"Не удалось получить погоду для города '{city}'.")
            logging.debug(f"Не удалось получить погоду для города '{city}' для пользователя с ID {user_id}.")

    send_main_menu(message.chat.id)

@safe_execute
@bot.message_handler(commands=['changecity'])
def changecity(message):
    user_id = message.from_user.id
    logging.debug(f"Получена команда /changecity от пользователя {user_id}")
    
    user = get_user(user_id)
    if user and user.preferred_city:
        reply = bot.reply_to(message, f"Ваш текущий город — {user.preferred_city}. \n"
                             f"Введите новый город для обновления!")
    else:
        reply = bot.reply_to(message, "Вы ещё не указали свой город! \n"
                             f"Укажите новый город.")
    
    bot.register_next_step_handler(reply, lambda m: process_new_city(m, show_menu=True))

@safe_execute
@bot.message_handler(func=lambda message: message.text in ["🌦 Узнать погоду", "⚙️ Настройки", "🏙 Изменить город", "🔔 Уведомления", "↪️ Назад"])
def menu_handler(message):
    if message.text == "🌦 Узнать погоду":
        weather(message)
    elif message.text == "⚙️ Настройки":
        send_settings_menu(message.chat.id)
    elif message.text == "🏙 Изменить город":
        changecity(message)
    elif message.text == "🔔 Уведомления":
        bot.send_message(message.chat.id, "Функция уведомлений ещё в разработке!")
        send_settings_menu(message.chat.id)
    elif message.text == "↪️ Назад":
        send_main_menu(message.chat.id)

@safe_execute
@bot.message_handler(commands=['help'])
def help(message):
    user_id = message.from_user.id
    logging.debug(f"Получена команда /help от пользователя с ID {user_id}.")
    
    help_text = (
        "Доступные команды:\n"
        "/start - Запустить бота.\n"
        "/weather - Получить данные о погоде.\n"
        "/changecity - Сменить город.\n"
        "/help - Список доступных команд."
    )
    bot.reply_to(message, help_text)
    logging.debug(f"Пользователю с ID {user_id} отправлен список команд.")

if __name__ == '__main__':
    logging.debug("Бот запущен.")
    bot.polling(none_stop=True)
