#!/bin/bash
# Автоустановщик VPS-бота на чистый Ubuntu/Debian VPS (запускать от root).
#   bash install.sh
set -euo pipefail

say() { echo -e "\n\033[1;36m>>> $*\033[0m"; }
ok()  { echo -e "\033[1;32m[OK] $*\033[0m"; }
err() { echo -e "\033[1;31m[FAIL] $*\033[0m"; }

[ "$(id -u)" = "0" ] || { err "запустите от root"; exit 1; }
cd "$(dirname "$(readlink -f "$0")")"

say "1/5 Базовые пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg git unzip jq

say "2/5 Docker"
if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker || true
docker --version

if ! docker compose version >/dev/null 2>&1; then
    apt-get install -y -qq docker-compose-plugin || true
fi
docker compose version

say "3/5 Проверка работоспособности Docker"
if ! docker run --rm hello-world >/dev/null 2>&1; then
    err "docker run не работает (типично для LXC/OpenVZ) — запускаю fix-lxc-docker.sh"
    chmod +x fix-lxc-docker.sh
    ./fix-lxc-docker.sh || { err "Docker так и не заработал — см. вывод выше"; exit 1; }
fi
ok "Docker работает"

say "4/5 Конфиг"
mkdir -p data
if [ ! -f .env ]; then
    cp .env.example .env
    ok "создан .env — ОБЯЗАТЕЛЬНО впишите свой BOT_TOKEN и ADMIN_ID: nano .env"
fi
chmod 600 .env

say "5/5 Сборка и запуск бота"
docker compose up -d --build
sleep 5
docker compose ps
echo
ok "Готово. Логи: docker compose logs -f --tail=100"
