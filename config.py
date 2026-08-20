import os

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# .env — приоритет. Если его нет (частый случай на VPS) — берём .env.example,
# он специально НЕ удаляется из репозитория и копируется в образ.
for _fname in (".env", ".env.example"):
    _path = os.path.join(BASE_DIR, _fname)
    if os.path.isfile(_path):
        load_dotenv(_path, override=False)

_DEFAULT_TOKEN = "8891391292:AAH_lUXnQATh30uYVcK-3BQquMECgskbJNg"
_DEFAULT_ADMIN = 5429363551


def _env_str(key, default):
    val = (os.getenv(key) or "").strip()
    return val if val else default


def _env_int(key, default):
    val = (os.getenv(key) or "").strip()
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


BOT_TOKEN = _env_str("BOT_TOKEN", _DEFAULT_TOKEN)
ADMIN_ID  = _env_int("ADMIN_ID", _DEFAULT_ADMIN)

MAX_VPS_PER_USER    = _env_int("MAX_VPS_PER_USER", 1)   # обычным юзерам — 1 VPS
DEFAULT_TOTAL_SLOTS = _env_int("TOTAL_SLOTS", 10)       # всего слотов

VPS_MEMORY_LIMIT = _env_str("VPS_MEMORY_LIMIT", "512m")
VPS_CPU_QUOTA    = _env_int("VPS_CPU_QUOTA", 50000)
VPS_CPU_PERIOD   = _env_int("VPS_CPU_PERIOD", 100000)
VPS_IMAGE_NAME   = _env_str("VPS_IMAGE_NAME", "vps-bot-ubuntu")

# Абсолютный путь — чтобы БД не терялась при другом рабочем каталоге на сервере
DB_PATH = _env_str("DB_PATH", os.path.join(BASE_DIR, "data", "vps_bot.db"))


def _detect_lxc():
    """В LXC AppArmor ломает runc — контейнеры нужно создавать unconfined."""
    forced = (os.getenv("LXC_MODE") or "").strip().lower()
    if forced in ("1", "true", "yes"):
        return True
    if forced in ("0", "false", "no"):
        return False
    try:
        with open("/proc/1/environ", "rb") as f:
            if b"container=lxc" in f.read():
                return True
    except Exception:
        pass
    for probe in ("/dev/.lxc", "/run/.containerenv"):
        if os.path.exists(probe):
            return True
    try:
        with open("/proc/self/cgroup", "r") as f:
            if "lxc" in f.read():
                return True
    except Exception:
        pass
    return False


LXC_MODE = _detect_lxc()
