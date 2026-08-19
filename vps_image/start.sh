#!/bin/bash
# VPS container entry point - keeps container alive

echo "==================================="
echo "  VPS Container — Ubuntu 22.04"
echo "  Started: $(date)"
echo "==================================="

# Verify tmate is available
if command -v tmate >/dev/null 2>&1; then
    echo "[OK] tmate: $(tmate -V 2>/dev/null || echo 'installed')"
else
    echo "[WARN] tmate not found"
fi

# Keep running
exec tail -f /dev/null
