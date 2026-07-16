FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY requirements-bot.txt .
RUN pip install --no-cache-dir -r requirements-bot.txt
COPY app.py .
COPY bot/ bot/
RUN useradd -m botuser && mkdir -p /app/data && chown -R botuser /app/data
USER botuser
VOLUME /app/data
CMD ["python", "-m", "bot.main"]
