import requests
from data import WEATHER_API_KEY

def get_weather(city):
    url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru"

    response = requests.get(url)

    response_data = response.json()

    if response_data.get("cod") != 200:
        return None

    temp = response_data["main"]["temp"]
    description = response_data["weather"][0]["description"]
    city_name = response_data["name"]

    return f"Погода в г.{city_name}: {temp}°C, {description}"
