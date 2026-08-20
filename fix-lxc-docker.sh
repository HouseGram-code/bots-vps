#!/bin/bash
# Автофикс Docker внутри LXC-контейнера.
# Лечит: runc create failed ... remount-private MS_PRIVATE: permission denied
# Причина: патч CVE-2025-52881 в runc 1.2.8+/1.3.3+ конфликтует с AppArmor в LXC.

set -u

say() { echo -e "\n\033[1;36m>>> $*\033[0m"; }
ok()  { echo -e "\033[1;32m[OK] $*\033[0m"; }
err() { echo -e "\033[1;31m[FAIL] $*\033[0m"; }

test_docker() {
    docker run --rm hello-world >/dev/null 2>&1
}

# systemctl часто маскирует docker при откате пакетов — всегда снимаем
unmask_docker() {
    systemctl unmask docker.service docker.socket containerd.service >/dev/null 2>&1
    systemctl daemon-reload >/dev/null 2>&1
    systemctl reset-failed docker >/dev/null 2>&1
}

restart_docker() {
    unmask_docker
    systemctl enable containerd >/dev/null 2>&1
    systemctl start containerd >/dev/null 2>&1
    systemctl restart docker >/dev/null 2>&1
    sleep 3
    systemctl is-active docker >/dev/null 2>&1 || {
        err "демон не стартовал:"
        # "copy stream failed / closed fifo" — шум от завершённых контейнеров, не причина
        journalctl -u docker --no-pager 2>/dev/null \
            | grep -iE "level=(error|fatal)" \
            | grep -viE "copy stream|closed fifo" | tail -5
        return 1
    }
    return 0
}

if [ "$(id -u)" != "0" ]; then
    err "запустите от root"; exit 1
fi

say "Текущее состояние"
unmask_docker
docker --version 2>/dev/null || { err "docker не установлен"; exit 1; }
runc --version 2>/dev/null | head -1
systemd-detect-virt || true

if test_docker; then
    ok "Docker уже работает, фикс не нужен"; exit 0
fi

# ── 1. daemon.json: без containerd-snapshotter, драйвер fuse-overlayfs/vfs ─────
say "Шаг 1/4: настройка storage driver"
systemctl stop docker docker.socket 2>/dev/null
mkdir -p /etc/docker

DRIVER="vfs"
if command -v fuse-overlayfs >/dev/null 2>&1 || apt-get install -y fuse-overlayfs >/dev/null 2>&1; then
    DRIVER="fuse-overlayfs"
fi
echo "используем storage-driver: $DRIVER"

cat > /etc/docker/daemon.json <<EOF
{
  "features": { "containerd-snapshotter": false },
  "storage-driver": "$DRIVER"
}
EOF

# ── 2. systemd drop-in: отключаем AppArmor-интеграцию Docker ──────────────────
say "Шаг 2/4: отключение AppArmor-интеграции Docker"
mkdir -p /etc/systemd/system/docker.service.d
# MountFlags устарел и ломает старт новых версий dockerd — не используем
cat > /etc/systemd/system/docker.service.d/lxc.conf <<'EOF'
[Service]
Environment=container=lxc
ExecStartPre=-/bin/mount --make-rshared /
EOF

restart_docker

if test_docker; then
    ok "Заработало после шага 2"; exit 0
fi

# ── 3. Откат containerd.io до предпатчевой версии ─────────────────────────────
say "Шаг 3/4: откат containerd.io"
echo "Доступные версии:"
apt-cache madison containerd.io 2>/dev/null | head -10

for VER in "1.7.27-1" "1.7.28-1" "1.7.26-1"; do
    echo "пробуем containerd.io=$VER"
    if apt-get install -y --allow-downgrades "containerd.io=$VER" >/dev/null 2>&1; then
        apt-mark hold containerd.io >/dev/null
        restart_docker
        if test_docker; then
            ok "Заработало с containerd.io=$VER (зафиксирован через apt-mark hold)"
            exit 0
        fi
    fi
done

# ── 4. Подмена бинарника runc на 1.2.6 ────────────────────────────────────────
say "Шаг 4/4: подмена runc на 1.2.6"
ARCH=$(uname -m)
case "$ARCH" in
    x86_64) RA="amd64" ;;
    aarch64|arm64) RA="arm64" ;;
    *) err "неизвестная архитектура $ARCH"; exit 1 ;;
esac

if curl -fsSL -o /tmp/runc.new \
    "https://github.com/opencontainers/runc/releases/download/v1.2.6/runc.${RA}"; then
    chmod +x /tmp/runc.new
    RUNC_BIN=$(command -v runc || echo /usr/bin/runc)
    [ -f "$RUNC_BIN.bak" ] || cp "$RUNC_BIN" "$RUNC_BIN.bak"
    mv /tmp/runc.new "$RUNC_BIN"
    echo "установлен: $($RUNC_BIN --version | head -1)"
    restart_docker
    if test_docker; then
        ok "Заработало с runc 1.2.6 (бэкап: $RUNC_BIN.bak)"
        exit 0
    fi
fi

err "Ни один обход не помог."
echo
echo "Диагностика:"
systemctl is-active docker && echo "  демон работает, проблема в runc/ядре" || echo "  демон не запущен"
docker run --rm hello-world 2>&1 | tail -3
echo
echo "Остаётся два варианта:"
echo "  1) Попросить хостера включить на хосте:"
echo "       lxc.apparmor.profile: unconfined"
echo "       features: nesting=1,keyctl=1"
echo "     и сделать полный рестарт контейнера."
echo "  2) Перейти на KVM/VDS — там Docker работает без обходов."
exit 1
