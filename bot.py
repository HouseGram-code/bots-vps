#!/usr/bin/env python3
"""VPSNovaBot — Free VPS in Telegram"""

import html
import os
import re
import sys
import time
import threading
import traceback

import telebot
from telebot import types

import database as db
import docker_manager as dm
from config import BOT_TOKEN, ADMIN_ID, MAX_VPS_PER_USER

# .env / .env.example грузит config.py — повторный load_dotenv() тут не нужен

if not BOT_TOKEN or ":" not in BOT_TOKEN:
    print("❌ BOT_TOKEN не задан. Укажите его в .env или .env.example")
    raise SystemExit(1)

_deploy_lock = threading.Lock()

BOT_VERSION = "1.0 бета"

# Анкета на VPS: uid -> {"step", "purpose", "rules", "plan", "cid", "mid", ...}
_apps = {}

PLAN_LABELS = {
    "test":   "🕐 Пара дней — попробовать",
    "weeks":  "📅 Несколько недель — под проект",
    "always": "♾️ Постоянно — бот/сайт 24/7",
}


def _esc(s):
    """Экранируем текст юзера — иначе '<' в ответе ломает HTML-разметку."""
    return html.escape(str(s or ""), quote=False)

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
    except Exception as e:
        # "message is not modified" и прочие ошибки не должны ронять поток
        if "message is not modified" not in str(e):
            print(f"[edit] {e}")

def _vps_card(vps, stats=None):
    vid, uid, cid, cname, status, osname, created = vps[:7]
    created = str(created or "")
    sem = "🟢 Online" if status == "running" else "🔴 Offline"
    lines = [
        f"🖥️ <b>{cname}</b>",
        "",
        "📋 <b>Информация</b>",
        f"├ 🆔 ID: <code>{vid}</code>",
        f"├ 🐧 OS: {osname}",
        f"├ 📡 Статус: {sem}",
        f"└ 📅 Создан: {created[:10] or '—'}",
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

# ── Анкета / модерация ────────────────────────────────────────

def kb_app_cancel():
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(types.InlineKeyboardButton("❌ Отменить заявку", callback_data="app_cancel"))
    return m

def kb_app_rules():
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(types.InlineKeyboardButton("✅ Да, обязуюсь соблюдать", callback_data="app_rules_yes"))
    m.add(types.InlineKeyboardButton("📜 Сначала прочитать правила", callback_data="rules"))
    m.add(types.InlineKeyboardButton("❌ Отменить заявку", callback_data="app_cancel"))
    return m

def kb_app_plan():
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(types.InlineKeyboardButton("🕐 Пара дней — просто попробовать", callback_data="app_plan_test"))
    m.add(types.InlineKeyboardButton("📅 Несколько недель — под проект", callback_data="app_plan_weeks"))
    m.add(types.InlineKeyboardButton("♾️ Постоянно — бот или сайт 24/7", callback_data="app_plan_always"))
    m.add(types.InlineKeyboardButton("❌ Отменить заявку", callback_data="app_cancel"))
    return m

def kb_app_confirm():
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(types.InlineKeyboardButton("📨 Отправить заявку", callback_data="app_send"))
    m.add(types.InlineKeyboardButton("✏️ Заполнить заново", callback_data="app_restart"))
    m.add(types.InlineKeyboardButton("❌ Отменить", callback_data="app_cancel"))
    return m

def kb_admin_app(app_id):
    m = types.InlineKeyboardMarkup(row_width=2)
    m.add(
        types.InlineKeyboardButton("✅ Принять",   callback_data=f"adm_app_ok_{app_id}"),
        types.InlineKeyboardButton("❌ Отклонить", callback_data=f"adm_app_no_{app_id}"),
    )
    m.add(types.InlineKeyboardButton("📨 К списку заявок", callback_data="adm_apps"))
    return m

def kb_vps_list(rows):
    m = types.InlineKeyboardMarkup(row_width=1)
    for row in rows:
        vid, uid, cid, cname, status, osname, created = row[:7]
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
    m.add(types.InlineKeyboardButton("📜 Правила сервиса", callback_data="rules"))
    m.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_main"))
    return m

def kb_rules_back():
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(types.InlineKeyboardButton("❓ Частые вопросы", callback_data="faq"))
    m.add(types.InlineKeyboardButton("🔙 Назад", callback_data="profile"))
    return m

def kb_faq():
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(types.InlineKeyboardButton("❌ VPS не работает",         callback_data="faq_1"))
    m.add(types.InlineKeyboardButton("⚠️ Произошёл сбой",          callback_data="faq_2"))
    m.add(types.InlineKeyboardButton("📈 Почему сервер нагружен?", callback_data="faq_3"))
    m.add(types.InlineKeyboardButton("💚 Бесплатно — реально?",   callback_data="faq_4"))
    m.add(types.InlineKeyboardButton("📜 Правила сервиса",       callback_data="rules"))
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
    m.add(types.InlineKeyboardButton("📣 Рассылка",    callback_data="adm_bcast"))
    pending = db.count_pending_applications()
    m.add(types.InlineKeyboardButton(
        f"📨 Заявки ({pending})" if pending else "📨 Заявки",
        callback_data="adm_apps"))
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
    _pending.pop(msg.from_user.id, None)   # сбрасываем висячий ввод админки
    _apps.pop(msg.from_user.id, None)
    _do_deploy_msg(msg.chat.id, msg.from_user.id,
                   msg.from_user.username, msg.from_user.first_name)

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower().startswith("!manage"))
def cmd_manage_cmd(msg):
    if msg.chat.type != "private":
        return
    db.upsert_user(msg.from_user.id, msg.from_user.username, msg.from_user.first_name)
    if not _check(msg):
        return
    _pending.pop(msg.from_user.id, None)
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
    text   = (msg.text or "").strip()
    if not text:
        bot.send_message(msg.chat.id, "❌ Нужен текст. Попробуйте снова.", reply_markup=kb_admin())
        return

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

    elif action == "bcast":
        body = getattr(msg, "html_text", None) or text
        _pending[uid] = {"action": "bcast_ready", "data": body}
        m = types.InlineKeyboardMarkup(row_width=1)
        m.add(types.InlineKeyboardButton(f"📣 Отправить всем ({db.count_all_users()})",
                                         callback_data="adm_bcast_go"))
        m.add(types.InlineKeyboardButton("❌ Отмена", callback_data="adm_bcast_no"))
        try:
            bot.send_message(msg.chat.id, "👀 <b>Предпросмотр рассылки:</b>")
            bot.send_message(msg.chat.id, body)
            bot.send_message(msg.chat.id,
                             "Отправляем это сообщение всем пользователям?",
                             reply_markup=m)
        except Exception as e:
            _pending.pop(uid, None)
            bot.send_message(msg.chat.id,
                             f"❌ Не получилось показать предпросмотр: {e}\n\n"
                             "Проверьте разметку и попроб��йте снова.",
                             reply_markup=kb_admin())

    elif action == "reject_app":
        _reject_application(msg.chat.id, task["data"], text)

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
        # имя контейнера Docker принимает только [a-zA-Z0-9_.-]
        cname = re.sub(r"[^a-zA-Z0-9_.-]", "-", cname)[:60].strip("-") or f"vps-{target_uid}-gift"
        # раньше выдача VPS админом игнорировала лимит слотов
        if db.count_all_vps() >= db.get_total_slots():
            bot.send_message(msg.chat.id,
                f"❌ Слоты заняты ({db.count_all_vps()}/{db.get_total_slots()}). "
                "Увеличьте число слотов в админке.",
                reply_markup=kb_admin())
            return
        # Deploy container
        c = dm.create_container(target_uid, cname)
        if c:
            db.upsert_user(target_uid, "", "")
            db.add_vps(target_uid, c.id, cname)
            bot.send_message(msg.chat.id,
                f"✅ VPS <b>{cname}</b> выдан пользователю <code>{target_uid}</code>",
                reply_markup=kb_admin())
            try:
                bot.send_message(target_uid,
                    f"🎁 Вам выдан VPS <b>{cname}</b>!\n"
                    "Откройте <b>📋 Мои серверы</b> или отправьте !manage")
            except Exception as e:
                print(f"[notify] {e}")
        else:
            bot.send_message(msg.chat.id, "❌ Ошибка создания контейнера.", reply_markup=kb_admin())

# ═══════════════════════════════════════════════════════════════════════════
#  Анкета на VPS + модерация заявок
# ═══════════════════════════════════════════════════════════════════════════

APP_Q1 = (
    "📝 <b>Заявка на бесплатный VPS</b>\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Отлично! Чтобы вы смогли получить сервер, ответьте на 3 коротких вопроса — "
    "заявку рассмотрит администратор.\n\n"
    "<b>Вопрос 1 из 3</b>  ▰▱▱\n"
    "🎯 <b>Для чего вам VPS?</b>\n\n"
    "<i>Напишите ответ обычным сообщением. Например: «хостинг Telegram-бота», "
    "«учусь работать с Linux», «небольшой сайт».</i>"
)


def _app_q2_text(st):
    return (
        "📝 <b>Заявка на бесплатный VPS</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ <b>1. Цель:</b> {_esc(st.get('purpose'))}\n\n"
        "<b>Вопрос 2 из 3</b>  ▰▰▱\n"
        "📜 <b>Готовы не нарушать правила сервиса?</b>\n\n"
        "Коротко: вежливое общение с админом и пользователями, 1 VPS в одни руки, "
        "без майнинга/DDoS и прочего вреда, без 100% CPU круглосуточно.\n\n"
        "<i>Можно сначала открыть полные правила — анкета не потеряется.</i>"
    )


def _app_q3_text(st):
    return (
        "📝 <b>Заявка на бесплатный VPS</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ <b>1. Цель:</b> {_esc(st.get('purpose'))}\n"
        "✅ <b>2. Правила:</b> обязуюсь соблюдать\n\n"
        "<b>Вопрос 3 из 3</b>  ▰▰▰\n"
        "⏳ <b>На какой срок вам нужен сервер?</b>\n\n"
        "Отвечайте честно — так мы видим, сколько слотов держать свободными "
        "и кому продлевать VPS в первую очередь.\n\n"
        "<i>Любой ответ нормальный: даже «просто попробовать» — повод выдать сервер.</i>"
    )


def _app_confirm_text(st):
    return (
        "📋 <b>Проверьте заявку</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 <b>Цель:</b> {_esc(st.get('purpose'))}\n"
        "📜 <b>Правила:</b> ✅ обязуюсь соблюдать\n"
        f"⏳ <b>Срок:</b> {PLAN_LABELS.get(st.get('plan'), '—')}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Всё верно? Нажмите <b>«📨 Отправить заявку»</b> — её увидит администратор."
    )


def _app_expired(cid, mid, uid):
    _apps.pop(uid, None)
    _edit(cid, mid,
          "⌛ <b>Анкета устарела</b>\n\nНачните заново: «🖥️ Получить VPS».",
          kb_main(uid))


def _start_application(cid, mid, uid, username="", first_name=""):
    used, total = db.count_all_vps(), db.get_total_slots()
    if used >= total:
        _edit(cid, mid,
              f"😔 <b>Свободных слотов нет</b>\n\nЗанято {used}/{total}. "
              "Загляните позже — слоты освобождаются.", kb_back())
        return
    if db.count_vps(uid) >= MAX_VPS_PER_USER:
        _edit(cid, mid,
              f"❌ <b>У вас уже есть VPS</b>\n\nЛимит — {MAX_VPS_PER_USER} на пользователя. "
              "Удалите текущий сервер, чтобы подать новую заявку.", kb_back())
        return
    app = db.get_user_pending_application(uid)
    if app:
        _edit(cid, mid,
              f"⏳ <b>Заявка #{app[0]} уже на рассмотрении</b>\n\n"
              "Администратор скоро её посмотрит — решение придёт в этот чат.\n"
              "<i>Дублировать заявки не нужно 🙏</i>", kb_back())
        return
    _apps[uid] = {"step": "purpose", "purpose": "", "rules": False, "plan": "",
                  "cid": cid, "mid": mid,
                  "username": username or "", "first_name": first_name or ""}
    _edit(cid, mid, APP_Q1, kb_app_cancel())


@bot.message_handler(func=lambda m: m.chat.type == "private"
                     and (_apps.get(m.from_user.id) or {}).get("step") == "purpose"
                     and bool(m.text) and not m.text.startswith("/"))
def handle_application_purpose(msg):
    uid = msg.from_user.id
    st  = _apps.get(uid)
    if not st:
        return
    text = (msg.text or "").strip()
    if len(text) < 5:
        bot.send_message(msg.chat.id,
                         "✏️ Слишком коротко. Опишите цель чуть подробнее (от 5 символов).")
        return
    st["purpose"] = text[:300]
    st["step"]    = "rules"
    try:
        bot.delete_message(msg.chat.id, msg.message_id)   # чистим чат
    except Exception:
        pass
    _edit(st["cid"], st["mid"], _app_q2_text(st), kb_app_rules())


def _admin_app_text(app):
    aid, auid, uname, fname, purpose, rules_ok, exp, status, reason, created = app[:10]
    who = f"@{uname}" if uname else "—"
    st_map = {"pending": "⏳ На рассмотрении",
              "approved": "✅ Принята",
              "rejected": "❌ Отклонена"}
    txt = (
        f"📨 <b>Заявка #{aid}</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "👤 <b>Пользователь</b>\n"
        f"├ 📛 Имя: {_esc(fname) or '—'}\n"
        f"├ 🔖 Username: {_esc(who)}\n"
        f"└ 🆔 ID: <code>{auid}</code>\n\n"
        "📋 <b>Ответы на вопросы</b>\n"
        f"├ 🎯 Цель: {_esc(purpose)}\n"
        f"├ 📜 Правила: {'✅ обязуется соблюдать' if rules_ok else '❌ не подтвердил'}\n"
        f"└ ⏳ Срок: {_esc(exp) or '—'}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🖥️ VPS у юзера сейчас: <b>{db.count_vps(auid)}</b>\n"
        f"📦 Слоты: <b>{db.count_all_vps()}/{db.get_total_slots()}</b>\n"
        f"📅 Подана: {str(created or '')[:16]}\n"
        f"📡 Статус: {st_map.get(status, status)}"
    )
    if reason:
        txt += f"\n💬 Причина отказа: {_esc(reason)}"
    return txt


def _send_application(cid, mid, uid, st):
    app_id = db.add_application(uid, st.get("username"), st.get("first_name"),
                                st.get("purpose"), st.get("rules"),
                                PLAN_LABELS.get(st.get("plan"), "—"))
    _apps.pop(uid, None)
    _edit(cid, mid,
          "✅ <b>Отлично, заявка отправлена!</b>\n\n"
          "━━━━━━━━━━━━━━━━━━━━━━\n\n"
          f"🆔 Номер заявки: <code>#{app_id}</code>\n"
          "⏳ <b>Ожидайте</b> — администратор рассмотрит её в ближайшее время.\n\n"
          "📬 Решение придёт сюда, в этот чат.\n"
          "Если заявку одобрят — VPS создастся автоматически и вы сразу получите "
          "карточку сервера с доступом.\n\n"
          "<i>Спасибо за терпение! 🙏</i>",
          kb_back())
    try:
        bot.send_message(ADMIN_ID,
                         "🔔 <b>Новая заявка на VPS</b>\n\n"
                         + _admin_app_text(db.get_application(app_id)),
                         reply_markup=kb_admin_app(app_id))
    except Exception as e:
        print(f"[notify admin] {e}")


def _approve_application(cid, mid, app_id):
    app = db.get_application(app_id)
    if not app:
        _edit(cid, mid, "❌ Заявка не найдена.", kb_admin())
        return
    if app[7] != "pending":
        _edit(cid, mid, f"ℹ️ Заявка #{app_id} уже обработана.", kb_back("adm_apps"))
        return

    target = app[1]
    _edit(cid, mid, f"⚙️ <b>Создаём VPS по заявке #{app_id}...</b>")

    with _deploy_lock:
        if db.count_all_vps() >= db.get_total_slots():
            _edit(cid, mid,
                  "😔 <b>Слоты заняты</b>\n\nУвеличьте число слотов в админке и повторите.",
                  kb_admin_app(app_id))
            return
        if db.count_vps(target) >= MAX_VPS_PER_USER:
            _edit(cid, mid,
                  f"⚠️ У пользователя <code>{target}</code> уже есть VPS "
                  f"(лимит {MAX_VPS_PER_USER}).",
                  kb_admin_app(app_id))
            return
        name = f"vps-{target}-{int(time.time())}"
        c = dm.create_container(target, name)
        if not c:
            _edit(cid, mid,
                  "❌ <b>Не удалось создать контейнер</b>\n\nПроверьте Docker и повторите.",
                  kb_admin_app(app_id))
            return
        db.upsert_user(target, app[2] or "", app[3] or "")
        vps_id = db.add_vps(target, c.id, name)
        db.set_application_status(app_id, "approved")

    # выдаём сервер пользователю сразу же
    try:
        bot.send_message(target,
                         f"🎉 <b>Заявка #{app_id} одобрена!</b>\n\n"
                         "Ваш бесплатный VPS уже создан и запущен. Приятной работы!\n"
                         "<i>Напоминаем про правила — они в профиле 📜</i>")
        vps = db.get_vps(vps_id)
        bot.send_message(target, _vps_card(vps, dm.get_stats(c.id)),
                         reply_markup=kb_vps_ctrl(vps_id, "running"))
    except Exception as e:
        print(f"[notify user] {e}")

    _edit(cid, mid,
          f"✅ <b>Заявка #{app_id} принята</b>\n\n"
          f"VPS <b>{name}</b> выдан пользователю <code>{target}</code> — "
          "уведомление отправлено.",
          kb_back("adm_apps"))


def _reject_application(admin_chat, app_id, reason_text):
    app = db.get_application(app_id)
    if not app:
        bot.send_message(admin_chat, "❌ Заявка не найдена.", reply_markup=kb_admin())
        return
    if app[7] != "pending":
        bot.send_message(admin_chat, f"ℹ️ Заявка #{app_id} уже обработана.",
                         reply_markup=kb_admin())
        return
    raw = (reason_text or "").strip()
    reason = "" if raw in ("-", "—", "") else raw[:300]
    db.set_application_status(app_id, "rejected", reason)
    target = app[1]
    try:
        bot.send_message(target,
                         f"❌ <b>Заявка #{app_id} отклонена</b>\n\n"
                         + (f"💬 <b>Причина:</b> {_esc(reason)}\n\n" if reason else "")
                         + "VPS в этот раз не выдан. Можно подать новую заявку: "
                           "опишите цель подробнее и подтвердите готовность соблюдать правила.\n\n"
                           "<i>Спасибо за понимание! 🙏</i>")
    except Exception as e:
        print(f"[notify user] {e}")
    bot.send_message(admin_chat,
                     f"❌ Заявка #{app_id} отклонена"
                     + (f" (причина: {_esc(reason)})" if reason else " без причины")
                     + ". VPS не выдан.",
                     reply_markup=kb_admin())


def _broadcast_worker(cid, mid, body):
    """Рассылка в фоне: не блокирует бота и держится в лимитах Telegram."""
    users = db.get_all_users()
    total = len(users)
    ok = fail = 0
    for i, u in enumerate(users, 1):
        target = u[0]
        try:
            if db.is_banned(target):
                continue
            bot.send_message(target, body)
            ok += 1
        except Exception:
            fail += 1                      # чаще всего — юзер заблокировал бота
        time.sleep(0.06)                   # лимит Telegram �� ~30 сообщений/сек
        if i % 25 == 0:
            try:
                _edit(cid, mid,
                      "📣 <b>Рассылка идёт...</b>\n\n"
                      f"✅ Доставлено: <b>{ok}</b>\n"
                      f"❌ Ошибок: <b>{fail}</b>\n"
                      f"📊 Обработано: <b>{i}/{total}</b>")
            except Exception:
                pass
    _edit(cid, mid,
          "📣 <b>Рассылка завершена</b>\n\n"
          "━━━━━━━━━━━━━━━━━━━━━━\n\n"
          f"✅ Доставлено: <b>{ok}</b>\n"
          f"❌ Не доставлено: <b>{fail}</b>\n"
          f"👥 Всего в базе: <b>{total}</b>\n\n"
          "<i>Не доставлено — те, кто заблокировал бота или удалил чат.</i>",
          kb_admin())

# ══════════════════════════════════════════════════════════════════════════════
#  Callbacks
# ══════════════════════════════════════════════════════════════════════════════

@bot.callback_query_handler(func=lambda c: True)
def on_callback(call):
    """Обёртка: любая ошибка (напр. битый callback_data → ValueError)
    больше не роняет обработку апдейтов."""
    try:
        _on_callback(call)
    except Exception:
        traceback.print_exc()
        try:
            bot.answer_callback_query(call.id, "⚠️ Ошибка, попробуйте ещё раз")
        except Exception:
            pass


def _on_callback(call):
    if call.message.chat.type != "private":
        return

    uid  = call.from_user.id
    cid  = call.message.chat.id
    mid  = call.message.message_id
    data = call.data

    db.upsert_user(uid, call.from_user.username, call.from_user.first_name)
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass  # устаревший callback — не причина падать

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

    elif data == "faq_4":
        _edit(cid, mid,
              "💚 <b>Бесплатно — реально?</b>\n\n"
              "━━━━━━━━━━━━━━━━━━━━━━\n\n"
              "✅ <b>Да, тариф полностью бесплатный.</b>\n"
              "Без оплаты, без карты и без скрытых платежей.\n\n"
              "🎁 <b>Что вы получаете:</b>\n"
              "├ 🐧 Ubuntu 22.04 с root-доступом\n"
              "├ 🧠 512 MB RAM • 💻 0.5 vCPU • 💿 512 MB\n"
              "└ 🔑 SSH через TMATE\n\n"
              "⚠️ <b>Говорим честно:</b>\n"
              "бесплатные VPS живут на общем хосте, поэтому иногда бывает "
              "небольшая нагрузка сервиса: просадки скорости, задержки при "
              "создании VPS или короткие тех. работы.\n\n"
              "🛠️ Мы всё понимаем и постоянно работаем над стабильностью — "
              "будем стараться всё почи��ить и ускорить.\n\n"
              "<i>Спасибо за понимание и терпение! 🙏</i>",
              kb_faq_back())

    # ── Правила ────────────────────────────────────────────────
    elif data == "rules":
        _edit(cid, mid,
              "📜 <b>Правила сервиса</b>\n\n"
              "━━━━━━━━━━━━━━━━━━━━━━\n\n"
              "<b>1️⃣ Вежливость</b>\n"
              "Обращайтесь к администратору и другим пользователям вежливо. "
              "Без оскорблений, угроз, спама и требований в приказном тоне.\n\n"
              "<b>2️⃣ Один VPS — один человек</b>\n"
              "Мультиаккаунты ради лишних слотов запрещены — слотов мало, "
              "их должно хватить всем.\n\n"
              "<b>3️⃣ Никакого вреда</b>\n"
              "Запрещены майнинг, DDoS, брутфорс, ботнеты, сканеры сетей, "
              "фишинг и любая незаконная активность.\n\n"
              "<b>4️⃣ Берегите ресурсы</b>\n"
              "Не держите CPU в 100% круглосуточно: сборки, стресс-тесты и "
              "тяжёлые задачи мешают остальным и могут быть остановлены.\n\n"
              "<b>5️⃣ Без важных данных</b>\n"
              "Тариф бесплатный: VPS может быть перезапущен или очищен. "
              "Делайте бэкапы сами.\n\n"
              "━━━━━━━━━━━━━━━━━━━━━━\n\n"
              "🚫 Нарушение любого пункта — удаление VPS или бан без возврата слота.\n"
              "💬 Вопрос или спорная ситуация? Напишите админу спокойно и по делу — "
              "таким обращениям помогаем в первую очередь.",
              # если правила открыты из анкеты — возвращаем к вопросу 2
              kb_app_rules() if (_apps.get(uid) or {}).get("step") == "rules"
              else kb_rules_back())

    # ── Deploy ─────────────────────────────────────────────────────────────
    elif data.startswith("deploy_"):
        if _is_admin(uid):
            _deploy_cb(cid, mid, uid)          # админ получает VPS без модерации
        else:
            _start_application(cid, mid, uid,
                               call.from_user.username, call.from_user.first_name)

    # ── Анкета на VPS ───────────────────────────────────────────
    elif data == "app_cancel":
        _apps.pop(uid, None)
        _edit(cid, mid,
              "❌ <b>Заявка отменена</b>\n\nМожете подать её заново в любой момент.",
              kb_main(uid))

    elif data == "app_restart":
        st = _apps.get(uid)
        if not st:
            _start_application(cid, mid, uid,
                               call.from_user.username, call.from_user.first_name)
        else:
            st.update({"step": "purpose", "purpose": "", "rules": False, "plan": "",
                       "cid": cid, "mid": mid})
            _edit(cid, mid, APP_Q1, kb_app_cancel())

    elif data == "app_rules_yes":
        st = _apps.get(uid)
        if not st:
            _app_expired(cid, mid, uid)
        else:
            st["rules"] = True
            st["step"] = "plan"
            st["cid"], st["mid"] = cid, mid
            _edit(cid, mid, _app_q3_text(st), kb_app_plan())

    elif data.startswith("app_plan_"):
        st = _apps.get(uid)
        if not st:
            _app_expired(cid, mid, uid)
        else:
            st["plan"] = data.rsplit("_", 1)[1]
            st["step"] = "confirm"
            st["cid"], st["mid"] = cid, mid
            _edit(cid, mid, _app_confirm_text(st), kb_app_confirm())

    elif data == "app_send":
        st = _apps.get(uid)
        if not st or st.get("step") != "confirm":
            _app_expired(cid, mid, uid)
        else:
            _send_application(cid, mid, uid, st)

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
          f"📨 Заявок ждёт:   <b>{db.count_pending_applications()}</b>\n"
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

    elif data == "adm_bcast":
        _pending[uid] = {"action": "bcast", "data": None}
        _edit(cid, mid,
              "📣 <b>Рассылка всем пользователям</b>\n\n"
              "━━━━━━━━━━━━━━━━━━━━━━\n\n"
              f"👥 Получателей в базе: <b>{db.count_all_users()}</b>\n\n"
              "Отправьте текст рассылки одним сообщением — можно с эмодзи "
              "и обычным форматированием Telegram.\n"
              "Перед отправкой покажу предпросмотр.",
              kb_back("admin"))

    elif data == "adm_bcast_go":
        task = _pending.pop(uid, None)
        body = (task or {}).get("data")
        if not body:
            _edit(cid, mid, "⌛ Текст рассылки потерялся. Начните заново.", kb_admin())
            return
        _edit(cid, mid, "📣 <b>Рассылка запущена...</b>")
        threading.Thread(target=_broadcast_worker, args=(cid, mid, body),
                         daemon=True).start()

    elif data == "adm_bcast_no":
        _pending.pop(uid, None)
        _edit(cid, mid, "❌ Рассылка отменена.", kb_admin())

    elif data == "adm_apps":
        apps = db.get_pending_applications()
        if not apps:
            _edit(cid, mid,
                  "📭 <b>Новых заявок нет</b>\n\nВсе заявки обработаны.", kb_admin())
            return
        m = types.InlineKeyboardMarkup(row_width=1)
        for a in apps[:10]:
            who = f"@{a[2]}" if a[2] else (a[3] or str(a[1]))
            m.add(types.InlineKeyboardButton(
                f"#{a[0]} • {who} • {(a[4] or '')[:25]}",
                callback_data=f"adm_app_{a[0]}"))
        m.add(types.InlineKeyboardButton("🔙 Назад", callback_data="admin"))
        _edit(cid, mid,
              f"📨 <b>Заявки на модерации: {len(apps)}</b>\n\n"
              "Выберите заявку, чтобы принять или отклонить её:", m)

    elif data.startswith("adm_app_ok_"):
        _approve_application(cid, mid, int(data.rsplit("_", 1)[1]))

    elif data.startswith("adm_app_no_"):
        app_id = int(data.rsplit("_", 1)[1])
        _pending[uid] = {"action": "reject_app", "data": app_id}
        _edit(cid, mid,
              f"❌ <b>Отклонение заявки #{app_id}</b>\n\n"
              "Напишите причину отказа сообщением — её увидит пользователь.\n"
              "Отправьте <code>-</code>, чтобы отклонить без причины.",
              kb_back("adm_apps"))

    elif data.startswith("adm_app_"):
        app = db.get_application(int(data.rsplit("_", 1)[1]))
        if not app:
            _edit(cid, mid, "❌ Заявка не найдена.", kb_admin())
            return
        _edit(cid, mid, _admin_app_text(app),
              kb_admin_app(app[0]) if app[7] == "pending" else kb_back("adm_apps"))

    elif data == "adm_give_vps":
        _pending[uid] = {"action": "give_vps", "data": None}
        _edit(cid, mid,
              "🎁 <b>Выдать VPS пользователю</b>\n\nВведите Telegram ID пользователя:",
              kb_back("admin"))

    elif data == "admin":
        _show_admin(cid, mid)

# ══════════════════════════════════════════════════════════════════════════════
#  Feature handlers
# ═══════════��══════════════════════════════════════════════════════════════════

def _show_profile(call):
    uid = call.from_user.id
    user = db.get_user(uid)
    n = db.count_vps(uid)
    uname = f"@{call.from_user.username}" if call.from_user.username else "—"
    reg = str(user[3])[:10] if user and len(user) > 3 and user[3] else "сегодня"
    limit = "∞" if _is_admin(uid) else str(MAX_VPS_PER_USER)
    app = db.get_user_pending_application(uid)
    app_line = f"├ 📨 Заявка:   <b>#{app[0]} — на рассмотрении</b>\n" if app else ""
    _edit(call.message.chat.id, call.message.message_id,
          f"👤 <b>Про��иль</b>\n\n"
          "━━━━━━━━━━━━━━━━━━━━━━\n\n"
          f"├ 📛 Имя:      <b>{call.from_user.first_name}</b>\n"
          f"├ 🔖 Username: {uname}\n"
          f"├ 🆔 ID:       <code>{uid}</code>\n"
          f"├ 🖥️ Серверов: <b>{n}</b> / {limit}\n"
          f"{app_line}"
          f"├ 📅 С нами:   {reg}\n"
          f"└ 🤖 Версия бота: <b>{BOT_VERSION}</b>\n\n"
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
        try:
            c = dm.get_container(r[2])
        except Exception:
            c = None
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

def _do_deploy_msg(chat_id, uid, username="", first_name=""):
    # обычные пользователи проходят модерацию, админ получает VPS сразу
    if not _is_admin(uid):
        m = bot.send_message(chat_id, "📝 <b>Готовим анкету на VPS...</b>")
        _start_application(chat_id, m.message_id, uid, username, first_name)
        return
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
    try:
        for frame in DEPLOY_ANIM[1:]:
            time.sleep(0.8)
            _edit(cid, mid, frame)

        # Лок: без него два одновременных деплоя пробивали лимит слотов
        with _deploy_lock:
            if db.count_all_vps() >= db.get_total_slots():
                _edit(cid, mid, "😔 <b>Слоты заняты</b>\n\nПопробуйте позже.", kb_back())
                return
            if not _is_admin(uid) and db.count_vps(uid) >= MAX_VPS_PER_USER:
                _edit(cid, mid, "❌ <b>Лимит!</b> У вас уже есть VPS.", kb_back())
                return

            name = f"vps-{uid}-{int(time.time())}"
            c = dm.create_container(uid, name)
            if not c:
                _edit(cid, mid,
                      "❌ <b>Ошибка создания VPS</b>\n\nНе удалось запустить контейнер.\n"
                      "Убедитесь что Docker запущен и сокет пробр��шен в контейнер бота.",
                      kb_back())
                return
            vps_id = db.add_vps(uid, c.id, name)

        vps   = db.get_vps(vps_id)
        stats = dm.get_stats(c.id)
        _edit(cid, mid, _vps_card(vps, stats), kb_vps_ctrl(vps_id, "running"))
    except Exception:
        traceback.print_exc()
        _edit(cid, mid, "❌ <b>Внутренняя ошибка при создании VPS</b>", kb_back())

# ── Controls ───────────────────────────────────────────────────────────────────

def _do_tmate(cid, mid, uid, vps_id):
    vps = db.get_vps(vps_id)
    if not vps or vps[1] != uid:
        return
    # раньше бот крутил анимацию 5 сек и только потом говорил об ошибке
    c = dm.get_container(vps[2])
    if not c or c.status != "running":
        db.update_status(vps_id, c.status if c else "exited")
        _edit(cid, mid,
              "⚠️ <b>VPS остановлен</b>\n\n"
              "Запустите сервер кнопкой <b>▶️ Запустить</b>, потом берите SSH.",
              kb_vps_ctrl(vps_id, "exited"))
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
                "Убе��итесь что VPS запущен.\n"
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

def main():
    print("🚀 VPSNovaBot запускается...")
    db.init_db()
    print("✅ База данных готова")

    print("🐳 Проверка Docker...")
    ok, info = dm.diagnose()
    print(info)
    if not ok:
        print("❌ Docker не готов — см. сообщение выше")
        raise SystemExit(1)

    print("🐳 Проверка Docker образа...")
    if not dm.ensure_image():
        print("❌ Не удалось собрать образ! Проверьте Docker и проброс /var/run/docker.sock")
        raise SystemExit(1)
    print("✅ Docker образ готов")

    # Снимаем возможный webhook — иначе polling получает 409 Conflict
    try:
        bot.remove_webhook()
        time.sleep(0.5)
    except Exception as e:
        print(f"[warn] remove_webhook: {e}")

    print("✅ VPSNovaBot запущен!")
    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=20,
                                 skip_pending=True, allowed_updates=None)
        except KeyboardInterrupt:
            print("⏹️ Остановка")
            break
        except Exception:
            traceback.print_exc()
            print("[polling] перезапуск через 5с...")
            time.sleep(5)


if __name__ == "__main__":
    main()
