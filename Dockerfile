FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY telegram_bot.py db.py .

# /data and /var/log are volume-mounted by docker-compose; create them so the
# app doesn't fail on first run if the host directories were empty.
RUN mkdir -p /data /var/log

CMD ["python", "telegram_bot.py"]
