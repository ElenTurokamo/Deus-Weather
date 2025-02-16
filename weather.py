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

def get_weekly_forecast(city):
    WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
    url = f"http://api.openweathermap.org/data/2.5/forecast?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
    
    response = requests.get(url)
    response_data = response.json()
    
    if response_data.get("cod") != "200":
        return None

    daily_data = {}
    for entry in response_data["list"]:
        date = entry["dt_txt"].split(" ")[0]  
        temp_min = entry["main"]["temp_min"]
        temp_max = entry["main"]["temp_max"]
        description = entry["weather"][0]["description"].capitalize()
        pop = entry.get("pop", 0) * 100  

        if date not in daily_data:
            daily_data[date] = {"temp_min": temp_min, "temp_max": temp_max, "description": description, "pop": pop}
        else:
            daily_data[date]["temp_min"] = min(daily_data[date]["temp_min"], temp_min)
            daily_data[date]["temp_max"] = max(daily_data[date]["temp_max"], temp_max)
            daily_data[date]["pop"] = max(daily_data[date]["pop"], pop)

    forecast_text = "📆 *Прогноз погоды на неделю:*\n\n"
    days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

    for i, (date, data) in enumerate(daily_data.items()):
        forecast_text += (f"✦ *{days[i % 7]}*\n"
                          f"▸ Погода: {data['description']}\n"
                          f"▸ Осадки: {round(data['pop'])}%\n"
                          f"▸ Температура: от {round(temp_min)}°C до {round(temp_max)}°C\n"
                          f"\n")

    return forecast_text

def get_today_forecast(city):
    WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
    url = f"http://api.openweathermap.org/data/2.5/forecast?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
    
    response = requests.get(url)
    response_data = response.json()
    
    if response_data.get("cod") != "200":
        return None

    today = response_data["list"][0]  
    temp_min = today["main"]["temp_min"]
    temp_max = today["main"]["temp_max"]
    description = today["weather"][0]["description"].capitalize()
    pop = today.get("pop", 0) * 100 

    forecast_text = (f"\n"
                 f"▸ Погода: {description}\n"
                 f"▸ Осадки: {round(pop)}%\n"
                 f"▸ Температура: от {round(temp_min)}°C до {round(temp_max)}°C\n")

    return forecast_text