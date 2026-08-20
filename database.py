import os
import sqlite3
import threading

from config import DB_PATH, DEFAULT_TOTAL_SLOTS

_lock = threading.RLock()


def _conn():
    d = os.path.dirname(os.path.abspath(DB_PATH))
    if d:
        os.makedirs(d, exist_ok=True)
    # timeout + WAL: иначе при работе из потоков падает 'database is locked'
    con = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    return con

def init_db():
    con = _conn()
    c = con.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id    INTEGER PRIMARY KEY,
            username   TEXT,
            first_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS vps (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        INTEGER,
            container_id   TEXT,
            container_name TEXT,
            status         TEXT DEFAULT 'running',
            os_name        TEXT DEFAULT 'Ubuntu 22.04',
            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS banned_users (
            user_id   INTEGER PRIMARY KEY,
            reason    TEXT DEFAULT '',
            banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # Default settings
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('maintenance', '0')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('total_slots', ?)",
              (str(DEFAULT_TOTAL_SLOTS),))

    con.commit()
    con.close()

# ── Users ──────────────────────────────────────────────────────────────────────

def upsert_user(user_id, username, first_name):
    con = _conn()
    # было INSERT OR REPLACE — он сбрасывал created_at при каждом клике
    con.execute(
        """INSERT INTO users (user_id, username, first_name) VALUES (?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
                                              first_name=excluded.first_name""",
        (user_id, username or "", first_name or "")
    )
    con.commit()
    con.close()

def get_user(user_id):
    con = _conn()
    row = con.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    con.close()
    return row

def get_all_users():
    con = _conn()
    rows = con.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    con.close()
    return rows

def count_all_users():
    con = _conn()
    n = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    con.close()
    return n

# ── VPS ────────────────────────────────────────────────────────────────────────

def add_vps(user_id, container_id, container_name, os_name="Ubuntu 22.04"):
    con = _conn()
    cur = con.execute(
        "INSERT INTO vps (user_id, container_id, container_name, os_name) VALUES (?,?,?,?)",
        (user_id, container_id, container_name, os_name)
    )
    vid = cur.lastrowid
    con.commit()
    con.close()
    return vid

def get_user_vps(user_id):
    con = _conn()
    rows = con.execute("SELECT * FROM vps WHERE user_id=?", (user_id,)).fetchall()
    con.close()
    return rows

def get_vps(vps_id):
    con = _conn()
    row = con.execute("SELECT * FROM vps WHERE id=?", (vps_id,)).fetchone()
    con.close()
    return row

def update_status(vps_id, status):
    con = _conn()
    con.execute("UPDATE vps SET status=? WHERE id=?", (status, vps_id))
    con.commit()
    con.close()

def delete_vps(vps_id):
    con = _conn()
    con.execute("DELETE FROM vps WHERE id=?", (vps_id,))
    con.commit()
    con.close()

def count_vps(user_id):
    con = _conn()
    n = con.execute("SELECT COUNT(*) FROM vps WHERE user_id=?", (user_id,)).fetchone()[0]
    con.close()
    return n

def count_all_vps():
    """Total VPS rows across all users (active slots used)."""
    con = _conn()
    n = con.execute("SELECT COUNT(*) FROM vps").fetchone()[0]
    con.close()
    return n

def get_all_vps():
    con = _conn()
    rows = con.execute("SELECT * FROM vps ORDER BY created_at DESC").fetchall()
    con.close()
    return rows

# ── Ban ────────────────────────────────────────────────────────────────────────

def ban_user(user_id, reason=""):
    con = _conn()
    con.execute(
        "INSERT OR REPLACE INTO banned_users (user_id, reason) VALUES (?,?)",
        (user_id, reason)
    )
    con.commit()
    con.close()

def unban_user(user_id):
    con = _conn()
    con.execute("DELETE FROM banned_users WHERE user_id=?", (user_id,))
    con.commit()
    con.close()

def is_banned(user_id):
    con = _conn()
    row = con.execute("SELECT 1 FROM banned_users WHERE user_id=?", (user_id,)).fetchone()
    con.close()
    return row is not None

def get_all_banned():
    con = _conn()
    rows = con.execute("SELECT * FROM banned_users ORDER BY banned_at DESC").fetchall()
    con.close()
    return rows

# ── Settings ───────────────────────────────────────────────────────────────────

def get_setting(key):
    con = _conn()
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    con.close()
    return row[0] if row else None

def set_setting(key, value):
    con = _conn()
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, str(value)))
    con.commit()
    con.close()

def is_maintenance():
    return get_setting("maintenance") == "1"

def get_total_slots():
    v = get_setting("total_slots")
    return int(v) if v else DEFAULT_TOTAL_SLOTS

def set_total_slots(n):
    set_setting("total_slots", str(n))
