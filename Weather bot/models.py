from sqlalchemy import Column, Integer, BigInteger, String
from sqlalchemy.ext.declarative import declarative_base
Base = declarative_base()

class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, unique=True, nullable=False) 
    username = Column(String(50), unique=True, nullable=True)
    preferred_city = Column(String(100), nullable=True)