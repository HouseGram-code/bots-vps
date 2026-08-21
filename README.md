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
| VPS/user | 1 (MAX_VPS_PER_USER) |

## Быстрый старт (VPS, от root)

```bash
apt update && apt install -y unzip curl git
unzip -o bots-vps-master.zip -d /opt && cd /opt/bots-vps-master
cp .env.example .env && nano .env      # свой BOT_TOKEN и ADMIN_ID
chmod +x install.sh && ./install.sh
```

`install.sh` сам поставит Docker + compose-плагин, проверит `docker run hello-world`,
при необходимости запустит `fix-lxc-docker.sh` (LXC/OpenVZ) и подымет бота.

Вручную то же самое:

```bash
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker
mkdir -p data && cp .env.example .env   # и вписать свой токен
docker compose up -d --build
docker compose logs -f --tail=100
```

⚠️ Токен из `.env.example` — демонстрационный и публичный. Обязательно замените
его своим и отзовите старый через @BotFather.

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
