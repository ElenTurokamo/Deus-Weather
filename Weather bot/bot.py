import telebot
from weather import get_weather
import data
import logging
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import User, Base

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

bot = telebot.TeleBot(data.BOT_TOKEN)

DATABASE_URL = "mysql+pymysql://user:password@localhost/weather_bot"
engine = create_engine(DATABASE_URL, echo=True)
SessionLocal = sessionmaker(bind=engine)

conversation_id = {}

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
    else:
        if preferred_city:
            user.preferred_city = preferred_city
        if username:
            user.username = username
    db.commit()
    db.close()

def log_action(action, message):
    user = message.from_user
    log_message = f"{action} | Time: {datetime.now().isoformat()} | User ID: {user.id} | Username: {user.first_name or ''} {user.last_name or ''} | Message: {message.text}"
    logging.debug(log_message)

def update_conversation(user_id):
    conversation_id[user_id] = conversation_id.get(user_id, 0) + 1
    logging.debug(f"Обновление conversation_id для User ID {user_id}: {conversation_id[user_id]}")
    return conversation_id[user_id]

@bot.message_handler(commands=['start'])
def start(message):
    log_action("Получена команда /start", message)
    user_id = message.from_user.id
    conv_id = update_conversation(user_id)
    reply = bot.reply_to(message, f"Привет, {message.from_user.first_name}! Используй /weather чтобы добавить свой город и получать последние данные о погоде.")
    logging.debug(f"Ответ на /start отправлен пользователю {user_id} в ответ на сообщение {message.message_id}")

@bot.message_handler(commands=['weather'])
def weather(message):
    log_action("Получена команда /weather", message)
    user_id = message.from_user.id
    conv_id = update_conversation(user_id)

    user = get_user(user_id)

    if not user or not user.preferred_city:
        reply = bot.reply_to(message, "Для начала — укажи город.")
        logging.debug(f"Пользователю {user_id} отправлено сообщение о необходимости указать город.")
        bot.register_next_step_handler(message, lambda m, cid=conv_id: save_city(m, cid))
    else:
        city = user.preferred_city
        logging.debug(f"Пользователь {user_id} имеет сохранённый город: {city}")
        weather_info = get_weather(city)
        if weather_info:
            reply = bot.reply_to(message, weather_info)
        else:
            reply = bot.reply_to(message, f"Не удалось получить погоду для города {city}.")
        logging.debug(f"Пользователю {user_id} отправлена информация о погоде.")

if __name__ == '__main__':
    logging.debug("Бот запущен.")
    bot.polling(none_stop=True)
