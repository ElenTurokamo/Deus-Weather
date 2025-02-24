#ИМПОРТЫ
from dotenv import load_dotenv
import requests
import os

load_dotenv()

def get_weather(city):
    WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
    url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
    
    response = requests.get(url)
    response_data = response.json()
    
    if response_data.get("cod") != 200:
        return None

    return {
        "city_name": response_data["name"],
        "temp": response_data["main"]["temp"],
        "description": response_data["weather"][0]["description"],
        "humidity": response_data["main"]["humidity"],
        "wind_speed": response_data["wind"]["speed"],
        "pressure": round(response_data["main"]["pressure"] * 0.75006),
        "visibility": response_data.get("visibility", 0),
    }

#ПОЛУЧЕНИЕ ПРОГНОЗА НА НЕДЕЛЮ ИЗ API
def fetch_weekly_forecast(city):
    WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
    url = f"http://api.openweathermap.org/data/2.5/forecast?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru"

    response = requests.get(url)
    response_data = response.json()

    if response_data.get("cod") != "200":
        return None

    return response_data["list"]

#ПОЛУЧЕНИЕ ПРОГНОЗА НА СЕГОДНЯ ИЗ API
def fetch_today_forecast(city):
    WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
    url = f"http://api.openweathermap.org/data/2.5/forecast?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru"

    response = requests.get(url)
    response_data = response.json()

    if response_data.get("cod") != "200":
        return None

    return response_data["list"] 
