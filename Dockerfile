FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Бот общается с Docker через сокет по HTTP API (python SDK),
# поэтому пакет docker.io НЕ нужен — он раньше ломал/раздувал сборку на VPS.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates tzdata && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# .env.example копируется всегда (в .dockerignore он исключён из игнора),
# чтобы на сервере не нужно было создавать .env вручную
COPY . .

RUN mkdir -p /app/data

CMD ["python", "-u", "bot.py"]
