while true; do
    python weather_checker.py
    echo "Чекер упал, перезапускаем..." >> logs.txt
    sleep 10
done