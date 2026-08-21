#!/bin/bash
# VPS container entry point — keeps container alive

echo "==================================="
echo "  VPS Container - Ubuntu 22.04"
echo "  Started: $(date)"
echo "==================================="

# tmate требует ssh-ключ пользователя
if [ ! -f /root/.ssh/id_rsa ]; then
    mkdir -p /root/.ssh
    chmod 700 /root/.ssh
    ssh-keygen -q -t rsa -b 2048 -N "" -f /root/.ssh/id_rsa >/dev/null 2>&1 || true
fi

if command -v tmate >/dev/null 2>&1; then
    echo "[OK] tmate: $(tmate -V 2>/dev/null || echo installed)"
else
    echo "[WARN] tmate not found"
fi

exec tail -f /dev/null
