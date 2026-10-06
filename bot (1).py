# -*- coding: utf-8 -*-
"""
STREET DOG URGANCH — Telegram bot + buyurtma qabul qilish serveri.

Bitta jarayonda ikkita narsa ishlaydi:
  1) Telegram bot (buyruqlar, admin tugmalari, mijozga xabarlar);
  2) Kichik HTTP server (aiohttp): ilova (Mini App) buyurtmani shu yerga yuboradi.

Sozlamalar .env faylida (qarang: .env.example). Token kodda saqlanmaydi.
"""
from __future__ import annotations

import asyncio
import functools
import hashlib
import hmac
import html
import json
import logging
import math
import os
import re
import sqlite3
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web
from telegram import (
    BotCommand,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    MenuButtonWebApp,
    ReplyKeyboardRemove,
    Update,
    WebAppInfo,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)
from telegram.request import HTTPXRequest


# =====================================================================
#  SOZLAMALAR (.env fayldan o'qiladi)
# =====================================================================
def load_env_file(path: Path) -> None:
    """Oddiy .env o'qigich (qo'shimcha kutubxona kerak emas)."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


BASE_DIR = Path(__file__).resolve().parent
load_env_file(BASE_DIR / ".env")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("streetdog")


def env_int(name: str, default=None):
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("%s qiymati noto'g'ri (%r), e'tiborga olinmadi", name, raw)
        return default


def env_float(name: str):
    raw = os.getenv(name, "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        log.warning("%s qiymati noto'g'ri (%r), e'tiborga olinmadi", name, raw)
        return None


def env_int_list(name: str, default: str) -> list:
    result = []
    for part in os.getenv(name, default).replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            result.append(int(part))
    return result


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://jamshid0217.github.io/street-dog-app/").strip()
ADMIN_IDS = env_int_list("ADMIN_IDS", "6069854654")
COURIER_CHAT_ID = env_int("COURIER_CHAT_ID", None)  # kuryerlar guruhi (ixtiyoriy)
ALLOWED_ORIGINS = [
    o.strip().rstrip("/")
    for o in os.getenv("ALLOWED_ORIGINS", "https://jamshid0217.github.io").split(",")
    if o.strip()
]
HOST = os.getenv("HOST", "0.0.0.0").strip()
PORT = env_int("PORT", 8080)
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "street_dog.db")).strip()

CONTACT_PHONE = os.getenv("CONTACT_PHONE", "+998 91 966 40 40").strip()
CAFE_ADDRESS = os.getenv("CAFE_ADDRESS", "Urganch shahri").strip()
WORK_HOURS = os.getenv("WORK_HOURS", "").strip()
CAFE_LAT = env_float("CAFE_LAT")
CAFE_LON = env_float("CAFE_LON")

# O'zbekiston vaqti (UTC+5, yozgi/qishki vaqt yo'q)
LOCAL_TZ = timezone(timedelta(hours=5))

# initData necha soniyagacha eskirmagan hisoblanadi (2 kun)
INIT_DATA_MAX_AGE = 2 * 24 * 3600

# =====================================================================
#  MENYU (index.html dagi mahsulotlar bilan bir xil bo'lishi SHART)
#  Narx yoki yangi taom qo'shsangiz, bu yerni ham yangilang.
# =====================================================================
CATEGORIES = {
    "hotdog": "🌭 Xot-Dog",
    "burger": "🍔 Burger",
    "garnir": "🍟 Garnirlar",
    "drink": "🥤 Ichimlik",
}

PRODUCTS = {
    'h1_25': {"category": 'hotdog', "name": 'Achchiq xot-dog', "price": 25000},
    'h1_35': {"category": 'hotdog', "name": 'Achchiq xot-dog', "price": 35000},
    'h2_30': {"category": 'hotdog', "name": 'Chikago xot-dog', "price": 30000},
    'h2_45': {"category": 'hotdog', "name": 'Chikago xot-dog', "price": 45000},
    'h3_50': {"category": 'hotdog', "name": 'Amerikancha xot-dog', "price": 50000},
    'h4_30': {"category": 'hotdog', "name": 'Nyu-York xot-dog', "price": 30000},
    'h5_30': {"category": 'hotdog', "name": 'Mehiko xot-dog', "price": 30000},
    'h5_45': {"category": 'hotdog', "name": 'Mehiko xot-dog', "price": 45000},
    'h6_35': {"category": 'hotdog', "name": 'Klassik xot-dog', "price": 35000},
    'h7_30': {"category": 'hotdog', "name": 'Chikken xot-dog', "price": 30000},
    'h7_40': {"category": 'hotdog', "name": 'Chikken xot-dog', "price": 40000},
    'h8_25': {"category": 'hotdog', "name": 'Fransuzcha xot-dog', "price": 25000},
    'h9_30': {"category": 'hotdog', "name": 'Premium xot-dog', "price": 30000},
    'h10_20': {"category": 'hotdog', "name": 'Bolalar xot-dogi', "price": 20000},
    'h11_40': {"category": 'hotdog', "name": 'Royol Fresh', "price": 40000},
    'h12_35': {"category": 'hotdog', "name": 'Sendvich', "price": 35000},
    'h13_35': {"category": 'hotdog', "name": 'Italyancha sendvich', "price": 35000},
    'b1_65': {"category": 'burger', "name": 'Strit burger', "price": 65000},
    'b1_80': {"category": 'burger', "name": 'Strit burger', "price": 80000},
    'b1_95': {"category": 'burger', "name": 'Strit burger', "price": 95000},
    'b2_65': {"category": 'burger', "name": 'Klassik burger', "price": 65000},
    'b2_80': {"category": 'burger', "name": 'Klassik burger', "price": 80000},
    'b2_95': {"category": 'burger', "name": 'Klassik burger', "price": 95000},
    'b3_65': {"category": 'burger', "name": 'Tryufelli burger', "price": 65000},
    'b3_80': {"category": 'burger', "name": 'Tryufelli burger', "price": 80000},
    'b3_95': {"category": 'burger', "name": 'Tryufelli burger', "price": 95000},
    'b4_65': {"category": 'burger', "name": 'Achchiq burger', "price": 65000},
    'b4_80': {"category": 'burger', "name": 'Achchiq burger', "price": 80000},
    'b4_95': {"category": 'burger', "name": 'Achchiq burger', "price": 95000},
    'b5_75': {"category": 'burger', "name": 'Steyk burger', "price": 75000},
    'b6_40': {"category": 'burger', "name": 'Chizburger', "price": 40000},
    'b7_45': {"category": 'burger', "name": 'Chikken burger', "price": 45000},
    'b8_65': {"category": 'burger', "name": 'BBK burger', "price": 65000},
    'b8_80': {"category": 'burger', "name": 'BBK burger', "price": 80000},
    'b8_95': {"category": 'burger', "name": 'BBK burger', "price": 95000},
    'g1_58': {"category": 'garnir', "name": 'Strips boks', "price": 58000},
    'g2_60': {"category": 'garnir', "name": 'Mitboks', "price": 60000},
    'g3_35': {"category": 'garnir', "name": 'Pishloqli yostiqchalar', "price": 35000},
    'g4_30': {"category": 'garnir', "name": 'Tryufelli fri', "price": 30000},
    'g5_25': {"category": 'garnir', "name": 'Aydaxo kartoshka', "price": 25000},
    'g6_20': {"category": 'garnir', "name": 'Kartoshka fri', "price": 20000},
    'p3': {"category": 'drink', "name": 'Coca-Cola 0.5L', "price": 8000},
    'p4': {"category": 'drink', "name": 'Fanta 0.5L', "price": 8000},
    'p5': {"category": 'drink', "name": 'Sprite 0.5L', "price": 8000},
}

PAYMENT_METHODS = {"Naqd / Karta", "Click / Payme"}

STATUS_LABELS = {
    "new": "🆕 Yangi",
    "accepted": "✅ Qabul qilindi",
    "ready": "👨‍🍳 Tayyor",
    "onway": "🛵 Yo'lda",
    "done": "✔️ Yakunlandi",
    "cancelled": "❌ Bekor qilindi",
}

# Qaysi holatdan qaysi holatga o'tish mumkin
TRANSITIONS = {
    "new": {"accepted", "cancelled"},
    "accepted": {"ready", "cancelled"},
    "ready": {"onway", "done", "cancelled"},
    "onway": {"done", "cancelled"},
}

DEFAULT_CLOSED_MESSAGE = "Hozir buyurtma qabul qilinmaydi. Iltimos, keyinroq urinib ko'ring."

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

ADMIN_HELP = (
    "<b>🛠 Admin buyruqlari</b>\n"
    "/ochish — buyurtma qabul qilishni ochish\n"
    "/yopish [izoh] — buyurtma qabul qilishni to'xtatish\n"
    "/stop — stop-list (tugagan taomlarni o'chirish/yoqish)\n"
    "/stats — statistika\n"
    "/broadcast matn — barcha mijozlarga xabar yuborish"
)


# =====================================================================
#  YORDAMCHI FUNKSIYALAR
# =====================================================================
def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def fmt_money(value) -> str:
    return f"{int(value):,}".replace(",", " ")


def fmt_time(ts: int) -> str:
    return datetime.fromtimestamp(ts, LOCAL_TZ).strftime("%d.%m.%Y %H:%M")


BACKGROUND_TASKS: set = set()


def _task_done(task: asyncio.Task) -> None:
    BACKGROUND_TASKS.discard(task)
    if not task.cancelled() and task.exception():
        log.error("Fon vazifasida xato", exc_info=task.exception())


def spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    BACKGROUND_TASKS.add(task)
    task.add_done_callback(_task_done)
    return task


# =====================================================================
#  MA'LUMOTLAR BAZASI (SQLite)
# =====================================================================
_db = None


def db() -> sqlite3.Connection:
    global _db
    if _db is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        conn.execute("PRAGMA foreign_keys=ON")
        _db = conn
    return _db


def init_db() -> None:
    db().executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id    INTEGER PRIMARY KEY,
            username   TEXT,
            full_name  TEXT,
            first_seen INTEGER NOT NULL,
            last_seen  INTEGER NOT NULL,
            blocked    INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS orders (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id     TEXT,
            user_id       INTEGER NOT NULL,
            username      TEXT,
            full_name     TEXT,
            phone         TEXT NOT NULL,
            delivery_type TEXT NOT NULL,
            address       TEXT,
            lat           REAL,
            lon           REAL,
            payment       TEXT NOT NULL,
            items_json    TEXT NOT NULL,
            total         INTEGER NOT NULL,
            status        TEXT NOT NULL DEFAULT 'new',
            rating        INTEGER,
            created_at    INTEGER NOT NULL,
            updated_at    INTEGER NOT NULL,
            UNIQUE (user_id, client_id)
        );
        CREATE INDEX IF NOT EXISTS idx_orders_created ON orders (created_at);
        CREATE INDEX IF NOT EXISTS idx_orders_user ON orders (user_id);
        CREATE TABLE IF NOT EXISTS order_msgs (
            order_id   INTEGER NOT NULL,
            chat_id    INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            kind       TEXT NOT NULL,
            PRIMARY KEY (order_id, chat_id, message_id)
        );
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS stoplist (
            product_id TEXT PRIMARY KEY
        );
        """
    )


# ---- sozlamalar (ochiq/yopiq) ----
def get_setting(key: str, default: str = "") -> str:
    row = db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row and row["value"] is not None else default


def set_setting(key: str, value: str) -> None:
    db().execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def is_open() -> bool:
    return get_setting("is_open", "1") == "1"


def closed_message() -> str:
    return get_setting("closed_message", "") or DEFAULT_CLOSED_MESSAGE


# ---- stop-list ----
def get_stoplist() -> set:
    return {r["product_id"] for r in db().execute("SELECT product_id FROM stoplist")}


def toggle_stop(product_id: str) -> bool:
    """True qaytarsa — mahsulot endi O'CHIRILGAN (tugagan)."""
    conn = db()
    row = conn.execute("SELECT 1 FROM stoplist WHERE product_id=?", (product_id,)).fetchone()
    if row:
        conn.execute("DELETE FROM stoplist WHERE product_id=?", (product_id,))
        return False
    conn.execute("INSERT INTO stoplist (product_id) VALUES (?)", (product_id,))
    return True


def clear_stoplist() -> None:
    db().execute("DELETE FROM stoplist")


# ---- foydalanuvchilar ----
def upsert_user(user_id: int, username, full_name) -> None:
    now = int(time.time())
    db().execute(
        "INSERT INTO users (user_id, username, full_name, first_seen, last_seen, blocked) "
        "VALUES (?, ?, ?, ?, ?, 0) "
        "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, "
        "full_name=excluded.full_name, last_seen=excluded.last_seen, blocked=0",
        (user_id, username, full_name, now, now),
    )


def mark_blocked(user_id: int) -> None:
    db().execute("UPDATE users SET blocked=1 WHERE user_id=?", (user_id,))


# ---- buyurtmalar ----
def create_order(user: dict, client_id, phone, delivery, address, lat, lon, payment, items, total):
    """(order_id, total, yangi_yaratildimi) qaytaradi."""
    conn = db()
    uid = user["id"]
    if client_id:
        row = conn.execute(
            "SELECT id, total FROM orders WHERE user_id=? AND client_id=?", (uid, client_id)
        ).fetchone()
        if row:
            return row["id"], row["total"], False
    now = int(time.time())
    cur = conn.execute(
        "INSERT INTO orders (client_id, user_id, username, full_name, phone, delivery_type, "
        "address, lat, lon, payment, items_json, total, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)",
        (
            client_id,
            uid,
            user.get("username"),
            user.get("full_name"),
            phone,
            delivery,
            address,
            lat,
            lon,
            payment,
            json.dumps(items, ensure_ascii=False),
            total,
            now,
            now,
        ),
    )
    return cur.lastrowid, total, True


def get_order(order_id: int):
    return db().execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()


def set_order_status(order_id: int, status: str) -> None:
    db().execute(
        "UPDATE orders SET status=?, updated_at=? WHERE id=?", (status, int(time.time()), order_id)
    )


def set_order_rating(order_id: int, rating: int) -> None:
    db().execute("UPDATE orders SET rating=? WHERE id=?", (rating, order_id))


def recent_order_count(user_id: int, seconds: int) -> int:
    since = int(time.time()) - seconds
    row = db().execute(
        "SELECT COUNT(*) AS n FROM orders WHERE user_id=? AND created_at>=?", (user_id, since)
    ).fetchone()
    return row["n"]


def save_msg(order_id: int, chat_id: int, message_id: int, kind: str) -> None:
    db().execute(
        "INSERT OR IGNORE INTO order_msgs (order_id, chat_id, message_id, kind) VALUES (?, ?, ?, ?)",
        (order_id, chat_id, message_id, kind),
    )


def get_msgs(order_id: int):
    return db().execute(
        "SELECT chat_id, message_id, kind FROM order_msgs WHERE order_id=?", (order_id,)
    ).fetchall()


# =====================================================================
#  TELEGRAM MINI APP MA'LUMOTINI TEKSHIRISH (soxta buyurtmalardan himoya)
# =====================================================================
def validate_init_data(init_data: str, token: str, max_age: int = INIT_DATA_MAX_AGE):
    """Telegram imzosi to'g'ri bo'lsa foydalanuvchi (dict), aks holda None."""
    if not init_data or not token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        return None
    try:
        auth_date = int(pairs.get("auth_date", "0"))
        user = json.loads(pairs.get("user", ""))
        user_id = int(user["id"])
    except (ValueError, KeyError, TypeError):
        return None
    if time.time() - auth_date > max_age:
        return None
    full_name = " ".join(
        p for p in (user.get("first_name"), user.get("last_name")) if p
    ).strip()
    return {"id": user_id, "username": user.get("username"), "full_name": full_name or "Mijoz"}


# =====================================================================
#  TELEGRAM YUBORISH (429 bo'lsa kutib qayta urinadi)
# =====================================================================
def _retry_seconds(exc) -> float:
    value = exc.retry_after
    if hasattr(value, "total_seconds"):
        return float(value.total_seconds())
    return float(value)


async def tg_call(func, *args, retries: int = 5, **kwargs):
    last = None
    for attempt in range(retries):
        try:
            return await func(*args, **kwargs)
        except RetryAfter as exc:
            await asyncio.sleep(_retry_seconds(exc) + 0.5)
        except (BadRequest, Forbidden):
            raise
        except NetworkError as exc:  # TimedOut ham shu yerga kiradi
            last = exc
            await asyncio.sleep(1 + attempt)
    if last is not None:
        raise last
    raise TelegramError("Telegram so'rovi bajarilmadi (juda ko'p urinish)")


async def safe_send(bot, chat_id, text, reply_markup=None):
    """HTML xabar yuboradi. Muvaffaqiyatsiz bo'lsa None qaytaradi (xato bermaydi)."""
    try:
        return await tg_call(
            bot.send_message,
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
            link_preview_options=NO_PREVIEW,
        )
    except Forbidden:
        log.warning("Xabar yuborib bo'lmadi (bloklangan/boshlanmagan): %s", chat_id)
    except TelegramError as exc:
        log.error("Xabar yuborishda xato (%s): %s", chat_id, exc)
    return None


async def safe_edit(bot, chat_id, message_id, text, reply_markup=None):
    try:
        await tg_call(
            bot.edit_message_text,
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
            link_preview_options=NO_PREVIEW,
        )
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            log.error("Xabarni tahrirlashda xato (%s/%s): %s", chat_id, message_id, exc)
    except TelegramError as exc:
        log.error("Xabarni tahrirlashda xato (%s/%s): %s", chat_id, message_id, exc)


async def safe_location(bot, chat_id, lat, lon):
    try:
        await tg_call(bot.send_location, chat_id=chat_id, latitude=lat, longitude=lon)
    except TelegramError as exc:
        log.warning("Lokatsiya yuborilmadi (%s): %s", chat_id, exc)


# =====================================================================
#  XABAR MATNLARI VA TUGMALAR
# =====================================================================
def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)


def status_label(order) -> str:
    if order["status"] == "done":
        return "✔️ Yetkazildi" if order["delivery_type"] == "delivery" else "✔️ Olib ketildi"
    return STATUS_LABELS.get(order["status"], order["status"])


def order_text(order) -> str:
    items = json.loads(order["items_json"])
    lines = [
        f"• {esc(i['name'])} x {i['count']} = {fmt_money(i['price'] * i['count'])} so'm"
        for i in items
    ]
    is_delivery = order["delivery_type"] == "delivery"
    uname = f" (@{esc(order['username'])})" if order["username"] else ""
    customer = f'<a href="tg://user?id={order["user_id"]}">{esc(order["full_name"] or "Mijoz")}</a>{uname}'
    parts = [
        f"🛒 <b>BUYURTMA #{order['id']}</b>",
        f"📌 Holat: <b>{esc(status_label(order))}</b>",
        "",
        f"👤 Mijoz: {customer}",
        f"📞 Tel: {esc(order['phone'])}",
        f"🚚 Turi: {'Dostavka 🛵' if is_delivery else 'Olib ketish 🏃‍♂️'}",
        f"📍 Manzil: {esc(order['address']) if is_delivery else 'Olib ketish (kafe joyidan)'}",
    ]
    if is_delivery and order["lat"] is not None and order["lon"] is not None:
        parts.append(
            f'🗺 <a href="https://maps.google.com/?q={order["lat"]},{order["lon"]}">Xaritada ochish</a>'
        )
    parts += [
        f"💳 To'lov: {esc(order['payment'])}",
        "",
        "📦 <b>Buyurtma tarkibi:</b>",
        *lines,
        "",
        f"💵 <b>Jami: {fmt_money(order['total'])} so'm</b>",
        f"🕒 {fmt_time(order['created_at'])}",
    ]
    return "\n".join(parts)


def receipt_text(order) -> str:
    items = json.loads(order["items_json"])
    lines = [
        f"• {esc(i['name'])} x {i['count']} = {fmt_money(i['price'] * i['count'])} so'm"
        for i in items
    ]
    is_delivery = order["delivery_type"] == "delivery"
    return "\n".join(
        [
            f"✅ <b>Buyurtmangiz qabul qilindi! (#{order['id']})</b>",
            "",
            *lines,
            "",
            f"💵 <b>Jami: {fmt_money(order['total'])} so'm</b>",
            f"🚚 {'Dostavka 🛵' if is_delivery else 'Olib ketish 🏃‍♂️'}",
            f"💳 {esc(order['payment'])}",
            "",
            "Buyurtma holati o'zgarganda sizga xabar beramiz. 😊",
            f"Savollar bo'lsa: {esc(CONTACT_PHONE)}",
        ]
    )


def admin_keyboard(order):
    oid = order["id"]
    status = order["status"]
    is_delivery = order["delivery_type"] == "delivery"
    rows = []
    if status == "new":
        rows.append([_btn("✅ Qabul qilish", f"st:{oid}:accepted")])
    elif status == "accepted":
        rows.append([_btn("👨‍🍳 Tayyor", f"st:{oid}:ready")])
    elif status == "ready":
        if is_delivery:
            rows.append([_btn("🛵 Yo'lga chiqdi", f"st:{oid}:onway")])
        else:
            rows.append([_btn("✔️ Olib ketildi", f"st:{oid}:done")])
    elif status == "onway":
        rows.append([_btn("✔️ Yetkazildi", f"st:{oid}:done")])
    else:
        return None
    rows.append([_btn("❌ Bekor qilish", f"cq:{oid}")])
    return InlineKeyboardMarkup(rows)


def cancel_confirm_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[_btn("✅ Ha, bekor qilish", f"st:{order_id}:cancelled"), _btn("↩️ Yo'q", f"cb:{order_id}")]]
    )


def rating_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_btn(f"{n}⭐", f"rate:{order_id}:{n}") for n in range(1, 6)]])


def customer_status_text(order):
    oid = order["id"]
    is_delivery = order["delivery_type"] == "delivery"
    status = order["status"]
    if status == "accepted":
        return f"✅ Buyurtmangiz #{oid} qabul qilindi! Tayyorlashni boshladik. 👨‍🍳"
    if status == "ready":
        if is_delivery:
            return f"🍽 Buyurtmangiz #{oid} tayyor! Tez orada kuryerga topshiramiz."
        return f"🍽 Buyurtmangiz #{oid} tayyor! Kafemizga kelib olib ketishingiz mumkin."
    if status == "onway":
        return f"🛵 Buyurtmangiz #{oid} yo'lda! Kuryer tez orada yetib boradi."
    if status == "done":
        if is_delivery:
            return f"✔️ Buyurtmangiz #{oid} yetkazildi. Yoqimli ishtaha! 😋"
        return f"✔️ Buyurtmangiz #{oid} topshirildi. Yoqimli ishtaha! 😋"
    if status == "cancelled":
        return (
            f"❌ Afsuski, buyurtmangiz #{oid} bekor qilindi.\n"
            f"Savollar bo'lsa, bog'laning: {esc(CONTACT_PHONE)}"
        )
    return None


# =====================================================================
#  BUYURTMA BILDIRISHNOMALARI
# =====================================================================
async def notify_new_order(bot, order_id: int) -> None:
    order = get_order(order_id)
    if not order:
        return
    text = order_text(order)
    has_location = (
        order["delivery_type"] == "delivery" and order["lat"] is not None and order["lon"] is not None
    )

    targets = [(admin_id, "admin") for admin_id in ADMIN_IDS]
    if COURIER_CHAT_ID is not None:
        targets.append((COURIER_CHAT_ID, "courier"))

    for chat_id, kind in targets:
        markup = admin_keyboard(order) if kind == "admin" else None
        msg = await safe_send(bot, chat_id, text, reply_markup=markup)
        if msg:
            save_msg(order_id, chat_id, msg.message_id, kind)
        if has_location:
            await safe_location(bot, chat_id, order["lat"], order["lon"])

    receipt = await safe_send(bot, order["user_id"], receipt_text(order))
    if receipt is None:
        warn = (
            f"⚠️ Buyurtma #{order_id}: mijozga bot xabar yubora olmadi "
            f"(botni Start qilmagan bo'lishi mumkin). Telefon orqali bog'laning: {esc(order['phone'])}"
        )
        for admin_id in ADMIN_IDS:
            await safe_send(bot, admin_id, warn)


async def refresh_order_messages(bot, order_id: int) -> None:
    order = get_order(order_id)
    if not order:
        return
    text = order_text(order)
    for m in get_msgs(order_id):
        markup = admin_keyboard(order) if m["kind"] == "admin" else None
        await safe_edit(bot, m["chat_id"], m["message_id"], text, markup)


async def notify_customer_status(bot, order) -> None:
    text = customer_status_text(order)
    if not text:
        return
    await safe_send(bot, order["user_id"], text)
    if order["status"] == "done":
        await safe_send(
            bot,
            order["user_id"],
            "Xizmatimizni baholang, bu biz uchun juda muhim: 👇",
            reply_markup=rating_keyboard(order["id"]),
        )


# =====================================================================
#  HTTP API (Mini App shu yerga murojaat qiladi)
# =====================================================================
def cors_headers(request) -> dict:
    origin = request.headers.get("Origin", "").rstrip("/")
    headers = {
        "Vary": "Origin",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Max-Age": "86400",
    }
    if "*" in ALLOWED_ORIGINS:
        headers["Access-Control-Allow-Origin"] = "*"
    elif origin and origin in ALLOWED_ORIGINS:
        headers["Access-Control-Allow-Origin"] = origin
    return headers


@web.middleware
async def cors_middleware(request, handler):
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        response = exc
    except Exception:  # kutilmagan xato — foydalanuvchiga tushunarli javob
        log.exception("API xatosi: %s %s", request.method, request.path)
        response = web.json_response(
            {"ok": False, "error": "Serverda xatolik yuz berdi. Qaytadan urinib ko'ring."},
            status=500,
        )
    for key, value in cors_headers(request).items():
        response.headers[key] = value
    return response


def jerr(message: str, status: int = 400, **extra):
    return web.json_response({"ok": False, "error": message, **extra}, status=status)


async def api_options(request):
    return web.Response(status=204)


async def api_health(request):
    return web.Response(text="Street Dog server ishlayapti ✅")


async def api_status(request):
    return web.json_response(
        {
            "ok": True,
            "open": is_open(),
            "message": "" if is_open() else closed_message(),
            "stoplist": sorted(get_stoplist()),
        }
    )


async def read_json(request):
    try:
        body = await request.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None


def parse_location(raw):
    if not isinstance(raw, dict):
        return None, None
    try:
        lat = float(raw.get("latitude"))
        lon = float(raw.get("longitude"))
    except (TypeError, ValueError):
        return None, None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None, None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None, None
    return round(lat, 6), round(lon, 6)


async def api_order(request):
    body = await read_json(request)
    if body is None:
        return jerr("So'rov noto'g'ri formatda.")

    user = validate_init_data(str(body.get("init_data", "")), BOT_TOKEN)
    if not user:
        return jerr(
            "Telegram orqali tasdiqlanmadi. Ilovani Telegramdagi 'Open App' tugmasi orqali qayta oching.",
            401,
        )

    if not is_open():
        return jerr(closed_message(), 403, code="closed")

    # --- savat ---
    raw_items = body.get("items")
    if not isinstance(raw_items, list) or not raw_items or len(raw_items) > 60:
        return jerr("Savat bo'sh yoki noto'g'ri.")
    merged: dict = {}
    for entry in raw_items:
        if not isinstance(entry, dict):
            return jerr("Savat ma'lumoti noto'g'ri.")
        pid = entry.get("id")
        try:
            count = int(entry.get("count"))
        except (TypeError, ValueError, OverflowError):
            return jerr("Mahsulot soni noto'g'ri.")
        if not isinstance(pid, str) or pid not in PRODUCTS:
            return jerr("Menyuda yo'q mahsulot topildi. Ilovani yangilab, qaytadan urinib ko'ring.")
        if count < 1 or count > 50:
            return jerr("Mahsulot soni 1 dan 50 gacha bo'lishi kerak.")
        merged[pid] = merged.get(pid, 0) + count
    if any(c > 50 for c in merged.values()):
        return jerr("Bitta mahsulotdan 50 tadan ko'p buyurtma berib bo'lmaydi.")

    stopped = get_stoplist()
    unavailable = [PRODUCTS[p]["name"] for p in merged if p in stopped]
    if unavailable:
        names = ", ".join(sorted(set(unavailable)))
        return jerr(
            f"Afsuski, quyidagi taomlar hozircha tugagan: {names}. Iltimos, savatdan olib tashlang.",
            409,
            code="stop",
        )

    items = []
    total = 0
    for pid, count in merged.items():
        product = PRODUCTS[pid]
        items.append({"id": pid, "name": product["name"], "price": product["price"], "count": count})
        total += product["price"] * count

    # --- aloqa va yetkazish ---
    phone = str(body.get("phone", "")).strip()[:30]
    digits = re.sub(r"\D", "", phone)
    if not (9 <= len(digits) <= 15):
        return jerr("Telefon raqami noto'g'ri. Masalan: +998 90 123 45 67")

    delivery = body.get("delivery")
    if delivery not in ("delivery", "pickup"):
        return jerr("Yetkazib berish turi noto'g'ri.")

    address = str(body.get("address", "")).strip()[:300]
    lat = lon = None
    if delivery == "delivery":
        if not address:
            return jerr("Iltimos, yetkazib berish manzilini kiriting.")
        lat, lon = parse_location(body.get("location"))
    else:
        address = ""

    payment = body.get("payment")
    if not isinstance(payment, str) or payment not in PAYMENT_METHODS:
        return jerr("To'lov usuli noto'g'ri.")

    client_id = str(body.get("client_id", "")).strip()[:64] or None

    # --- suiiste'molga qarshi cheklov ---
    if recent_order_count(user["id"], 600) >= 10:
        return jerr("Juda ko'p buyurtma yuborildi. Birozdan keyin urinib ko'ring.", 429)

    upsert_user(user["id"], user["username"], user["full_name"])
    order_id, order_total, created = create_order(
        user, client_id, phone, delivery, address or None, lat, lon, payment, items, total
    )
    if created:
        # Buyurtma bazaga saqlandi. Telegramga yuborish orqada ketadi —
        # Telegram band bo'lsa ham mijoz xatolik ko'rmaydi.
        spawn(notify_new_order(request.app["bot"], order_id))
    return web.json_response({"ok": True, "order_id": order_id, "total": order_total})


async def api_my_orders(request):
    body = await read_json(request)
    if body is None:
        return jerr("So'rov noto'g'ri formatda.")
    user = validate_init_data(str(body.get("init_data", "")), BOT_TOKEN)
    if not user:
        return jerr("Telegram orqali tasdiqlanmadi.", 401)
    rows = db().execute(
        "SELECT id, status, total, created_at FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 30",
        (user["id"],),
    ).fetchall()
    return web.json_response(
        {
            "ok": True,
            "orders": [
                {"id": r["id"], "status": r["status"], "total": r["total"], "created_at": r["created_at"]}
                for r in rows
            ],
        }
    )


def build_web_app(bot) -> web.Application:
    app = web.Application(middlewares=[cors_middleware], client_max_size=64 * 1024)
    app["bot"] = bot
    app.router.add_get("/", api_health)
    app.router.add_get("/api/status", api_status)
    app.router.add_post("/api/order", api_order)
    app.router.add_post("/api/my_orders", api_my_orders)
    for path in ("/api/status", "/api/order", "/api/my_orders"):
        app.router.add_route("OPTIONS", path, api_options)
    return app


# =====================================================================
#  BOT BUYRUQLARI (mijozlar uchun)
# =====================================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not update.message:
        return
    upsert_user(user.id, user.username, user.full_name)
    nickname = f"@{user.username}" if user.username else user.full_name
    text = (
        f"Xush kelibsiz, <b>{esc(nickname)}</b>! 👋\n\n"
        "Buyurtma berish uchun chap pastdagi <b>'Open App'</b> tugmasini bosing."
    )
    if not is_open():
        text += f"\n\n⛔ {esc(closed_message())}"
    if user.id in ADMIN_IDS:
        text += "\n\n" + ADMIN_HELP
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=ReplyKeyboardRemove()
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    text = (
        "<b>ℹ️ Yordam</b>\n"
        "🍽 Buyurtma berish: chap pastdagi <b>'Open App'</b> tugmasi\n"
        "📞 /aloqa — bog'lanish\n"
        "📍 /manzil — manzilimiz\n"
        "Buyurtma holati o'zgarsa, shu yerda xabar olasiz."
    )
    if update.effective_user and update.effective_user.id in ADMIN_IDS:
        text += "\n\n" + ADMIN_HELP
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_aloqa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    lines = ["<b>📞 Aloqa</b>", f"Telefon: {esc(CONTACT_PHONE)}", f"Manzil: {esc(CAFE_ADDRESS)}"]
    if WORK_HOURS:
        lines.append(f"Ish vaqti: {esc(WORK_HOURS)}")
    lines.append("Holat: " + ("🟢 Ochiqmiz" if is_open() else "🔴 Hozir yopiqmiz"))
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_manzil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    await update.message.reply_text(
        f"📍 Manzil: {esc(CAFE_ADDRESS)}", parse_mode=ParseMode.HTML
    )
    if CAFE_LAT is not None and CAFE_LON is not None:
        await safe_location(context.bot, update.effective_chat.id, CAFE_LAT, CAFE_LON)


# =====================================================================
#  ADMIN BUYRUQLARI
# =====================================================================
def admin_only(handler):
    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or user.id not in ADMIN_IDS:
            return
        return await handler(update, context)

    return wrapper


@admin_only
async def cmd_ochish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_setting("is_open", "1")
    set_setting("closed_message", "")
    await update.message.reply_text("🟢 Buyurtma qabul qilish OCHILDI.")


@admin_only
async def cmd_yopish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    note = " ".join(context.args).strip()[:200] if context.args else ""
    set_setting("is_open", "0")
    set_setting("closed_message", note)
    shown = note or DEFAULT_CLOSED_MESSAGE
    await update.message.reply_text(
        f"🔴 Buyurtma qabul qilish YOPILDI.\nMijozlar ko'radigan xabar: {shown}\n\n"
        "Qayta ochish: /ochish"
    )


def _stop_main_view():
    stopped = get_stoplist()
    rows = []
    for cat, label in CATEGORIES.items():
        off = sum(1 for pid in stopped if pid in PRODUCTS and PRODUCTS[pid]["category"] == cat)
        title = f"{label} ({off} ta tugagan)" if off else label
        rows.append([_btn(title, f"sl:c:{cat}")])
    if stopped:
        rows.append([_btn("🔄 Hammasini yoqish", "sl:all")])
    text = (
        "🍽 <b>Stop-list</b>\n"
        "Bo'limni tanlang. Tugagan taomni o'chirsangiz, ilovada «Tugagan» deb ko'rinadi."
    )
    return text, InlineKeyboardMarkup(rows)


def _stop_category_view(cat: str):
    stopped = get_stoplist()
    rows = []
    for pid, product in PRODUCTS.items():
        if product["category"] != cat:
            continue
        mark = "⛔" if pid in stopped else "✅"
        rows.append([_btn(f"{mark} {product['name']} · {fmt_money(product['price'])}", f"sl:t:{cat}:{pid}")])
    rows.append([_btn("⬅️ Orqaga", "sl:m")])
    text = f"{esc(CATEGORIES.get(cat, cat))}\n✅ — bor,  ⛔ — tugagan.\nBosib holatini almashtiring."
    return text, InlineKeyboardMarkup(rows)


@admin_only
async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, markup = _stop_main_view()
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def _edit_callback_message(query, text: str, markup=None):
    try:
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            log.error("Callback xabarini tahrirlashda xato: %s", exc)


async def on_stop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("Ruxsat yo'q", show_alert=True)
        return
    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    if action == "m":
        text, markup = _stop_main_view()
    elif action == "all":
        clear_stoplist()
        text, markup = _stop_main_view()
    elif action == "c" and len(parts) == 3 and parts[2] in CATEGORIES:
        text, markup = _stop_category_view(parts[2])
    elif (
        action == "t"
        and len(parts) == 4
        and parts[2] in CATEGORIES
        and parts[3] in PRODUCTS
        and PRODUCTS[parts[3]]["category"] == parts[2]
    ):
        now_stopped = toggle_stop(parts[3])
        await query.answer("⛔ Tugagan deb belgilandi" if now_stopped else "✅ Yana mavjud")
        text, markup = _stop_category_view(parts[2])
        await _edit_callback_message(query, text, markup)
        return
    else:
        await query.answer()
        return
    await query.answer()
    await _edit_callback_message(query, text, markup)


@admin_only
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(build_stats_text(), parse_mode=ParseMode.HTML)


def build_stats_text() -> str:
    conn = db()
    now = datetime.now(LOCAL_TZ)
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    periods = [
        ("📅 Bugun", int(day0.timestamp())),
        ("🗓 Oxirgi 7 kun", int((day0 - timedelta(days=6)).timestamp())),
        ("🗓 Oxirgi 30 kun", int((day0 - timedelta(days=29)).timestamp())),
        ("📊 Hammasi", 0),
    ]
    lines = ["<b>📈 Statistika</b>", ""]
    for label, since in periods:
        rows = conn.execute(
            "SELECT status, total FROM orders WHERE created_at >= ?", (since,)
        ).fetchall()
        valid = [r for r in rows if r["status"] != "cancelled"]
        revenue = sum(r["total"] for r in valid)
        cancelled = len(rows) - len(valid)
        average = revenue // len(valid) if valid else 0
        orders_line = f"   Buyurtmalar: {len(valid)} ta"
        if cancelled:
            orders_line += f" (bekor qilingan: {cancelled})"
        lines += [
            f"<b>{label}</b>",
            orders_line,
            f"   Tushum: {fmt_money(revenue)} so'm",
            f"   O'rtacha chek: {fmt_money(average)} so'm",
            "",
        ]

    since_30 = int((day0 - timedelta(days=29)).timestamp())
    counter: Counter = Counter()
    for row in conn.execute(
        "SELECT items_json FROM orders WHERE created_at >= ? AND status != 'cancelled'", (since_30,)
    ):
        for item in json.loads(row["items_json"]):
            counter[item["name"]] += item["count"]
    lines.append("<b>🏆 Eng ko'p sotilgan (30 kun)</b>")
    if counter:
        for rank, (name, qty) in enumerate(counter.most_common(5), start=1):
            lines.append(f"   {rank}. {esc(name)} — {qty} ta")
    else:
        lines.append("   Hozircha ma'lumot yo'q")
    lines.append("")

    rating = conn.execute(
        "SELECT AVG(rating) AS avg, COUNT(rating) AS n FROM orders WHERE rating IS NOT NULL"
    ).fetchone()
    if rating["n"]:
        lines.append(f"⭐ O'rtacha baho: {rating['avg']:.2f} ({rating['n']} ta baho)")
    else:
        lines.append("⭐ O'rtacha baho: hozircha baho yo'q")

    users = conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(blocked), 0) AS b FROM users").fetchone()
    lines.append(f"👥 Mijozlar: {users['n']} ta (botni bloklaganlar: {users['b']})")
    stopped = len(get_stoplist())
    lines.append(f"⛔ Stop-listda: {stopped} ta taom")
    lines.append("Holat: " + ("🟢 Ochiq" if is_open() else "🔴 Yopiq"))
    return "\n".join(lines)


# ---- Broadcast ----
@admin_only
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = (update.message.text or "").split(maxsplit=1)
    text = parts[1].strip() if len(parts) > 1 else ""
    if not text:
        await update.message.reply_text(
            "Xabar matnini buyruqdan keyin yozing.\nMasalan:\n/broadcast Bugun barcha xot-doglarga 10% chegirma! 🌭"
        )
        return
    if len(text) > 3500:
        await update.message.reply_text("Xabar juda uzun (3500 belgidan oshmasin).")
        return
    context.application.bot_data.setdefault("broadcast", {})[update.effective_user.id] = text
    count = db().execute("SELECT COUNT(*) AS n FROM users WHERE blocked=0").fetchone()["n"]
    markup = InlineKeyboardMarkup(
        [[_btn("✅ Yuborish", "bc:yes"), _btn("❌ Bekor qilish", "bc:no")]]
    )
    await update.message.reply_text(
        f"📣 Quyidagi xabar {count} ta foydalanuvchiga yuboriladi:\n\n{text}", reply_markup=markup
    )


async def run_broadcast(bot, admin_id: int, text: str) -> None:
    users = db().execute("SELECT user_id FROM users WHERE blocked=0").fetchall()
    sent = failed = 0
    for row in users:
        uid = row["user_id"]
        try:
            await tg_call(bot.send_message, chat_id=uid, text=text)
            sent += 1
        except Forbidden:
            mark_blocked(uid)
            failed += 1
        except TelegramError:
            failed += 1
        await asyncio.sleep(0.06)
    await safe_send(
        bot, admin_id, f"📣 Xabar yuborildi.\n✅ Yetkazildi: {sent}\n❌ Yetmadi: {failed}"
    )


async def on_broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("Ruxsat yo'q", show_alert=True)
        return
    store = context.application.bot_data.setdefault("broadcast", {})
    text = store.pop(query.from_user.id, None)
    if query.data == "bc:no":
        await query.answer("Bekor qilindi")
        await query.edit_message_text("❌ Xabar yuborish bekor qilindi.")
        return
    if not text:
        await query.answer("Bu so'rov eskirgan. /broadcast ni qaytadan yozing.", show_alert=True)
        return
    await query.answer("Yuborish boshlandi")
    await query.edit_message_text("⏳ Xabar yuborilmoqda... Tugagach, natijani yozaman.")
    spawn(run_broadcast(context.bot, query.from_user.id, text))


# =====================================================================
#  BUYURTMA TUGMALARI (admin) VA BAHO (mijoz)
# =====================================================================
async def on_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("Ruxsat yo'q", show_alert=True)
        return
    try:
        _, raw_id, new_status = query.data.split(":")
        order_id = int(raw_id)
    except ValueError:
        await query.answer()
        return
    order = get_order(order_id)
    if not order:
        await query.answer("Buyurtma topilmadi", show_alert=True)
        return

    allowed = new_status in TRANSITIONS.get(order["status"], set())
    if new_status == "onway" and order["delivery_type"] != "delivery":
        allowed = False
    if not allowed:
        await query.answer("Bu buyurtma holati allaqachon o'zgargan.", show_alert=True)
        await refresh_order_messages(context.bot, order_id)
        return

    set_order_status(order_id, new_status)  # tekshiruv va yozish orasida await yo'q — xavfsiz
    await query.answer("Yangilandi ✅")
    await refresh_order_messages(context.bot, order_id)
    await notify_customer_status(context.bot, get_order(order_id))


async def on_cancel_ask_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("Ruxsat yo'q", show_alert=True)
        return
    order_id = int(query.data.split(":")[1])
    order = get_order(order_id)
    if not order or order["status"] not in TRANSITIONS:
        await query.answer("Bu buyurtmani bekor qilib bo'lmaydi.", show_alert=True)
        return
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=cancel_confirm_keyboard(order_id))


async def on_cancel_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("Ruxsat yo'q", show_alert=True)
        return
    order = get_order(int(query.data.split(":")[1]))
    await query.answer()
    if order:
        await query.edit_message_reply_markup(reply_markup=admin_keyboard(order))


async def on_rate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        _, raw_id, raw_rating = query.data.split(":")
        order_id, rating = int(raw_id), int(raw_rating)
    except ValueError:
        await query.answer()
        return
    order = get_order(order_id)
    if not order or order["user_id"] != query.from_user.id or not (1 <= rating <= 5):
        await query.answer("Bu buyurtma sizniki emas.", show_alert=True)
        return
    if order["rating"] is not None:
        await query.answer("Siz allaqachon baho bergansiz. Rahmat! 🙏")
        return
    set_order_rating(order_id, rating)
    await query.answer("Rahmat! 🙏")
    await query.edit_message_text(f"Bahoyingiz uchun rahmat! {'⭐' * rating}")
    for admin_id in ADMIN_IDS:
        await safe_send(
            context.bot, admin_id, f"⭐ Buyurtma #{order_id} uchun mijoz {rating} baho berdi."
        )


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    log.error("Kutilmagan xato", exc_info=context.error)


# =====================================================================
#  ISHGA TUSHIRISH
# =====================================================================
USER_COMMANDS = [
    BotCommand("start", "Botni ishga tushirish"),
    BotCommand("help", "Yordam"),
    BotCommand("aloqa", "Bog'lanish"),
    BotCommand("manzil", "Manzilimiz"),
]
ADMIN_COMMANDS = USER_COMMANDS + [
    BotCommand("ochish", "Buyurtma qabul qilishni ochish"),
    BotCommand("yopish", "Buyurtma qabul qilishni yopish"),
    BotCommand("stop", "Stop-list (tugagan taomlar)"),
    BotCommand("stats", "Statistika"),
    BotCommand("broadcast", "Hammaga xabar yuborish"),
]


async def post_init(application):
    # Chat chap pastida faqat "Open App" tugmasi turadi
    await application.bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(text="Open App", web_app=WebAppInfo(url=WEB_APP_URL))
    )
    try:
        await application.bot.set_my_commands(USER_COMMANDS)
        for admin_id in ADMIN_IDS:
            try:
                await application.bot.set_my_commands(
                    ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id)
                )
            except TelegramError as exc:
                log.warning("Admin (%s) buyruqlarini o'rnatib bo'lmadi: %s", admin_id, exc)
    except TelegramError as exc:
        log.warning("Buyruqlar ro'yxatini o'rnatib bo'lmadi: %s", exc)

    runner = web.AppRunner(build_web_app(application.bot))
    await runner.setup()
    site = web.TCPSite(runner, host=HOST, port=PORT)
    await site.start()
    application.bot_data["web_runner"] = runner
    log.info("Buyurtma serveri ishga tushdi: http://%s:%s", HOST, PORT)


async def post_shutdown(application):
    runner = application.bot_data.get("web_runner")
    if runner is not None:
        await runner.cleanup()


def main():
    if not BOT_TOKEN:
        raise SystemExit(
            "❌ BOT_TOKEN topilmadi.\n"
            "   bot.py yonida '.env' fayl yarating va ichiga yozing:\n"
            "   BOT_TOKEN=BotFather bergan token\n"
            "   (namuna: .env.example)"
        )
    if not ADMIN_IDS:
        raise SystemExit("❌ ADMIN_IDS topilmadi. .env faylga ADMIN_IDS=sizning_telegram_id yozing.")

    init_db()

    # Internet yoki tarmoq sekin kelganda xatolik bermasligi uchun timeout vaqtlari uzaytirildi
    request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0, write_timeout=30.0)

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .request(request)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("aloqa", cmd_aloqa))
    app.add_handler(CommandHandler("manzil", cmd_manzil))
    app.add_handler(CommandHandler("ochish", cmd_ochish))
    app.add_handler(CommandHandler("yopish", cmd_yopish))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))

    app.add_handler(CallbackQueryHandler(on_status_callback, pattern=r"^st:\d+:(accepted|ready|onway|done|cancelled)$"))
    app.add_handler(CallbackQueryHandler(on_cancel_ask_callback, pattern=r"^cq:\d+$"))
    app.add_handler(CallbackQueryHandler(on_cancel_back_callback, pattern=r"^cb:\d+$"))
    app.add_handler(CallbackQueryHandler(on_rate_callback, pattern=r"^rate:\d+:[1-5]$"))
    app.add_handler(CallbackQueryHandler(on_stop_callback, pattern=r"^sl:"))
    app.add_handler(CallbackQueryHandler(on_broadcast_callback, pattern=r"^bc:(yes|no)$"))
    app.add_error_handler(on_error)

    print("Bot muvaffaqiyatli ishga tushmoqda...")
    app.run_polling()


if __name__ == "__main__":
    main()
