import requests
import os
from dotenv import load_dotenv
import datetime

load_dotenv()

MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
    7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
}

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

def convert_precipitation_to_percent(precipitation_mm):
    if precipitation_mm > 0:
        return min(int(precipitation_mm * 100), 100)  
    else:
        return 0 

# Прогноз погоды на неделю
def get_weekly_forecast(city):
    WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
    url = f"http://api.openweathermap.org/data/2.5/forecast?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
    
    response = requests.get(url)
    response_data = response.json()
    
    if response_data.get("cod") != "200":
        return None

    daily_data = {}
    today = datetime.date.today()
    start_date = today + datetime.timedelta(days=1)

    for entry in response_data["list"]:
        date_str = entry["dt_txt"].split(" ")[0]
        date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        
        if date_obj < start_date: 
            continue
        if (date_obj - start_date).days >= 5:
            break

        temp = entry["main"]["temp"]
        description = entry["weather"][0]["description"].capitalize()

        # Извлекаем rain и snow
        rain = entry.get("rain", {}).get("3h", 0)
        snow = entry.get("snow", {}).get("3h", 0)

        # Рассчитываем осадки как сумму rain и snow в миллиметрах
        total_precipitation = rain + snow
        total_precipitation_percent = convert_precipitation_to_percent(total_precipitation)

        if date_obj not in daily_data:
            daily_data[date_obj] = {
                "temp_min": temp,
                "temp_max": temp,
                "description": description,
                "pop_values": []
            }

        daily_data[date_obj]["temp_min"] = min(daily_data[date_obj]["temp_min"], temp)
        daily_data[date_obj]["temp_max"] = max(daily_data[date_obj]["temp_max"], temp)
        daily_data[date_obj]["pop_values"].append(total_precipitation_percent)

    forecast_text = "📆 *Прогноз погоды на 5 дней:*\n\n"
    days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

    for date, data in sorted(daily_data.items()):
        day_name = days[date.weekday()]
        month_name = MONTHS_RU[date.month]
        avg_pop = round(sum(data["pop_values"]) / len(data["pop_values"])) if data["pop_values"] else 0

        forecast_text += (f"✦ *{day_name}, {date.day} {month_name}*\n"
                          f"▸ Погода: {data['description']}\n"
                          f"▸ Осадки: {avg_pop}%\n"
                          f"▸ Температура: от {round(data['temp_min'])}°C до {round(data['temp_max'])}°C\n"
                          f"\n")

    return forecast_text

# Прогноз погоды на сегодня
def get_today_forecast(city):
    WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
    url = f"http://api.openweathermap.org/data/2.5/forecast?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
    
    response = requests.get(url)
    response_data = response.json()
    
    if response_data.get("cod") != "200":
        return None

    today_date = datetime.date.today()
    month_name = MONTHS_RU[today_date.month]

    today_forecast_list = [entry for entry in response_data["list"] if today_date.strftime("%Y-%m-%d") in entry["dt_txt"]]

    if not today_forecast_list:
        return "Прогноз на сегодня недоступен."

    temp_min = min(entry["main"]["temp"] for entry in today_forecast_list)
    temp_max = max(entry["main"]["temp"] for entry in today_forecast_list)
    description = today_forecast_list[0]["weather"][0]["description"].capitalize()


    rain = sum(entry.get("rain", {}).get("3h", 0) for entry in today_forecast_list)
    snow = sum(entry.get("snow", {}).get("3h", 0) for entry in today_forecast_list)


    total_precipitation = rain + snow
    total_precipitation_percent = convert_precipitation_to_percent(total_precipitation)

    forecast_text = (f"🌤 *Сегодня, {today_date.day} {month_name}*\n"
                     f"\n"
                     f"▸ Погода: {description}\n"
                     f"▸ Осадки: {total_precipitation_percent}%\n"
                     f"▸ Температура: от {round(temp_min)}°C до {round(temp_max)}°C\n")

    return forecast_text
