import docker
import time
import threading
from config import VPS_IMAGE_NAME, VPS_MEMORY_LIMIT, VPS_CPU_QUOTA, VPS_CPU_PERIOD

_client = None

def client():
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client

# ── Image ──────────────────────────────────────────────────────────────────────

def ensure_image():
    """Build the VPS image if not present."""
    try:
        client().images.get(VPS_IMAGE_NAME)
        return True
    except docker.errors.ImageNotFound:
        pass
    try:
        import os
        image_dir = os.path.join(os.path.dirname(__file__), "vps_image")
        print(f"[docker] Building image {VPS_IMAGE_NAME} from {image_dir} ...")
        client().images.build(path=image_dir, tag=VPS_IMAGE_NAME, rm=True)
        print("[docker] Image built successfully.")
        return True
    except Exception as e:
        print(f"[docker] Build error: {e}")
        return False

# ── Container CRUD ────────────────────────────────────────────────────────────

def create_container(user_id: int, name: str):
    try:
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
            restart_policy={"Name": "no"},
        )
        return c
    except Exception as e:
        print(f"[docker] create error: {e}")
        return None

def get_container(container_id: str):
    try:
        return client().containers.get(container_id)
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
            c.remove(force=True)
            return True
        except Exception:
            pass
    return False

# ── Stats ──────────────────────────────────────────────────────────────────────

def get_stats(container_id: str):
    """Return dict with cpu, mem_mb, mem_limit_mb, uptime or None."""
    try:
        c = get_container(container_id)
        if not c or c.status != "running":
            return None
        raw = c.stats(stream=False)

        # CPU %
        cd = raw["cpu_stats"]["cpu_usage"]["total_usage"] - \
             raw["precpu_stats"]["cpu_usage"]["total_usage"]
        sd = raw["cpu_stats"].get("system_cpu_usage", 0) - \
             raw["precpu_stats"].get("system_cpu_usage", 0)
        ncpu = raw["cpu_stats"].get("online_cpus", 1)
        cpu_pct = round((cd / sd) * ncpu * 100.0, 1) if sd > 0 else 0.0

        # Memory
        mem = raw["memory_stats"]
        usage = mem.get("usage", 0) - mem.get("stats", {}).get("cache", 0)
        limit = mem.get("limit", 1)
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
        # Kill any leftover session
        c.exec_run("pkill -f tmate", user="root")
        time.sleep(1)
        # Start detached session
        c.exec_run(
            "bash -c 'rm -f /tmp/t.sock && tmate -S /tmp/t.sock new-session -d 2>/dev/null'",
            detach=True, user="root"
        )
        time.sleep(4)
        # Retrieve SSH line
        for attempt in range(3):
            res = c.exec_run(
                "tmate -S /tmp/t.sock display -p '#{tmate_ssh}'",
                user="root"
            )
            out = res.output.decode("utf-8", errors="ignore").strip()
            if out and "@" in out and "tmate.io" in out:
                return out
            time.sleep(2)
        return None
    except Exception as e:
        print(f"[docker] tmate error: {e}")
        return None
