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
import atexit
import csv
import functools
import hashlib
import hmac
import html
import io
import json
import logging
import math
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import textwrap
import threading
import time
import unicodedata
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
    MessageHandler,
    filters,
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
# (.env fayl endi kerak emas — sozlamalar pastda, shu faylning o'zida)

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


# =====================================================================
#  SOZLAMALAR — hamma narsa SHU YERDA. Boshqa fayl kerak emas.
#  ⚠️ Bu faylni hech kimga yubormang va internetga (GitHub) yuklamang —
#     ichida bot tokeni bor.
# =====================================================================
BOT_TOKEN = '8983485871:AAErGK9UHLKIiP9ShCV6eKShozOfzz8CIiI'  # @BotFather bergan token
ADMIN_IDS = [6069854654]  # admin(lar) Telegram ID si
COURIER_CHAT_ID = -5491727953  # kuryerlar guruhi ID si (ixtiyoriy)

CONTACT_PHONE = '+998 91 966 40 40'
CAFE_ADDRESS = 'Urganch shahri'
WORK_HOURS = '24/7 (har kuni, kechayu-kunduz)'
CAFE_LAT = None  # kafe lokatsiyasi (ixtiyoriy)
CAFE_LON = None

# ---- CHEK PRINTER (XPrinter va boshqa termoprinterlar) ----
PRINTER_ENABLED = True  # yangi buyurtma kelganda chek avtomatik chiqadi. O'chirish: False
PRINTER_NAME = ""       # Windows'dagi printer nomi. Bo'sh qoldirsangiz — Windows'ning standart printeri
PRINTER_IP = ""         # LAN/Wi-Fi printer bo'lsa uning IP si, masalan "192.168.1.50" (USB bo'lsa bo'sh)
PRINTER_PORT = 9100
PRINTER_WIDTH = 48      # 80 mm qog'oz = 48, 58 mm qog'oz = 32
CAFE_NAME = "STREET DOG"

# ---- DOSTAVKA ----
DELIVERY_FEE = 10000  # dostavka narxi (so'm), taxminan 5 km gacha. Olib ketishda 0.

# ---- SMENA HISOBOTI ----
COMMISSION_PERCENT = 5          # savdodan sizning ulushingiz (foiz)
NEW_PRODUCTS = []               # "Yangi" belgisi qo'yiladigan taom kodlari, masalan ['b5_75', 'g3_35']
HIT_MIN_SOLD = 3                # 30 kunda kamida shuncha dona sotilgan eng yaxshi 5 ta taom "Hit" bo'ladi
NEW_ORDER_ALERT_MIN = 10        # yangi buyurtma shuncha daqiqa qabul qilinmasa, adminga ogohlantirish
AUTO_SCHEDULE = False           # WORK_HOURS bo'yicha o'zi ochiladi/yopiladi (qo'lda /ochish /yopish keyingi ochilish/yopilishgacha ishlaydi)
SMENA_HISOBOT_VAQTI = "23:00"   # hisobot har kuni shu vaqtda o'zi yuboriladi (SS:DD)
HISOBOT_IDS = []                # hisobot kimga borsin (Telegram ID). Bo'sh bo'lsa — ADMIN_IDS ga
REPORT_IDS = HISOBOT_IDS or ADMIN_IDS

# Ilova manzili ODATDA bo'sh qoladi: bot internetga chiqish manzilini (cloudflared)
# o'zi topadi va "Open App" tugmasiga o'zi qo'yadi. Faqat doimiy hosting/domeningiz
# bo'lsa, shu yerga yozing: "https://sizning-domen.uz"
WEB_APP_URL = ""

# ---- DOIMIY SAYT (bot o'chiq bo'lsa ham menyu ochiladi) ----
# index.html ni GitHub Pages (yoki Netlify/Cloudflare Pages) ga yuklab, doimiy manzilni shu yerga yozing:
#   PERMANENT_SITE_URL = "https://jamshid0217.github.io/streetdog/"
# Bo'sh qoldirsangiz, ilovani bot o'zi (vaqtinchalik cloudflared manzili orqali) ochadi.
PERMANENT_SITE_URL = ""

PORT = 8080
HOST = "127.0.0.1"  # faqat shu kompyuter ichida; tashqariga cloudflared olib chiqadi
ALLOWED_ORIGINS = ["https://jamshid0217.github.io"]  # ilova serverdan ochilgani uchun endi muhim emas
if PERMANENT_SITE_URL:  # doimiy saytdan kelgan so'rovlarga ruxsat (CORS)
    from urllib.parse import urlsplit as _us
    _p = _us(PERMANENT_SITE_URL)
    if _p.scheme and _p.netloc and f"{_p.scheme}://{_p.netloc}" not in ALLOWED_ORIGINS:
        ALLOWED_ORIGINS.append(f"{_p.scheme}://{_p.netloc}")
DB_PATH = str(BASE_DIR / "street_dog.db")  # buyurtmalar bazasi — o'zi yaratiladi

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
}

PRODUCTS = {
    'h1_25': {"category": 'hotdog', "name": 'Острый хот-дог (C)', "price": 25000},
    'h1_35': {"category": 'hotdog', "name": 'Острый хот-дог (M)', "price": 35000},
    'h2_30': {"category": 'hotdog', "name": 'Чикаго хот-дог (C)', "price": 30000},
    'h2_45': {"category": 'hotdog', "name": 'Чикаго хот-дог (M)', "price": 45000},
    'h3_50': {"category": 'hotdog', "name": 'Американский хот-дог', "price": 50000},
    'h4_30': {"category": 'hotdog', "name": 'Нью-Йорк хот-дог', "price": 30000},
    'h5_30': {"category": 'hotdog', "name": 'Мехико хот-дог (C)', "price": 30000},
    'h5_45': {"category": 'hotdog', "name": 'Мехико хот-дог (M)', "price": 45000},
    'h6_35': {"category": 'hotdog', "name": 'Классический хот-дог', "price": 35000},
    'h7_30': {"category": 'hotdog', "name": 'Чикен хот-дог (C)', "price": 30000},
    'h7_40': {"category": 'hotdog', "name": 'Чикен хот-дог (M)', "price": 40000},
    'h8_25': {"category": 'hotdog', "name": 'Французский хот-дог', "price": 25000},
    'h9_50': {"category": 'hotdog', "name": 'Премиум хот-дог', "price": 50000},
    'h10_20': {"category": 'hotdog', "name": 'Детский хот-дог', "price": 20000},
    'h11_40': {"category": 'hotdog', "name": 'Роял фреш', "price": 40000},
    'h12_35': {"category": 'hotdog', "name": 'Сендвич', "price": 35000},
    'h13_35': {"category": 'hotdog', "name": 'Итальянский сендвич', "price": 35000},
    'b1_65': {"category": 'burger', "name": 'Стрит бургер (M)', "price": 65000},
    'b1_80': {"category": 'burger', "name": 'Стрит бургер (L)', "price": 80000},
    'b1_95': {"category": 'burger', "name": 'Стрит бургер (XL)', "price": 95000},
    'b2_65': {"category": 'burger', "name": 'Классический (M)', "price": 65000},
    'b2_80': {"category": 'burger', "name": 'Классический (L)', "price": 80000},
    'b2_95': {"category": 'burger', "name": 'Классический (XL)', "price": 95000},
    'b3_65': {"category": 'burger', "name": 'Трюфельный (M)', "price": 65000},
    'b3_80': {"category": 'burger', "name": 'Трюфельный (L)', "price": 80000},
    'b3_95': {"category": 'burger', "name": 'Трюфельный (XL)', "price": 95000},
    'b4_65': {"category": 'burger', "name": 'Острый (M)', "price": 65000},
    'b4_80': {"category": 'burger', "name": 'Острый (L)', "price": 80000},
    'b4_95': {"category": 'burger', "name": 'Острый (XL)', "price": 95000},
    'b5_75': {"category": 'burger', "name": 'Стейк бургер', "price": 75000},
    'b6_40': {"category": 'burger', "name": 'Чизбургер', "price": 40000},
    'b7_45': {"category": 'burger', "name": 'Чикен бургер', "price": 45000},
    'b8_65': {"category": 'burger', "name": 'ББК (M)', "price": 65000},
    'b8_80': {"category": 'burger', "name": 'ББК (L)', "price": 80000},
    'b8_95': {"category": 'burger', "name": 'ББК (XL)', "price": 95000},
    'g1_58': {"category": 'garnir', "name": 'СтрипБокс', "price": 58000},
    'g2_60': {"category": 'garnir', "name": 'МитБокс', "price": 60000},
    'g3_35': {"category": 'garnir', "name": 'Сырные подушки', "price": 35000},
    'g4_30': {"category": 'garnir', "name": 'Фри с трюфелем', "price": 30000},
    'g5_25': {"category": 'garnir', "name": 'Картофель Айдахо', "price": 25000},
    'g6_20': {"category": 'garnir', "name": 'Картофель фри', "price": 20000},
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
    "/broadcast matn — barcha mijozlarga xabar yuborish\n"
    "/chek [raqam] — chekni qayta chiqarish (raqamsiz — sinov cheki)\n"
    "/raqam [N] — kafedagi oxirgi buyurtma raqamini belgilash\n"
    "/hisobot — joriy smena hisoboti\n"
    "/smena — smenani yopish va hisobotni yuborish\n"
    "/mijozlar — mijozlar soni va eng faollari\n"
    "/eksport — mijozlar ro'yxatini fayl (Excel) qilib olish"
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
    # Menyudan olib tashlangan taomlar stop-listda qolib ketmasin
    db().execute(
        "DELETE FROM stoplist WHERE product_id NOT IN (%s)" % ",".join("?" * len(PRODUCTS)),
        tuple(PRODUCTS),
    )
    try:  # eski bazalar uchun: kunlik buyurtma raqami ustuni
        db().execute("ALTER TABLE orders ADD COLUMN day_no INTEGER")
    except sqlite3.OperationalError:
        pass
    for col in ("courier_id INTEGER", "courier_name TEXT", "courier_phone TEXT"):  # eski bazalar uchun: kuryer
        try:
            db().execute("ALTER TABLE orders ADD COLUMN " + col)
        except sqlite3.OperationalError:
            pass
    db().execute("CREATE TABLE IF NOT EXISTS couriers (user_id INTEGER PRIMARY KEY, phone TEXT NOT NULL)")
    try:  # eski bazalar uchun: mijoz izohi ustuni
        db().execute("ALTER TABLE orders ADD COLUMN note TEXT")
    except sqlite3.OperationalError:
        pass
    try:  # eski bazalar uchun: dostavka haqi ustuni
        db().execute("ALTER TABLE orders ADD COLUMN delivery_fee INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass


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
def create_order(user: dict, client_id, phone, delivery, address, lat, lon, payment, items, total, delivery_fee=0, note=None):
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
    day_no = next_order_no(now)  # hisoblash va yozish orasida await yo'q — raqamlar takrorlanmaydi
    cur = conn.execute(
        "INSERT INTO orders (client_id, user_id, username, full_name, phone, delivery_type, "
        "address, lat, lon, payment, items_json, total, status, created_at, updated_at, day_no, delivery_fee, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?, ?, ?)",
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
            day_no,
            delivery_fee,
            note,
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


# ---- kunlik buyurtma raqami (#1, #2, #3 ... har kuni 1 dan boshlanadi) ----
def _day_bounds(ts: int):
    d = datetime.fromtimestamp(ts, LOCAL_TZ)
    day0 = d.replace(hour=0, minute=0, second=0, microsecond=0)
    return day0, int(day0.timestamp()), int((day0 + timedelta(days=1)).timestamp())


def _num_floor(date_str: str) -> int:
    raw = get_setting("no_floor", "")
    if "|" in raw:
        d, n = raw.split("|", 1)
        if d == date_str and n.lstrip("-").isdigit():
            return int(n)
    return 0


def last_order_no_today(ts=None) -> int:
    """Bugungi eng katta buyurtma raqami (kafe aytgan raqam ham hisobga olinadi)."""
    ts = ts or int(time.time())
    day0, start, end = _day_bounds(ts)
    row = db().execute(
        "SELECT MAX(COALESCE(day_no, 0)) AS m FROM orders WHERE created_at>=? AND created_at<?",
        (start, end),
    ).fetchone()
    return max(row["m"] or 0, _num_floor(day0.strftime("%Y-%m-%d")))


def next_order_no(ts=None) -> int:
    return last_order_no_today(ts) + 1


def set_last_order_no(n: int) -> int:
    """Kafedagi hozirgi oxirgi buyurtma raqamini belgilaydi. Keyingi buyurtma raqamini qaytaradi."""
    day0, _, _ = _day_bounds(int(time.time()))
    set_setting("no_floor", f"{day0.strftime('%Y-%m-%d')}|{n}")
    return next_order_no()


def order_fee(order) -> int:
    """Buyurtmadagi dostavka haqi (eski buyurtmalarda 0)."""
    try:
        return int(order["delivery_fee"] or 0)
    except (IndexError, KeyError, TypeError, ValueError):
        return 0


def order_no(order) -> int:
    """Mijoz, admin va chekda ko'rinadigan raqam."""
    try:
        value = order["day_no"]
    except (IndexError, KeyError):
        value = None
    return value if value is not None else order["id"]


def find_order_by_no(n: int):
    _, start, end = _day_bounds(int(time.time()))
    return db().execute(
        "SELECT * FROM orders WHERE day_no=? AND created_at>=? AND created_at<? ORDER BY id DESC LIMIT 1",
        (n, start, end),
    ).fetchone()


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
        f"🛒 <b>BUYURTMA #{order_no(order)}</b>",
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
        *([f"🛵 Kuryer: <b>{esc(order['courier_name'])}</b> · {esc(order['courier_phone'] or '')}"] if order["courier_name"] else []),
        *([f"📝 Izoh: <b>{esc(order['note'])}</b>"] if order["note"] else []),
        "",
        "📦 <b>Buyurtma tarkibi:</b>",
        *lines,
        *([f"🛵 Dostavka: {fmt_money(order_fee(order))} so'm"] if order_fee(order) else []),
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
            f"✅ <b>Buyurtmangiz qabul qilindi! (#{order_no(order)})</b>",
            "",
            *lines,
            *([f"🛵 Dostavka: {fmt_money(order_fee(order))} so'm"] if order_fee(order) else []),
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


def courier_keyboard(order):
    if order["delivery_type"] != "delivery" or order["status"] in ("done", "cancelled"):
        return None
    oid = order["id"]
    if not order["courier_id"]:
        return InlineKeyboardMarkup([[_btn("🛵 Men olaman", f"cr:{oid}")]])
    return InlineKeyboardMarkup([[_btn("✔️ Yetkazildi", f"cd:{oid}")]])


def cancel_confirm_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[_btn("✅ Ha, bekor qilish", f"st:{order_id}:cancelled"), _btn("↩️ Yo'q", f"cb:{order_id}")]]
    )


def rating_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_btn(f"{n}⭐", f"rate:{order_id}:{n}") for n in range(1, 6)]])


def customer_status_text(order):
    oid = order_no(order)
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
        markup = admin_keyboard(order) if kind == "admin" else courier_keyboard(order)
        msg = await safe_send(bot, chat_id, text, reply_markup=markup)
        if msg:
            save_msg(order_id, chat_id, msg.message_id, kind)
        if has_location:
            await safe_location(bot, chat_id, order["lat"], order["lon"])

    receipt = await safe_send(bot, order["user_id"], receipt_text(order))
    if receipt is None:
        warn = (
            f"⚠️ Buyurtma #{order_no(order)}: mijozga bot xabar yubora olmadi "
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
        markup = admin_keyboard(order) if m["kind"] == "admin" else courier_keyboard(order)
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
#  CHEK PRINTER (XPrinter va boshqa ESC/POS termoprinterlar)
# =====================================================================
ESC = b"\x1b"
GS = b"\x1d"
_print_lock = threading.Lock()

_CYR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "j",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "x", "ц": "s",
    "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "'", "ы": "i", "ь": "", "э": "e", "ю": "yu",
    "я": "ya", "ў": "o'", "қ": "q", "ғ": "g'", "ҳ": "h",
}
_APOS = set("ʻʼ‘’`´ʹ′")


def to_ascii(text) -> str:
    """Termoprinter odatda faqat lotin harflarini chiqaradi: kirill/emoji/maxsus belgilarni soddalashtiramiz."""
    out = []
    for ch in str(text if text is not None else ""):
        low = ch.lower()
        if ch in _APOS:
            out.append("'")
        elif low in _CYR:
            t = _CYR[low]
            out.append(t if ch == low else t.capitalize())
        else:
            out.append(unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode("ascii"))
    return "".join(c for c in "".join(out) if 32 <= ord(c) < 127)


def _wrap(text: str, width: int) -> list:
    return textwrap.wrap(to_ascii(text), max(width, 8)) or [""]


def _lr(left: str, right: str, width: int) -> list:
    """Chapda matn, o'ngda summa (uzun bo'lsa keyingi qatorga o'tadi)."""
    right = to_ascii(right)
    lines = _wrap(left, width - len(right) - 1)
    lines[-1] = lines[-1].ljust(width - len(right)) + right
    return lines


class _Chek:
    def __init__(self):
        self.buf = bytearray(ESC + b"@")  # printerni tozalash

    def align(self, n: int):  # 0 chap, 1 o'rta
        self.buf += ESC + b"a" + bytes([n])

    def bold(self, on: bool):
        self.buf += ESC + b"E" + bytes([1 if on else 0])

    def size(self, n: int):  # 0x00 oddiy, 0x11 ikki barobar
        self.buf += GS + b"!" + bytes([n])

    def line(self, text: str = ""):
        self.buf += to_ascii(text).encode("ascii", "replace") + b"\n"

    def finish(self) -> bytes:
        self.buf += b"\n\n\n\n" + GS + b"V" + b"\x42\x00"  # surib, qirqish
        return bytes(self.buf)


def build_chek(order) -> bytes:
    W = max(PRINTER_WIDTH, 20)
    items = json.loads(order["items_json"])
    is_delivery = order["delivery_type"] == "delivery"
    c = _Chek()
    c.align(1)
    c.bold(True)
    c.size(0x11)
    c.line(CAFE_NAME)
    c.size(0x00)
    c.bold(False)
    if CAFE_ADDRESS:
        c.line(CAFE_ADDRESS)
    if CONTACT_PHONE:
        c.line(CONTACT_PHONE)
    c.line("-" * W)
    c.bold(True)
    c.size(0x11)
    c.line(f"BUYURTMA #{order_no(order)}")
    c.size(0x00)
    c.bold(False)
    c.line(fmt_time(order["created_at"]))
    c.line("-" * W)
    c.align(0)
    c.line("Turi: " + ("DOSTAVKA" if is_delivery else "OLIB KETISH"))
    c.line("Mijoz: " + (order["full_name"] or "Mijoz"))
    c.line("Tel: " + str(order["phone"]))
    if is_delivery and order["address"]:
        for ln in _wrap("Manzil: " + order["address"], W):
            c.line(ln)
    c.line("To'lov: " + str(order["payment"]))
    if order["note"]:
        for ln in _wrap("IZOH: " + order["note"], W):
            c.line(ln)
    c.line("-" * W)
    for i in items:
        for ln in _lr(f"{i['count']} x {i['name']}", fmt_money(i["price"] * i["count"]), W):
            c.line(ln)
    if order_fee(order):
        for ln in _lr("Dostavka", fmt_money(order_fee(order)), W):
            c.line(ln)
    c.line("-" * W)
    c.bold(True)
    c.size(0x11)
    for ln in _lr("JAMI:", fmt_money(order["total"]), W // 2):
        c.line(ln)
    c.size(0x00)
    c.bold(False)
    c.line("so'm")
    c.line("-" * W)
    c.align(1)
    c.line("Rahmat! Yoqimli ishtaha!")
    return c.finish()


def build_test_chek() -> bytes:
    W = max(PRINTER_WIDTH, 20)
    c = _Chek()
    c.align(1)
    c.bold(True)
    c.size(0x11)
    c.line(CAFE_NAME)
    c.size(0x00)
    c.bold(False)
    c.line("SINOV CHEKI")
    c.line("-" * W)
    c.line("Printer ishlayapti!")
    c.line(fmt_time(int(time.time())))
    return c.finish()


def _print_windows(data: bytes, printer_name: str) -> str:
    import ctypes
    from ctypes import wintypes

    winspool = ctypes.WinDLL("winspool.drv", use_last_error=True)

    class DOC_INFO_1(ctypes.Structure):
        _fields_ = [
            ("pDocName", wintypes.LPWSTR),
            ("pOutputFile", wintypes.LPWSTR),
            ("pDatatype", wintypes.LPWSTR),
        ]

    winspool.GetDefaultPrinterW.argtypes = [wintypes.LPWSTR, wintypes.LPDWORD]
    winspool.GetDefaultPrinterW.restype = wintypes.BOOL
    winspool.OpenPrinterW.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.HANDLE), wintypes.LPVOID]
    winspool.OpenPrinterW.restype = wintypes.BOOL
    winspool.StartDocPrinterW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(DOC_INFO_1)]
    winspool.StartDocPrinterW.restype = wintypes.DWORD
    winspool.StartPagePrinter.argtypes = [wintypes.HANDLE]
    winspool.StartPagePrinter.restype = wintypes.BOOL
    winspool.WritePrinter.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD, wintypes.LPDWORD]
    winspool.WritePrinter.restype = wintypes.BOOL
    winspool.EndPagePrinter.argtypes = [wintypes.HANDLE]
    winspool.EndDocPrinter.argtypes = [wintypes.HANDLE]
    winspool.ClosePrinter.argtypes = [wintypes.HANDLE]

    if not printer_name:
        size = wintypes.DWORD(0)
        winspool.GetDefaultPrinterW(None, ctypes.byref(size))
        buf = ctypes.create_unicode_buffer(max(size.value, 1))
        if not winspool.GetDefaultPrinterW(buf, ctypes.byref(size)):
            raise OSError(
                "Windows'da standart printer tanlanmagan. Printerni standart qiling "
                "yoki bot.py dagi PRINTER_NAME ga uning nomini yozing."
            )
        printer_name = buf.value

    handle = wintypes.HANDLE()
    if not winspool.OpenPrinterW(printer_name, ctypes.byref(handle), None):
        raise OSError(f"Printer topilmadi: '{printer_name}'. Windows'dagi printer nomini tekshiring.")
    try:
        doc = DOC_INFO_1("Street Dog chek", None, "RAW")
        if not winspool.StartDocPrinterW(handle, 1, ctypes.byref(doc)):
            raise OSError(f"Printer ishga tushmadi: '{printer_name}'")
        try:
            winspool.StartPagePrinter(handle)
            written = wintypes.DWORD(0)
            ok = winspool.WritePrinter(handle, data, len(data), ctypes.byref(written))
            winspool.EndPagePrinter(handle)
            if not ok or written.value != len(data):
                raise OSError(f"Printerga yozib bo'lmadi: '{printer_name}'")
        finally:
            winspool.EndDocPrinter(handle)
    finally:
        winspool.ClosePrinter(handle)
    return printer_name


def send_to_printer(data: bytes) -> str:
    """Chekni printerga yuboradi (bloklaydi). Qaysi printerga ketganini qaytaradi."""
    with _print_lock:
        if PRINTER_IP:
            with socket.create_connection((PRINTER_IP, PRINTER_PORT), timeout=5) as sock:
                sock.settimeout(10)
                sock.sendall(data)
            return f"{PRINTER_IP}:{PRINTER_PORT}"
        if os.name == "nt":
            return _print_windows(data, PRINTER_NAME)
        raise OSError("Bu kompyuter Windows emas. LAN printer uchun PRINTER_IP ni yozing.")


async def print_order_chek(bot, order_id: int) -> None:
    if not PRINTER_ENABLED:
        return
    order = get_order(order_id)
    if not order:
        return
    try:
        data = build_chek(order)
        await asyncio.get_running_loop().run_in_executor(None, send_to_printer, data)
    except Exception as exc:
        log.warning("Chek chiqmadi (#%s): %s", order_no(order), exc)
        warn = (
            f"⚠️ Chek chiqmadi (buyurtma #{order_no(order)}): {esc(exc)}\n"
            f"Printerni tekshirib, /chek {order_no(order)} bilan qayta chiqaring."
        )
        for admin_id in ADMIN_IDS:
            await safe_send(bot, admin_id, warn)


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


async def serve_index(request):
    """Ilovaning o'zini (index.html) shu server beradi — GitHub Pages kerak emas."""
    page = BASE_DIR / "index.html"
    if not page.exists():
        return web.Response(
            text="Street Dog server ishlayapti ✅\n(index.html bot.py bilan bir papkada emas)",
            status=404,
        )
    return web.FileResponse(page, headers={"Cache-Control": "no-store"})


_HIT_CACHE = [0, []]


def hit_products() -> list:
    if time.time() < _HIT_CACHE[0]:
        return _HIT_CACHE[1]
    since = int(time.time()) - 30 * 86400
    counter: Counter = Counter()
    for row in db().execute("SELECT items_json FROM orders WHERE created_at>=? AND status!='cancelled'", (since,)):
        for it in json.loads(row["items_json"]):
            counter[it["id"]] += it["count"]
    hits = [pid for pid, n in counter.most_common(5) if n >= HIT_MIN_SOLD and pid in PRODUCTS]
    _HIT_CACHE[0], _HIT_CACHE[1] = time.time() + 120, hits
    return hits


async def api_status(request):
    return web.json_response(
        {
            "hits": hit_products(),
            "new": [p for p in NEW_PRODUCTS if p in PRODUCTS],
            "ok": True,
            "open": is_open(),
            "message": "" if is_open() else closed_message(),
            "stoplist": sorted(get_stoplist()),
            "delivery_fee": DELIVERY_FEE,
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

    delivery_fee = DELIVERY_FEE if delivery == "delivery" else 0
    total += delivery_fee

    payment = body.get("payment")
    if not isinstance(payment, str) or payment not in PAYMENT_METHODS:
        return jerr("To'lov usuli noto'g'ri.")

    client_id = str(body.get("client_id", "")).strip()[:64] or None
    note = str(body.get("note", "")).strip()[:200] or None

    # --- suiiste'molga qarshi cheklov ---
    if recent_order_count(user["id"], 600) >= 10:
        return jerr("Juda ko'p buyurtma yuborildi. Birozdan keyin urinib ko'ring.", 429)

    upsert_user(user["id"], user["username"], user["full_name"])
    order_id, order_total, created = create_order(
        user, client_id, phone, delivery, address or None, lat, lon, payment, items, total, delivery_fee, note
    )
    if created:
        # Buyurtma bazaga saqlandi. Telegramga yuborish orqada ketadi —
        # Telegram band bo'lsa ham mijoz xatolik ko'rmaydi.
        spawn(notify_new_order(request.app["bot"], order_id))
        spawn(print_order_chek(request.app["bot"], order_id))
    return web.json_response(
        {"ok": True, "order_id": order_id, "order_no": order_no(get_order(order_id)), "total": order_total}
    )


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
    orders_count = db().execute(
        "SELECT COUNT(*) AS n FROM orders WHERE user_id=? AND status != 'cancelled'", (user["id"],)
    ).fetchone()["n"]
    return web.json_response(
        {
            "ok": True,
            "orders_count": orders_count,
            "orders": [
                {"id": r["id"], "status": r["status"], "total": r["total"], "created_at": r["created_at"]}
                for r in rows
            ],
        }
    )


_AVATAR_CACHE: dict = {}  # user_id -> (amal qilish muddati, rasm baytlari yoki None)


async def api_avatar(request):
    """Mijozning Telegram profil rasmini bot orqali olib beradi (ilovada profil rasmi uchun)."""
    body = await read_json(request)
    if body is None:
        return jerr("So'rov noto'g'ri formatda.")
    user = validate_init_data(str(body.get("init_data", "")), BOT_TOKEN)
    if not user:
        return jerr("Telegram orqali tasdiqlanmadi.", 401)
    uid = user["id"]
    hit = _AVATAR_CACHE.get(uid)
    if hit and hit[0] > time.time():
        data = hit[1]
    else:
        data = None
        try:
            bot = request.app["bot"]
            photos = await bot.get_user_profile_photos(uid, limit=1)
            if photos.total_count and photos.photos:
                sizes = photos.photos[0]
                pick = sizes[min(1, len(sizes) - 1)]  # o'rtacha o'lcham
                tg_file = await bot.get_file(pick.file_id)
                data = bytes(await tg_file.download_as_bytearray())
        except Exception as exc:
            log.warning("Profil rasmini olib bo'lmadi (%s): %s", uid, exc)
        if len(_AVATAR_CACHE) > 500:
            _AVATAR_CACHE.clear()
        _AVATAR_CACHE[uid] = (time.time() + 3600, data)
    if not data:
        return web.Response(status=404)
    return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


def build_web_app(bot) -> web.Application:
    app = web.Application(middlewares=[cors_middleware], client_max_size=64 * 1024)
    app["bot"] = bot
    app.router.add_get("/", serve_index)
    app.router.add_get("/health", api_health)
    app.router.add_get("/api/status", api_status)
    app.router.add_post("/api/order", api_order)
    app.router.add_post("/api/my_orders", api_my_orders)
    app.router.add_post("/api/avatar", api_avatar)
    for path in ("/api/status", "/api/order", "/api/my_orders", "/api/avatar"):
        app.router.add_route("OPTIONS", path, api_options)
    return app


# =====================================================================
#  BOT BUYRUQLARI (mijozlar uchun)
# =====================================================================
def open_app_markup():
    """Ilovani bir bosishda ochadigan katta tugma (ilova manzili tayyor bo'lsa)."""
    if not WEB_APP_URL:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🍽 Menyu / Меню — buyurtma berish", web_app=WebAppInfo(url=WEB_APP_URL))]]
    )


async def on_any_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mijoz botga istalgan xabar yozsa, ilovani ochish tugmasini yuboramiz."""
    if not update.message or not update.effective_user:
        return
    upsert_user(update.effective_user.id, update.effective_user.username, update.effective_user.full_name)
    await update.message.reply_text(
        "Buyurtma berish uchun tugmani bosing 👇\nНажмите кнопку, чтобы сделать заказ 👇",
        reply_markup=open_app_markup(),
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not update.message:
        return
    upsert_user(user.id, user.username, user.full_name)
    nickname = f"@{user.username}" if user.username else user.full_name
    text = (
        f"Xush kelibsiz, <b>{esc(nickname)}</b>! 👋\n\n"
        "Buyurtma berish uchun pastdagi <b>«Menyu»</b> tugmasini (yoki chap pastdagi <b>'Open App'</b> ni) bosing."
    )
    if not is_open():
        text += f"\n\n⛔ {esc(closed_message())}"
    if user.id in ADMIN_IDS:
        text += "\n\n" + ADMIN_HELP
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=open_app_markup() or ReplyKeyboardRemove()
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
            "SELECT status, total - COALESCE(delivery_fee, 0) AS total FROM orders WHERE created_at >= ?", (since,)
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


# ---- Smena hisoboti va yangi buyruqlar ----
def report_only(handler):
    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or (user.id not in ADMIN_IDS and user.id not in REPORT_IDS):
            return
        return await handler(update, context)

    return wrapper


def shift_since() -> int:
    raw = get_setting("last_report_ts", "")
    if raw.isdigit():
        return int(raw)
    return _day_bounds(int(time.time()))[1]


def build_shift_report(since: int, until: int, note: str = ""):
    rows = db().execute(
        "SELECT status, total - COALESCE(delivery_fee, 0) AS total, payment, delivery_type FROM orders WHERE created_at>=? AND created_at<?",
        (since, until),
    ).fetchall()
    valid = [r for r in rows if r["status"] != "cancelled"]
    cancelled = len(rows) - len(valid)
    revenue = sum(r["total"] for r in valid)
    share = int(revenue * COMMISSION_PERCENT / 100 + 0.5)
    pct = f"{COMMISSION_PERCENT:g}"

    t0 = datetime.fromtimestamp(since, LOCAL_TZ)
    t1 = datetime.fromtimestamp(until, LOCAL_TZ)
    if t0.date() == t1.date():
        period = f"{t1.strftime('%d.%m.%Y')} · {t0.strftime('%H:%M')} → {t1.strftime('%H:%M')}"
    else:
        period = f"{t0.strftime('%d.%m %H:%M')} → {t1.strftime('%d.%m.%Y %H:%M')}"

    orders_line = f"🧾 Buyurtmalar: {len(valid)} ta"
    if cancelled:
        orders_line += f" (bekor qilingan: {cancelled})"
    lines = [f"📊 <b>SMENA HISOBOTI</b>{(' ' + esc(note)) if note else ''}", f"📅 {period}", "", orders_line]
    lines += [
        f"💰 Savdo: <b>{fmt_money(revenue)} so'm</b>",
        f"🤝 Sizning ulushingiz ({pct}%): <b>{fmt_money(share)} so'm</b>",
    ]
    if valid:
        by_pay: dict = {}
        for r in valid:
            s, n = by_pay.get(r["payment"], (0, 0))
            by_pay[r["payment"]] = (s + r["total"], n + 1)
        lines.append("")
        for name, (s, n) in sorted(by_pay.items(), key=lambda kv: -kv[1][0]):
            lines.append(f"💳 {esc(name)}: {fmt_money(s)} so'm ({n} ta)")
        dl = sum(1 for r in valid if r["delivery_type"] == "delivery")
        lines.append(f"🛵 Dostavka: {dl} ta · 🏃 Olib ketish: {len(valid) - dl} ta")
    lines += ["", "ℹ️ Faqat bot orqali kelgan buyurtmalar hisoblanadi. Dostavka haqi savdoga kirmaydi."]
    return "\n".join(lines), len(valid), revenue, share


async def send_shift_report(bot, since: int, until: int, note: str = "") -> int:
    text, _, _, _ = build_shift_report(since, until, note)
    for chat_id in REPORT_IDS:
        await safe_send(bot, chat_id, text)
    set_setting("last_report_date", datetime.fromtimestamp(until, LOCAL_TZ).strftime("%Y-%m-%d"))
    return until


def _orders_between(since: int, until: int) -> int:
    return db().execute(
        "SELECT COUNT(*) AS n FROM orders WHERE created_at>=? AND created_at<?", (since, until)
    ).fetchone()["n"]


def _report_time():
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", SMENA_HISOBOT_VAQTI or "")
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        return int(m.group(1)), int(m.group(2))
    return 23, 0


async def run_report_checks(bot, now: datetime, first_pass: bool = False) -> None:
    """Har kuni belgilangan vaqtda smena hisobotini yuboradi. Bot o'chiq turgan kunlar uchun — ishga tushganda."""
    today = now.strftime("%Y-%m-%d")
    day0 = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    if first_pass:
        since = shift_since()
        if since < day0:  # oldingi kun(lar) hisoboti yuborilmay qolgan
            if _orders_between(since, day0):
                await send_shift_report(bot, since, day0, "(o'tkazib yuborilgan smena)")
            set_setting("last_report_ts", str(day0))
    hh, mm = _report_time()
    due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if now >= due and get_setting("auto_report_date") != today:
        set_setting("auto_report_date", today)  # ikki marta yuborilmasligi uchun avval belgilaymiz
        until = int(now.timestamp())
        since = shift_since()
        if _orders_between(since, until) == 0 and get_setting("last_report_date") == today:
            return  # bugun hisobot yuborilgan va undan keyin buyurtma bo'lmagan
        await send_shift_report(bot, since, until)
        set_setting("last_report_ts", str(until))


STUCK_ALERTED: set = set()


async def check_stuck_orders(bot) -> None:
    limit = int(time.time()) - NEW_ORDER_ALERT_MIN * 60
    rows = db().execute(
        "SELECT * FROM orders WHERE status='new' AND created_at<? AND created_at>?", (limit, limit - 6 * 3600)
    ).fetchall()
    for o in rows:
        if o["id"] in STUCK_ALERTED:
            continue
        STUCK_ALERTED.add(o["id"])
        for admin_id in ADMIN_IDS:
            await safe_send(
                bot, admin_id,
                f"⚠️ Buyurtma #{order_no(o)} {NEW_ORDER_ALERT_MIN} daqiqadan beri qabul qilinmadi!\n📞 {esc(o['phone'])}",
            )


def _work_range():
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})\s*", WORK_HOURS or "")
    if not m:
        return None
    a, b_, c, d = map(int, m.groups())
    return a * 60 + b_, c * 60 + d


def check_schedule(now: datetime) -> None:
    rng = _work_range()
    if not AUTO_SCHEDULE or not rng:
        return
    start, end = rng
    cur = now.hour * 60 + now.minute
    should_open = (start <= cur < end) if start < end else (cur >= start or cur < end)
    state = "open" if should_open else "closed"
    if get_setting("sched_state") == state:
        return
    set_setting("sched_state", state)
    if should_open:
        set_setting("is_open", "1")
        set_setting("closed_message", "")
    else:
        set_setting("is_open", "0")
        set_setting("closed_message", f"Hozir yopiqmiz. Ish vaqti: {WORK_HOURS}")


async def smena_scheduler(bot) -> None:
    first = True
    while True:
        try:
            check_schedule(datetime.now(LOCAL_TZ))
            await check_stuck_orders(bot)
            await run_report_checks(bot, datetime.now(LOCAL_TZ), first_pass=first)
            first = False
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Smena hisoboti xatosi")
        await asyncio.sleep(30)


@report_only
async def cmd_hisobot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, _, _, _ = build_shift_report(shift_since(), int(time.time()))
    await update.message.reply_text(
        text + "\n\n(Bu joriy smena hisoboti. Smenani yopish: /smena)", parse_mode=ParseMode.HTML
    )


@report_only
async def cmd_smena(update: Update, context: ContextTypes.DEFAULT_TYPE):
    until = int(time.time())
    await send_shift_report(context.bot, shift_since(), until)
    set_setting("last_report_ts", str(until))
    await update.message.reply_text("✅ Smena yopildi, hisobot yuborildi. Keyingi hisobot shu paytdan boshlab hisoblanadi.")


@admin_only
async def cmd_raqam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            f"Keyingi buyurtma raqami: #{next_order_no()}\n"
            "O'zgartirish: /raqam 4  (kafedagi hozirgi oxirgi buyurtma raqami 4 bo'lsa)"
        )
        return
    arg = context.args[0].lstrip("#")
    if not arg.isdigit() or int(arg) > 100000:
        await update.message.reply_text("Raqam noto'g'ri. Masalan: /raqam 4")
        return
    nxt = set_last_order_no(int(arg))
    await update.message.reply_text(f"✅ Tayyor. Keyingi buyurtma #{nxt} bo'ladi.")


@admin_only
async def cmd_chek(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not PRINTER_ENABLED:
        await update.message.reply_text("Printer o'chirilgan (bot.py da PRINTER_ENABLED = False).")
        return
    if context.args:
        arg = context.args[0].lstrip("#")
        order = find_order_by_no(int(arg)) if arg.isdigit() else None
        if not order:
            await update.message.reply_text("Bugun bunday raqamli buyurtma topilmadi. Masalan: /chek 5")
            return
        data, label = build_chek(order), f"Buyurtma #{order_no(order)} cheki"
    else:
        data, label = build_test_chek(), "Sinov cheki"
    try:
        where = await asyncio.get_running_loop().run_in_executor(None, send_to_printer, data)
        await update.message.reply_text(f"🖨 {label} printerga yuborildi ({where}).")
    except Exception as exc:
        await update.message.reply_text(f"❌ Chek chiqmadi: {exc}")


# ---- Mijozlar bazasi: /mijozlar va /eksport ----
CUSTOMERS_SQL = """
    SELECT u.user_id, u.username, u.full_name, u.first_seen, u.last_seen, u.blocked,
           COALESCE(o.cnt, 0) AS cnt, COALESCE(o.spent, 0) AS spent, o.last_order,
           (SELECT phone FROM orders WHERE user_id = u.user_id ORDER BY id DESC LIMIT 1) AS phone
    FROM users u
    LEFT JOIN (
        SELECT user_id, COUNT(*) AS cnt, SUM(total) AS spent, MAX(created_at) AS last_order
        FROM orders WHERE status != 'cancelled' GROUP BY user_id
    ) o ON o.user_id = u.user_id
    ORDER BY cnt DESC, u.last_seen DESC
"""


def customer_rows():
    return db().execute(CUSTOMERS_SQL).fetchall()


def _csv_safe(value) -> str:
    """Excel'da formula sifatida ishlab ketmasligi uchun (=, +, -, @ bilan boshlansa)."""
    s = "" if value is None else str(value).replace("\r", " ").replace("\n", " ").strip()
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t") else s


def _csv_phone(phone) -> str:
    digits = re.sub(r"\D", "", str(phone or ""))
    if len(digits) == 12 and digits.startswith("998"):  # bo'sh joylar Excel'da raqamga aylanib ketmasligi uchun
        return f"+998 {digits[3:5]} {digits[5:8]} {digits[8:10]} {digits[10:12]}"
    return _csv_safe(phone)


def _csv_dt(ts) -> str:
    return datetime.fromtimestamp(ts, LOCAL_TZ).strftime("%d.%m.%Y %H:%M") if ts else ""


def build_customers_csv() -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow(
        ["Telegram ID", "Ism", "Username", "Telefon", "Buyurtmalar soni", "Jami summa (so'm)",
         "Oxirgi buyurtma", "Birinchi kirgan", "Oxirgi faollik", "Botni bloklagan"]
    )
    for r in customer_rows():
        writer.writerow(
            [
                r["user_id"],
                _csv_safe(r["full_name"]),
                _csv_safe(r["username"]),
                _csv_phone(r["phone"]),
                r["cnt"],
                r["spent"],
                _csv_dt(r["last_order"]),
                _csv_dt(r["first_seen"]),
                _csv_dt(r["last_seen"]),
                "ha" if r["blocked"] else "",
            ]
        )
    return ("\ufeff" + buf.getvalue()).encode("utf-8")  # BOM — Excel o'zbek/rus harflarini to'g'ri ochishi uchun


def build_customers_summary() -> str:
    rows = customer_rows()
    week_ago = int(time.time()) - 7 * 86400
    ordered = [r for r in rows if r["cnt"]]
    lines = [
        "<b>👥 Mijozlar</b>",
        "",
        f"Jami: {len(rows)} ta",
        f"Buyurtma bergan: {len(ordered)} ta",
        f"Oxirgi 7 kunda qo'shilgan: {sum(1 for r in rows if r['first_seen'] >= week_ago)} ta",
        f"Botni bloklagan: {sum(1 for r in rows if r['blocked'])} ta",
        "",
        "<b>🏆 Eng faol mijozlar</b>",
    ]
    for rank, r in enumerate(ordered[:10], start=1):
        uname = f" (@{esc(r['username'])})" if r["username"] else ""
        lines.append(
            f"{rank}. {esc(r['full_name'] or 'Mijoz')}{uname} — {r['cnt']} ta, {fmt_money(r['spent'])} so'm"
        )
    if not ordered:
        lines.append("Hozircha buyurtma bergan mijoz yo'q")
    lines += ["", "To'liq ro'yxat (Excel / Google Sheets uchun fayl): /eksport"]
    return "\n".join(lines)


@admin_only
async def cmd_mijozlar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(build_customers_summary(), parse_mode=ParseMode.HTML)


@admin_only
async def cmd_eksport(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = build_customers_csv()
    filename = f"mijozlar_{datetime.now(LOCAL_TZ).strftime('%Y-%m-%d')}.csv"
    await context.bot.send_document(
        chat_id=update.effective_chat.id,
        document=data,
        filename=filename,
        caption=f"👥 Mijozlar ro'yxati: {len(customer_rows())} ta",
    )


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


async def cmd_telefon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kuryer o'z telefon raqamini bir marta saqlaydi: /telefon +998 90 123 45 67"""
    if not update.message or not update.effective_user:
        return
    raw = " ".join(context.args).strip() if context.args else ""
    digits = re.sub(r"\D", "", raw)
    if not (9 <= len(digits) <= 15):
        await update.message.reply_text("Raqamni shunday yozing:\n/telefon +998 90 123 45 67")
        return
    phone = f"+{digits}" if len(digits) > 9 else digits
    db().execute(
        "INSERT INTO couriers (user_id, phone) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET phone=excluded.phone",
        (update.effective_user.id, phone),
    )
    await update.message.reply_text(f"✅ Saqlandi: {phone}\nEndi kuryerlar guruhida «🛵 Men olaman» tugmasini bosa olasiz.")


async def on_courier_take(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    oid = int(query.data.split(":")[1])
    order = get_order(oid)
    if not order or order["status"] in ("done", "cancelled"):
        await query.answer("Bu buyurtma yopilgan.", show_alert=True)
        return
    name = query.from_user.full_name or "Kuryer"
    row = db().execute("SELECT phone FROM couriers WHERE user_id=?", (query.from_user.id,)).fetchone()
    if not row:
        await query.answer(
            "Avval botga SHAXSIY xabarda telefon raqamingizni yozing:\n/telefon +998 90 123 45 67\nKeyin qayta bosing.",
            show_alert=True,
        )
        return
    phone = row["phone"]
    cur = db().execute(
        "UPDATE orders SET courier_id=?, courier_name=?, courier_phone=? WHERE id=? AND courier_id IS NULL",
        (query.from_user.id, name, phone, oid),
    )
    if cur.rowcount == 0:
        await query.answer("Bu buyurtmani boshqa kuryer oldi.", show_alert=True)
        await refresh_order_messages(context.bot, oid)
        return
    await query.answer("Buyurtma sizga biriktirildi ✅")
    await refresh_order_messages(context.bot, oid)
    await safe_send(context.bot, order["user_id"], f"🛵 Kuryer <b>{esc(name)}</b> buyurtmangizni #{order_no(order)} oldi.\n📞 Kuryer telefoni: {esc(phone)}")
    for admin_id in ADMIN_IDS:
        await safe_send(context.bot, admin_id, f"🛵 #{order_no(order)} buyurtmani {esc(name)} ({esc(phone)}) oldi.")


async def on_courier_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    oid = int(query.data.split(":")[1])
    order = get_order(oid)
    if not order or order["courier_id"] != query.from_user.id:
        await query.answer("Bu buyurtma sizga biriktirilmagan.", show_alert=True)
        return
    if order["status"] not in ("ready", "onway"):
        await query.answer("Buyurtma hali tayyor emas. Tayyor bo'lgach bosing.", show_alert=True)
        return
    set_order_status(oid, "done")
    await query.answer("Yetkazildi ✅")
    await refresh_order_messages(context.bot, oid)
    await notify_customer_status(context.bot, get_order(oid))


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
            context.bot, admin_id, f"⭐ Buyurtma #{order_no(order)} uchun mijoz {rating} baho berdi."
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
    BotCommand("chek", "Chekni qayta chiqarish / sinov"),
    BotCommand("raqam", "Buyurtma raqamini belgilash"),
    BotCommand("hisobot", "Joriy smena hisoboti"),
    BotCommand("smena", "Smenani yopish"),
    BotCommand("mijozlar", "Mijozlar bazasi"),
    BotCommand("eksport", "Mijozlar ro'yxati (fayl)"),
]


# ---- INTERNETGA CHIQISH (cloudflared) — avtomatik ----
TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
_tunnel_proc = None


def find_cloudflared():
    candidates = [shutil.which("cloudflared"), shutil.which("cloudflared.exe"), str(BASE_DIR / "cloudflared.exe")]
    for var, sub in (
        ("ProgramFiles(x86)", "cloudflared"),
        ("ProgramFiles", "cloudflared"),
        ("LOCALAPPDATA", "Microsoft/WinGet/Links"),
    ):
        base = os.environ.get(var)
        if base:
            candidates.append(str(Path(base) / sub / "cloudflared.exe"))
    for c in candidates:
        if c and Path(c).exists():
            return c
    return None


def site_link(tunnel_url: str) -> str:
    """Doimiy sayt bo'lsa: u ochiladi va server manzili ?api= bilan beriladi."""
    if not PERMANENT_SITE_URL:
        return tunnel_url
    sep = "&" if "?" in PERMANENT_SITE_URL else "?"
    return f"{PERMANENT_SITE_URL}{sep}api={tunnel_url}"


def stop_tunnel() -> None:
    global _tunnel_proc
    proc, _tunnel_proc = _tunnel_proc, None
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass


def start_tunnel(exe: str, port: int, timeout: float = 60.0):
    """cloudflared ni ishga tushiradi va https://....trycloudflare.com manzilini qaytaradi."""
    global _tunnel_proc
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(
        [exe, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate", "--protocol", "http2"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **kwargs,
    )
    _tunnel_proc = proc
    found = threading.Event()
    box = {}

    def reader():
        # stderr ni oxirigacha o'qib turish shart, aks holda cloudflared qotib qoladi
        for line in proc.stderr:
            if "url" not in box:
                for m in TUNNEL_RE.finditer(line):
                    if m.group(0) != "https://api.trycloudflare.com":
                        box["url"] = m.group(0)
                        found.set()
                        break
        found.set()

    threading.Thread(target=reader, daemon=True).start()
    found.wait(timeout)
    return box.get("url")


atexit.register(stop_tunnel)
# ---- /INTERNETGA CHIQISH ----


async def post_init(application):
    global WEB_APP_URL
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
    spawn(smena_scheduler(application.bot))

    # Ilovaga internetdan kirish manzili
    if not WEB_APP_URL:
        exe = find_cloudflared()
        if exe is None:
            print(
                "\n⚠️  cloudflared topilmadi, shuning uchun ilova internetdan ochilmaydi.\n"
                "   Bir marta o'rnating (PowerShell'da):\n"
                "       winget install --id Cloudflare.cloudflared\n"
                "   Keyin PowerShell/VS Code'ni yopib qayta oching va botni qayta ishga tushiring.\n"
            )
        else:
            print("🌐 Internetga chiqish manzili olinmoqda (10-30 soniya)...")
            loop = asyncio.get_running_loop()
            url = await loop.run_in_executor(None, start_tunnel, exe, PORT)
            if url:
                WEB_APP_URL = site_link(url)
            else:
                print("⚠️  Manzil olinmadi. Internetni tekshirib, botni qayta ishga tushiring.")

    if WEB_APP_URL:
        # Chat chap pastida faqat "Open App" tugmasi turadi
        await application.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Open App", web_app=WebAppInfo(url=WEB_APP_URL))
        )
        print(f"\n✅ Ilova manzili: {WEB_APP_URL}\n   Telegramda botga /start yuboring va 'Open App' ni bosing.\n")


async def post_shutdown(application):
    stop_tunnel()
    runner = application.bot_data.get("web_runner")
    if runner is not None:
        await runner.cleanup()


def main():
    if not BOT_TOKEN:
        raise SystemExit(
            "❌ BOT_TOKEN topilmadi.\n"
            "   bot.py ning yuqorisidagi  BOT_TOKEN = \"\"  qatoriga @BotFather bergan tokenni yozing."
        )
    if not ADMIN_IDS:
        raise SystemExit("❌ ADMIN_IDS topilmadi. bot.py yuqorisidagi ADMIN_IDS = [...] ga Telegram ID yozing.")

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
    app.add_handler(CommandHandler("telefon", cmd_telefon))
    app.add_handler(CommandHandler("aloqa", cmd_aloqa))
    app.add_handler(CommandHandler("manzil", cmd_manzil))
    app.add_handler(CommandHandler("ochish", cmd_ochish))
    app.add_handler(CommandHandler("yopish", cmd_yopish))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("chek", cmd_chek))
    app.add_handler(CommandHandler("raqam", cmd_raqam))
    app.add_handler(CommandHandler("hisobot", cmd_hisobot))
    app.add_handler(CommandHandler("smena", cmd_smena))
    app.add_handler(CommandHandler("mijozlar", cmd_mijozlar))
    app.add_handler(CommandHandler("eksport", cmd_eksport))

    app.add_handler(CallbackQueryHandler(on_status_callback, pattern=r"^st:\d+:(accepted|ready|onway|done|cancelled)$"))
    app.add_handler(CallbackQueryHandler(on_courier_take, pattern=r"^cr:\d+$"))
    app.add_handler(CallbackQueryHandler(on_courier_done, pattern=r"^cd:\d+$"))
    app.add_handler(CallbackQueryHandler(on_cancel_ask_callback, pattern=r"^cq:\d+$"))
    app.add_handler(CallbackQueryHandler(on_cancel_back_callback, pattern=r"^cb:\d+$"))
    app.add_handler(CallbackQueryHandler(on_rate_callback, pattern=r"^rate:\d+:[1-5]$"))
    app.add_handler(CallbackQueryHandler(on_stop_callback, pattern=r"^sl:"))
    app.add_handler(CallbackQueryHandler(on_broadcast_callback, pattern=r"^bc:(yes|no)$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, on_any_text))
    app.add_error_handler(on_error)

    print("Bot muvaffaqiyatli ishga tushmoqda...")
    app.run_polling()


if __name__ == "__main__":
    main()
