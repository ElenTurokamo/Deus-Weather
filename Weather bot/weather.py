import requests
from data import WEATHER_API_KEY

def format_weather(city_name, temp, description):
    return f"Погода в г.{city_name}: {description}.\nТемпература: {temp}°C"

def get_weather(city):
    url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru"

    response = requests.get(url)

    response_data = response.json()

    if response_data.get("cod") != 200:
        return None

    temp = response_data["main"]["temp"]
    description = response_data["weather"][0]["description"]
    city_name = response_data["name"]

    return format_weather(city_name, temp, description)
