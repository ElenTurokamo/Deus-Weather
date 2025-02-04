import telebot
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
                bot.reply_to(args[0], "Произошла ошибка при обработке запроса. Попробуйте позже.")
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
def process_new_city(message):
    user_id = message.from_user.id
    city = message.text.strip()
    
    if city.startswith("/") or not city:
        reply = bot.reply_to(message, "Пожалуйста, введите название города, а не команду.")
        logging.debug(f"Пользователь {user_id} ввёл некорректную команду или пустой город.")
        bot.register_next_step_handler(reply, process_new_city)
        return
    
    if not re.match(r'^[A-Za-zА-Яа-яЁё\s\-]+$', city):
        reply = bot.reply_to(message, "Название города может содержать только буквы, пробелы и дефисы.")
        logging.debug(f"Пользователь {user_id} ввёл недопустимые символы в названии города: {city}")
        bot.register_next_step_handler(reply, process_new_city)
        return
    
    user = get_user(user_id)
    if user:
        if user.preferred_city == city:
            reply = bot.reply_to(message, f"Город '{city}' уже сохранён.")
            logging.debug(f"Пользователь {user_id} уже указал город: {city}")
        else:
            user.preferred_city = city
            save_user(user_id, preferred_city=city)
            reply = bot.reply_to(message, f"Ваш город '{city}' успешно обновлён.")
            logging.debug(f"Город для пользователя {user_id} был успешно обновлён на {city}")
    else:
        save_user(user_id, preferred_city=city)
        reply = bot.reply_to(message, f"Ваш город '{city}' успешно сохранён.")
        logging.debug(f"Новый пользователь {user_id} указал город: {city}")

@safe_execute
@bot.message_handler(commands=['start'])
def start(message):
    log_action("Получена команда /start", message)
    user_id = message.from_user.id
    conv_id = update_conversation(user_id)
    reply = bot.reply_to(message, f"Привет, {message.from_user.first_name}! Используй /weather чтобы добавить свой город и получать последние данные о погоде.")
    logging.debug(f"Ответ на команду /start отправлен пользователю с ID {user_id}.")

@safe_execute
@bot.message_handler(commands=['weather'])
def weather(message):
    user_id = message.from_user.id
    logging.debug(f"Получена команда /weather от пользователя с ID {user_id}.")
    user = get_user(user_id)
    if not user or not user.preferred_city:
        logging.debug(f"У пользователя с ID {user_id} не выбран город. Запрашиваем город.")
        reply = bot.reply_to(message, "Для начала — укажи город.")
        bot.register_next_step_handler(message, process_new_city)
    else:
        city = user.preferred_city
        logging.debug(f"Пользователь с ID {user_id} имеет сохранённый город: '{city}'.")
        weather_info = get_weather(city)
        if weather_info:
            reply = bot.reply_to(message, weather_info)
            logging.debug(f"Информация о погоде для города '{city}' отправлена пользователю с ID {user_id}.")
        else:
            reply = bot.reply_to(message, f"Не удалось получить погоду для города '{city}'.")
            logging.debug(f"Не удалось получить погоду для города '{city}' для пользователя с ID {user_id}.")

@safe_execute
@bot.message_handler(commands=['changecity'])
def changecity(message):
    user_id = message.from_user.id
    logging.debug(f"Получена команда /changecity от пользователя {user_id}")
    
    user = get_user(user_id)
    if user and user.preferred_city:
        reply = bot.reply_to(message, f"Ваш текущий город: {user.preferred_city}. Введите новый город для обновления.")
    else:
        reply = bot.reply_to(message, "Вы ещё не указали город. Введите новый город для погоды.")
    
    bot.register_next_step_handler(reply, process_new_city)

if __name__ == '__main__':
    logging.debug("Бот запущен.")
    bot.polling(none_stop=True)