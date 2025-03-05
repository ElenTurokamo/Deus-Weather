import os
from sqlalchemy import Column, Integer, BigInteger, String, Float, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime
from dotenv import load_dotenv

Base = declarative_base()
load_dotenv()

ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    raise ValueError("Отсутствует ключ шифрования! Добавьте ENCRYPTION_KEY в .env")


class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String(255), unique=True, nullable=True)
    preferred_city = Column(String(255), nullable=True)
    notifications_enabled = Column(Boolean, default=False, server_default='0', nullable=False)
    temp_unit = Column(String(10), default="C") 
    pressure_unit = Column(String(10), default="mmHg") 
    wind_speed_unit = Column(String(10), default="m/s") 

class CheckedCities(Base):
    __tablename__ = 'checked_cities'

    id = Column(Integer, primary_key=True, autoincrement=True)
    city_name = Column(String(100), unique=True, nullable=False)
    weather_info = Column(String(255), nullable=False)
    temperature = Column(Float, nullable=False)
    last_checked = Column(DateTime, default=datetime.utcnow)
    
    last_temperature = Column(Float, nullable=True)
    last_wind_speed = Column(Float, nullable=True)
    last_humidity = Column(Integer, nullable=True)

    pressure = Column(Integer, nullable=True) 
    visibility = Column(Integer, nullable=True) 
    description = Column(String(255), nullable=True)
