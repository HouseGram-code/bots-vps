#!/usr/bin/env python3
"""VPSNovaBot — Free VPS in Telegram"""

import os
import time
import threading

import telebot
from telebot import types
from dotenv import load_dotenv

load_dotenv()

import database as db
import docker_manager as dm
from config import BOT_TOKEN, ADMIN_ID, MAX_VPS_PER_USER

# ══════════════════════════════════════════════════════════════════════════════
#  Bot instance
# ══════════════════════════════════════════════════════════════════════════════

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ══════════════════════════════════════════════════════════════════════════════
#  Animations
# ══════════════════════════════════════════════════════════════════════════════

DEPLOY_ANIM = [
    "⚙️ <b>Инициализация VPS...</b>\n\n▱▱▱▱▱▱▱▱▱▱  <code> 0%</code>",
    "🔧 <b>Настройка окружения...</b>\n\n▰▰▱▱▱▱▱▱▱▱  <code>20%</code>",
    "📦 <b>Загрузка пакетов...</b>\n\n▰▰▰▰▱▱▱▱▱▱  <code>40%</code>",
    "🐳 <b>Создание контейнера...</b>\n\n▰▰▰▰▰▰▱▱▱▱  <code>60%</code>",
    "🔒 <b>Применение лимитов...</b>\n\n▰▰▰▰▰▰▰▰▱▱  <code>80%</code>",
    "🐧 <b>Запуск Ubuntu 22.04...</b>\n\n▰▰▰▰▰▰▰▰▰▱  <code>95%</code>",
    "✅ <b>VPS готов!</b>\n\n▰▰▰▰▰▰▰▰▰▰  <code>100%</code>",
]

TMATE_ANIM = [
    "🔐 <b>Генерация SSH ключей...</b>\n\n🔑 ░░░░░░░░░░",
    "🔐 <b>Запуск tmate агента...</b>\n\n🔑 ▓▓▓░░░░░░░",
    "🌐 <b>Подключение к tmate.io...</b>\n\n🔑 ▓▓▓▓▓▓░░░░",
    "🔒 <b>Шифрование туннеля...</b>\n\n🔑 ▓▓▓▓▓▓▓▓░░",
    "✅ <b>SSH сессия создана!</b>\n\n🔑 ▓▓▓▓▓▓▓▓▓▓",
]

MAINTENANCE_TEXT = (
    "🔧 <b>Технические работы</b>\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "⚙️ Мы всё понимаем и активно работаем над устранением проблемы.\n\n"
    "🛠️ <b>Причина:</b> высокая нагрузка на серверы\n"
    "⏳ <b>Статус:</b> чиним, стараемся как можно быстрее\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Приносим извинения за временные неудобства.\n"
    "Спасибо за ваше терпение! 🙏\n\n"
    "<i>Как только работы завершатся — бот сразу заработает.</i>"
)

# ══════════════════════════════════════════════════════════════════════════════
#  Guards
# ══════════════════════════════════════════════════════════════════════════════

def _is_admin(uid):
    return uid == ADMIN_ID

def _check(message_or_call):
    """Return False and reply if user is banned or maintenance active."""
    if hasattr(message_or_call, 'message'):
        # CallbackQuery
        uid  = message_or_call.from_user.id
        chat = message_or_call.message.chat.id
    else:
        uid  = message_or_call.from_user.id
        chat = message_or_call.chat.id

    # Block groups
    if hasattr(message_or_call, 'chat') and message_or_call.chat.type != 'private':
        return False
    if hasattr(message_or_call, 'message') and message_or_call.message.chat.type != 'private':
        return False

    if _is_admin(uid):
        return True

    if db.is_banned(uid):
        bot.send_message(chat,
            "🚫 <b>Доступ ограничен</b>\n\n"
            "Вы заблокированы администратором.\n"
            "Если считаете это ошибкой — свяжитесь с поддержкой.")
        return False

    if db.is_maintenance():
        bot.send_message(chat, MAINTENANCE_TEXT)
        return False

    return True

# ══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _bar(val, total, length=8):
    if total <= 0:
        return "░" * length
    filled = min(int((val / total) * length), length)
    return "▓" * filled + "░" * (length - filled)

def _edit(chat_id, msg_id, text, kb=None):
    try:
        bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id,
                              reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass

def _vps_card(vps, stats=None):
    vid, uid, cid, cname, status, osname, created = vps
    sem = "🟢 Online" if status == "running" else "🔴 Offline"
    lines = [
        f"🖥️ <b>{cname}</b>",
        "",
        "📋 <b>Информация</b>",
        f"├ 🆔 ID: <code>{vid}</code>",
        f"├ 🐧 OS: {osname}",
        f"├ 📡 Статус: {sem}",
        f"└ 📅 Создан: {created[:10]}",
        "",
        "⚙️ <b>Конфигурация</b>",
        "├ 💻 CPU: 0.5 vCore",
        "├ 🧠 RAM: 512 MB",
        "├ 💿 Disk: 512 MB",
        f"└ 🌐 OS: {osname}",
    ]
    if stats:
        cpu = stats["cpu"]
        mem = stats["mem_mb"]
        lim = stats["lim_mb"]
        upt = stats["uptime"]
        lines += [
            "",
            "📊 <b>Live Usage</b>",
            f"├ ⏱️ Uptime: {upt}",
            f"├ 🖥️ CPU:    {_bar(cpu, 100)} {cpu}%",
            f"├ 💾 RAM:    {mem:.0f}/{lim:.0f} MB  {_bar(mem, lim)}",
            "└ 💿 Disk:   N/A",
        ]
    else:
        lines += ["", "<i>⚡ Запустите VPS, чтобы увидеть статистику</i>"]
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════════════════
#  Keyboards
# ══════════════════════════════════════════════════════════════════════════════

def kb_main(uid):
    m = types.InlineKeyboardMarkup(row_width=2)
    m.add(
        types.InlineKeyboardButton("👤 Профиль",      callback_data="profile"),
        types.InlineKeyboardButton("🖥️ Получить VPS", callback_data="get_vps"),
    )
    m.add(types.InlineKeyboardButton("📋 Мои серверы", callback_data="manage"))
    if _is_admin(uid):
        m.add(types.InlineKeyboardButton("⚙️ Админ панель", callback_data="admin"))
    return m

def kb_back(dest="back_main"):
    m = types.InlineKeyboardMarkup()
    m.add(types.InlineKeyboardButton("🔙 Назад", callback_data=dest))
    return m

def kb_os():
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(types.InlineKeyboardButton("🐧 Ubuntu 22.04 LTS", callback_data="deploy_ubuntu2204"))
    m.add(types.InlineKeyboardButton("🔙 Назад",            callback_data="back_main"))
    return m

def kb_vps_list(rows):
    m = types.InlineKeyboardMarkup(row_width=1)
    for row in rows:
        vid, uid, cid, cname, status, osname, created = row
        em = "🟢" if status == "running" else "🔴"
        m.add(types.InlineKeyboardButton(f"{em} {cname}  |  {osname}",
                                          callback_data=f"vps_{vid}"))
    m.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_main"))
    return m

def kb_vps_ctrl(vps_id, status):
    m = types.InlineKeyboardMarkup(row_width=2)
    m.add(
        types.InlineKeyboardButton("🔑 TMATE SSH",  callback_data=f"tmate_{vps_id}"),
        types.InlineKeyboardButton("📊 Обновить",   callback_data=f"stats_{vps_id}"),
    )
    if status == "running":
        m.add(
            types.InlineKeyboardButton("🔄 Перезагрузить", callback_data=f"restart_{vps_id}"),
            types.InlineKeyboardButton("⏹️ Остановить",    callback_data=f"stop_{vps_id}"),
        )
    else:
        m.add(types.InlineKeyboardButton("▶️ Запустить", callback_data=f"start_{vps_id}"))
    m.add(
        types.InlineKeyboardButton("🗑️ Удалить VPS", callback_data=f"delete_ask_{vps_id}"),
        types.InlineKeyboardButton("🔙 Назад",        callback_data="manage"),
    )
    return m

def kb_confirm_del(vps_id):
    m = types.InlineKeyboardMarkup(row_width=2)
    m.add(
        types.InlineKeyboardButton("✅ Да, удалить",  callback_data=f"delete_ok_{vps_id}"),
        types.InlineKeyboardButton("❌ Отмена",        callback_data=f"vps_{vps_id}"),
    )
    return m

def kb_profile():
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(types.InlineKeyboardButton("❓ Частые вопросы (FAQ)", callback_data="faq"))
    m.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_main"))
    return m

def kb_faq():
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(types.InlineKeyboardButton("❌ VPS не работает",         callback_data="faq_1"))
    m.add(types.InlineKeyboardButton("⚠️ Произошёл сбой",          callback_data="faq_2"))
    m.add(types.InlineKeyboardButton("📈 Почему сервер нагружен?", callback_data="faq_3"))
    m.add(types.InlineKeyboardButton("🔙 Назад",                   callback_data="profile"))
    return m

def kb_faq_back():
    m = types.InlineKeyboardMarkup()
    m.add(types.InlineKeyboardButton("🔙 К вопросам", callback_data="faq"))
    return m

# ── Admin keyboards ────────────────────────────────────────────────────────────

def kb_admin():
    m = types.InlineKeyboardMarkup(row_width=2)
    maint = db.is_maintenance()
    maint_label = "🟢 Вкл тех. работы" if not maint else "🔴 Выкл тех. работы"
    m.add(
        types.InlineKeyboardButton("📊 Статистика",   callback_data="adm_stats"),
        types.InlineKeyboardButton("👥 Пользователи", callback_data="adm_users"),
    )
    m.add(
        types.InlineKeyboardButton(maint_label,        callback_data="adm_toggle_maint"),
        types.InlineKeyboardButton("🎰 Слоты",         callback_data="adm_slots"),
    )
    m.add(
        types.InlineKeyboardButton("🎁 Выдать VPS",    callback_data="adm_give_vps"),
        types.InlineKeyboardButton("🚫 Забанить",       callback_data="adm_ban"),
    )
    m.add(types.InlineKeyboardButton("✅ Разбанить",   callback_data="adm_unban"))
    m.add(types.InlineKeyboardButton("🔙 Назад",       callback_data="back_main"))
    return m

# ══════════════════════════════════════════════════════════════════════════════
#  /start
# ══════════════════════════════════════════════════════════════════════════════

@bot.message_handler(commands=["start"])
def cmd_start(msg):
    if msg.chat.type != "private":
        return
    db.upsert_user(msg.from_user.id, msg.from_user.username, msg.from_user.first_name)
    if not _check(msg):
        return

    name = msg.from_user.first_name or "пользователь"
    used = db.count_all_vps()
    total = db.get_total_slots()

    text = (
        f"🚀 <b>Добро пожаловать в VPSNovaBot!</b>\n\n"
        f"👋 Привет, <b>{name}</b>!\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🌟 <b>VPSNovaBot</b> — получи бесплатный VPS прямо в Telegram!\n\n"
        "🎯 <b>Что умеет бот:</b>\n"
        "├ 🐧 VPS на Ubuntu 22.04\n"
        "├ 🔑 SSH доступ через TMATE\n"
        "├ 📊 Мониторинг CPU / RAM\n"
        "└ ⚙️ Полное управление сервером\n\n"
        f"📦 <b>Слоты:</b> {used}/{total} занято\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "👇 Выбери действие:"
    )
    bot.send_message(msg.chat.id, text, reply_markup=kb_main(msg.from_user.id))

# ══════════════════════════════════════════════════════════════════════════════
#  !deploy / !manage
# ══════════════════════════════════════════════════════════════════════════════

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower().startswith("!deploy"))
def cmd_deploy(msg):
    if msg.chat.type != "private":
        return
    db.upsert_user(msg.from_user.id, msg.from_user.username, msg.from_user.first_name)
    if not _check(msg):
        return
    _do_deploy_msg(msg.chat.id, msg.from_user.id)

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower().startswith("!manage"))
def cmd_manage_cmd(msg):
    if msg.chat.type != "private":
        return
    db.upsert_user(msg.from_user.id, msg.from_user.username, msg.from_user.first_name)
    if not _check(msg):
        return
    _show_list_msg(msg.chat.id, msg.from_user.id)

# ══════════════════════════════════════════════════════════════════════════════
#  Pending admin input (FSM-like)
# ══════════════════════════════════════════════════════════════════════════════

_pending = {}   # uid -> {"action": str, "data": any}

@bot.message_handler(func=lambda m: m.chat.type == "private" and
                     m.from_user.id == ADMIN_ID and
                     m.from_user.id in _pending)
def handle_admin_input(msg):
    uid  = msg.from_user.id
    task = _pending.pop(uid, None)
    if not task:
        return

    action = task["action"]
    text   = msg.text.strip()

    if action == "ban":
        try:
            target = int(text)
            db.ban_user(target)
            bot.send_message(msg.chat.id,
                f"✅ Пользователь <code>{target}</code> заблокирован.",
                reply_markup=kb_admin())
        except ValueError:
            bot.send_message(msg.chat.id, "❌ Неверный ID. Введите число.", reply_markup=kb_admin())

    elif action == "unban":
        try:
            target = int(text)
            db.unban_user(target)
            bot.send_message(msg.chat.id,
                f"✅ Пользователь <code>{target}</code> разблокирован.",
                reply_markup=kb_admin())
        except ValueError:
            bot.send_message(msg.chat.id, "❌ Неверный ID.", reply_markup=kb_admin())

    elif action == "set_slots":
        try:
            n = int(text)
            if n < 1:
                raise ValueError
            db.set_total_slots(n)
            bot.send_message(msg.chat.id,
                f"✅ Слоты обновлены: <b>{n}</b>",
                reply_markup=kb_admin())
        except ValueError:
            bot.send_message(msg.chat.id, "❌ Неверное число.", reply_markup=kb_admin())

    elif action == "give_vps":
        try:
            target = int(text)
            _pending[uid] = {"action": "give_vps_2", "data": target}
            bot.send_message(msg.chat.id,
                f"👤 Выдаём VPS пользователю <code>{target}</code>\n\n"
                "Введите <b>имя контейнера</b> (например: <code>vps-gift-001</code>):")
        except ValueError:
            bot.send_message(msg.chat.id, "❌ Неверный ID.", reply_markup=kb_admin())

    elif action == "give_vps_2":
        target_uid = task["data"]
        cname = text if text else f"vps-{target_uid}-gift"
        # Deploy container
        c = dm.create_container(target_uid, cname)
        if c:
            db.upsert_user(target_uid, "", "")
            db.add_vps(target_uid, c.id, cname)
            bot.send_message(msg.chat.id,
                f"✅ VPS <b>{cname}</b> выдан пользователю <code>{target_uid}</code>",
                reply_markup=kb_admin())
        else:
            bot.send_message(msg.chat.id, "❌ Ошибка создания контейнера.", reply_markup=kb_admin())

# ══════════════════════════════════════════════════════════════════════════════
#  Callbacks
# ══════════════════════════════════════════════════════════════════════════════

@bot.callback_query_handler(func=lambda c: True)
def on_callback(call):
    if call.message.chat.type != "private":
        return

    uid  = call.from_user.id
    cid  = call.message.chat.id
    mid  = call.message.message_id
    data = call.data

    db.upsert_user(uid, call.from_user.username, call.from_user.first_name)
    bot.answer_callback_query(call.id)

    # Admin-only callbacks
    if data.startswith("adm_"):
        if not _is_admin(uid):
            return
        _handle_admin_cb(uid, cid, mid, data)
        return

    # General guard (maintenance / ban)
    if not _is_admin(uid):
        if db.is_banned(uid):
            _edit(cid, mid,
                  "🚫 <b>Вы заблокированы</b>\n\nОбратитесь к администратору.")
            return
        if db.is_maintenance():
            _edit(cid, mid, MAINTENANCE_TEXT)
            return

    # ── Navigation ─────────────────────────────────────────────────────────
    if data == "back_main":
        used  = db.count_all_vps()
        total = db.get_total_slots()
        _edit(cid, mid,
              f"🚀 <b>VPSNovaBot</b>\n\n"
              f"📦 Слоты: {used}/{total}\n\n"
              "👇 Выбери действие:",
              kb_main(uid))

    elif data == "profile":
        _show_profile(call)

    elif data == "get_vps":
        _show_os(cid, mid)

    elif data == "manage":
        _show_list_cb(cid, mid, uid)

    elif data == "admin":
        if _is_admin(uid):
            _show_admin(cid, mid)

    # ── FAQ ────────────────────────────────────────────────────────────────
    elif data == "faq":
        _edit(cid, mid,
              "❓ <b>Частые вопросы</b>\n\n"
              "Выбери интересующий вопрос:",
              kb_faq())

    elif data == "faq_1":
        _edit(cid, mid,
              "❌ <b>VPS не работает</b>\n\n"
              "━━━━━━━━━━━━━━━━━━━━━━\n\n"
              "Если ваш VPS перестал отвечать:\n\n"
              "1️⃣ Нажмите <b>📊 Обновить</b> — проверьте статус\n"
              "2️⃣ Попробуйте <b>🔄 Перезагрузить</b> сервер\n"
              "3️⃣ Если не помогает — <b>⏹️ Остановите</b> и снова <b>▶️ Запустите</b>\n"
              "4️⃣ В крайнем случае удалите и создайте новый VPS\n\n"
              "⚠️ <i>Docker контейнеры иногда зависают — рестарт обычно решает проблему</i>",
              kb_faq_back())

    elif data == "faq_2":
        _edit(cid, mid,
              "⚠️ <b>Произошёл сбой</b>\n\n"
              "━━━━━━━━━━━━━━━━━━━━━━\n\n"
              "Возможные причины сбоя:\n\n"
              "🔸 <b>Нехватка памяти</b> — процесс потребил >512 MB RAM\n"
              "🔸 <b>Краш процесса</b> — программа внутри VPS упала\n"
              "🔸 <b>Системный сбой</b> — хост-машина перезагрузилась\n\n"
              "✅ <b>Как исправить:</b>\n"
              "├ Перезапустите VPS через панель управления\n"
              "├ Не запускайте слишком тяжёлые процессы\n"
              "└ Следите за потреблением RAM в статистике\n\n"
              "<i>Если проблема повторяется — создайте новый VPS</i>",
              kb_faq_back())

    elif data == "faq_3":
        _edit(cid, mid,
              "📈 <b>Почему сервер нагружен?</b>\n\n"
              "━━━━━━━━━━━━━━━━━━━━━━\n\n"
              "Высокая нагрузка возникает по нескольким причинам:\n\n"
              "🔸 <b>Конкуренция ресурсов</b>\n"
              "   Все VPS работают на одном хосте и делят CPU\n\n"
              "🔸 <b>Лимиты контейнера</b>\n"
              "   Каждый VPS ограничен 0.5 vCPU / 512 MB RAM\n\n"
              "🔸 <b>Ваш процесс</b>\n"
              "   Тяжёлые задачи (компиляция, майнинг и т.д.)\n"
              "   упираются в лимит быстрее\n\n"
              "💡 <b>Совет:</b> следите за показателем CPU% в <b>📊 Обновить</b>.\n"
              "Если >80% — остановите лишние процессы внутри VPS.",
              kb_faq_back())

    # ── Deploy ─────────────────────────────────────────────────────────────
    elif data.startswith("deploy_"):
        _deploy_cb(cid, mid, uid)

    # ── VPS panel ──────────────────────────────────────────────────────────
    elif data.startswith("vps_"):
        vps_id = int(data.split("_", 1)[1])
        _show_vps(cid, mid, uid, vps_id)

    elif data.startswith("stats_"):
        vps_id = int(data.split("_", 1)[1])
        _show_vps(cid, mid, uid, vps_id)

    elif data.startswith("tmate_"):
        vps_id = int(data.split("_", 1)[1])
        _do_tmate(cid, mid, uid, vps_id)

    elif data.startswith("restart_"):
        vps_id = int(data.split("_", 1)[1])
        _do_restart(cid, mid, uid, vps_id)

    elif data.startswith("stop_"):
        vps_id = int(data.split("_", 1)[1])
        _do_stop(cid, mid, uid, vps_id)

    elif data.startswith("start_"):
        vps_id = int(data.split("_", 1)[1])
        _do_start(cid, mid, uid, vps_id)

    elif data.startswith("delete_ask_"):
        vps_id = int(data.split("_", 2)[2])
        vps = db.get_vps(vps_id)
        if vps and vps[1] == uid:
            _edit(cid, mid,
                  f"⚠️ <b>Удаление VPS</b>\n\n"
                  f"Удалить <b>{vps[3]}</b>?\n\n"
                  "<i>Все данные будут потеряны!</i>",
                  kb_confirm_del(vps_id))

    elif data.startswith("delete_ok_"):
        vps_id = int(data.split("_", 2)[2])
        _do_delete(cid, mid, uid, vps_id)

# ══════════════════════════════════════════════════════════════════════════════
#  Admin handlers
# ══════════════════════════════════════════════════════════════════════════════

def _show_admin(cid, mid):
    users  = db.count_all_users()
    used   = db.count_all_vps()
    total  = db.get_total_slots()
    maint  = "🔴 Включены" if db.is_maintenance() else "🟢 Выключены"
    banned = len(db.get_all_banned())
    _edit(cid, mid,
          "⚙️ <b>Админ панель</b>\n\n"
          "━━━━━━━━━━━━━━━━━━━━━━\n\n"
          f"👥 Пользователей: <b>{users}</b>\n"
          f"🖥️ VPS занято:    <b>{used}/{total}</b>\n"
          f"🚫 Забанено:      <b>{banned}</b>\n"
          f"🔧 Тех. работы:   {maint}\n\n"
          "━━━━━━━━━━━━━━━━━━━━━━",
          kb_admin())

def _handle_admin_cb(uid, cid, mid, data):
    if data == "adm_stats":
        _show_admin(cid, mid)

    elif data == "adm_users":
        users = db.get_all_users()
        if not users:
            _edit(cid, mid, "👥 Нет пользователей.", kb_admin())
            return
        lines = ["👥 <b>Пользователи</b>\n"]
        for u in users[:20]:
            ban_mark = " 🚫" if db.is_banned(u[0]) else ""
            uname = f"@{u[1]}" if u[1] else "—"
            lines.append(f"├ <code>{u[0]}</code> {u[2]} ({uname}){ban_mark}")
        _edit(cid, mid, "\n".join(lines), kb_admin())

    elif data == "adm_toggle_maint":
        current = db.is_maintenance()
        db.set_setting("maintenance", "0" if current else "1")
        new_state = "включены 🔴" if not current else "выключены 🟢"
        _edit(cid, mid,
              f"🔧 <b>Технические работы</b> {new_state}",
              kb_admin())

    elif data == "adm_slots":
        used  = db.count_all_vps()
        total = db.get_total_slots()
        m = types.InlineKeyboardMarkup(row_width=3)
        m.add(
            types.InlineKeyboardButton("➖ 1",  callback_data="adm_slots_sub1"),
            types.InlineKeyboardButton(f"{used}/{total}", callback_data="adm_stats"),
            types.InlineKeyboardButton("➕ 1",  callback_data="adm_slots_add1"),
        )
        m.add(types.InlineKeyboardButton("✏️ Задать вручную", callback_data="adm_slots_set"))
        m.add(types.InlineKeyboardButton("🔙 Назад",           callback_data="admin"))
        _edit(cid, mid,
              f"🎰 <b>Управление слотами</b>\n\n"
              f"Используется: <b>{used}</b>\n"
              f"Всего слотов: <b>{total}</b>\n"
              f"Свободно:     <b>{total - used}</b>",
              m)

    elif data == "adm_slots_add1":
        t = db.get_total_slots()
        db.set_total_slots(t + 1)
        _handle_admin_cb(uid, cid, mid, "adm_slots")

    elif data == "adm_slots_sub1":
        t = db.get_total_slots()
        if t > 1:
            db.set_total_slots(t - 1)
        _handle_admin_cb(uid, cid, mid, "adm_slots")

    elif data == "adm_slots_set":
        _pending[uid] = {"action": "set_slots", "data": None}
        _edit(cid, mid,
              "✏️ Введите новое количество слотов (число):",
              kb_back("adm_slots"))

    elif data == "adm_ban":
        _pending[uid] = {"action": "ban", "data": None}
        _edit(cid, mid,
              "🚫 <b>Забанить пользователя</b>\n\nВведите Telegram ID пользователя:",
              kb_back("admin"))

    elif data == "adm_unban":
        banned = db.get_all_banned()
        if not banned:
            _edit(cid, mid, "✅ Нет забаненных пользователей.", kb_admin())
            return
        m = types.InlineKeyboardMarkup(row_width=1)
        for b in banned[:10]:
            m.add(types.InlineKeyboardButton(
                f"✅ Разбанить {b[0]}",
                callback_data=f"adm_unban_id_{b[0]}"
            ))
        m.add(types.InlineKeyboardButton("🔙 Назад", callback_data="admin"))
        _edit(cid, mid, "🚫 <b>Забаненные пользователи:</b>", m)

    elif data.startswith("adm_unban_id_"):
        target = int(data.split("_")[-1])
        db.unban_user(target)
        _edit(cid, mid, f"✅ Пользователь <code>{target}</code> разбанен.", kb_admin())

    elif data == "adm_give_vps":
        _pending[uid] = {"action": "give_vps", "data": None}
        _edit(cid, mid,
              "🎁 <b>Выдать VPS пользователю</b>\n\nВведите Telegram ID пользователя:",
              kb_back("admin"))

    elif data == "admin":
        _show_admin(cid, mid)

# ══════════════════════════════════════════════════════════════════════════════
#  Feature handlers
# ══════════════════════════════════════════════════════════════════════════════

def _show_profile(call):
    uid = call.from_user.id
    user = db.get_user(uid)
    n = db.count_vps(uid)
    uname = f"@{call.from_user.username}" if call.from_user.username else "—"
    reg = user[3][:10] if user else "сегодня"
    limit = "∞" if _is_admin(uid) else str(MAX_VPS_PER_USER)
    _edit(call.message.chat.id, call.message.message_id,
          f"👤 <b>Профиль</b>\n\n"
          "━━━━━━━━━━━━━━━━━━━━━━\n\n"
          f"├ 📛 Имя:      <b>{call.from_user.first_name}</b>\n"
          f"├ 🔖 Username: {uname}\n"
          f"├ 🆔 ID:       <code>{uid}</code>\n"
          f"├ 🖥️ Серверов: <b>{n}</b> / {limit}\n"
          f"└ 📅 С нами:   {reg}\n\n"
          "━━━━━━━━━━━━━━━━━━━━━━",
          kb_profile())

def _show_os(cid, mid):
    _edit(cid, mid,
          "🖥️ <b>Выбор операционной системы</b>\n\n"
          "🐧 <b>Ubuntu 22.04 LTS</b>\n"
          "   ├ Долгосрочная поддержка\n"
          "   ├ Стабильная и надёжная\n"
          "   └ 512 MB RAM  •  0.5 vCPU",
          kb_os())

def _show_list_msg(chat_id, uid):
    rows = db.get_user_vps(uid)
    if not rows:
        bot.send_message(chat_id,
                         "📋 <b>Мои серверы</b>\n\nУ вас нет VPS.\n"
                         "Используйте <b>!deploy</b> для создания.")
        return
    _refresh_statuses(rows)
    rows = db.get_user_vps(uid)
    text = f"📋 <b>Мои серверы</b>  ({len(rows)})\n\n"
    for r in rows:
        em = "🟢" if r[4] == "running" else "🔴"
        text += f"{em} <b>{r[3]}</b> — {r[5]}\n"
    bot.send_message(chat_id, text, reply_markup=kb_vps_list(rows))

def _show_list_cb(cid, mid, uid):
    rows = db.get_user_vps(uid)
    if not rows:
        _edit(cid, mid,
              "📋 <b>Мои серверы</b>\n\nУ вас нет VPS.",
              types.InlineKeyboardMarkup().add(
                  types.InlineKeyboardButton("🖥️ Получить VPS", callback_data="get_vps"),
                  types.InlineKeyboardButton("🔙 Назад",         callback_data="back_main"),
              ))
        return
    _refresh_statuses(rows)
    rows = db.get_user_vps(uid)
    text = f"📋 <b>Мои серверы</b>  ({len(rows)})\n\n"
    for r in rows:
        em = "🟢" if r[4] == "running" else "🔴"
        text += f"{em} <b>{r[3]}</b> — {r[5]}\n"
    _edit(cid, mid, text, kb_vps_list(rows))

def _refresh_statuses(rows):
    for r in rows:
        c = dm.get_container(r[2])
        db.update_status(r[0], c.status if c else "exited")

def _show_vps(cid, mid, uid, vps_id):
    vps = db.get_vps(vps_id)
    if not vps or vps[1] != uid:
        _edit(cid, mid, "❌ VPS не найден.", kb_back())
        return
    c = dm.get_container(vps[2])
    status = c.status if c else "exited"
    db.update_status(vps_id, status)
    vps = db.get_vps(vps_id)
    stats = dm.get_stats(vps[2]) if status == "running" else None
    _edit(cid, mid, _vps_card(vps, stats), kb_vps_ctrl(vps_id, status))

# ── Deploy ─────────────────────────────────────────────────────────────────────

def _deploy_cb(cid, mid, uid):
    used  = db.count_all_vps()
    total = db.get_total_slots()

    if used >= total:
        _edit(cid, mid,
              f"😔 <b>Слоты заняты</b>\n\n"
              f"Все {total} слотов сейчас заняты.\n"
              "Попробуйте позже или обратитесь к администратору.",
              kb_back())
        return

    if not _is_admin(uid) and db.count_vps(uid) >= MAX_VPS_PER_USER:
        _edit(cid, mid,
              f"❌ <b>Лимит!</b>  У вас уже есть VPS.\n"
              "Удалите его чтобы создать новый.",
              types.InlineKeyboardMarkup().add(
                  types.InlineKeyboardButton("📋 Мои серверы", callback_data="manage")
              ))
        return

    _edit(cid, mid, DEPLOY_ANIM[0])
    threading.Thread(target=_deploy_worker, args=(cid, mid, uid), daemon=True).start()

def _do_deploy_msg(chat_id, uid):
    used  = db.count_all_vps()
    total = db.get_total_slots()
    if used >= total:
        bot.send_message(chat_id, f"😔 Слоты заняты ({used}/{total}). Попробуйте позже.")
        return
    if not _is_admin(uid) and db.count_vps(uid) >= MAX_VPS_PER_USER:
        bot.send_message(chat_id, "❌ У вас уже есть VPS. Удалите его для создания нового.")
        return
    msg = bot.send_message(chat_id, DEPLOY_ANIM[0])
    threading.Thread(target=_deploy_worker, args=(chat_id, msg.message_id, uid), daemon=True).start()

def _deploy_worker(cid, mid, uid):
    for frame in DEPLOY_ANIM[1:]:
        time.sleep(0.8)
        _edit(cid, mid, frame)

    name = f"vps-{uid}-{int(time.time())}"
    c = dm.create_container(uid, name)
    if not c:
        _edit(cid, mid,
              "❌ <b>Ошибка создания VPS</b>\n\nНе удалось запустить контейнер.\n"
              "Убедитесь что Docker запущен.",
              kb_back())
        return

    vps_id = db.add_vps(uid, c.id, name)
    vps    = db.get_vps(vps_id)
    stats  = dm.get_stats(c.id)
    _edit(cid, mid, _vps_card(vps, stats), kb_vps_ctrl(vps_id, "running"))

# ── Controls ───────────────────────────────────────────────────────────────────

def _do_tmate(cid, mid, uid, vps_id):
    vps = db.get_vps(vps_id)
    if not vps or vps[1] != uid:
        return
    _edit(cid, mid, TMATE_ANIM[0])

    def worker():
        for frame in TMATE_ANIM[1:]:
            time.sleep(0.9)
            _edit(cid, mid, frame)

        ssh = dm.get_tmate_ssh(vps[2])
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("🔄 Обновить",  callback_data=f"tmate_{vps_id}"),
            types.InlineKeyboardButton("🔙 К серверу", callback_data=f"vps_{vps_id}"),
        )
        if ssh:
            text = (
                "🔑 <b>TMATE SSH сессия</b>\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "✅ Сессия активна!\n\n"
                "📋 <b>Подключение:</b>\n"
                f"<code>ssh {ssh}</code>\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚠️ <i>Сессия живёт пока VPS запущен</i>"
            )
        else:
            text = (
                "❌ <b>TMATE не запустился</b>\n\n"
                "Убедитесь что VPS запущен.\n"
                "Подождите 30 сек после старта и повторите."
            )
        _edit(cid, mid, text, kb)

    threading.Thread(target=worker, daemon=True).start()

def _do_restart(cid, mid, uid, vps_id):
    vps = db.get_vps(vps_id)
    if not vps or vps[1] != uid:
        return
    _edit(cid, mid, "🔄 <b>Перезагружаем VPS...</b>")
    dm.restart_container(vps[2])
    time.sleep(3)
    _show_vps(cid, mid, uid, vps_id)

def _do_stop(cid, mid, uid, vps_id):
    vps = db.get_vps(vps_id)
    if not vps or vps[1] != uid:
        return
    _edit(cid, mid, "⏹️ <b>Останавливаем VPS...</b>")
    dm.stop_container(vps[2])
    db.update_status(vps_id, "exited")
    time.sleep(1)
    _show_vps(cid, mid, uid, vps_id)

def _do_start(cid, mid, uid, vps_id):
    vps = db.get_vps(vps_id)
    if not vps or vps[1] != uid:
        return
    _edit(cid, mid, "▶️ <b>Запускаем VPS...</b>")
    dm.start_container(vps[2])
    db.update_status(vps_id, "running")
    time.sleep(2)
    _show_vps(cid, mid, uid, vps_id)

def _do_delete(cid, mid, uid, vps_id):
    vps = db.get_vps(vps_id)
    if not vps or vps[1] != uid:
        return
    _edit(cid, mid, "🗑️ <b>Удаляем VPS...</b>")
    dm.remove_container(vps[2])
    db.delete_vps(vps_id)
    _edit(cid, mid,
          "✅ <b>VPS удалён</b>\n\nСервер успешно удалён.",
          types.InlineKeyboardMarkup().add(
              types.InlineKeyboardButton("🔙 Главное меню", callback_data="back_main")
          ))

# ══════════════════════════════════════════════════════════════════════════════
#  Entry
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("🚀 VPSNovaBot запускается...")
    db.init_db()
    print("✅ База данных готова")

    print("🐳 Проверка Docker образа...")
    if not dm.ensure_image():
        print("❌ Не удалось собрать образ! Проверьте Docker.")
        raise SystemExit(1)
    print("✅ Docker образ готов")

    print("✅ VPSNovaBot запущен!")
    bot.infinity_polling(timeout=10, long_polling_timeout=5)
