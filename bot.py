#ИМПОРТЫ
from telebot import types
from dotenv import load_dotenv
from logging.handlers import RotatingFileHandler
from handlers import *
from logic import active_sessions

import logging
import os
import requests
import time
import telebot

#ТЕКУЩАЯ СЕССИЯ

#ШИФРОВАНИЕ
load_dotenv()
bot_start_time = time.time()

#ЛОГИРОВАНИЕ
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

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

conversation_id = {}

#ОТПРАВКА МЕНЮ
def send_menu(user_id, text, buttons):
    chat_id = active_sessions.get(user_id)
    if not chat_id:
        logging.error(f"Ошибка: нет активного chat_id для user_id {user_id}")
        return

    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    for row in buttons:
        keyboard.row(*row)

    bot.send_message(chat_id, text, reply_markup=keyboard)

def send_main_menu(chat_id):
    buttons = [["🌦 Узнать погоду", "📅 Прогноз погоды"], ["⚙️ Настройки"]]
    send_menu(chat_id, "Выберите опцию:", buttons)

def send_settings_menu(chat_id):
    buttons = [["🏙 Изменить город", "🔔 Уведомления"], ["📏 Формат данных"], ["↪️ Назад"]]
    send_menu(chat_id, "Выберите настройку:", buttons)

#ОБРАБОТКА КОМАНД
@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    active_sessions[message.from_user.id] = message.chat.id
    user_id = message.from_user.id
    chat_id = message.chat.id
    active_sessions[user_id] = chat_id

    commands_map = {
        "/start": start,
        "/help": help_command,
        "/weather": weather,
        "/changecity": changecity,
        "🌦 Узнать погоду": weather,
        "📅 Прогноз погоды": forecast_menu,
        "⚙️ Настройки": send_settings_menu,
        "🏙 Изменить город": changecity,
        "🔔 Уведомления": notifications_settings,
        "📏 Формат данных": format_settings,
        "↪️ Назад": send_main_menu
    }

    if active_sessions.get(user_id) != chat_id:
        active_sessions[user_id] = chat_id
        logging.debug(f"Обновлён chat_id для user_id {user_id}: {chat_id}")

    if message.text in commands_map:
        commands_map[message.text](message)
    else:
        bot.send_message(chat_id, "Я вас не понял. Используйте команды меню!")

#ЗАПУСК БОТА
if __name__ == '__main__':
    logging.info("Бот запущен.")

    MAX_RETRIES = 10
    attempt = 1  

    while attempt <= MAX_RETRIES:
        try:
            logging.info(f"Попытка #{attempt}: Запускаем polling...")
            bot.infinity_polling(timeout=10, long_polling_timeout=10)
        except requests.exceptions.ReadTimeout:
            logging.warning(f"Попытка #{attempt}: Read timeout. Повторный запуск через 5 секунд...")
        except requests.exceptions.ConnectionError as e:
            logging.error(f"Попытка #{attempt}: Ошибка соединения: {e}. Повторный запуск через 5 секунд...")
        except Exception as e:
            logging.critical(f"Попытка #{attempt}: Неизвестная ошибка: {e}. Повторный запуск через 5 секунд...")
        finally:
            attempt += 1
            time.sleep(5)

    logging.critical("Достигнуто максимальное количество попыток! Бот остановлен.")  
