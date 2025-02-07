import requests
import os
from dotenv import load_dotenv

load_dotenv()

def format_weather(city_name, temp, description, humidity, wind_speed, pressure, visibility):
    return (f"Сейчас в г.{city_name}:\n"
            f"\n"
            f"▸ Погода: {description}\n"
            f"▸ Температура: {temp}°C\n"
            f"▸ Влажность: {humidity}%\n"
            f"▸ Скорость ветра: {wind_speed} м/с\n"
            f"▸ Давление: {pressure} hPa\n"
            f"▸ Видимость: {visibility} м")

def get_weather(city):
    WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
    url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
    
    response = requests.get(url)
    response_data = response.json()
    
    if response_data.get("cod") != 200:
        return None
    
    temp = response_data["main"]["temp"]
    description = response_data["weather"][0]["description"]
    city_name = response_data["name"]
    humidity = response_data["main"]["humidity"]
    wind_speed = response_data["wind"]["speed"]
    pressure = round(response_data["main"]["pressure"] * 0.75006)
    visibility = response_data.get("visibility", "Неизвестно")
    
    return format_weather(city_name, temp, description, humidity, wind_speed, pressure, visibility)
