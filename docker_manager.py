import os
import time
import threading

import docker

from config import (
    VPS_IMAGE_NAME, VPS_MEMORY_LIMIT, VPS_CPU_QUOTA, VPS_CPU_PERIOD,
    LXC_MODE,
)


def _security_opt():
    """В LXC AppArmor блокирует runc (MS_PRIVATE: permission denied).
    Отключаем профиль для выдаваемых VPS-контейнеров."""
    return ["apparmor=unconfined"] if LXC_MODE else None

_client = None
_client_lock = threading.Lock()


def client():
    """Ленивый потокобезопасный клиент. На VPS docker.from_env() часто падал,
    если DOCKER_HOST не задан — фоллбэк на unix-сокет."""
    global _client
    with _client_lock:
        if _client is None:
            try:
                _client = docker.from_env(timeout=120)
                _client.ping()
            except Exception as e:
                print(f"[docker] from_env failed: {e}; trying unix socket")
                _client = docker.DockerClient(
                    base_url=os.getenv("DOCKER_HOST", "unix:///var/run/docker.sock"),
                    timeout=120,
                )
                _client.ping()
        return _client

# ── Diagnostics ───────────────────────────────────────────────────────

def diagnose():
    """Проверяет доступность демона и отдаёт человеческую подсказку."""
    try:
        info = client().info()
    except Exception as e:
        return False, (
            f"[docker] демон недоступен: {e}\n"
            "  → проверьте проброс /var/run/docker.sock и systemctl status docker"
        )
    driver = info.get("Driver", "?")
    runc_v = (info.get("RuncCommit", {}) or {}).get("ID", "?")[:12]
    lines = [
        f"[docker] версия: {info.get('ServerVersion', '?')}",
        f"[docker] storage driver: {driver}",
        f"[docker] runc: {runc_v}",
        f"[docker] LXC_MODE: {LXC_MODE}",
    ]
    if LXC_MODE:
        lines.append("[docker] LXC обнаружен — VPS создаются с apparmor=unconfined")
    return True, "\n".join(lines)


# ── Image ──────────────────────────────────────────────────────────────────────

def ensure_image():
    """Build the VPS image if not present."""
    try:
        client().images.get(VPS_IMAGE_NAME)
        return True
    except docker.errors.ImageNotFound:
        pass
    except Exception as e:
        print(f"[docker] daemon unreachable: {e}")
        return False

    image_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vps_image")
    if not os.path.isdir(image_dir):
        print(f"[docker] image dir not found: {image_dir}")
        return False
    try:
        print(f"[docker] Building image {VPS_IMAGE_NAME} from {image_dir} ...")
        _img, logs = client().images.build(
            path=image_dir, tag=VPS_IMAGE_NAME, rm=True, forcerm=True, pull=False
        )
        for chunk in logs:
            line = (chunk.get("stream") or "").rstrip()
            if line:
                print(f"[build] {line}")
        print("[docker] Image built successfully.")
        return True
    except Exception as e:
        print(f"[docker] Build error: {e}")
        return False

# ── Container CRUD ────────────────────────────────────────────────────────────

def create_container(user_id: int, name: str):
    try:
        # если контейнер с таким именем висит — убираем, иначе 409 Conflict
        try:
            old = client().containers.get(name)
            old.remove(force=True)
        except Exception:
            pass
        c = client().containers.run(
            VPS_IMAGE_NAME,
            name=name,
            detach=True,
            stdin_open=True,
            tty=True,
            mem_limit=VPS_MEMORY_LIMIT,
            cpu_period=VPS_CPU_PERIOD,
            cpu_quota=VPS_CPU_QUOTA,
            labels={"vps-bot-user": str(user_id), "vps-bot": "1"},
            restart_policy={"Name": "unless-stopped"},
            pids_limit=256,
            network_mode="bridge",
            security_opt=_security_opt(),
        )
        return c
    except Exception as e:
        # Фоллбэк: если демон не знает security_opt/pids_limit — пробуем минимально
        print(f"[docker] create error: {e}; retry без доп. опций")
        try:
            return client().containers.run(
                VPS_IMAGE_NAME,
                name=name,
                detach=True,
                stdin_open=True,
                tty=True,
                mem_limit=VPS_MEMORY_LIMIT,
                labels={"vps-bot-user": str(user_id), "vps-bot": "1"},
            )
        except Exception as e2:
            print(f"[docker] create failed: {e2}")
            return None

def get_container(container_id: str):
    if not container_id:
        return None
    try:
        c = client().containers.get(container_id)
        c.reload()
        return c
    except Exception:
        return None

def start_container(container_id: str) -> bool:
    c = get_container(container_id)
    if c:
        try:
            c.start()
            return True
        except Exception:
            pass
    return False

def stop_container(container_id: str) -> bool:
    c = get_container(container_id)
    if c:
        try:
            c.stop(timeout=10)
            return True
        except Exception:
            pass
    return False

def restart_container(container_id: str) -> bool:
    c = get_container(container_id)
    if c:
        try:
            c.restart(timeout=10)
            return True
        except Exception:
            pass
    return False

def remove_container(container_id: str) -> bool:
    c = get_container(container_id)
    if c:
        try:
            # restart_policy=unless-stopped может поднять контейнер снова — сначала гасим
            try:
                c.stop(timeout=5)
            except Exception:
                pass
            c.remove(force=True, v=True)
            return True
        except Exception as e:
            print(f"[docker] remove error: {e}")
    return False

# ── Stats ──────────────────────────────────────────────────────────────────────

def get_stats(container_id: str):
    """Return dict with cpu, mem_mb, mem_limit_mb, uptime or None."""
    try:
        c = get_container(container_id)
        if not c or c.status != "running":
            return None
        raw = c.stats(stream=False)

        # CPU % (cgroup v1/v2 safe)
        cpu_now  = raw.get("cpu_stats", {})
        cpu_prev = raw.get("precpu_stats", {})
        cd = cpu_now.get("cpu_usage", {}).get("total_usage", 0) - \
             cpu_prev.get("cpu_usage", {}).get("total_usage", 0)
        sd = (cpu_now.get("system_cpu_usage") or 0) - \
             (cpu_prev.get("system_cpu_usage") or 0)
        ncpu = cpu_now.get("online_cpus") or \
               len(cpu_now.get("cpu_usage", {}).get("percpu_usage") or [1]) or 1
        cpu_pct = round((cd / sd) * ncpu * 100.0, 1) if sd > 0 and cd > 0 else 0.0
        cpu_pct = max(0.0, min(cpu_pct, 100.0 * ncpu))

        # Memory (cgroup v2 использует inactive_file, v1 — cache)
        mem = raw.get("memory_stats", {}) or {}
        mstats = mem.get("stats", {}) or {}
        cache = mstats.get("inactive_file", mstats.get("cache", 0)) or 0
        usage = max((mem.get("usage", 0) or 0) - cache, 0)
        limit = mem.get("limit") or 1
        mem_mb = round(usage / 1048576, 0)
        lim_mb = round(limit / 1048576, 0)

        # Uptime
        c.reload()
        from datetime import datetime, timezone
        started = c.attrs["State"]["StartedAt"]
        started_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - started_dt
        t = int(delta.total_seconds())
        if t < 0:
            t = 0
        d, r = divmod(t, 86400)
        h, r = divmod(r, 3600)
        m = r // 60
        uptime = (f"{d}d " if d else "") + (f"{h}h " if h else "") + f"{m}m"

        return {
            "cpu": cpu_pct,
            "mem_mb": mem_mb,
            "lim_mb": lim_mb,
            "uptime": uptime.strip(),
            "status": c.status,
        }
    except Exception as e:
        print(f"[docker] stats error: {e}")
        return None

# ── TMATE ──────────────────────────────────────────────────────────────────────

def get_tmate_ssh(container_id: str) -> str | None:
    """Run tmate inside container, return SSH string or None."""
    c = get_container(container_id)
    if not c or c.status != "running":
        return None
    try:
        # tmate не стартует без ssh-ключа — гарантируем его наличие
        c.exec_run(
            ["bash", "-lc",
             "mkdir -p /root/.ssh && chmod 700 /root/.ssh && "
             "[ -f /root/.ssh/id_rsa ] || ssh-keygen -q -t rsa -b 2048 -N '' -f /root/.ssh/id_rsa"],
            user="root",
        )
        # Сносим старую сессию
        c.exec_run(["bash", "-lc", "pkill -f tmate || true; rm -f /tmp/t.sock"], user="root")
        time.sleep(1)
        # Стартуем detached-сессию
        c.exec_run(
            ["bash", "-lc", "tmate -S /tmp/t.sock new-session -d"],
            detach=True, user="root",
        )
        # Ждём готовность сокета вместо слепого sleep(4)
        # без timeout этот exec мог висеть вечно и навсегда блокировал поток бота
        c.exec_run(
            ["bash", "-lc", "timeout 25 tmate -S /tmp/t.sock wait tmate-ready || true"],
            user="root",
        )
        for _ in range(6):
            res = c.exec_run(
                ["bash", "-lc", "tmate -S /tmp/t.sock display -p '#{tmate_ssh}'"],
                user="root",
            )
            out = res.output.decode("utf-8", errors="ignore").strip()
            if out and "@" in out and "tmate.io" in out:
                return out.splitlines()[-1].strip()
            time.sleep(2)
        return None
    except Exception as e:
        print(f"[docker] tmate error: {e}")
        return None
