from cryptography.fernet import Fernet
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


encryptor = Fernet(ENCRYPTION_KEY.encode())

def encrypt_data(data):
    return encryptor.encrypt(data.encode()).decode() if data else None

def decrypt_data(encrypted_data):
    return encryptor.decrypt(encrypted_data.encode()).decode() if encrypted_data else None

class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String(255), unique=True, nullable=True)
    preferred_city = Column(String(255), nullable=True)
    notifications_enabled = Column(Boolean, default=False, server_default='0', nullable=False)

    def set_username(self, username):
        self.username = encrypt_data(username)

    def get_username(self):
        return decrypt_data(self.username) if self.username else None

    def set_preferred_city(self, city):
        self.preferred_city = encrypt_data(city)

    def get_preferred_city(self):
        return decrypt_data(self.preferred_city) if self.preferred_city else None

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
