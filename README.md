# 🖥️ VPS Telegram Bot

Бесплатный VPS-бот для Telegram на Python + Docker.  
Каждый пользователь получает Docker-контейнер Ubuntu 22.04 с ограниченными ресурсами.

## Возможности

- 🖥️ Создание VPS (Docker контейнер Ubuntu 22.04)
- 🔑 SSH доступ через TMATE
- 📊 Мониторинг CPU / RAM / Uptime
- ⏯️ Старт / Стоп / Рестарт VPS
- 🗑️ Удаление VPS
- Команды: `!deploy`, `!manage`

## Лимиты на VPS

| Ресурс  | Лимит    |
|---------|----------|
| RAM     | 512 MB   |
| CPU     | 0.5 vCPU |
| Disk    | ~512 MB  |
| VPS/user | 2       |

## Быстрый старт

### 1. Настройка

```bash
cp .env.example .env
# Отредактируй .env — вставь токен бота от @BotFather
```

### 2. Запуск через Docker Compose (рекомендуется)

```bash
docker compose up -d --build
```

### 3. Запуск локально (Python)

```bash
pip install -r requirements.txt
export BOT_TOKEN="your_token_here"    # или добавь в .env
python bot.py
```

## Требования

- Docker 20+ установлен и запущен
- Python 3.10+ (если запускаешь локально)
- Токен бота от [@BotFather](https://t.me/BotFather)

## Команды бота

| Команда     | Описание             |
|-------------|----------------------|
| `/start`    | Главное меню         |
| `!deploy`   | Создать новый VPS    |
| `!manage`   | Список моих серверов |

## Структура проекта

```
.
├── bot.py              # Основной файл бота
├── docker_manager.py   # Управление Docker контейнерами
├── database.py         # SQLite база данных
├── config.py           # Конфигурация
├── requirements.txt
├── Dockerfile          # Образ для запуска бота
├── docker-compose.yml
├── .env.example
├── vps_image/
│   ├── Dockerfile      # Образ для VPS контейнеров
│   └── start.sh
└── data/               # SQLite БД (создаётся автоматически)
```
