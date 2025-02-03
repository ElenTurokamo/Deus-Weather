CREATE DATABASE IF NOT EXISTS weather_bot;
USE weather_bot;

CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT UNIQUE NOT NULL,
    username VARCHAR(50) NULL,
    preferred_city VARCHAR(100) NULL
);