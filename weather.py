#ИМПОРТЫ
from dotenv import load_dotenv
from timezonefinder import TimezoneFinder
from datetime import datetime, timedelta

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
        "feels_like": response_data["main"]["feels_like"],
        "description": response_data["weather"][0]["description"],
        "humidity": response_data["main"]["humidity"],
        "wind_speed": response_data["wind"]["speed"],
        "wind_direction": response_data["wind"].get("deg", 0),  
        "wind_gust": response_data["wind"].get("gust", 0), 
        "clouds": response_data["clouds"].get("all", 0),
        "pressure": round(response_data["main"]["pressure"]),
        "visibility": response_data.get("visibility", 0),
        "coordinates": {
            "lat": response_data["coord"]["lat"],
            "lon": response_data["coord"]["lon"]
        }  
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

#РАСШИФРОВКА ГОРОДА
def resolve_city_from_coords(lat, lon):
    """Определяет город по координатам через OpenWeather Geocoding API."""
    try:
        WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
        url = "http://api.openweathermap.org/geo/1.0/reverse"
        params = {
            "lat": lat,
            "lon": lon,
            "limit": 1,
            "appid": WEATHER_API_KEY,
            "lang": "ru"
        }
        response = requests.get(url, params=params)
        data = response.json()
        if response.status_code == 200 and data:
            city = data[0].get("name")
            return city
        else:
            return None
    except Exception as e:
        return None
    
    
#ПОЛУЧЕНИЕ ПРОГНОЗА НА СЕГОДНЯ ИЗ API
def fetch_today_forecast(city):
    WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
    url = f"http://api.openweathermap.org/data/2.5/forecast?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru"

    response = requests.get(url)
    response_data = response.json()

    if response_data.get("cod") != "200":
        return None

    return response_data["list"] 

#ПОЛУЧЕНИЕ ПРОГНОЗА НА СЕГОДНЯ ИЗ API
def fetch_tomorrow_forecast(city):
    WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
    url = f"http://api.openweathermap.org/data/2.5/forecast?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
    response = requests.get(url)
    response_data = response.json()

    if response_data.get("cod") != "200":
        return None

    return response_data["list"] 

#ПОЛУЧЕНИЕ ЧАСОВОГО ПОЯСА ПОЛЬЗОВАТЕЛЯ
def get_city_timezone(city):
    """Получает часовой пояс для города на основе его координат."""
    weather_data = get_weather(city)
    if not weather_data or "coordinates" not in weather_data:
        return None  

    coordinates = weather_data["coordinates"]
    tf = TimezoneFinder()
    timezone = tf.timezone_at(lat=coordinates['lat'], lng=coordinates['lon'])

    return timezone