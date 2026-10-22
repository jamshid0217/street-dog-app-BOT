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
import random
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
BOT_TOKEN = '8983485871:AAEItnJAPmd2xQJx7ON72SOQsu_egvYgsvU'  # @BotFather bergan token
# ---- ROLLAR ----
# SUPER ADMIN (egasi): HAMMA buyruq — statistika, hisobot, mijozlar bazasi, xabar yuborish va h.k.
# ADMIN (menejer/kassir): faqat kerakli buyruqlar — /ochish /yopish /stop /chek va buyurtma tugmalari.
# ⚠️ O'z Telegram ID ingizni SUPER_ADMIN_IDS ga yozing (ID ni @userinfobot dan bilib olasiz).
SUPER_ADMIN_IDS = [6069854654]   # super admin(lar)
ADMIN_IDS = [6048997378]         # oddiy admin(lar)
STAFF_IDS = SUPER_ADMIN_IDS + [i for i in ADMIN_IDS if i not in SUPER_ADMIN_IDS]  # barcha xodimlar (filial adminlari ham shu yerga qo'shiladi)
# Filial adminlari: har bir filialga BRANCHES ichida "admin_ids" yoziladi (pastda). Admin FAQAT o'z filialining buyurtmalarini ko'radi.
SUPER_SEES_ALL = True   # True = super admin (egasi) HAMMA filial buyurtmalarini ko'radi. False = faqat o'zi biriktirilgan filiallarni
COURIER_CHAT_ID = -5491727953  # kuryerlar guruhi ID si (ixtiyoriy)

CONTACT_PHONE = '+998 91 966 40 40'
CAFE_ADDRESS = 'Urganch shahri'
WORK_HOURS = '24/7 (har kuni, kechayu-kunduz)'
CAFE_LAT = None  # kafe lokatsiyasi (ixtiyoriy)
CAFE_LON = None

# ---- "Sizni sog'indik" xabari (/qaytarish) va promokodlar ----
# Matnda ishlatiladi: {hello} {percent} {code} {days}. HTML teglar (<b>, <code>) ishlaydi.
WINBACK_TEXT = (
    "👋 {hello} sog'indik!\n\n"
    "Street Dog'da sizni mazali buyurtma kutmoqda. Faqat siz uchun — <b>{percent}% chegirma</b>! 🎁\n\n"
    "Promokod: <code>{code}</code>\n"
    "Amal qilish muddati: {days} kun.\n\n"
    "Ilovada buyurtma berayotganda promokodni kiriting 👇"
)

# ---- AVTOMATIK "Sizni sog'indik" (promokodsiz, chegirmasiz) ----
# Bot har kuni AUTO_WINBACK_TIME da o'zi tekshiradi: oxirgi buyurtmasiga AUTO_WINBACK_DAYS kundan ko'p bo'lgan
# har bir mijozga chiroyli "sog'indik" xabari yuboradi. Yoqish/o'chirish botdan ham mumkin: /avto on  |  /avto off
AUTO_WINBACK = True                # True = yoqilgan
AUTO_WINBACK_DAYS = 15             # shuncha kundan beri buyurtma bermaganlarga yuboriladi
AUTO_WINBACK_TIME = "18:15"        # xabarlar har kuni aynan shu vaqtda yuboriladi (SS:DD)
AUTO_WINBACK_LATE_MIN = 120        # bot shu vaqtda o'chiq bo'lib, keyin yonsa: shuncha daqiqagacha kechiksa ham yuboradi, undan keyin ertaga
AUTO_WINBACK_COOLDOWN_DAYS = 30    # bir mijozga qayta yuborishdan oldin kamida shuncha kun kutiladi
AUTO_WINBACK_MAX_TIMES = 2         # bitta "yo'qolish" davrida bir mijozga eng ko'pi bilan necha marta yuboriladi
AUTO_WINBACK_MAX_PER_DAY = 300     # kuniga eng ko'pi bilan nechta xabar
AUTO_WINBACK_INCLUDE_NEVER = False  # True = hech qachon buyurtma bermagan (lekin 15 kundan beri botda bor) mijozlarga ham

# Har safar shu matnlardan biri tasodifiy tanlanadi. {intro} — "oxirgi buyurtmadan N kun o'tdi" va sevimli taomi (bor bo'lsa).
AUTO_WINBACK_TEXTS = [
    "👋 {hello} sog'indik!\n\n{intro}"
    "Street Dog'da sizni doim kutamiz. Bugun kechqurun o'zingizni mazali burger yoki xot-dog bilan xursand qiling! 🍔🌭\n\n"
    "Buyurtma berish uchun pastdagi tugmani bosing 👇",
    "🥺 {hello} juda sog'indik!\n\n{intro}"
    "Oshpazlarimiz ham sizni kutib qolishdi. Qaytib keling — o'sha mazali ta'm yana sizni kutmoqda! 🍔\n\n"
    "Menyuni ochish uchun tugmani bosing 👇",
    "🌭 {hello} sog'indik!\n\n{intro}"
    "Kun charchoq bo'ldimi? Kechki ovqatni biz tayyorlab, issiq-issiq yetkazib beramiz! 🛵🔥\n\n"
    "Buyurtma berish uchun tugmani bosing 👇",
]

# ---- CHEK PRINTER (XPrinter va boshqa termoprinterlar) ----
PRINTER_ENABLED = False  # yangi buyurtma kelganda chek avtomatik chiqadi. O'chirish: False
PRINTER_NAME = ""       # Windows'dagi printer nomi. Bo'sh qoldirsangiz — Windows'ning standart printeri
PRINTER_IP = ""         # LAN/Wi-Fi printer bo'lsa uning IP si, masalan "192.168.1.50" (USB bo'lsa bo'sh)
PRINTER_PORT = 9100
PRINTER_WIDTH = 48      # 80 mm qog'oz = 48, 58 mm qog'oz = 32
# Har filialga alohida printer: BRANCHES ichida filialga "printer_ip" (LAN/Wi-Fi printer) yoki "printer_name" (shu kompyuterga
# ulangan printerning Windows'dagi nomi) yoziladi; kerak bo'lsa "printer_port" va "printer_width" ham. Yozilmasa — yuqoridagi umumiy printer.
CAFE_NAME = "STREET DOG"

# ---- DOSTAVKA ----
DELIVERY_FEE = 10000  # dostavka narxi (so'm), taxminan 5 km gacha. Olib ketishda 0.
# ---- FILIALLAR (ixtiyoriy) ----
# Bo'sh ro'yxat = bitta kafe (hammasi avvalgidek ishlaydi). Filial qo'shsangiz:
#  • Dostavka: mijoz joylashuviga ENG YAQIN filial avtomatik tanlanadi.
#  • Eng yaqin filialdan ham MAX_DELIVERY_KM dan uzoq bo'lsa — dostavka qabul qilinmaydi.
#  • Olib ketish: mijoz filialni o'zi tanlaydi.
#  • Buyurtma shu filial kuryerlar guruhiga (courier_chat_id) boradi; yo'q bo'lsa — umumiy COURIER_CHAT_ID ga.
# Koordinatani Google Maps'da filial ustini bosib olasiz (masalan 41.5500, 60.6300).
BRANCHES = []
# BRANCHES = [
#     {"id": "markaz", "name": "Markaz filiali", "lat": 41.5500, "lon": 60.6300, "courier_chat_id": None, "admin_ids": [111111111]},
#     {"id": "sharq",  "name": "Sharq filiali",  "lat": 41.5700, "lon": 60.6600, "courier_chat_id": None, "admin_ids": [222222222]},
# ]
# "id" — faqat lotin harf/raqam (masalan "markaz"), ":" va bo'sh joy bo'lmasin.
# "admin_ids" — shu filial buyurtmalarini oladigan va boshqaradigan adminlarning Telegram ID lari (bir nechta bo'lishi mumkin).
# Admin boshqa filial buyurtmasini ko'rmaydi va tugmalarini bosa olmaydi,
# /ochish /yopish /stop ham faqat o'z filiali uchun ishlaydi (super admin — hamma filial uchun). "admin_ids" bo'sh bo'lsa — buyurtma hamma adminga boradi.
MAX_DELIVERY_KM = 7   # eng yaqin filialdan shuncha km dan uzoqqa dostavka yo'q (0 = cheklov yo'q). Masofa to'g'ri chiziq bo'yicha.
for _br in BRANCHES:  # filial adminlari ham xodim hisoblanadi (buyruqlar, tugmalar uchun)
    for _aid in (_br.get("admin_ids") or []):
        if _aid not in STAFF_IDS:
            STAFF_IDS.append(_aid)

# ---- SMENA HISOBOTI ----
COMMISSION_PERCENT = 5          # savdodan sizning ulushingiz (foiz)
NEW_PRODUCTS = []               # "Yangi" belgisi qo'yiladigan taom kodlari, masalan ['b5_75', 'g3_35']
HIT_MIN_SOLD = 3                # 30 kunda kamida shuncha dona sotilgan eng yaxshi 5 ta taom "Hit" bo'ladi
REVIEW_URL = ""                # Google Maps sharh havolasi: 5 baho bergan mijozga yuboriladi (bo'sh = yuborilmaydi)
LOW_RATING_MAX = 2              # shu baho va undan past bo'lsa adminga shoshilinch ogohlantirish
NEW_ORDER_ALERT_MIN = 3         # yangi buyurtma shuncha daqiqa qabul qilinmasa, adminga birinchi ogohlantirish
NEW_ORDER_REMIND_EVERY_MIN = 3  # qabul qilinmaguncha shuncha daqiqada bir marta qayta eslatiladi
NEW_ORDER_REMIND_MAX = 10       # eng ko'pi bilan nechta eslatma (cheksiz xabar yuborib yubormaslik uchun)

# ---- ZAXIRA NUSXA va BOT TO'XTASA OGOHLANTIRISH ----
BACKUP_ENABLED = True           # har kuni baza nusxasi Telegramga yuboriladi (fayl yoki papka kerak emas)
BACKUP_TIME = "04:00"           # har kuni shu vaqtdan keyin (kompyuter o'chiq bo'lsa — yonganda darrov) yuboriladi
BACKUP_CHAT_IDS: list = []      # bo'sh bo'lsa — super adminlarga. Boshqa joyga ham yuborish uchun ID larni yozing
HEALTH_SELF_CHECK = True        # bot ilovaning internetdan ochilishini har 2 daqiqada tekshiradi, uzilsa sizga yozadi
# Kompyuter yoki internet butunlay o'chsa, bot o'zi xabar yubora olmaydi — buning uchun TASHQI nazoratchi kerak:
# healthchecks.io da tekin akkaunt oching → "Add Check" (Period: 1 minute, Grace: 3 minutes) → Integrations'dan Telegram'ni ulang →
# check manzilini (https://hc-ping.com/....) shu yerga yozing. Bot har daqiqada "tirikman" deb yozib turadi, to'xtasa — Telegramga xabar keladi.
HEALTH_URL = "https://hc-ping.com/7fc012e4-62da-45fd-88e4-6b4c8b609825"
AUTO_SCHEDULE = False           # WORK_HOURS bo'yicha o'zi ochiladi/yopiladi (qo'lda /ochish /yopish keyingi ochilish/yopilishgacha ishlaydi)
SMENA_HISOBOT_VAQTI = "23:00"   # hisobot har kuni shu vaqtda o'zi yuboriladi (SS:DD)
HISOBOT_IDS = []                # hisobot kimga borsin (Telegram ID). Bo'sh bo'lsa — faqat SUPER_ADMIN_IDS ga
REPORT_IDS = HISOBOT_IDS or SUPER_ADMIN_IDS

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
#  MENYU — yagona manba: bot bazasi. Ilova (index.html) menyuni serverdan oladi.
#  Narx, nom, rasm o'zgartirish va yangi taom qo'shish: Telegramda /menyu
# =====================================================================
CATEGORIES = {
    "hotdog": "🌭 Xot-Dog",
    "burger": "🍔 Burger",
    "garnir": "🍟 Garnirlar",
}

PRODUCTS: dict = {}  # menyu bazadan (custom_products jadvali) yuklanadi — yagona manba. O'zgartirish: Telegramda /menyu

# Faqat BIRINCHI ishga tushganda bazaga ko'chiriladi (id, bo'lim, nom, narx, rasm havolasi, tavsif).
# Shundan keyin menyu faqat bazada yashaydi: bu ro'yxatni o'zgartirish hech narsaga ta'sir qilmaydi.
BASE_MENU = [
    ('h1_25', 'hotdog', 'Острый хот-дог (C)', 25000, 'https://i.ibb.co/whFmZhY4/uploaded-image.jpg', 'Булочка, сосиска 2х, корейский баклажан, свит чили соус, фирменный соус, халапеньо'),
    ('h1_35', 'hotdog', 'Острый хот-дог (M)', 35000, 'https://i.ibb.co/whFmZhY4/uploaded-image.jpg', 'Булочка, сосиска 2х, корейский баклажан, свит чили соус, фирменный соус, халапеньо'),
    ('h2_30', 'hotdog', 'Чикаго хот-дог (C)', 30000, 'https://i.ibb.co/d0h8q4T9/uploaded-image.jpg', 'Булочка, сосиска 2х, мясо тушеное, сырный соус, фирменный соус'),
    ('h2_45', 'hotdog', 'Чикаго хот-дог (M)', 45000, 'https://i.ibb.co/d0h8q4T9/uploaded-image.jpg', 'Булочка, сосиска 2х, мясо тушеное, сырный соус, фирменный соус'),
    ('h3_50', 'hotdog', 'Американский хот-дог', 50000, 'https://i.ibb.co/SwyLsq23/uploaded-image.jpg', 'Сытный американский хот-дог с сосиской, горчичным соусом и мясной начинкой'),
    ('h4_30', 'hotdog', 'Нью-Йорк хот-дог', 30000, 'https://i.ibb.co/1JX6pT55/uploaded-image.jpg', 'Булочка, сосиска 2х, сол. огурец, горчичный соус, чипсы, кетчуп, майонез'),
    ('h5_30', 'hotdog', 'Мехико хот-дог (C)', 30000, 'https://i.ibb.co/V04WZqTY/uploaded-image.jpg', 'Булочка, сосиска 2х, фарш, медовый соус, фирменный соус'),
    ('h5_45', 'hotdog', 'Мехико хот-дог (M)', 45000, 'https://i.ibb.co/V04WZqTY/uploaded-image.jpg', 'Булочка, сосиска 2х, фарш, медовый соус, фирменный соус'),
    ('h6_35', 'hotdog', 'Классический хот-дог', 35000, 'https://i.ibb.co/nsBf3tYp/uploaded-image.jpg', 'Булочка, сосиска 2х, помидор, огурец, айсберг, сырный соус, кетчуп, майонез'),
    ('h7_30', 'hotdog', 'Чикен хот-дог (C)', 30000, 'https://i.ibb.co/Q3BMMmBc/uploaded-image.jpg', 'Булочка, сосиска 2х, курица терияки, сыр чеддер, чипсы, свит чили соус, кетчуп'),
    ('h7_40', 'hotdog', 'Чикен хот-дог (M)', 40000, 'https://i.ibb.co/Q3BMMmBc/uploaded-image.jpg', 'Булочка, сосиска 2х, курица терияки, сыр чеддер, чипсы, свит чили соус, кетчуп'),
    ('h8_25', 'hotdog', 'Французский хот-дог', 25000, 'https://i.ibb.co/DHCWRTzt/uploaded-image.jpg', 'Твинс батон, сосиска, сырный соус, фирменный соус'),
    ('h9_50', 'hotdog', 'Премиум хот-дог', 50000, 'https://i.ibb.co/wZYLgqbt/uploaded-image.jpg', 'Булочка, сосиска 2х, фирменный соус, чеддер сыр, мясо тушеное, сыр моцарелла'),
    ('h10_20', 'hotdog', 'Детский хот-дог', 20000, 'https://i.ibb.co/20BhbqBf/uploaded-image.jpg', 'Небольшой хот-дог для детей: мягкая булочка, сосиска и нежный соус'),
    ('h11_40', 'hotdog', 'Роял фреш', 40000, 'https://i.ibb.co/kVcNDpP9/uploaded-image.jpg', 'Сосиска 3х, корейский баклажан, фри, халапеньо, сырный соус, фирменный соус, мини чиабатта'),
    ('h12_35', 'hotdog', 'Сендвич', 35000, 'https://i.ibb.co/VWHCz3Px/uploaded-image.jpg', 'Горячий тостовый сендвич, нарезанный треугольниками'),
    ('h13_35', 'hotdog', 'Итальянский сендвич', 35000, 'https://i.ibb.co/5Xw3QhmR/uploaded-image.jpg', 'Сытный сендвич в итальянском стиле на мягкой булочке со свежими овощами'),
    ('b1_65', 'burger', 'Стрит бургер (M)', 65000, 'https://i.ibb.co/kgkQ0Cf9/uploaded-image.jpg', 'Фирменный бургер с двумя котлетами и расплавленным сыром'),
    ('b1_80', 'burger', 'Стрит бургер (L)', 80000, 'https://i.ibb.co/kgkQ0Cf9/uploaded-image.jpg', 'Фирменный бургер с двумя котлетами и расплавленным сыром'),
    ('b1_95', 'burger', 'Стрит бургер (XL)', 95000, 'https://i.ibb.co/kgkQ0Cf9/uploaded-image.jpg', 'Фирменный бургер с двумя котлетами и расплавленным сыром'),
    ('b2_65', 'burger', 'Классический (M)', 65000, 'https://i.ibb.co/Yvwj0C3/uploaded-image.jpg', 'Булочка, фарш, бургер соус, сыр чеддер, помидор, айсберг, сол. огурец'),
    ('b2_80', 'burger', 'Классический (L)', 80000, 'https://i.ibb.co/Yvwj0C3/uploaded-image.jpg', 'Булочка, фарш, бургер соус, сыр чеддер, помидор, айсберг, сол. огурец'),
    ('b2_95', 'burger', 'Классический (XL)', 95000, 'https://i.ibb.co/Yvwj0C3/uploaded-image.jpg', 'Булочка, фарш, бургер соус, сыр чеддер, помидор, айсберг, сол. огурец'),
    ('b3_65', 'burger', 'Трюфельный (M)', 65000, 'https://i.ibb.co/pvHX3pK4/uploaded-image.jpg', 'Булочка, фарш, трюфельный соус, сыр президент'),
    ('b3_80', 'burger', 'Трюфельный (L)', 80000, 'https://i.ibb.co/pvHX3pK4/uploaded-image.jpg', 'Булочка, фарш, трюфельный соус, сыр президент'),
    ('b3_95', 'burger', 'Трюфельный (XL)', 95000, 'https://i.ibb.co/pvHX3pK4/uploaded-image.jpg', 'Булочка, фарш, трюфельный соус, сыр президент'),
    ('b4_65', 'burger', 'Острый (M)', 65000, 'https://i.ibb.co/r2Kxj7Cp/uploaded-image.jpg', 'Булочка, фарш, острый соус, сыр чеддер, халапеньо'),
    ('b4_80', 'burger', 'Острый (L)', 80000, 'https://i.ibb.co/r2Kxj7Cp/uploaded-image.jpg', 'Булочка, фарш, острый соус, сыр чеддер, халапеньо'),
    ('b4_95', 'burger', 'Острый (XL)', 95000, 'https://i.ibb.co/r2Kxj7Cp/uploaded-image.jpg', 'Булочка, фарш, острый соус, сыр чеддер, халапеньо'),
    ('b5_75', 'burger', 'Стейк бургер', 75000, 'https://i.ibb.co/DfHMTmw8/uploaded-image.jpg', 'Булочка 10 см, бонфиле 130-150 гр, фирменный соус, сыр чеддер'),
    ('b6_40', 'burger', 'Чизбургер', 40000, 'https://i.ibb.co/pB3Pm196/uploaded-image.jpg', 'Булочка 10 см, фарш 65 гр, бургер соус, сыр чеддер, лук'),
    ('b7_45', 'burger', 'Чикен бургер', 45000, 'https://i.ibb.co/JFG2fX92/uploaded-image.jpg', 'Бургер с хрустящей куриной котлетой в панировке, сыром и свежим салатом'),
    ('b8_65', 'burger', 'ББК (M)', 65000, 'https://i.ibb.co/MxftJ80j/uploaded-image.jpg', 'Булочка, фарш, ББК соус, сыр чеддер, лук, сол. огурец'),
    ('b8_80', 'burger', 'ББК (L)', 80000, 'https://i.ibb.co/MxftJ80j/uploaded-image.jpg', 'Булочка, фарш, ББК соус, сыр чеддер, лук, сол. огурец'),
    ('b8_95', 'burger', 'ББК (XL)', 95000, 'https://i.ibb.co/MxftJ80j/uploaded-image.jpg', 'Булочка, фарш, ББК соус, сыр чеддер, лук, сол. огурец'),
    ('g1_58', 'garnir', 'СтрипБокс', 58000, 'https://i.ibb.co/bj5YyyzJ/uploaded-image.jpg', 'Куриный'),
    ('g2_60', 'garnir', 'МитБокс', 60000, 'https://i.ibb.co/gZJDFRcQ/uploaded-image.jpg', 'Мясной'),
    ('g3_35', 'garnir', 'Сырные подушки', 35000, 'https://i.ibb.co/Q79HDdyq/uploaded-image.jpg', 'Хрустящие золотистые подушечки с сырной начинкой, подаются с соусом'),
    ('g4_30', 'garnir', 'Фри с трюфелем', 30000, 'https://i.ibb.co/M5Jym5y0/uploaded-image.jpg', 'Хрустящий картофель фри с трюфельным соусом'),
    ('g5_25', 'garnir', 'Картофель Айдахо', 25000, 'https://i.ibb.co/fG4LCkQ3/uploaded-image.jpg', 'Румяные дольки картофеля по-деревенски (Айдахо)'),
    ('g6_20', 'garnir', 'Картофель фри', 20000, 'https://i.ibb.co/3Y53x7B3/uploaded-image.jpg', 'Хрустящий золотистый картофель фри'),
]

PAYMENT_METHODS = {"Naqd", "Karta", "Click", "Naqd / Karta", "Click / Payme"}  # eski nomlar eski ilovalar uchun

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


def default_closed_text() -> str:
    """Yopiq paytda mijozga ko'rinadigan xabar. WORK_HOURS "10:00-23:00" ko'rinishida bo'lsa — ish vaqti ham yoziladi."""
    rng = _work_range()
    if rng:
        a = f"{rng[0] // 60:02d}:{rng[0] % 60:02d}"
        b = f"{rng[1] // 60:02d}:{rng[1] % 60:02d}"
        return f"⏰ Ish vaqtimiz tugadi. Biz har kuni {a} dan {b} gacha ishlaymiz. Shu vaqtda buyurtma bering — sizni kutamiz! 😊"
    return DEFAULT_CLOSED_MESSAGE

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

SUPER_HELP = (
    "<b>👑 Super admin buyruqlari</b>\n"
    "/ochish — buyurtma qabul qilishni ochish\n"
    "/yopish [izoh] — buyurtma qabul qilishni to'xtatish\n"
    "/menyu — MENYU BOSHQARUVI: yangi taom qo'shish, narx/nom/rasmni o'zgartirish, o'chirish\n"
    "/stop — stop-list (tugagan taomlarni o'chirish/yoqish)\n"
    "/stats — statistika\n"
    "/broadcast matn — barcha mijozlarga xabar yuborish\n"
    "/chek [raqam] — chekni qayta chiqarish (raqamsiz — sinov cheki)\n"
    "/raqam [N] — kafedagi oxirgi buyurtma raqamini belgilash\n"
    "/hisobot — joriy smena hisoboti\n"
    "/smena — smenani yopish va hisobotni yuborish\n"
    "/mijozlar — mijozlar soni va eng faollari\n"
    "/eksport — mijozlar ro'yxatini fayl (Excel) qilib olish\n"
    "/avto — avtomatik «Sizni sog'indik» holati (/avto on · /avto off)"
)
ADMIN_HELP = (
    "<b>🛠 Admin buyruqlari</b>\n"
    "/ochish — buyurtma qabul qilishni ochish\n"
    "/yopish [izoh] — buyurtma qabul qilishni to'xtatish\n"
    "/stop — stop-list (tugagan taomlarni o'chirish/yoqish)\n"
    "/chek [raqam] — chekni qayta chiqarish (raqamsiz — sinov cheki)\n"
    "Buyurtmani bekor qilsangiz, sababini yozasiz — u mijozga yuboriladi."
)


def help_for(user_id) -> str:
    if user_id in SUPER_ADMIN_IDS:
        return SUPER_HELP
    if user_id in STAFF_IDS:
        return ADMIN_HELP
    return ""


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


# ---- filiallar ----
def _km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def find_branch(branch_id):
    for br in BRANCHES:
        if br["id"] == branch_id:
            return br
    return None


def nearest_branch(lat: float, lon: float):
    """(eng yaqin filial, masofa km) qaytaradi."""
    best = min(BRANCHES, key=lambda br: _km(lat, lon, br["lat"], br["lon"]))
    return best, _km(lat, lon, best["lat"], best["lon"])


def branch_name(branch_id) -> str:
    br = find_branch(branch_id)
    return br["name"] if br else (branch_id or "")


def branch_label(order, with_km: bool = False) -> str:
    br = find_branch(order["branch"])
    if not br:
        return ""
    if with_km and order["delivery_type"] == "delivery" and order["lat"] is not None and order["lon"] is not None:
        return f"{br['name']} · {_km(order['lat'], order['lon'], br['lat'], br['lon']):.1f} km"
    return br["name"]


def branch_courier_chat(branch_id):
    br = find_branch(branch_id)
    return (br.get("courier_chat_id") if br else None) or COURIER_CHAT_ID


def order_staff(order) -> list:
    """Shu buyurtmani ko'rishi va boshqarishi mumkin bo'lgan adminlar (faqat o'z filiali)."""
    br = find_branch(order["branch"]) if order["branch"] else None
    ids = list(br.get("admin_ids") or []) if br else []
    if not ids:  # filialga admin biriktirilmagan (yoki filial yo'q) — avvalgidek hammaga
        return list(STAFF_IDS)
    if SUPER_SEES_ALL:
        ids += [i for i in SUPER_ADMIN_IDS if i not in ids]
    return ids


def can_handle(user_id: int, order) -> bool:
    return user_id in order_staff(order)


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
        CREATE TABLE IF NOT EXISTS promo_codes (
            code        TEXT PRIMARY KEY,
            percent     INTEGER NOT NULL,
            valid_until INTEGER NOT NULL,
            user_id     INTEGER,
            max_uses    INTEGER,
            used        INTEGER NOT NULL DEFAULT 0,
            active      INTEGER NOT NULL DEFAULT 1,
            note        TEXT NOT NULL DEFAULT '',
            created_at  INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS promo_uses (
            code       TEXT NOT NULL,
            user_id    INTEGER NOT NULL,
            order_id   INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY (code, user_id)
        );
        CREATE TABLE IF NOT EXISTS banned (
            kind       TEXT NOT NULL,
            value      TEXT NOT NULL,
            label      TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            PRIMARY KEY (kind, value)
        );
        CREATE TABLE IF NOT EXISTS custom_products (
            id         TEXT PRIMARY KEY,
            category   TEXT NOT NULL,
            name       TEXT NOT NULL,
            price      INTEGER NOT NULL,
            descr      TEXT NOT NULL DEFAULT '',
            file_id    TEXT NOT NULL DEFAULT '',
            img_url    TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS price_overrides (
            product_id TEXT PRIMARY KEY,
            price      INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS branch_stoplist (
            branch_id  TEXT NOT NULL,
            product_id TEXT NOT NULL,
            PRIMARY KEY (branch_id, product_id)
        );
        """
    )
    try:  # eski bazalar uchun: rasm havolasi ustuni
        db().execute("ALTER TABLE custom_products ADD COLUMN img_url TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    seed_menu()  # birinchi ishga tushganda eski menyu bazaga ko'chadi
    load_custom_products()  # butun menyu bazadan yuklanadi (stop-list tozalashdan OLDIN)
    # Menyudan olib tashlangan taomlar stop-listda qolib ketmasin
    db().execute(
        "DELETE FROM stoplist WHERE product_id NOT IN (%s)" % ",".join("?" * len(PRODUCTS)),
        tuple(PRODUCTS),
    )
    db().execute(
        "DELETE FROM branch_stoplist WHERE product_id NOT IN (%s)" % ",".join("?" * len(PRODUCTS)),
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
    db().execute("CREATE TABLE IF NOT EXISTS winback_log (user_id INTEGER NOT NULL, sent_at INTEGER NOT NULL)")  # avtomatik «sog'indik» tarixi
    db().execute("CREATE INDEX IF NOT EXISTS idx_winback_user ON winback_log (user_id)")
    db().execute("CREATE TABLE IF NOT EXISTS courier_loc (user_id INTEGER PRIMARY KEY, lat REAL, lon REAL, updated_at INTEGER)")
    db().execute("CREATE TABLE IF NOT EXISTS couriers (user_id INTEGER PRIMARY KEY, phone TEXT NOT NULL)")
    try:  # eski bazalar uchun: mijoz izohi ustuni
        db().execute("ALTER TABLE orders ADD COLUMN note TEXT")
    except sqlite3.OperationalError:
        pass
    try:  # eski bazalar uchun: dostavka haqi ustuni
        db().execute("ALTER TABLE orders ADD COLUMN delivery_fee INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    for _ddl in (
        "ALTER TABLE orders ADD COLUMN discount INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE orders ADD COLUMN promo TEXT",
    ):
        try:  # eski bazalar uchun: chegirma va promokod ustunlari
            db().execute(_ddl)
        except sqlite3.OperationalError:
            pass
    try:  # eski bazalar uchun: bekor qilish sababi
        db().execute("ALTER TABLE orders ADD COLUMN cancel_reason TEXT")
    except sqlite3.OperationalError:
        pass
    try:  # eski bazalar uchun: filial ustuni
        db().execute("ALTER TABLE orders ADD COLUMN branch TEXT")
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
    return get_setting("closed_message", "") or default_closed_text()


# ---- filial bo'yicha ochiq/yopiq (umumiy "is_open" — bosh kalit, u yopiq bo'lsa hamma filial yopiq) ----
def branch_is_open(branch_id) -> bool:
    return is_open() and get_setting(f"is_open:{branch_id}", "1") == "1"


def branch_closed_message(branch_id) -> str:
    if not is_open():
        return closed_message()
    return get_setting(f"closed_message:{branch_id}", "") or default_closed_text()


def any_open() -> bool:
    """Kamida bitta filial (yoki filialsiz bo'lsa — kafe) ochiqmi."""
    return any(branch_is_open(b["id"]) for b in BRANCHES) if BRANCHES else is_open()


def any_closed_message() -> str:
    if BRANCHES and is_open():
        return branch_closed_message(BRANCHES[0]["id"])
    return closed_message()


# ---- stop-list (branch_id berilsa — shu filial uchun, bo'lmasa — umumiy) ----
def get_stoplist(branch_id=None) -> set:
    if branch_id:
        rows = db().execute("SELECT product_id FROM branch_stoplist WHERE branch_id=?", (branch_id,))
    else:
        rows = db().execute("SELECT product_id FROM stoplist")
    return {r["product_id"] for r in rows}


def toggle_stop(product_id: str, branch_id=None) -> bool:
    """True qaytarsa — mahsulot endi O'CHIRILGAN (tugagan)."""
    conn = db()
    if branch_id:
        row = conn.execute("SELECT 1 FROM branch_stoplist WHERE branch_id=? AND product_id=?", (branch_id, product_id)).fetchone()
        if row:
            conn.execute("DELETE FROM branch_stoplist WHERE branch_id=? AND product_id=?", (branch_id, product_id))
            return False
        conn.execute("INSERT INTO branch_stoplist (branch_id, product_id) VALUES (?, ?)", (branch_id, product_id))
        return True
    row = conn.execute("SELECT 1 FROM stoplist WHERE product_id=?", (product_id,)).fetchone()
    if row:
        conn.execute("DELETE FROM stoplist WHERE product_id=?", (product_id,))
        return False
    conn.execute("INSERT INTO stoplist (product_id) VALUES (?)", (product_id,))
    return True


def clear_stoplist(branch_id=None) -> None:
    if branch_id:
        db().execute("DELETE FROM branch_stoplist WHERE branch_id=?", (branch_id,))
    else:
        db().execute("DELETE FROM stoplist")


# ---- promokodlar ----
_PROMO_TRIES: dict = {}
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def norm_code(raw) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(raw or "")).upper()[:20]


def promo_check(raw_code, user_id: int, subtotal: int):
    """(promo_qatori, chegirma_summasi, xato_matni). Kod bo'sh bo'lsa — (None, 0, None)."""
    code = norm_code(raw_code)
    if not code:
        return None, 0, None
    conn = db()
    row = conn.execute("SELECT * FROM promo_codes WHERE code=?", (code,)).fetchone()
    if not row or not row["active"]:
        return None, 0, "Promokod topilmadi. Tekshirib, qaytadan kiriting."
    if row["valid_until"] < int(time.time()):
        return None, 0, "Bu promokodning muddati tugagan."
    if row["user_id"] and row["user_id"] != user_id:
        return None, 0, "Bu promokod boshqa mijoz uchun."
    if row["max_uses"] and row["used"] >= row["max_uses"]:
        return None, 0, "Bu promokodning limiti tugagan."
    if conn.execute("SELECT 1 FROM promo_uses WHERE code=? AND user_id=?", (code, user_id)).fetchone():
        return None, 0, "Siz bu promokodni allaqachon ishlatgansiz."
    discount = int(subtotal * row["percent"] / 100) // 100 * 100
    return row, discount, None


def promo_consume(code: str, user_id: int, order_id: int) -> None:
    conn = db()
    conn.execute(
        "INSERT OR IGNORE INTO promo_uses (code, user_id, order_id, created_at) VALUES (?,?,?,?)",
        (code, user_id, order_id, int(time.time())),
    )
    conn.execute("UPDATE promo_codes SET used = used + 1 WHERE code=?", (code,))


def promo_release(order_id: int) -> None:
    """Kafe o'zi buyurtmani bekor qilsa, mijozning promokodi qaytariladi."""
    conn = db()
    row = conn.execute("SELECT code FROM promo_uses WHERE order_id=?", (order_id,)).fetchone()
    if row:
        conn.execute("DELETE FROM promo_uses WHERE order_id=?", (order_id,))
        conn.execute("UPDATE promo_codes SET used = MAX(used - 1, 0) WHERE code=?", (row["code"],))


def order_exists(user_id: int, client_id) -> bool:
    if not client_id:
        return False
    return bool(db().execute("SELECT 1 FROM orders WHERE user_id=? AND client_id=?", (user_id, client_id)).fetchone())


def order_discount(order) -> int:
    try:
        return int(order["discount"] or 0)
    except (IndexError, KeyError, TypeError, ValueError):
        return 0


def new_personal_code(percent: int, valid_days: int, user_id: int, note: str = "winback") -> str:
    import random
    conn = db()
    while True:
        code = "S" + "".join(random.choice(_CODE_ALPHABET) for _ in range(7))
        if not conn.execute("SELECT 1 FROM promo_codes WHERE code=?", (code,)).fetchone():
            break
    now = int(time.time())
    conn.execute(
        "INSERT INTO promo_codes (code, percent, valid_until, user_id, max_uses, note, created_at) VALUES (?,?,?,?,1,?,?)",
        (code, percent, now + valid_days * 86400, user_id, note, now),
    )
    return code


# ---- bloklangan mijozlar (soxta buyurtmalarga qarshi) ----
def phone_key(phone) -> str:
    """Telefon raqamning oxirgi 9 raqami: +998 90 123 45 67, 901234567, 998901234567 — hammasi bir xil."""
    d = re.sub(r"\D", "", str(phone or ""))
    return d[-9:] if len(d) >= 9 else d


def ban(kind: str, value: str, label: str = "") -> None:
    db().execute(
        "INSERT INTO banned (kind, value, label, created_at) VALUES (?,?,?,?) "
        "ON CONFLICT(kind, value) DO UPDATE SET label=excluded.label",
        (kind, str(value), label[:200], int(time.time())),
    )


def unban(kind: str, value: str) -> int:
    return db().execute("DELETE FROM banned WHERE kind=? AND value=?", (kind, str(value))).rowcount


def is_banned(user_id=None, phone=None) -> bool:
    conn = db()
    if user_id is not None and conn.execute("SELECT 1 FROM banned WHERE kind='user' AND value=?", (str(user_id),)).fetchone():
        return True
    key = phone_key(phone) if phone else ""
    if key and conn.execute("SELECT 1 FROM banned WHERE kind='phone' AND value=?", (key,)).fetchone():
        return True
    return False


# ---- admin /yangi bilan qo'shgan taomlar ----
_DISH_IMG_CACHE: dict = {}


def clean_text(value, limit: int) -> str:
    """Ilovada HTML/JS ni buzadigan belgilarni olib tashlaydi."""
    t = re.sub(r"\s+", " ", str(value or "")).strip()
    t = re.sub(r'["<>\\&`]', "", t)
    return t[:limit].strip()


def seed_menu() -> None:
    """Eski (kodda yozilgan) menyuni bir marta bazaga ko'chiradi. Avval /narx bilan o'zgargan narxlar saqlanadi."""
    conn = db()
    if get_setting("menu_seeded") == "1":
        return
    for i, (pid, cat, name, price, img, desc) in enumerate(BASE_MENU, start=1):
        conn.execute(
            "INSERT OR IGNORE INTO custom_products (id, category, name, price, descr, file_id, img_url, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (pid, cat, name, int(price), desc, "", img, i),  # created_at=i: tartib saqlanadi, yangi taomlar oxirida
        )
    for r in conn.execute("SELECT product_id, price FROM price_overrides").fetchall():
        conn.execute("UPDATE custom_products SET price=? WHERE id=?", (int(r["price"]), r["product_id"]))
    conn.execute("DELETE FROM price_overrides")
    set_setting("menu_seeded", "1")


def load_custom_products() -> None:
    PRODUCTS.clear()
    for r in db().execute("SELECT * FROM custom_products ORDER BY created_at, id"):
        PRODUCTS[r["id"]] = {
            "category": r["category"], "name": r["name"], "price": int(r["price"]),
            "custom": True, "desc": r["descr"] or "", "file_id": r["file_id"] or "", "img_url": r["img_url"] or "",
        }


def add_custom_product(category: str, name: str, price: int, desc: str, file_id: str) -> str:
    n = int(time.time())
    while f"n{n}" in PRODUCTS:
        n += 1
    pid = f"n{n}"
    db().execute(
        "INSERT INTO custom_products (id, category, name, price, descr, file_id, created_at) VALUES (?,?,?,?,?,?,?)",
        (pid, category, name, int(price), desc, file_id or "", int(time.time())),
    )
    PRODUCTS[pid] = {"category": category, "name": name, "price": int(price), "custom": True, "desc": desc, "file_id": file_id or "", "img_url": ""}
    return pid


def update_product(pid: str, **fields) -> None:
    """Taom nomi / tavsifi / rasmini o'zgartiradi (name, desc, file_id, img_url)."""
    cols = {"name": "name", "desc": "descr", "file_id": "file_id", "img_url": "img_url"}
    sets = ", ".join(f"{cols[k]}=?" for k in fields)
    db().execute(f"UPDATE custom_products SET {sets} WHERE id=?", (*fields.values(), pid))
    PRODUCTS[pid].update(fields)
    _DISH_IMG_CACHE.pop(pid, None)


def delete_custom_product(pid: str) -> bool:
    """Faqat admin qo'shgan taomni o'chiradi (asosiy menyu taomlari o'chmaydi — ularni stop-listga qo'ying)."""
    if not PRODUCTS.get(pid, {}).get("custom"):
        return False
    conn = db()
    conn.execute("DELETE FROM custom_products WHERE id=?", (pid,))
    conn.execute("DELETE FROM price_overrides WHERE product_id=?", (pid,))
    conn.execute("DELETE FROM stoplist WHERE product_id=?", (pid,))
    conn.execute("DELETE FROM branch_stoplist WHERE product_id=?", (pid,))
    PRODUCTS.pop(pid, None)
    _DISH_IMG_CACHE.pop(pid, None)
    return True


# ---- narxlarni bot orqali o'zgartirish (/narx) ----
def apply_price_overrides() -> None:
    """Bazada saqlangan narxlarni PRODUCTS ga qo'llaydi (bot qayta yonganda ham saqlanib qoladi)."""
    for r in db().execute("SELECT product_id, price FROM price_overrides"):
        if r["product_id"] in PRODUCTS:
            PRODUCTS[r["product_id"]]["price"] = int(r["price"])


def set_price(product_id: str, price: int) -> None:
    db().execute("UPDATE custom_products SET price=? WHERE id=?", (int(price), product_id))
    PRODUCTS[product_id]["price"] = int(price)


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
def create_order(user: dict, client_id, phone, delivery, address, lat, lon, payment, items, total, delivery_fee=0, note=None, branch=None, discount=0, promo=None):
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
        "address, lat, lon, payment, items_json, total, status, created_at, updated_at, day_no, delivery_fee, note, branch, discount, promo) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?, ?, ?, ?, ?, ?)",
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
            branch,
            discount,
            promo,
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
        *([f"📝 Bekor sababi: <i>{esc(order['cancel_reason'])}</i>"] if order["status"] == "cancelled" and order["cancel_reason"] else []),
        "",
        f"👤 Mijoz: {customer}",
        f"📞 Tel: {esc(order['phone'])}",
        *([f"🏪 Filial: <b>{esc(branch_label(order, True))}</b>"] if order["branch"] and branch_label(order) else []),
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
        *([f"🎁 Chegirma ({esc(order['promo'])}): −{fmt_money(order_discount(order))} so'm"] if order_discount(order) else []),
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
            *([f"🎁 Chegirma ({esc(order['promo'])}): −{fmt_money(order_discount(order))} so'm"] if order_discount(order) else []),
            "",
            f"💵 <b>Jami: {fmt_money(order['total'])} so'm</b>",
            f"🚚 {'Dostavka 🛵' if is_delivery else 'Olib ketish 🏃‍♂️'}",
            *([f"🏪 Filial: {esc(branch_label(order))}"] if order["branch"] and branch_label(order) else []),
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
    if status == "new":
        rows.append([_btn("🚫 Bloklash (soxta buyurtma)", f"bl:{oid}")])
    return InlineKeyboardMarkup(rows)


def courier_keyboard(order):
    if order["delivery_type"] != "delivery" or order["status"] in ("done", "cancelled"):
        return None
    oid = order["id"]
    if not order["courier_id"]:
        return InlineKeyboardMarkup([[_btn("🛵 Men olaman", f"cr:{oid}")]])
    return InlineKeyboardMarkup([[_btn("✔️ Yetkazildi", f"cd:{oid}")]])


# Bekor qilishda admin sabab tanlaydi — sabab mijozga yuboriladi
CANCEL_REASONS = {
    "nop": "buyurtmangizdagi mahsulot hozir mavjud emas",
    "nom": "kerakli masalliq hozir tugagan",
}


def cancel_confirm_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [_btn("📦 Mahsulot yo'q", f"cx:{order_id}:nop")],
            [_btn("🥩 Masalliq yo'q", f"cx:{order_id}:nom")],
            [_btn("✍️ Boshqa sabab yozish", f"cx:{order_id}:oth")],
            [_btn("↩️ Orqaga", f"cb:{order_id}")],
        ]
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
        reason = (order["cancel_reason"] or "").strip()
        return (
            f"❌ Afsuski, buyurtmangiz #{oid} bekor qilindi.\n"
            + (f"📝 Sabab: {esc(reason)}\n" if reason else "")
            + f"Savollar bo'lsa, bog'laning: {esc(CONTACT_PHONE)}"
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

    targets = [(admin_id, "admin") for admin_id in order_staff(order)]
    courier_chat = branch_courier_chat(order["branch"])
    if courier_chat is not None and order["delivery_type"] == "delivery":  # olib ketish kuryerga kerak emas
        targets.append((courier_chat, "courier"))

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
        for admin_id in order_staff(order):
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
_print_locks: dict = {}
_print_locks_guard = threading.Lock()


def _lock_for(target: str) -> threading.Lock:
    """Har printer uchun alohida qulf: bitta printer o'chiq bo'lsa, boshqa filial cheki kutib qolmaydi."""
    with _print_locks_guard:
        return _print_locks.setdefault(target, threading.Lock())


def _printer_for(branch_id):
    """(ip, port, windows_nomi) — filialning o'z printeri bo'lsa o'shani, bo'lmasa umumiy printerni qaytaradi."""
    br = find_branch(branch_id) if branch_id else None
    if br and (br.get("printer_ip") or br.get("printer_name")):
        return br.get("printer_ip") or "", int(br.get("printer_port") or PRINTER_PORT), br.get("printer_name") or ""
    return PRINTER_IP, PRINTER_PORT, PRINTER_NAME


def _printer_width(branch_id) -> int:
    br = find_branch(branch_id) if branch_id else None
    return int((br or {}).get("printer_width") or PRINTER_WIDTH)

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
    W = max(_printer_width(order["branch"]), 20)
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
    if order_discount(order):
        for ln in _lr("Chegirma " + str(order["promo"] or ""), "-" + fmt_money(order_discount(order)), W):
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


def build_test_chek(branch_id=None) -> bytes:
    W = max(_printer_width(branch_id), 20)
    c = _Chek()
    c.align(1)
    c.bold(True)
    c.size(0x11)
    c.line(CAFE_NAME)
    c.size(0x00)
    c.bold(False)
    if branch_id:
        c.line(branch_name(branch_id))
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


def send_to_printer(data: bytes, branch_id=None) -> str:
    """Chekni (filialning) printeriga yuboradi (bloklaydi). Qaysi printerga ketganini qaytaradi."""
    ip, port, name = _printer_for(branch_id)
    with _lock_for(f"{ip}:{port}" if ip else f"win:{name}"):
        if ip:
            with socket.create_connection((ip, port), timeout=5) as sock:
                sock.settimeout(10)
                sock.sendall(data)
            return f"{ip}:{port}"
        if os.name == "nt":
            return _print_windows(data, name)
        raise OSError("Bu kompyuter Windows emas. LAN printer uchun printer_ip (yoki PRINTER_IP) ni yozing.")


async def print_order_chek(bot, order_id: int) -> None:
    if not PRINTER_ENABLED:
        return
    order = get_order(order_id)
    if not order:
        return
    try:
        data = build_chek(order)
        await asyncio.get_running_loop().run_in_executor(None, send_to_printer, data, order["branch"])
    except Exception as exc:
        log.warning("Chek chiqmadi (#%s): %s", order_no(order), exc)
        warn = (
            f"⚠️ Chek chiqmadi (buyurtma #{order_no(order)}): {esc(exc)}\n"
            f"Printerni tekshirib, /chek {order_no(order)} bilan qayta chiqaring."
        )
        for admin_id in order_staff(order):
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
    if BRANCHES:
        infos = []
        for br in BRANCHES:
            o = branch_is_open(br["id"])
            infos.append({
                "id": br["id"], "name": br["name"], "open": o,
                "message": "" if o else branch_closed_message(br["id"]),
                "stoplist": sorted(get_stoplist(br["id"])),
            })
        opened = any(i["open"] for i in infos)
        message = "" if opened else any_closed_message()
        # Umumiy ko'rinish: hech bo'lmasa bitta filial ochiq bo'lsa — ochiq; taom hamma filialda tugagan bo'lsagina "tugagan"
        stoplist = sorted(set.intersection(*[set(i["stoplist"]) for i in infos]))
    else:
        infos = []
        opened, message, stoplist = is_open(), ("" if is_open() else closed_message()), sorted(get_stoplist())
    return web.json_response(
        {
            "hits": hit_products(),
            "new": [p for p in NEW_PRODUCTS if p in PRODUCTS],
            "ok": True,
            "open": opened,
            "message": message,
            "stoplist": stoplist,
            "delivery_fee": DELIVERY_FEE,
            "prices": {pid: p["price"] for pid, p in PRODUCTS.items()},
            "menu": [
                {
                    "id": pid, "category": p["category"], "price": p["price"],
                    "name": p["name"].replace("'", "’"), "desc": (p.get("desc") or "").replace("'", "’"),
                    "img": (
                        f"/api/dish_img/{pid}?v={hashlib.md5(p['file_id'].encode()).hexdigest()[:8]}" if p.get("file_id")
                        else (p.get("img_url") or "")
                    ),
                }
                for pid, p in PRODUCTS.items()
            ],
            "branches": infos,
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

    if is_banned(user_id=user["id"]):
        log.info("Bloklangan mijoz buyurtma berishga urindi: %s", user["id"])
        return jerr("Kechirasiz, buyurtmangizni hozir qabul qila olmaymiz.", 403, code="banned")

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
    if is_banned(phone=phone):
        log.info("Bloklangan telefon bilan buyurtma urinishi: %s", phone_key(phone))
        return jerr("Kechirasiz, buyurtmangizni hozir qabul qila olmaymiz.", 403, code="banned")

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

    branch = None
    if BRANCHES:
        if delivery == "delivery":
            if lat is None:
                return jerr("Eng yaqin filialni aniqlash uchun joylashuvingizni yuboring.", code="no_location")
            near, km = nearest_branch(lat, lon)
            if MAX_DELIVERY_KM and km > MAX_DELIVERY_KM:
                return jerr(
                    f"Kechirasiz, manzilingiz eng yaqin filialimizdan ({near['name']}) {km:.1f} km uzoqda — "
                    "dostavka hududidan tashqarida. Olib ketishni tanlashingiz mumkin.",
                    422,
                    code="too_far",
                )
            branch = near["id"]
        else:
            branch = str(body.get("branch", "")).strip()
            if not find_branch(branch):
                return jerr("Olib ketish uchun filialni tanlang.", code="branch")
    elif delivery == "delivery" and MAX_DELIVERY_KM and CAFE_LAT is not None and CAFE_LON is not None:
        # Filiallar yo'q: masofa kafening o'z koordinatasiga (CAFE_LAT/CAFE_LON) nisbatan tekshiriladi
        if lat is None:
            return jerr("Dostavka masofasini aniqlash uchun joylashuvingizni yuboring.", code="no_location")
        km = _km(lat, lon, CAFE_LAT, CAFE_LON)
        if km > MAX_DELIVERY_KM:
            return jerr(
                f"Kechirasiz, manzilingiz kafemizdan {km:.1f} km uzoqda — dostavka hududidan tashqarida. "
                "Olib ketishni tanlashingiz mumkin.",
                422,
                code="too_far",
            )

    # --- shu filial ochiqmi va savatdagi taomlar shu filialda bormi ---
    if branch and not branch_is_open(branch):
        return jerr(f"{branch_name(branch)}: {branch_closed_message(branch)}", 403, code="closed")
    stopped = get_stoplist(branch)
    unavailable = [PRODUCTS[p]["name"] for p in merged if p in stopped]
    if unavailable:
        names = ", ".join(sorted(set(unavailable)))
        where = f" ({branch_name(branch)})" if branch else ""
        return jerr(
            f"Afsuski, quyidagi taomlar hozircha tugagan{where}: {names}. Iltimos, savatdan olib tashlang.",
            409,
            code="stop",
        )

    delivery_fee = DELIVERY_FEE if delivery == "delivery" else 0
    items_total = total
    total += delivery_fee

    payment = body.get("payment")
    if not isinstance(payment, str) or payment not in PAYMENT_METHODS:
        return jerr("To'lov usuli noto'g'ri.")

    client_id = str(body.get("client_id", "")).strip()[:64] or None
    note = str(body.get("note", "")).strip()[:200] or None

    # --- suiiste'molga qarshi cheklov ---
    if recent_order_count(user["id"], 600) >= 10:
        return jerr("Juda ko'p buyurtma yuborildi. Birozdan keyin urinib ko'ring.", 429)

    # --- promokod (tekshiruv va yozib qo'yish orasida await yo'q — bir kodni ikki marta ishlatib bo'lmaydi) ---
    promo_row, discount, promo_code = None, 0, None
    raw_promo = str(body.get("promo", "")).strip()
    if raw_promo and not order_exists(user["id"], client_id):
        promo_row, discount, perr = promo_check(raw_promo, user["id"], items_total)
        if perr:
            return jerr(perr, 400, code="promo")
        if promo_row:
            promo_code = promo_row["code"]
            total -= discount

    upsert_user(user["id"], user["username"], user["full_name"])
    order_id, order_total, created = create_order(
        user, client_id, phone, delivery, address or None, lat, lon, payment, items, total, delivery_fee, note, branch,
        discount, promo_code,
    )
    if created and promo_row:
        promo_consume(promo_code, user["id"], order_id)
    if created:
        # Buyurtma bazaga saqlandi. Telegramga yuborish orqada ketadi —
        # Telegram band bo'lsa ham mijoz xatolik ko'rmaydi.
        spawn(notify_new_order(request.app["bot"], order_id))
        spawn(print_order_chek(request.app["bot"], order_id))
    return web.json_response(
        {"ok": True, "order_id": order_id, "order_no": order_no(get_order(order_id)), "total": order_total, "branch": branch_name(branch)}
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


async def api_promo_check(request):
    """Mijoz promokodni kiritganda chegirma foizini ko'rsatadi (buyurtma berilmaydi, kod ishlatilmaydi)."""
    body = await read_json(request)
    if body is None:
        return jerr("So'rov noto'g'ri formatda.")
    user = validate_init_data(str(body.get("init_data", "")), BOT_TOKEN)
    if not user:
        return jerr("Telegram orqali tasdiqlanmadi.", 401)
    now = time.time()
    tries = [t for t in _PROMO_TRIES.get(user["id"], []) if now - t < 600]
    if len(tries) >= 12:
        return jerr("Juda ko'p urinish. Birozdan keyin qaytadan kiriting.", 429)
    tries.append(now)
    _PROMO_TRIES[user["id"]] = tries
    row, _disc, err = promo_check(body.get("code", ""), user["id"], 0)
    if err or not row:
        return jerr(err or "Promokodni kiriting.", 400, code="promo")
    return web.json_response({"ok": True, "code": row["code"], "percent": row["percent"]})


async def api_dish_img(request):
    """Admin /yangi orqali yuborgan taom rasmini Telegramdan olib beradi."""
    pid = request.match_info.get("pid", "")
    p = PRODUCTS.get(pid)
    if not p or not p.get("file_id"):
        return web.Response(status=404)
    data = _DISH_IMG_CACHE.get(pid)
    if data is None:
        try:
            tg_file = await request.app["bot"].get_file(p["file_id"])
            data = bytes(await tg_file.download_as_bytearray())
        except Exception as exc:
            log.warning("Taom rasmini olib bo'lmadi (%s): %s", pid, exc)
            return web.Response(status=502)
        if len(_DISH_IMG_CACHE) > 100:
            _DISH_IMG_CACHE.clear()
        _DISH_IMG_CACHE[pid] = data
    return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


def build_web_app(bot) -> web.Application:
    app = web.Application(middlewares=[cors_middleware], client_max_size=64 * 1024)
    app["bot"] = bot
    app.router.add_get("/", serve_index)
    app.router.add_get("/health", api_health)
    app.router.add_get("/api/status", api_status)
    app.router.add_post("/api/order", api_order)
    app.router.add_post("/api/my_orders", api_my_orders)
    app.router.add_post("/api/avatar", api_avatar)
    app.router.add_post("/api/promo_check", api_promo_check)
    app.router.add_get("/api/dish_img/{pid}", api_dish_img)
    app.router.add_post("/api/track", api_track)
    for path in ("/api/status", "/api/order", "/api/my_orders", "/api/avatar", "/api/track", "/api/promo_check"):
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


async def refresh_menu_button(bot, chat_id: int) -> None:
    """Mijozning 'Open App' tugmasini hozirgi manzilga yangilaydi (eski manzil eslab qolinmasin)."""
    if not WEB_APP_URL:
        return
    try:
        await bot.set_chat_menu_button(
            chat_id=chat_id,
            menu_button=MenuButtonWebApp(text="Open App", web_app=WebAppInfo(url=WEB_APP_URL)),
        )
    except Exception:
        pass


async def on_any_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mijoz botga istalgan xabar yozsa, ilovani ochish tugmasini yuboramiz."""
    if not update.message or not update.effective_user:
        return
    if update.effective_user.id in SUPER_ADMIN_IDS and context.application.bot_data.get("dish_wait", {}).get(update.effective_user.id):
        await handle_dish_text(update, context)
        return
    if update.effective_user.id in SUPER_ADMIN_IDS and context.application.bot_data.get("edit_wait", {}).get(update.effective_user.id):
        await handle_edit_text(update, context)
        return
    if update.effective_user.id in SUPER_ADMIN_IDS and context.application.bot_data.get("price_wait", {}).get(update.effective_user.id):
        await handle_price_text(update, context)
        return
    if update.effective_user.id in STAFF_IDS and context.application.bot_data.get("cancel_wait", {}).get(update.effective_user.id):
        await handle_cancel_reason_text(update, context)
        return
    upsert_user(update.effective_user.id, update.effective_user.username, update.effective_user.full_name)
    await refresh_menu_button(context.bot, update.effective_chat.id)
    await update.message.reply_text(
        "Buyurtma berish uchun tugmani bosing 👇\nНажмите кнопку, чтобы сделать заказ 👇",
        reply_markup=open_app_markup(),
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat:
        await refresh_menu_button(context.bot, update.effective_chat.id)
    user = update.effective_user
    if not user or not update.message:
        return
    upsert_user(user.id, user.username, user.full_name)
    nickname = f"@{user.username}" if user.username else user.full_name
    text = (
        f"Xush kelibsiz, <b>{esc(nickname)}</b>! 👋\n\n"
        "Buyurtma berish uchun pastdagi <b>«Menyu»</b> tugmasini (yoki chap pastdagi <b>'Open App'</b> ni) bosing."
    )
    if not any_open():
        text += f"\n\n⛔ {esc(any_closed_message())}"
    if help_for(user.id):
        text += "\n\n" + help_for(user.id)
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
    if update.effective_user and help_for(update.effective_user.id):
        text += "\n\n" + help_for(update.effective_user.id)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_aloqa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    lines = ["<b>📞 Aloqa</b>", f"Telefon: {esc(CONTACT_PHONE)}", f"Manzil: {esc(CAFE_ADDRESS)}"]
    if WORK_HOURS:
        lines.append(f"Ish vaqti: {esc(WORK_HOURS)}")
    lines.append("Holat: " + ("🟢 Ochiqmiz" if any_open() else "🔴 Hozir yopiqmiz"))
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
        if not user or user.id not in STAFF_IDS:
            return
        return await handler(update, context)

    return wrapper


def super_only(handler):
    """Faqat super admin (egasi) uchun buyruqlar."""
    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or user.id not in SUPER_ADMIN_IDS:
            return
        return await handler(update, context)

    return wrapper


def branches_of(user_id: int) -> list:
    """Shu admin ochish/yopish va stop-listni boshqara oladigan filiallar (filial bo'lmasa — bo'sh)."""
    if not BRANCHES:
        return []
    if user_id in SUPER_ADMIN_IDS:
        return list(BRANCHES)
    return [b for b in BRANCHES if user_id in (b.get("admin_ids") or []) or not b.get("admin_ids")]


def _set_branch_state(branch_ids, open_: bool, note: str = "") -> str:
    names = []
    for bid in branch_ids:
        set_setting(f"is_open:{bid}", "1" if open_ else "0")
        set_setting(f"closed_message:{bid}", "" if open_ else note)
        names.append(branch_name(bid))
    if open_:  # umumiy (bosh) yopilish qolib ketgan bo'lsa, uni ham yechamiz
        set_setting("is_open", "1")
        set_setting("closed_message", "")
    return ", ".join(names)


def _openclose_text(name: str, open_: bool, note: str = "") -> str:
    if open_:
        return f"🟢 {name}: buyurtma qabul qilish OCHILDI."
    shown = note or default_closed_text()
    return f"🔴 {name}: buyurtma qabul qilish YOPILDI.\nMijozlar ko'radigan xabar: {shown}\n\nQayta ochish: /ochish"


async def _openclose_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, open_: bool):
    uid = update.effective_user.id
    note = "" if open_ else (" ".join(context.args).strip()[:200] if context.args else "")
    if not BRANCHES:  # filiallar yo'q — avvalgidek butun kafe
        set_setting("is_open", "1" if open_ else "0")
        set_setting("closed_message", "" if open_ else note)
        await update.message.reply_text(
            "🟢 Buyurtma qabul qilish OCHILDI." if open_ else _openclose_text("Kafe", False, note).replace("Kafe: ", "")
        )
        return
    brs = branches_of(uid)
    if not brs:
        await update.message.reply_text("Sizga filial biriktirilmagan.")
        return
    if len(brs) == 1:
        name = _set_branch_state([brs[0]["id"]], open_, note)
        await update.message.reply_text(_openclose_text(name, open_, note))
        return
    context.application.bot_data.setdefault("close_note", {})[uid] = note
    rows = [[_btn(f"🏪 {b['name']} " + ("🟢" if branch_is_open(b["id"]) else "🔴"), f"oc:{'o' if open_ else 'y'}:{b['id']}")] for b in brs]
    rows.append([_btn("🏪 Hammasi", f"oc:{'o' if open_ else 'y'}:all")])
    await update.message.reply_text(
        ("Qaysi filialni OCHASIZ?" if open_ else "Qaysi filialni YOPASIZ?") + "\n(🟢 — hozir ochiq, 🔴 — hozir yopiq)",
        reply_markup=InlineKeyboardMarkup(rows),
    )


@admin_only
async def cmd_ochish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _openclose_cmd(update, context, True)


@admin_only
async def cmd_yopish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _openclose_cmd(update, context, False)


async def on_openclose_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid not in STAFF_IDS:
        await query.answer("Ruxsat yo'q", show_alert=True)
        return
    parts = query.data.split(":")  # oc:o|y:<filial id | all>
    if len(parts) != 3 or parts[1] not in ("o", "y"):
        await query.answer()
        return
    allowed = [b["id"] for b in branches_of(uid)]
    ids = allowed if parts[2] == "all" else ([parts[2]] if parts[2] in allowed else [])
    if not ids:
        await query.answer("Bu filial sizniki emas.", show_alert=True)
        return
    open_ = parts[1] == "o"
    note = "" if open_ else context.application.bot_data.get("close_note", {}).get(uid, "")
    name = _set_branch_state(ids, open_, note)
    await query.answer("Bajarildi ✅")
    await _edit_callback_message(query, esc(_openclose_text(name, open_, note)), None)


def _stop_pick_view(brs):
    rows = [[_btn(f"🏪 {b['name']} ({len(get_stoplist(b['id']))} ta tugagan)", f"sl:b:{b['id']}")] for b in brs]
    return "🍽 <b>Stop-list</b>\nQaysi filial uchun?", InlineKeyboardMarkup(rows)


def _stop_main_view(branch=None):
    br = branch or ""
    stopped = get_stoplist(branch)
    rows = []
    for cat, label in CATEGORIES.items():
        off = sum(1 for pid in stopped if pid in PRODUCTS and PRODUCTS[pid]["category"] == cat)
        title = f"{label} ({off} ta tugagan)" if off else label
        rows.append([_btn(title, f"sl:c:{cat}:{br}")])
    if stopped:
        rows.append([_btn("🔄 Hammasini yoqish", f"sl:all:{br}")])
    head = "🍽 <b>Stop-list</b>" + (f" — 🏪 {esc(branch_name(branch))}" if branch else "")
    text = head + "\nBo'limni tanlang. Tugagan taomni o'chirsangiz, ilovada «Tugagan» deb ko'rinadi."
    return text, InlineKeyboardMarkup(rows)


def _stop_category_view(cat: str, branch=None):
    br = branch or ""
    stopped = get_stoplist(branch)
    rows = []
    for pid, product in PRODUCTS.items():
        if product["category"] != cat:
            continue
        mark = "⛔" if pid in stopped else "✅"
        rows.append([_btn(f"{mark} {product['name']} · {fmt_money(product['price'])}", f"sl:t:{cat}:{pid}:{br}")])
    rows.append([_btn("⬅️ Orqaga", f"sl:m:{br}")])
    where = f" — 🏪 {esc(branch_name(branch))}" if branch else ""
    text = f"{esc(CATEGORIES.get(cat, cat))}{where}\n✅ — bor,  ⛔ — tugagan.\nBosib holatini almashtiring."
    return text, InlineKeyboardMarkup(rows)


@admin_only
async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    brs = branches_of(update.effective_user.id)
    if BRANCHES and not brs:
        await update.message.reply_text("Sizga filial biriktirilmagan.")
        return
    if len(brs) > 1:
        text, markup = _stop_pick_view(brs)
    else:
        text, markup = _stop_main_view(brs[0]["id"] if brs else None)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


def _price_cat_view():
    rows = [[_btn(label, f"pr:c:{cat}")] for cat, label in CATEGORIES.items()] + [[_btn("⬅️ Menyu", "mn:m")]]
    return "💰 <b>Taomlarni tahrirlash</b>\nBo'limni tanlang, keyin taomni bosib narxi, nomi, tavsifi yoki rasmini o'zgartirasiz.", InlineKeyboardMarkup(rows)


def _price_list_view(cat: str):
    rows = []
    for pid, p in PRODUCTS.items():
        if p["category"] == cat:
            rows.append([_btn(f"{p['name']} · {fmt_money(p['price'])} so'm", f"pr:t:{pid}")])
    rows.append([_btn("⬅️ Orqaga", "pr:m")])
    return f"💰 {esc(CATEGORIES.get(cat, cat))}\nTahrirlamoqchi bo'lgan taomni bosing.", InlineKeyboardMarkup(rows)


def _menu_panel_view():
    rows = [
        [_btn("➕ Yangi taom qo'shish", "mn:new")],
        [_btn("💰 Narx / taomni tahrirlash", "mn:edit")],
        [_btn("⛔ Stop-list (tugagan taomlar)", "mn:stop")],
    ]
    return (
        f"🍽 <b>Menyu boshqaruvi</b>\nTaomlar soni: {len(PRODUCTS)} ta\n"
        "Bu yerdagi o'zgarishlar ilovada 30 soniya ichida ko'rinadi.",
        InlineKeyboardMarkup(rows),
    )


def _clear_waits(context, uid) -> None:
    for key in ("price_wait", "dish_wait", "edit_wait"):
        context.application.bot_data.setdefault(key, {}).pop(uid, None)


@super_only
async def cmd_menyu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _clear_waits(context, update.effective_user.id)
    text, markup = _menu_panel_view()
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def on_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid not in SUPER_ADMIN_IDS:
        await query.answer("Ruxsat yo'q", show_alert=True)
        return
    action = query.data.split(":")[1] if ":" in query.data else ""
    await query.answer()
    _clear_waits(context, uid)
    if action == "m":
        text, markup = _menu_panel_view()
    elif action == "new":
        context.application.bot_data.setdefault("dish_wait", {})[uid] = {"step": "cat"}
        rows = [[_btn(label, f"dn:c:{cat}")] for cat, label in CATEGORIES.items()] + [[_btn("⬅️ Orqaga", "mn:m")]]
        text, markup = "🆕 <b>Yangi taom qo'shish</b>\nQaysi bo'limga qo'shamiz?", InlineKeyboardMarkup(rows)
    elif action == "edit":
        text, markup = _price_cat_view()
    elif action == "stop":
        brs = branches_of(uid)
        if BRANCHES and not brs:
            text, markup = "Sizga filial biriktirilmagan.", None
        elif len(brs) > 1:
            text, markup = _stop_pick_view(brs)
        else:
            text, markup = _stop_main_view(brs[0]["id"] if brs else None)
    else:
        return
    await _edit_callback_message(query, text, markup)


def _product_card(pid: str):
    p = PRODUCTS[pid]
    text = (
        f"✏️ <b>{esc(p['name'])}</b>\n"
        f"Bo'lim: {esc(CATEGORIES.get(p['category'], p['category']))}\n"
        f"Narxi: <b>{fmt_money(p['price'])} so'm</b>\n"
        f"Tavsif: {esc(p.get('desc') or '—')}\n"
        f"Rasm: {'bor' if (p.get('file_id') or p.get('img_url')) else 'yoq'}"
    )
    rows = [
        [_btn("💰 Narxi", f"pr:p:{pid}"), _btn("✏️ Nomi", f"pr:e:name:{pid}")],
        [_btn("📝 Tavsifi", f"pr:e:desc:{pid}"), _btn("🖼 Rasmi", f"pr:e:img:{pid}")],
        [_btn("🗑 Menyudan o'chirish", f"pr:x:{pid}")],
        [_btn("⬅️ Orqaga", f"pr:c:{p['category']}")],
    ]
    return text, InlineKeyboardMarkup(rows)


@super_only
async def cmd_narx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _clear_waits(context, update.effective_user.id)
    text, markup = _price_cat_view()
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def on_price_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid not in SUPER_ADMIN_IDS:
        await query.answer("Ruxsat yo'q", show_alert=True)
        return
    wait = context.application.bot_data.setdefault("price_wait", {})
    parts = query.data.split(":")  # pr:m | pr:c:<cat> | pr:t:<pid>
    action = parts[1] if len(parts) > 1 else ""
    if action == "m":
        wait.pop(uid, None)
        text, markup = _price_cat_view()
    elif action == "c" and len(parts) == 3 and parts[2] in CATEGORIES:
        wait.pop(uid, None)
        text, markup = _price_list_view(parts[2])
    elif action == "t" and len(parts) == 3 and parts[2] in PRODUCTS:
        wait.pop(uid, None)
        context.application.bot_data.setdefault("edit_wait", {}).pop(uid, None)
        text, markup = _product_card(parts[2])
    elif action == "p" and len(parts) == 3 and parts[2] in PRODUCTS:
        p = PRODUCTS[parts[2]]
        wait[uid] = parts[2]
        context.application.bot_data.setdefault("edit_wait", {}).pop(uid, None)
        await query.answer()
        await _edit_callback_message(
            query,
            f"💰 <b>{esc(p['name'])}</b>\nHozirgi narx: <b>{fmt_money(p['price'])} so'm</b>\n\n"
            "✍️ Yangi narxni raqam bilan yozing (so'mda), masalan: <code>80000</code>",
            InlineKeyboardMarkup([[_btn("⬅️ Bekor qilish", f"pr:t:{parts[2]}")]]),
        )
        return
    elif action == "e" and len(parts) == 4 and parts[2] in ("name", "desc", "img") and parts[3] in PRODUCTS:
        p = PRODUCTS[parts[3]]
        wait.pop(uid, None)
        context.application.bot_data.setdefault("edit_wait", {})[uid] = {"pid": parts[3], "field": parts[2]}
        ask = {
            "name": "✍️ Yangi nomini yozing:",
            "desc": "📝 Yangi tavsifni yozing (tavsifni o'chirish uchun «-» yozing):",
            "img": "🖼 Yangi rasmni yuboring (rasm sifatida, fayl emas):",
        }[parts[2]]
        await query.answer()
        await _edit_callback_message(
            query, f"<b>{esc(p['name'])}</b>\n\n{ask}",
            InlineKeyboardMarkup([[_btn("⬅️ Bekor qilish", f"pr:t:{parts[3]}")]]),
        )
        return
    elif action == "x" and len(parts) == 3 and PRODUCTS.get(parts[2], {}).get("custom"):
        wait.pop(uid, None)
        p = PRODUCTS[parts[2]]
        await query.answer()
        await _edit_callback_message(
            query,
            f"🗑 <b>{esc(p['name'])}</b> menyudan butunlay o'chirilsinmi?",
            InlineKeyboardMarkup([[_btn("✅ Ha, o'chirish", f"pr:xx:{parts[2]}")], [_btn("⬅️ Yo'q", f"pr:t:{parts[2]}")]]),
        )
        return
    elif action == "xx" and len(parts) == 3 and PRODUCTS.get(parts[2], {}).get("custom"):
        cat = PRODUCTS[parts[2]]["category"]
        delete_custom_product(parts[2])
        await query.answer("O'chirildi ✅")
        text, markup = _price_list_view(cat)
        await _edit_callback_message(query, text, markup)
        return
    else:
        await query.answer()
        return
    await query.answer()
    await _edit_callback_message(query, text, markup)


async def handle_edit_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    waits = context.application.bot_data.setdefault("edit_wait", {})
    w = waits.get(uid)
    if not w or w["pid"] not in PRODUCTS:
        waits.pop(uid, None)
        return
    text = update.message.text or ""
    if w["field"] == "name":
        name = clean_text(text, 60)
        if len(name) < 2:
            await update.message.reply_text("Taom nomini yozing (kamida 2 ta harf).")
            return
        update_product(w["pid"], name=name)
    elif w["field"] == "desc":
        update_product(w["pid"], desc="" if text.strip() in ("-", "—") else clean_text(text, 200))
    else:
        await update.message.reply_text("Rasm yuboring (rasm sifatida) yoki «Bekor qilish» tugmasini bosing.")
        return
    waits.pop(uid, None)
    await update.message.reply_text(
        "✅ Saqlandi. Ilovada 30 soniya ichida yangilanadi.",
        reply_markup=InlineKeyboardMarkup([[_btn("↩️ Taomga qaytish", f"pr:t:{w['pid']}")]]),
    )


async def handle_price_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    wait = context.application.bot_data.setdefault("price_wait", {})
    pid = wait.get(uid)
    if not pid or pid not in PRODUCTS:
        wait.pop(uid, None)
        return
    digits = re.sub(r"[\s,._]", "", (update.message.text or ""))
    if not digits.isdigit() or not (1000 <= int(digits) <= 10_000_000):
        await update.message.reply_text("Narxni faqat raqam bilan yozing (1000 dan 10 000 000 gacha). Masalan: 80000")
        return
    old = PRODUCTS[pid]["price"]
    set_price(pid, int(digits))
    wait.pop(uid, None)
    await update.message.reply_text(
        f"✅ {PRODUCTS[pid]['name']}: {fmt_money(old)} → {fmt_money(int(digits))} so'm.\n"
        "Ilovada 30 soniya ichida yangilanadi. Eski buyurtmalar narxi o'zgarmaydi.\n\n"
        "Yana o'zgartirish: /narx"
    )


def _dish_summary(d: dict) -> str:
    return (
        "🆕 <b>Yangi taom</b>\n"
        f"Bo'lim: {esc(CATEGORIES.get(d['cat'], d['cat']))}\n"
        f"Nomi: <b>{esc(d['name'])}</b>\n"
        f"Narxi: {fmt_money(d['price'])} so'm\n"
        f"Tavsif: {esc(d['desc'] or '—')}\n"
        f"Rasm: {'bor' if d.get('file_id') else 'yoq'}"
    )


def _dish_confirm_markup():
    return InlineKeyboardMarkup([[_btn("✅ Menyuga qo'shish", "dn:ok")], [_btn("❌ Bekor qilish", "dn:x")]])


def _dish_cancel_markup():
    return InlineKeyboardMarkup([[_btn("❌ Bekor qilish", "dn:x")]])


@super_only
async def cmd_yangi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    _clear_waits(context, uid)
    context.application.bot_data.setdefault("dish_wait", {})[uid] = {"step": "cat"}
    rows = [[_btn(label, f"dn:c:{cat}")] for cat, label in CATEGORIES.items()] + [[_btn("❌ Bekor qilish", "dn:x")]]
    await update.message.reply_text(
        "🆕 <b>Yangi taom qo'shish</b>\nQaysi bo'limga qo'shamiz?",
        parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows),
    )


async def handle_dish_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    d = context.application.bot_data.setdefault("dish_wait", {}).get(uid)
    if not d:
        return
    text = update.message.text or ""
    step = d["step"]
    if step == "name":
        name = clean_text(text, 60)
        if len(name) < 2:
            await update.message.reply_text("Taom nomini yozing (kamida 2 ta harf).")
            return
        d["name"], d["step"] = name, "price"
        await update.message.reply_text("💰 Narxini yozing (so'mda), masalan: 45000", reply_markup=_dish_cancel_markup())
    elif step == "price":
        digits = re.sub(r"[\s,._]", "", text)
        if not digits.isdigit() or not (1000 <= int(digits) <= 10_000_000):
            await update.message.reply_text("Narxni faqat raqam bilan yozing (1000 dan 10 000 000 gacha). Masalan: 45000")
            return
        d["price"], d["step"] = int(digits), "desc"
        await update.message.reply_text(
            "📝 Qisqa tavsif yozing (tarkibi). Kerak bo'lmasa «-» deb yozing.", reply_markup=_dish_cancel_markup()
        )
    elif step == "desc":
        d["desc"] = "" if text.strip() in ("-", "—") else clean_text(text, 200)
        d["step"] = "photo"
        await update.message.reply_text(
            "🖼 Taom rasmini yuboring (rasm sifatida, fayl emas).",
            reply_markup=InlineKeyboardMarkup([[_btn("Rasmsiz davom etish", "dn:skip")], [_btn("❌ Bekor qilish", "dn:x")]]),
        )
    elif step == "photo":
        await update.message.reply_text("Rasm yuboring yoki «Rasmsiz davom etish» tugmasini bosing.")
    else:
        await update.message.reply_text("Pastdagi tugmalardan birini bosing.")


async def on_dish_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user or update.effective_user.id not in SUPER_ADMIN_IDS:
        return
    ew = context.application.bot_data.get("edit_wait", {}).get(update.effective_user.id)
    if ew and ew["field"] == "img" and ew["pid"] in PRODUCTS:  # mavjud taom rasmini almashtirish
        update_product(ew["pid"], file_id=update.message.photo[-1].file_id, img_url="")
        context.application.bot_data["edit_wait"].pop(update.effective_user.id, None)
        await update.message.reply_text(
            "✅ Rasm almashtirildi. Ilovada 30 soniya ichida yangilanadi.",
            reply_markup=InlineKeyboardMarkup([[_btn("↩️ Taomga qaytish", f"pr:t:{ew['pid']}")]]),
        )
        return
    d = context.application.bot_data.get("dish_wait", {}).get(update.effective_user.id)
    if not d or d.get("step") != "photo":
        return
    d["file_id"], d["step"] = update.message.photo[-1].file_id, "confirm"
    await update.message.reply_photo(
        d["file_id"], caption=_dish_summary(d), parse_mode=ParseMode.HTML, reply_markup=_dish_confirm_markup()
    )


async def on_dish_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid not in SUPER_ADMIN_IDS:
        await query.answer("Ruxsat yo'q", show_alert=True)
        return
    waits = context.application.bot_data.setdefault("dish_wait", {})
    d = waits.get(uid)
    parts = query.data.split(":")  # dn:c:<cat> | dn:skip | dn:ok | dn:x
    action = parts[1] if len(parts) > 1 else ""
    if action == "x":
        waits.pop(uid, None)
        await query.answer()
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except TelegramError:
            pass
        await context.bot.send_message(query.message.chat_id, "Bekor qilindi.")
    elif action == "c" and len(parts) == 3 and parts[2] in CATEGORIES and d and d["step"] == "cat":
        d["cat"], d["step"] = parts[2], "name"
        await query.answer()
        await _edit_callback_message(
            query, f"{esc(CATEGORIES[parts[2]])}\n\n✍️ Taom nomini yozing:", _dish_cancel_markup()
        )
    elif action == "skip" and d and d["step"] == "photo":
        d["file_id"], d["step"] = "", "confirm"
        await query.answer()
        await _edit_callback_message(query, _dish_summary(d), _dish_confirm_markup())
    elif action == "ok" and d and d["step"] == "confirm":
        pid = add_custom_product(d["cat"], d["name"], d["price"], d["desc"], d.get("file_id", ""))
        waits.pop(uid, None)
        await query.answer("Qo'shildi ✅")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except TelegramError:
            pass
        await context.bot.send_message(
            query.message.chat_id,
            f"✅ «{PRODUCTS[pid]['name']}» menyuga qo'shildi ({fmt_money(PRODUCTS[pid]['price'])} so'm).\n"
            "Ilovada 30 soniya ichida paydo bo'ladi. Narxini o'zgartirish yoki o'chirish: /narx",
        )
    else:
        await query.answer("Bu tugma eskirgan. /yangi dan qaytadan boshlang.", show_alert=True)


async def _edit_callback_message(query, text: str, markup=None):
    try:
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            log.error("Callback xabarini tahrirlashda xato: %s", exc)


async def on_stop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid not in STAFF_IDS:
        await query.answer("Ruxsat yo'q", show_alert=True)
        return
    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    # shakllar: sl:b:<br>  sl:m:<br>  sl:all:<br>  sl:c:<cat>:<br>  sl:t:<cat>:<pid>:<br>   (filial yo'q bo'lsa <br> bo'sh)
    shapes = {"b": 3, "m": 3, "all": 3, "c": 4, "t": 5}
    if action not in shapes or len(parts) != shapes[action]:
        await query.answer()
        return
    branch = parts[-1] or None
    allowed = [b["id"] for b in branches_of(uid)]
    if (BRANCHES and branch not in allowed) or (not BRANCHES and branch):
        await query.answer("Bu filial sizniki emas.", show_alert=True)
        return
    if action in ("b", "m"):
        text, markup = _stop_main_view(branch)
    elif action == "all":
        clear_stoplist(branch)
        text, markup = _stop_main_view(branch)
    elif action == "c" and parts[2] in CATEGORIES:
        text, markup = _stop_category_view(parts[2], branch)
    elif (
        action == "t"
        and parts[2] in CATEGORIES
        and parts[3] in PRODUCTS
        and PRODUCTS[parts[3]]["category"] == parts[2]
    ):
        now_stopped = toggle_stop(parts[3], branch)
        await query.answer("⛔ Tugagan deb belgilandi" if now_stopped else "✅ Yana mavjud")
        text, markup = _stop_category_view(parts[2], branch)
        await _edit_callback_message(query, text, markup)
        return
    else:
        await query.answer()
        return
    await query.answer()
    await _edit_callback_message(query, text, markup)


@super_only
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
    if BRANCHES:
        for br in BRANCHES:
            lines.append(
                f"🏪 {esc(br['name'])}: " + ("🟢 ochiq" if branch_is_open(br["id"]) else "🔴 yopiq")
                + f" · stop-listda {len(get_stoplist(br['id']))} ta taom"
            )
    else:
        lines.append(f"⛔ Stop-listda: {len(get_stoplist())} ta taom")
        lines.append("Holat: " + ("🟢 Ochiq" if is_open() else "🔴 Yopiq"))
    return "\n".join(lines)


# ---- Smena hisoboti va yangi buyruqlar ----
def report_only(handler):
    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or (user.id not in SUPER_ADMIN_IDS and user.id not in REPORT_IDS):
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


STUCK_ALERTED: dict = {}  # buyurtma id -> (oxirgi eslatma vaqti, nechta eslatma yuborilgan)


async def check_stuck_orders(bot) -> None:
    now = int(time.time())
    limit = now - NEW_ORDER_ALERT_MIN * 60
    rows = db().execute(
        "SELECT * FROM orders WHERE status='new' AND created_at<? AND created_at>?", (limit, limit - 6 * 3600)
    ).fetchall()
    for o in rows:
        last, n = STUCK_ALERTED.get(o["id"], (0, 0))
        if n >= NEW_ORDER_REMIND_MAX:
            continue
        if n and now - last < NEW_ORDER_REMIND_EVERY_MIN * 60:
            continue
        STUCK_ALERTED[o["id"]] = (now, n + 1)
        mins = max(1, (now - o["created_at"]) // 60)
        for admin_id in order_staff(o):
            await safe_send(
                bot, admin_id,
                f"⚠️ Buyurtma #{order_no(o)} {mins} daqiqadan beri qabul qilinmadi! (eslatma {n + 1}/{NEW_ORDER_REMIND_MAX})\n"
                f"📞 {esc(o['phone'])}\nBuyurtma xabaridagi «✅ Qabul qilish» tugmasini bosing.",
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
        set_setting("closed_message", "")  # bo'sh = default_closed_text() (ish vaqti bilan)


_HEALTH: dict = {"last_ping": 0.0, "last_check": 0.0, "fails": 0, "alerted": False, "tunnel": "", "last_backup_try": 0.0}


async def notify_super(bot, text: str) -> None:
    for admin_id in SUPER_ADMIN_IDS:
        await safe_send(bot, admin_id, text)


def _http_ok(url: str, timeout: float = 8.0) -> bool:
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "street-dog-bot"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 400
    except Exception:
        return False


def make_backup_file() -> Path:
    """Bazaning izchil nusxasini vaqtinchalik faylga oladi (bot ishlab turgan bo'lsa ham xavfsiz)."""
    tmp = BASE_DIR / f".zaxira_{int(time.time())}.db"
    dst = sqlite3.connect(str(tmp))
    try:
        db().backup(dst)
    finally:
        dst.close()
    return tmp


async def send_backup(bot, reason: str = "avtomatik", chat_ids=None) -> bool:
    """Baza nusxasini Telegramga yuboradi. Kamida bittasiga yetib borsa — True."""
    import gzip
    targets = list(chat_ids or BACKUP_CHAT_IDS or SUPER_ADMIN_IDS)
    tmp = None
    send_path = None
    try:
        tmp = make_backup_file()
        send_path = tmp
        stamp = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d_%H%M")
        name = f"street_dog_{stamp}.db"
        if tmp.stat().st_size > 45 * 1024 * 1024:  # Telegram fayl chegarasi 50 MB — siqamiz
            send_path = Path(str(tmp) + ".gz")
            with open(tmp, "rb") as fin, gzip.open(send_path, "wb") as fout:
                shutil.copyfileobj(fin, fout)
            name += ".gz"
        if send_path.stat().st_size > 49 * 1024 * 1024:
            await notify_super(bot, "⚠️ Baza juda katta, Telegramga zaxira nusxa yuborib bo'lmadi. Bot papkasidagi street_dog.db ni qo'lda nusxalab oling.")
            return False
        conn = db()
        n_orders = conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"]
        n_users = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        caption = (
            f"💾 Street Dog bazasi ({reason}) — {stamp.replace('_', ' ')}\n"
            f"Buyurtmalar: {n_orders} · Mijozlar: {n_users}\n"
            "Tiklash: botni to'xtating, bu faylni street_dog.db nomi bilan bot.py yoniga qo'ying (.gz bo'lsa avval arxivdan chiqaring), botni yoqing."
        )
        ok = False
        for chat_id in targets:
            try:
                with open(send_path, "rb") as f:
                    await tg_call(bot.send_document, chat_id=chat_id, document=f, filename=name, caption=caption)
                ok = True
            except TelegramError as exc:
                log.warning("Zaxira nusxani %s ga yuborib bo'lmadi: %s", chat_id, exc)
        return ok
    except Exception:
        log.exception("Zaxira nusxa olishda xato")
        return False
    finally:
        for p in {tmp, send_path}:
            try:
                if p:
                    Path(p).unlink(missing_ok=True)
            except OSError:
                pass


async def check_backup(bot) -> None:
    if not BACKUP_ENABLED:
        return
    try:
        hh, mm = (int(x) for x in BACKUP_TIME.split(":"))
        now = datetime.now(LOCAL_TZ)
        if (now.hour, now.minute) < (hh, mm):
            return
        today = now.strftime("%Y-%m-%d")
        if get_setting("last_backup_day") == today:
            return
        if time.time() - _HEALTH["last_backup_try"] < 600:  # muvaffaqiyatsiz bo'lsa, 10 daqiqada bir urinamiz
            return
        _HEALTH["last_backup_try"] = time.time()
        if await send_backup(bot, "avtomatik"):
            set_setting("last_backup_day", today)
    except Exception:
        log.exception("Zaxira rejasi xatosi")


async def health_tick(bot) -> None:
    try:
        now = time.time()
        loop = asyncio.get_running_loop()
        if HEALTH_URL and now - _HEALTH["last_ping"] >= 60:
            _HEALTH["last_ping"] = now
            await loop.run_in_executor(None, _http_ok, HEALTH_URL)
        base = (_HEALTH.get("tunnel") or "").rstrip("/")
        if HEALTH_SELF_CHECK and base and now - _HEALTH["last_check"] >= 120:
            _HEALTH["last_check"] = now
            ok = await loop.run_in_executor(None, _http_ok, base + "/api/status")
            if ok:
                if _HEALTH["alerted"]:
                    await notify_super(bot, "✅ Ilova internetdan yana ochilyapti. Buyurtmalar qabul qilinmoqda.")
                _HEALTH["fails"], _HEALTH["alerted"] = 0, False
            else:
                _HEALTH["fails"] += 1
                if _HEALTH["fails"] >= 2 and not _HEALTH["alerted"]:
                    _HEALTH["alerted"] = True
                    await notify_super(
                        bot,
                        "⚠️ Ilova internetdan OCHILMAYAPTI (internet yoki cloudflared uzilgan bo'lishi mumkin). "
                        "Mijozlar buyurtma bera olmayapti.\nBotni qayta ishga tushiring (oynani yopib, bot.py ni qaytadan yoqing).",
                    )
    except Exception:
        log.exception("Sog'liq tekshiruvi xatosi")


@super_only
async def cmd_zaxira(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💾 Baza nusxasi tayyorlanmoqda...")
    ok = await send_backup(context.bot, "qo'lda", chat_ids=[update.effective_user.id])
    if not ok:
        await update.message.reply_text("❌ Zaxira nusxani yuborib bo'lmadi. Bot oynasidagi xatoni tekshiring.")


async def smena_scheduler(bot) -> None:
    first = True
    while True:
        try:
            set_setting("last_alive", str(int(time.time())))
            check_schedule(datetime.now(LOCAL_TZ))
            await check_stuck_orders(bot)
            await run_report_checks(bot, datetime.now(LOCAL_TZ), first_pass=first)
            first = False
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Smena hisoboti xatosi")
        await check_backup(bot)
        await check_auto_winback(bot)
        await health_tick(bot)
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


@super_only
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
    loop = asyncio.get_running_loop()
    if context.args:
        arg = context.args[0].lstrip("#")
        order = find_order_by_no(int(arg)) if arg.isdigit() else None
        if not order:
            await update.message.reply_text("Bugun bunday raqamli buyurtma topilmadi. Masalan: /chek 5")
            return
        if not can_handle(update.effective_user.id, order):
            await update.message.reply_text("Bu buyurtma sizning filialingizniki emas.")
            return
        try:
            where = await loop.run_in_executor(None, send_to_printer, build_chek(order), order["branch"])
            await update.message.reply_text(f"🖨 Buyurtma #{order_no(order)} cheki printerga yuborildi ({where}).")
        except Exception as exc:
            await update.message.reply_text(f"❌ Chek chiqmadi: {exc}")
        return
    # Sinov cheki: o'zi boshqaradigan har bir filialning printeriga (filial bo'lmasa — umumiy printerga)
    targets = [b["id"] for b in branches_of(update.effective_user.id)] or [None]
    for bid in targets:
        label = f"Sinov cheki ({branch_name(bid)})" if bid else "Sinov cheki"
        try:
            where = await loop.run_in_executor(None, send_to_printer, build_test_chek(bid), bid)
            await update.message.reply_text(f"🖨 {label} printerga yuborildi ({where}).")
        except Exception as exc:
            await update.message.reply_text(f"❌ {label} chiqmadi: {exc}")


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


@super_only
async def cmd_mijozlar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(build_customers_summary(), parse_mode=ParseMode.HTML)


@super_only
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
@super_only
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
    if query.from_user.id not in SUPER_ADMIN_IDS:
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
    if query.from_user.id not in STAFF_IDS:
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
    if not can_handle(query.from_user.id, order):
        await query.answer("Bu buyurtma sizning filialingizniki emas.", show_alert=True)
        return

    if new_status == "cancelled":  # sababsiz bekor qilib bo'lmaydi
        if order["status"] in TRANSITIONS:
            await query.answer()
            await query.edit_message_reply_markup(reply_markup=cancel_confirm_keyboard(order_id))
        else:
            await query.answer("Bu buyurtma holati allaqachon o'zgargan.", show_alert=True)
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
    if query.from_user.id not in STAFF_IDS:
        await query.answer("Ruxsat yo'q", show_alert=True)
        return
    order_id = int(query.data.split(":")[1])
    order = get_order(order_id)
    if not order or order["status"] not in TRANSITIONS:
        await query.answer("Bu buyurtmani bekor qilib bo'lmaydi.", show_alert=True)
        return
    if not can_handle(query.from_user.id, order):
        await query.answer("Bu buyurtma sizning filialingizniki emas.", show_alert=True)
        return
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=cancel_confirm_keyboard(order_id))


async def do_cancel(bot, order_id: int, reason: str, notify: bool = True) -> bool:
    """Buyurtmani sabab bilan bekor qiladi va mijozga xabar yuboradi."""
    order = get_order(order_id)
    if not order or order["status"] not in TRANSITIONS:
        return False
    cur = db().execute(
        "UPDATE orders SET status='cancelled', cancel_reason=?, updated_at=? WHERE id=? AND status=?",
        (reason, int(time.time()), order_id, order["status"]),
    )
    if cur.rowcount == 0:  # shu orada boshqa admin o'zgartirib bo'lgan
        return False
    if notify:  # kafe o'zi bekor qilsa — mijozning promokodi qaytariladi (soxta buyurtma bloklanganda qaytmaydi)
        promo_release(order_id)
    await refresh_order_messages(bot, order_id)
    if notify:
        await notify_customer_status(bot, get_order(order_id))
    return True


async def on_cancel_reason_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id not in STAFF_IDS:
        await query.answer("Ruxsat yo'q", show_alert=True)
        return
    _, raw_id, code = query.data.split(":")
    order_id = int(raw_id)
    order = get_order(order_id)
    if not order or order["status"] not in TRANSITIONS:
        await query.answer("Bu buyurtmani bekor qilib bo'lmaydi.", show_alert=True)
        await refresh_order_messages(context.bot, order_id)
        return
    if not can_handle(query.from_user.id, order):
        await query.answer("Bu buyurtma sizning filialingizniki emas.", show_alert=True)
        return
    wait = context.application.bot_data.setdefault("cancel_wait", {})
    if code == "oth":
        wait[query.from_user.id] = order_id
        await query.answer()
        await safe_send(
            context.bot, query.from_user.id,
            f"✍️ #{order_no(order)} buyurtmani bekor qilish sababini yozing — u mijozga yuboriladi.\n"
            "(Fikringizdan qaytsangiz, buyurtma xabaridagi «↩️ Orqaga» tugmasini bosing.)",
        )
        return
    wait.pop(query.from_user.id, None)
    ok = await do_cancel(context.bot, order_id, CANCEL_REASONS[code])
    await query.answer("Bekor qilindi, sabab mijozga yuborildi ✅" if ok else "Bu buyurtma holati allaqachon o'zgargan.", show_alert=not ok)


async def handle_cancel_reason_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin «Boshqa sabab» ni bosgandan keyin yozgan matni — mijozga sabab sifatida ketadi."""
    uid = update.effective_user.id
    wait = context.application.bot_data.setdefault("cancel_wait", {})
    order_id = wait.pop(uid, None)
    reason = (update.message.text or "").strip()[:200]
    order = get_order(order_id) if order_id else None
    if not order or not reason:
        return
    ok = await do_cancel(context.bot, order_id, reason)
    await update.message.reply_text(
        f"❌ #{order_no(order)} bekor qilindi, sabab mijozga yuborildi." if ok else "Bu buyurtma holati allaqachon o'zgargan."
    )


async def on_block_ask_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id not in STAFF_IDS:
        await query.answer("Ruxsat yo'q", show_alert=True)
        return
    order = get_order(int(query.data.split(":")[1]))
    if not order:
        await query.answer("Buyurtma topilmadi", show_alert=True)
        return
    if not can_handle(query.from_user.id, order):
        await query.answer("Bu buyurtma sizning filialingizniki emas.", show_alert=True)
        return
    await query.answer()
    await query.edit_message_reply_markup(
        reply_markup=InlineKeyboardMarkup(
            [
                [_btn("🚫 Ha, mijozni bloklash va buyurtmani bekor qilish", f"bk:{order['id']}")],
                [_btn("↩️ Orqaga", f"cb:{order['id']}")],
            ]
        )
    )


async def on_block_do_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id not in STAFF_IDS:
        await query.answer("Ruxsat yo'q", show_alert=True)
        return
    order = get_order(int(query.data.split(":")[1]))
    if not order:
        await query.answer("Buyurtma topilmadi", show_alert=True)
        return
    if not can_handle(query.from_user.id, order):
        await query.answer("Bu buyurtma sizning filialingizniki emas.", show_alert=True)
        return
    label = f"#{order_no(order)} buyurtmadan ({query.from_user.id} blokladi)"
    ban("user", str(order["user_id"]), label)
    ban("phone", phone_key(order["phone"]), label)
    await do_cancel(context.bot, order["id"], "Soxta buyurtma — mijoz bloklandi", notify=False)
    await query.answer("Mijoz bloklandi 🚫 Buyurtma bekor qilindi.", show_alert=True)


def _parse_ban_args(args):
    """'/blok id 123456' yoki '/blok +998901234567' -> (tur, qiymat) yoki None."""
    if not args:
        return None
    if args[0].lower() == "id":
        d = re.sub(r"\D", "", args[1]) if len(args) > 1 else ""
        return ("user", d) if d else None
    d = re.sub(r"\D", "", " ".join(args))
    return ("phone", phone_key(d)) if len(d) >= 9 else None


_BAN_USAGE = (
    "Telefon bo'yicha: /blok +998 90 123 45 67\n"
    "Telegram ID bo'yicha: /blok id 123456789\n"
    "Qaytarish (faqat super admin): /blokdan <shu raqam yoki id>\n"
    "Ro'yxat (faqat super admin): /bloklar"
)


@admin_only
async def cmd_blok(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parsed = _parse_ban_args(context.args)
    if not parsed:
        await update.message.reply_text("🚫 Mijozni bloklash\n" + _BAN_USAGE)
        return
    kind, value = parsed
    ban(kind, value, f"qo'lda ({update.effective_user.id} blokladi)")
    what = "Telegram ID" if kind == "user" else "telefon (oxirgi 9 raqam)"
    await update.message.reply_text(f"🚫 Bloklandi: {what} {value}\nQaytarish: /blokdan " + ("id " if kind == "user" else "") + value)


@super_only
async def cmd_blokdan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parsed = _parse_ban_args(context.args)
    if not parsed:
        await update.message.reply_text("♻️ Blokdan chiqarish\n" + _BAN_USAGE)
        return
    kind, value = parsed
    n = unban(kind, value)
    await update.message.reply_text("✅ Blokdan chiqarildi." if n else "Bunday bloklangan mijoz topilmadi.")


@super_only
async def cmd_bloklar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db().execute("SELECT * FROM banned ORDER BY created_at DESC LIMIT 60").fetchall()
    if not rows:
        await update.message.reply_text("Bloklangan mijozlar yo'q.")
        return
    lines = ["🚫 <b>Bloklanganlar</b> (oxirgi 60 tasi)"]
    for r in rows:
        when = time.strftime("%d.%m %H:%M", time.localtime(r["created_at"]))
        kind = "ID" if r["kind"] == "user" else "tel"
        lines.append(f"• {kind} <code>{esc(r['value'])}</code> — {esc(r['label'])} · {when}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


@super_only
async def cmd_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    now = int(time.time())
    if args and args[0].lower() == "off":
        code = norm_code(args[1]) if len(args) > 1 else ""
        n = db().execute("UPDATE promo_codes SET active=0 WHERE code=?", (code,)).rowcount if code else 0
        await update.message.reply_text("⛔ Promokod o'chirildi." if n else "Bunday promokod topilmadi. Masalan: /promo off STREET10")
        return
    if args:
        code = norm_code(args[0])
        try:
            percent, days = int(args[1]), int(args[2])
            limit = int(args[3]) if len(args) > 3 else None
        except (IndexError, ValueError):
            code = ""
        if len(code) < 3 or not (1 <= percent <= 90) or not (1 <= days <= 365) or (limit is not None and limit < 1):
            await update.message.reply_text("Namuna: /promo STREET10 10 30\n(kod, chegirma %, necha kun amal qiladi; oxirida ixtiyoriy: nechta mijoz ishlata oladi)")
            return
        if db().execute("SELECT 1 FROM promo_codes WHERE code=?", (code,)).fetchone():
            await update.message.reply_text("Bunday kod allaqachon bor. Boshqa nom tanlang yoki avval /promo off " + code)
            return
        db().execute(
            "INSERT INTO promo_codes (code, percent, valid_until, user_id, max_uses, note, created_at) VALUES (?,?,?,?,?,?,?)",
            (code, percent, now + days * 86400, None, limit, "public", now),
        )
        await update.message.reply_text(
            f"✅ Promokod tayyor: <code>{code}</code> — {percent}% chegirma, {days} kun"
            + (f", jami {limit} ta mijoz" if limit else "")
            + ".\nHar mijoz uni bir marta ishlata oladi. O'chirish: /promo off " + code,
            parse_mode=ParseMode.HTML,
        )
        return
    rows = db().execute(
        "SELECT * FROM promo_codes WHERE active=1 AND valid_until>? AND note!='winback' ORDER BY created_at DESC LIMIT 30", (now,)
    ).fetchall()
    wb = db().execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(used),0) AS u FROM promo_codes WHERE note='winback' AND created_at>?", (now - 30 * 86400,)
    ).fetchone()
    lines = ["🎁 <b>Promokodlar</b>", "Yaratish: /promo STREET10 10 30 (kod, %, kun)", "O'chirish: /promo off STREET10", ""]
    for r in rows:
        left = max(1, (r["valid_until"] - now) // 86400)
        lines.append(f"• <code>{esc(r['code'])}</code> — {r['percent']}% · {left} kun qoldi · ishlatilgan: {r['used']}" + (f"/{r['max_uses']}" if r["max_uses"] else ""))
    if not rows:
        lines.append("Faol umumiy promokod yo'q.")
    lines += ["", f"«Sizni sog'indik» xabari (oxirgi 30 kun): {wb['n']} ta kod yuborilgan, {wb['u']} tasi ishlatilgan."]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


def winback_targets(seg: str, days: int) -> list:
    """seg: old — oldin buyurtma bergan, lekin so'nggi {days} kunda bermagan; new — hech qachon buyurtma bermagan; all — ikkalasi."""
    now = int(time.time())
    conn = db()
    old = conn.execute(
        "SELECT u.user_id, u.full_name FROM users u JOIN "
        "(SELECT user_id, MAX(created_at) AS last FROM orders WHERE status!='cancelled' GROUP BY user_id) o ON o.user_id=u.user_id "
        "WHERE u.blocked=0 AND o.last<?", (now - days * 86400,),
    ).fetchall()
    never = conn.execute(
        "SELECT u.user_id, u.full_name FROM users u WHERE u.blocked=0 AND NOT EXISTS "
        "(SELECT 1 FROM orders o WHERE o.user_id=u.user_id AND o.status!='cancelled')"
    ).fetchall()
    rows = old if seg == "old" else never if seg == "new" else list(old) + list(never)
    pending = {
        r["user_id"] for r in conn.execute(
            "SELECT user_id FROM promo_codes WHERE note='winback' AND active=1 AND used=0 AND valid_until>? AND user_id IS NOT NULL", (now,)
        )
    }
    return [r for r in rows if r["user_id"] not in pending and r["user_id"] not in STAFF_IDS and not is_banned(user_id=r["user_id"])]


def winback_message(full_name, percent: int, code: str, valid_days: int) -> str:
    first = (full_name or "").strip().split(" ")[0] if full_name else ""
    hello = f"{esc(first)}, sizni" if first else "Sizni"
    return WINBACK_TEXT.format(hello=hello, percent=percent, code=code, days=valid_days)


@super_only
async def cmd_qaytarish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    try:
        percent = int(args[0]) if len(args) > 0 else 10
        days = int(args[1]) if len(args) > 1 else 30
        valid = int(args[2]) if len(args) > 2 else 7
    except ValueError:
        percent = 0
    if not (1 <= percent <= 50) or not (7 <= days <= 365) or not (1 <= valid <= 60):
        await update.message.reply_text(
            "Namuna: /qaytarish 15 30 7\n"
            "(chegirma % · necha kundan beri buyurtma bermaganlarga · promokod necha kun amal qiladi)\n"
            "Hech narsa yozmasangiz: 10% · 30 kun · 7 kun."
        )
        return
    context.application.bot_data.setdefault("winback", {})[update.effective_user.id] = {"percent": percent, "days": days, "valid": valid}
    n_old, n_new = len(winback_targets("old", days)), len(winback_targets("new", days))
    rows = [
        [_btn(f"So'nggi {days} kun buyurtma bermaganlar ({n_old})", "wb:s:old")],
        [_btn(f"Hech qachon buyurtma bermaganlar ({n_new})", "wb:s:new")],
        [_btn(f"Ikkalasi ({len(winback_targets('all', days))})", "wb:s:all")],
        [_btn("❌ Bekor qilish", "wb:no")],
    ]
    await update.message.reply_text(
        f"💌 <b>«Sizni sog'indik» xabari</b>\nHar mijozga o'zining promokodi yuboriladi: {percent}% chegirma, {valid} kun amal qiladi, bir marta ishlatiladi.\n\n"
        "Kimlarga yuboramiz?",
        parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows),
    )


async def run_winback(bot, admin_id: int, seg: str, p: dict) -> None:
    sent = failed = 0
    for row in winback_targets(seg, p["days"]):
        uid = row["user_id"]
        code = new_personal_code(p["percent"], p["valid"], uid)
        try:
            await tg_call(bot.send_message, chat_id=uid, text=winback_message(row["full_name"], p["percent"], code, p["valid"]),
                          parse_mode=ParseMode.HTML, reply_markup=open_app_markup())
            sent += 1
        except Forbidden:
            mark_blocked(uid)
            db().execute("DELETE FROM promo_codes WHERE code=?", (code,))
            failed += 1
        except TelegramError:
            db().execute("DELETE FROM promo_codes WHERE code=?", (code,))
            failed += 1
        await asyncio.sleep(0.06)
    await safe_send(
        bot, admin_id,
        f"💌 «Sizni sog'indik» xabari yuborildi ({p['percent']}% chegirma).\n✅ Yetkazildi: {sent}\n❌ Yetmadi: {failed}\n"
        "Natijani /promo da ko'rasiz (nechtasi ishlatilgani).",
    )


async def on_winback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid not in SUPER_ADMIN_IDS:
        await query.answer("Ruxsat yo'q", show_alert=True)
        return
    store = context.application.bot_data.setdefault("winback", {})
    p = store.get(uid)
    parts = query.data.split(":")  # wb:no | wb:s:<seg> | wb:go:<seg>
    action = parts[1] if len(parts) > 1 else ""
    if action == "no":
        store.pop(uid, None)
        await query.answer("Bekor qilindi")
        await _edit_callback_message(query, "❌ Yuborish bekor qilindi.")
        return
    if not p or len(parts) != 3 or parts[2] not in ("old", "new", "all"):
        await query.answer("Bu so'rov eskirgan. /qaytarish ni qaytadan yozing.", show_alert=True)
        return
    seg = parts[2]
    if action == "s":
        n = len(winback_targets(seg, p["days"]))
        if not n:
            await query.answer("Bu guruhda hozir hech kim yo'q.", show_alert=True)
            return
        await query.answer()
        sample = winback_message("Aziz Aliyev", p["percent"], "SXXXXXXX", p["valid"])
        await _edit_callback_message(
            query, f"📣 <b>{n} ta mijozga</b> quyidagi xabar yuboriladi (kod har kimga alohida):\n\n{sample}",
            InlineKeyboardMarkup([[_btn("✅ Yuborish", f"wb:go:{seg}")], [_btn("❌ Bekor qilish", "wb:no")]]),
        )
    elif action == "go":
        store.pop(uid, None)
        await query.answer("Yuborish boshlandi")
        await _edit_callback_message(query, "⏳ Xabarlar yuborilmoqda... Tugagach, natijani yozaman.")
        spawn(run_winback(context.bot, uid, seg, p))
    else:
        await query.answer()


# ---- AVTOMATIK "Sizni sog'indik" ----
def _auto_wb_enabled() -> bool:
    return get_setting("auto_winback", "1" if AUTO_WINBACK else "0") == "1"


def _auto_wb_time():
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", AUTO_WINBACK_TIME or "")
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        return int(m.group(1)), int(m.group(2))
    return 18, 15


def auto_winback_targets() -> list:
    """Oxirgi buyurtmasiga AUTO_WINBACK_DAYS kundan ko'p bo'lgan mijozlar (kutish va limitlar hisobga olingan).
    Eng yaqinda yo'qolganlar birinchi turadi — ularni qaytarish osonroq."""
    now = int(time.time())
    cutoff = now - AUTO_WINBACK_DAYS * 86400
    conn = db()
    rows = list(conn.execute(
        "SELECT u.user_id, u.full_name, o.last AS last_order FROM users u JOIN "
        "(SELECT user_id, MAX(created_at) AS last FROM orders WHERE status!='cancelled' GROUP BY user_id) o ON o.user_id=u.user_id "
        "WHERE u.blocked=0 AND o.last<? ORDER BY o.last DESC", (cutoff,),
    ))
    if AUTO_WINBACK_INCLUDE_NEVER:
        rows += list(conn.execute(
            "SELECT u.user_id, u.full_name, NULL AS last_order FROM users u WHERE u.blocked=0 AND u.first_seen<? AND NOT EXISTS "
            "(SELECT 1 FROM orders o WHERE o.user_id=u.user_id AND o.status!='cancelled') ORDER BY u.first_seen DESC", (cutoff,),
        ))
    sent_at: dict = {}
    for r in conn.execute("SELECT user_id, sent_at FROM winback_log"):
        sent_at.setdefault(r["user_id"], []).append(r["sent_at"])
    out = []
    for r in rows:
        uid = r["user_id"]
        # faqat joriy "yo'qolish" davrida yuborilganlar hisoblanadi: mijoz qaytib buyurtma bersa, hisob yangidan boshlanadi
        recent = [t for t in sent_at.get(uid, []) if t > (r["last_order"] or 0)]
        if len(recent) >= AUTO_WINBACK_MAX_TIMES:
            continue
        if recent and now - max(recent) < AUTO_WINBACK_COOLDOWN_DAYS * 86400:
            continue
        if uid in STAFF_IDS or is_banned(user_id=uid):
            continue
        out.append(r)
    return out


def favorite_dish(user_id: int):
    """Mijoz eng ko'p buyurtma bergan (va hozir menyuda bor, tugamagan) taom nomi."""
    counter: Counter = Counter()
    for row in db().execute("SELECT items_json FROM orders WHERE user_id=? AND status!='cancelled'", (user_id,)):
        for it in json.loads(row["items_json"]):
            counter[it["id"]] += it["count"]
    stopped = get_stoplist()
    for pid, _n in counter.most_common():
        if pid in PRODUCTS and pid not in stopped:
            return PRODUCTS[pid]["name"]
    return None


def auto_winback_message(full_name, user_id: int, last_order) -> str:
    first = (full_name or "").strip().split(" ")[0] if full_name else ""
    hello = f"{esc(first)}, sizni" if first else "Sizni"
    parts = []
    if last_order:
        ago = max(1, (int(time.time()) - last_order) // 86400)
        parts.append(f"Oxirgi buyurtmangizdan beri {ago} kun o'tdi.")
    fav = favorite_dish(user_id)
    if fav:
        parts.append(f"Sevimli taomingiz — <b>{esc(fav)}</b> — sizni kutmoqda! 😋")
    intro = ("\n".join(parts) + "\n\n") if parts else ""
    return random.choice(AUTO_WINBACK_TEXTS).format(hello=hello, intro=intro)


async def run_auto_winback(bot) -> tuple:
    sent = failed = 0
    for row in auto_winback_targets():
        if sent >= AUTO_WINBACK_MAX_PER_DAY or failed >= AUTO_WINBACK_MAX_PER_DAY:
            break
        uid = row["user_id"]
        try:
            await tg_call(
                bot.send_message, chat_id=uid,
                text=auto_winback_message(row["full_name"], uid, row["last_order"]),
                parse_mode=ParseMode.HTML, reply_markup=open_app_markup(),
            )
            db().execute("INSERT INTO winback_log (user_id, sent_at) VALUES (?, ?)", (uid, int(time.time())))
            sent += 1
        except Forbidden:  # mijoz botni bloklagan
            mark_blocked(uid)
            failed += 1
        except TelegramError:
            failed += 1
        await asyncio.sleep(0.06)
    return sent, failed


async def check_auto_winback(bot) -> None:
    """Har kuni AUTO_WINBACK_TIME da bir marta ishlaydi (kafe ochiq bo'lsa)."""
    if not _auto_wb_enabled():
        return
    try:
        now = datetime.now(LOCAL_TZ)
        hh, mm = _auto_wb_time()
        due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now < due or (now - due).total_seconds() > AUTO_WINBACK_LATE_MIN * 60:
            return
        today = now.strftime("%Y-%m-%d")
        if get_setting("auto_winback_day") == today or not any_open():
            return
        set_setting("auto_winback_day", today)  # ikki marta ishlamasligi uchun avval belgilaymiz
        sent, failed = await run_auto_winback(bot)
        if sent or failed:
            log.info("Avto «sog'indik»: yuborildi %s, yetmadi %s", sent, failed)
            await notify_super(
                bot,
                f"💌 Avtomatik «Sizni sog'indik»: {sent} ta mijozga yuborildi"
                + (f", {failed} tasiga yetmadi" if failed else "")
                + ".\nNatija: /avto",
            )
    except Exception:
        log.exception("Avtomatik «sog'indik» xatosi")


@super_only
async def cmd_avto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    arg = (context.args[0].lower() if context.args else "")
    if arg in ("on", "off"):
        set_setting("auto_winback", "1" if arg == "on" else "0")
    on = _auto_wb_enabled()
    state = "🟢 yoqilgan" if on else "🔴 o'chirilgan"
    wb = db().execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(EXISTS(SELECT 1 FROM orders o WHERE o.user_id=w.user_id AND o.created_at>w.sent_at "
        "AND o.status!='cancelled')), 0) AS back FROM winback_log w WHERE w.sent_at>?",
        (int(time.time()) - 30 * 86400,),
    ).fetchone()
    hh, mm = _auto_wb_time()
    await update.message.reply_text(
        f"💌 <b>Avtomatik «Sizni sog'indik»</b>: {state}\n\n"
        f"• {AUTO_WINBACK_DAYS} kundan beri buyurtma bermaganlarga har kuni {hh:02d}:{mm:02d} da yuboriladi\n"
        f"• Hozir navbatda: {len(auto_winback_targets())} ta mijoz\n"
        f"• Oxirgi 30 kunda: {wb['n']} ta yuborilgan, shulardan {wb['back']} tasi qaytib buyurtma bergan\n\n"
        "Yoqish: /avto on · O'chirish: /avto off",
        parse_mode=ParseMode.HTML,
    )


async def on_cancel_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id not in STAFF_IDS:
        await query.answer("Ruxsat yo'q", show_alert=True)
        return
    context.application.bot_data.setdefault("cancel_wait", {}).pop(query.from_user.id, None)
    order = get_order(int(query.data.split(":")[1]))
    if order and not can_handle(query.from_user.id, order):
        await query.answer("Bu buyurtma sizning filialingizniki emas.", show_alert=True)
        return
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


async def on_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kuryer botga 'Jonli joylashuv' (Live Location) yuborsa, uni saqlaymiz (faqat ro'yxatdagi kuryerlar)."""
    msg = update.edited_message or update.message
    if not msg or not msg.location or not msg.from_user:
        return
    if not db().execute("SELECT 1 FROM couriers WHERE user_id=?", (msg.from_user.id,)).fetchone():
        return
    db().execute(
        "INSERT INTO courier_loc (user_id, lat, lon, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET lat=excluded.lat, lon=excluded.lon, updated_at=excluded.updated_at",
        (msg.from_user.id, msg.location.latitude, msg.location.longitude, int(time.time())),
    )


def track_info(order_id: int, user_id: int) -> dict:
    o = get_order(order_id)
    if not o or o["user_id"] != user_id:
        return {"active": False}
    if o["delivery_type"] != "delivery" or not o["courier_id"] or o["status"] not in ("ready", "onway"):
        return {"active": False}
    loc = db().execute("SELECT lat, lon, updated_at FROM courier_loc WHERE user_id=?", (o["courier_id"],)).fetchone()
    fresh = bool(loc and time.time() - loc["updated_at"] < 600)
    return {
        "active": True,
        "status": o["status"],
        "name": o["courier_name"],
        "phone": o["courier_phone"],
        "courier": {"lat": loc["lat"], "lon": loc["lon"]} if fresh else None,
        "dest": {"lat": o["lat"], "lon": o["lon"]} if o["lat"] is not None else None,
    }


async def api_track(request):
    body = await read_json(request)
    if body is None:
        return jerr("So'rov noto'g'ri formatda.")
    user = validate_init_data(str(body.get("init_data", "")), BOT_TOKEN)
    if not user:
        return jerr("Telegram orqali tasdiqlanmadi.", 401)
    try:
        oid = int(body.get("order_id"))
    except (TypeError, ValueError):
        return jerr("Buyurtma topilmadi.")
    return web.json_response({"ok": True, **track_info(oid, user["id"])})


async def on_courier_take(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    oid = int(query.data.split(":")[1])
    order = get_order(oid)
    if not order or order["status"] in ("done", "cancelled"):
        await query.answer("Bu buyurtma yopilgan.", show_alert=True)
        return
    if order["delivery_type"] != "delivery":
        await query.answer("Bu olib ketish buyurtmasi — kuryer kerak emas.", show_alert=True)
        return
    if order["status"] == "new":
        await query.answer("Buyurtma hali admin tomonidan qabul qilinmagan. Qabul qilingach, qayta bosing.", show_alert=True)
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
    await safe_send(
        context.bot, query.from_user.id,
        "📍 Mijoz buyurtmani xaritada kuzatishi uchun shu yerga <b>Jonli joylashuv</b> yuboring:\n"
        "📎 → Joylashuv → «Jonli joylashuvni ulashish» (masalan, 8 soatga).",
    )
    await safe_send(context.bot, order["user_id"], f"🛵 Kuryer <b>{esc(name)}</b> buyurtmangizni #{order_no(order)} oldi.\n📞 Kuryer telefoni: {esc(phone)}")
    for admin_id in order_staff(order):
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
    if order["status"] == "ready":  # "Yo'lda" bosqichini o'tkazib yubormaymiz: mijozga ham xabar boradi
        cur = db().execute(
            "UPDATE orders SET status='onway', updated_at=? WHERE id=? AND status='ready'", (int(time.time()), oid)
        )
        if cur.rowcount:
            await notify_customer_status(context.bot, get_order(oid))
    cur = db().execute(
        "UPDATE orders SET status='done', updated_at=? WHERE id=? AND status IN ('ready','onway')",
        (int(time.time()), oid),
    )
    if cur.rowcount == 0:  # shu orada admin o'zgartirib bo'lgan (masalan, bekor qilgan yoki yakunlagan)
        await query.answer("Bu buyurtma holati allaqachon o'zgargan.", show_alert=True)
        await refresh_order_messages(context.bot, oid)
        return
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
    low = rating <= LOW_RATING_MAX
    for admin_id in order_staff(order):
        if low:
            await safe_send(
                context.bot, admin_id,
                f"🚨 <b>PAST BAHO: {'⭐' * rating}</b>\nBuyurtma #{order_no(order)} · 📞 {esc(order['phone'])}\n"
                f"👤 {esc(order['full_name'] or '')} {('@' + order['username']) if order['username'] else ''}\n"
                f"Mijoz bilan bog'lanib, muammoni hal qiling.",
            )
        else:
            await safe_send(context.bot, admin_id, f"⭐ Buyurtma #{order_no(order)} uchun mijoz {rating} baho berdi.")
    if low:
        await safe_send(context.bot, order["user_id"], "Noqulaylik uchun uzr so'raymiz 🙏 Siz bilan tez orada bog'lanamiz va muammoni hal qilamiz.")
    elif rating == 5 and REVIEW_URL:
        await safe_send(context.bot, order["user_id"], f"Rahmat! 🥰 Agar yoqqan bo'lsa, sharh qoldirsangiz juda xursand bo'lamiz:\n{REVIEW_URL}")


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
SUPER_COMMANDS = USER_COMMANDS + [
    BotCommand("ochish", "Buyurtma qabul qilishni ochish"),
    BotCommand("yopish", "Buyurtma qabul qilishni yopish"),
    BotCommand("menyu", "Menyu: taom qo'shish / narx / tahrirlash"),
    BotCommand("stop", "Stop-list (tugagan taomlar)"),
    BotCommand("blok", "Mijozni bloklash (soxta buyurtma)"),
    BotCommand("blokdan", "Mijozni blokdan chiqarish"),
    BotCommand("bloklar", "Bloklanganlar ro'yxati"),
    BotCommand("yangi", "Menyuga yangi taom qo'shish"),
    BotCommand("narx", "Taom narxlarini o'zgartirish / o'chirish"),
    BotCommand("zaxira", "Baza nusxasini hozir olish"),
    BotCommand("promo", "Promokod yaratish / ro'yxat"),
    BotCommand("qaytarish", "«Sizni sog'indik» chegirma xabari"),
    BotCommand("avto", "Avtomatik «Sizni sog'indik» (yoqish/o'chirish)"),
    BotCommand("stats", "Statistika"),
    BotCommand("broadcast", "Hammaga xabar yuborish"),
    BotCommand("chek", "Chekni qayta chiqarish / sinov"),
    BotCommand("raqam", "Buyurtma raqamini belgilash"),
    BotCommand("hisobot", "Joriy smena hisoboti"),
    BotCommand("smena", "Smenani yopish"),
    BotCommand("mijozlar", "Mijozlar bazasi"),
    BotCommand("eksport", "Mijozlar ro'yxati (fayl)"),
]
ADMIN_COMMANDS = USER_COMMANDS + [
    BotCommand("ochish", "Buyurtma qabul qilishni ochish"),
    BotCommand("yopish", "Buyurtma qabul qilishni yopish"),
    BotCommand("stop", "Stop-list (tugagan taomlar)"),
    BotCommand("blok", "Mijozni bloklash (soxta buyurtma)"),
    BotCommand("chek", "Chekni qayta chiqarish / sinov"),
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


def check_unexpected_stop(bot) -> None:
    """Bot kutilmaganda (o'chib, internet uzilib, xato bilan) to'xtab qolgan bo'lsa, super adminga aytadi."""
    try:
        now_ts = int(time.time())
        last_alive = int(get_setting("last_alive", "0") or 0)
        clean = get_setting("clean_stop") == "1"
        set_setting("clean_stop", "0")
        set_setting("last_alive", str(now_ts))
        if last_alive and not clean and now_ts - last_alive > 150:
            t1 = datetime.fromtimestamp(last_alive, LOCAL_TZ).strftime("%d.%m %H:%M")
            t2 = datetime.fromtimestamp(now_ts, LOCAL_TZ).strftime("%d.%m %H:%M")
            spawn(notify_super(
                bot,
                f"⚠️ Bot kutilmaganda to'xtab qolgan edi: {t1} dan {t2} gacha (~{(now_ts - last_alive) // 60} daqiqa ishlamadi). "
                "Hozir qayta ishga tushdi. Shu vaqtda buyurtma bermoqchi bo'lgan mijozlar bo'lsa, tekshirib ko'ring.",
            ))
    except Exception:
        log.exception("To'xtab qolish vaqtini tekshirishda xato")


async def post_init(application):
    global WEB_APP_URL
    try:
        await application.bot.set_my_commands(USER_COMMANDS)
        for admin_id in STAFF_IDS:
            try:
                await application.bot.set_my_commands(
                    (SUPER_COMMANDS if admin_id in SUPER_ADMIN_IDS else ADMIN_COMMANDS), scope=BotCommandScopeChat(chat_id=admin_id)
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
    check_unexpected_stop(application.bot)
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
                _HEALTH["tunnel"] = url
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
    try:
        set_setting("clean_stop", "1")  # odatiy (Ctrl+C) to'xtatish — keyingi yoqilganda "kutilmaganda to'xtadi" deb yozmaydi
    except Exception:
        pass
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
    if not SUPER_ADMIN_IDS:
        raise SystemExit("❌ SUPER_ADMIN_IDS topilmadi. bot.py yuqorisidagi SUPER_ADMIN_IDS = [...] ga o'z Telegram ID ingizni yozing.")

    init_db()

    if not BRANCHES and MAX_DELIVERY_KM and (CAFE_LAT is None or CAFE_LON is None):
        log.warning(
            "⚠️ CAFE_LAT/CAFE_LON yozilmagan — dostavka masofasi TEKSHIRILMAYAPTI (MAX_DELIVERY_KM=%s km ishlamaydi).",
            MAX_DELIVERY_KM,
        )

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
    app.add_handler(MessageHandler(filters.LOCATION, on_location))
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, on_dish_photo))
    app.add_handler(CommandHandler("aloqa", cmd_aloqa))
    app.add_handler(CommandHandler("manzil", cmd_manzil))
    app.add_handler(CommandHandler("ochish", cmd_ochish))
    app.add_handler(CommandHandler("yopish", cmd_yopish))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("menyu", cmd_menyu))
    app.add_handler(CommandHandler("narx", cmd_narx))
    app.add_handler(CommandHandler("zaxira", cmd_zaxira))
    app.add_handler(CommandHandler("promo", cmd_promo))
    app.add_handler(CommandHandler("qaytarish", cmd_qaytarish))
    app.add_handler(CommandHandler("avto", cmd_avto))
    app.add_handler(CallbackQueryHandler(on_winback_callback, pattern=r"^wb:"))
    app.add_handler(CommandHandler("yangi", cmd_yangi))
    app.add_handler(CommandHandler("blok", cmd_blok))
    app.add_handler(CommandHandler("blokdan", cmd_blokdan))
    app.add_handler(CommandHandler("bloklar", cmd_bloklar))
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
    app.add_handler(CallbackQueryHandler(on_block_ask_callback, pattern=r"^bl:\d+$"))
    app.add_handler(CallbackQueryHandler(on_block_do_callback, pattern=r"^bk:\d+$"))
    app.add_handler(CallbackQueryHandler(on_cancel_reason_callback, pattern=r"^cx:\d+:(nop|nom|oth)$"))
    app.add_handler(CallbackQueryHandler(on_rate_callback, pattern=r"^rate:\d+:[1-5]$"))
    app.add_handler(CallbackQueryHandler(on_stop_callback, pattern=r"^sl:"))
    app.add_handler(CallbackQueryHandler(on_openclose_callback, pattern=r"^oc:"))
    app.add_handler(CallbackQueryHandler(on_menu_callback, pattern=r"^mn:"))
    app.add_handler(CallbackQueryHandler(on_price_callback, pattern=r"^pr:"))
    app.add_handler(CallbackQueryHandler(on_dish_callback, pattern=r"^dn:"))
    app.add_handler(CallbackQueryHandler(on_broadcast_callback, pattern=r"^bc:(yes|no)$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, on_any_text))
    app.add_error_handler(on_error)

    print("Bot muvaffaqiyatli ishga tushmoqda...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
