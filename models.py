import os
from sqlalchemy import Column, Integer, BigInteger, String, Float, Boolean, DateTime, Text
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func
from sqlalchemy.dialects.mysql import JSON
from datetime import datetime
from dotenv import load_dotenv

Base = declarative_base()
load_dotenv()

class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, unique=True, nullable=False)
    unique_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String(255), unique=True, nullable=True)
    preferred_city = Column(String(255), nullable=True)
    
    notifications_settings = Column(
        JSON,
        default={
            "forecast_notifications": True,
            "bot_notifications": True,
            "weather_threshold_notifications": True
        },
        nullable=False
    )

    timezone = Column(String(50), nullable=True, default=None)

    tracked_weather_params = Column(JSON, nullable=False, default={
        "description": True,
        "temperature": True,
        "humidity": True,
        "precipitation": True,
        "pressure": True,
        "wind_speed": True,
        "visibility": False,
        "feels_like": True, 
        "clouds": False, 
        "wind_direction": False,    
        "wind_gust": False    
    })

    temp_unit = Column(String(10), default="C") 
    pressure_unit = Column(String(10), default="mmHg") 
    wind_speed_unit = Column(String(10), default="m/s") 
    
    level = Column(Integer, default=1) 
    exp = Column(Integer, default=0) 
    titles = Column(String(512), default="[]")  
    selected_titles = Column(String(512), default="[]")
    profile_card = Column(String(255), default="default.png")  
    logged = Column(Boolean, default=False) 


class CheckedCities(Base):
    __tablename__ = 'checked_cities'

    id = Column(Integer, primary_key=True, autoincrement=True)
    city_name = Column(String(100), unique=True, nullable=False) 
    
    last_temperature = Column(Float, nullable=True)
    last_feels_like = Column(Float, nullable=True)  
    last_wind_speed = Column(Float, nullable=True)
    last_wind_direction = Column(Integer, nullable=True)
    last_wind_gust = Column(Float, nullable=True) 
    last_humidity = Column(Integer, nullable=True)
    last_pressure = Column(Integer, nullable=True)
    last_visibility = Column(Integer, nullable=True)
    last_description = Column(String(255), nullable=True)
    last_clouds = Column(Integer, nullable=True) 
    last_precipitation = Column(Float, nullable=True) 

    temperature = Column(Float, nullable=False)
    feels_like = Column(Float, nullable=True)
    wind_speed = Column(Float, nullable=True)
    wind_direction = Column(Integer, nullable=True) 
    wind_gust = Column(Float, nullable=True)  
    humidity = Column(Integer, nullable=True)
    pressure = Column(Integer, nullable=True)
    visibility = Column(Integer, nullable=True)
    description = Column(String(255), nullable=True)
    clouds = Column(Integer, nullable=True) 
    precipitation = Column(Float, nullable=True) 

    last_checked = Column(DateTime, server_default=func.now())


class LocalVars(Base):
    __tablename__ = 'local_vars'

    user_id = Column(BigInteger, primary_key=True)
    last_menu_message = Column(JSON, nullable=True)
    last_settings_command = Column(JSON, nullable=True)
    last_user_command = Column(JSON, nullable=True)
    last_format_settings_menu = Column(JSON, nullable=True)
    last_bot_message = Column(JSON, nullable=True)
    last_daily_forecast = Column(JSON, nullable=True)