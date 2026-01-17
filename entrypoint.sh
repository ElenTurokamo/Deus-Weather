#!/bin/bash

python -c "
import os
from models import Base
from sqlalchemy import create_engine
engine = create_engine(os.getenv('DATABASE_URL', 'mysql://root:aMbmQeqiDUgRNwQYhLTaKaqabooLhidd@mysql.railway.internal:3306/railway'))
Base.metadata.create_all(engine)
"

if [ -z "$BOT_TOKEN" ]; then
  echo "❌ BOT_TOKEN не установлен!"
  exit 1
fi

# создаём папку логов безопасно
mkdir -p logs

# запускаем оба скрипта в foreground через `wait`
python bot.py &
python weather_timer.py &
wait