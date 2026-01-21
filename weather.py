from dotenv import load_dotenv
from timezonefinder import TimezoneFinder
from datetime import datetime, timedelta
import requests
import os
from texts import get_api_lang_code

load_dotenv()

def get_weather(city, lang="ru"):
    WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
    api_lang = get_api_lang_code(lang)
    url = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric&lang={api_lang}"
    
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

def fetch_weekly_forecast(city, lang="ru"):
    WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
    api_lang = get_api_lang_code(lang)
    url = f"https://api.openweathermap.org/data/2.5/forecast?q={city}&appid={WEATHER_API_KEY}&units=metric&lang={api_lang}"

    response = requests.get(url)
    response_data = response.json()

    if response_data.get("cod") != "200":
        return None

    return response_data["list"]

def resolve_city_from_coords(lat, lon, lang="ru"):
    try:
        WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
        api_lang = get_api_lang_code(lang)
        url = "https://api.openweathermap.org/geo/1.0/reverse"
        params = {
            "lat": lat,
            "lon": lon,
            "limit": 1,
            "appid": WEATHER_API_KEY,
            "lang": api_lang
        }
        response = requests.get(url, params=params)
        data = response.json()
        if response.status_code == 200 and data:
            return data[0].get("name")
        return None
    except Exception:
        return None
    
def fetch_today_forecast(city, lang="ru"):
    WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
    api_lang = get_api_lang_code(lang)
    url = f"https://api.openweathermap.org/data/2.5/forecast?q={city}&appid={WEATHER_API_KEY}&units=metric&lang={api_lang}"

    response = requests.get(url)
    response_data = response.json()

    if response_data.get("cod") != "200":
        return None

    return response_data["list"] 

def fetch_tomorrow_forecast(city, lang="ru"):
    WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
    api_lang = get_api_lang_code(lang)
    url = f"https://api.openweathermap.org/data/2.5/forecast?q={city}&appid={WEATHER_API_KEY}&units=metric&lang={api_lang}"
    
    response = requests.get(url)
    response_data = response.json()

    if response_data.get("cod") != "200":
        return None

    return response_data["list"] 

def get_city_timezone(city):
    weather_data = get_weather(city, lang="ru")
    if not weather_data or "coordinates" not in weather_data:
        return None  

    coordinates = weather_data["coordinates"]
    tf = TimezoneFinder()
    return tf.timezone_at(lat=coordinates['lat'], lng=coordinates['lon'])