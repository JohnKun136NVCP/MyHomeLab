# Contexto de construcción: services/
FROM python:3.15-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl tzdata \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common/homelab_common.py /app/
COPY bot/bot /app/bot
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "-m", "bot"]
