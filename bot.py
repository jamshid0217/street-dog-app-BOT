import json
import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo
from telegram import Update, ReplyKeyboardRemove, MenuButtonWebApp, WebAppInfo
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = "8983485871:AAGjZ4uZteoh-VSfZZU4kgd79wdelfAQniw"
WEB_APP_URL = "https://jamshid0217.github.io/street-dog-app/"

# Telegram ID'ingizni shu yerga yozing
ADMIN_ID = 123456789

# Kunlik buyurtmalarni xotirada saqlash
daily_orders = []

logging.basicConfig(level=logging.INFO)

async def post_init(application):
    await application.bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text="Open App",
            web_app=WebAppInfo(url=WEB_APP_URL)
        )
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Xush kelibsiz! Buyurtma berish uchun pastdagi 'Open App' tugmasini bosing.",
        reply_markup=ReplyKeyboardRemove()
    )

# 1. Web App'dan kelgan buyurtmani qabul qilish va Adminga xabar yuborish
async def web_app_data_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    web_app_data = update.message.web_app_data.data
    
    try:
        data = json.loads(web_app_data)
        items = data.get("items", [])
        total_price = data.get("total_price", 0)
        phone = data.get("phone", "Ko'rsatilmadi")
        address = data.get("address", "Ko'rsatilmadi")

        item_details = ""
        for item in items:
            title = item.get("title", "Mahsulot")
            count = item.get("count", 1)
            price = item.get("price", 0)
            item_details += f"• {title} x {count} = {price * count:,.0f} so'm\n"

        order_info = {
            "user_id": user.id,
            "user_name": user.full_name,
            "total_price": total_price,
            "time": datetime.now().strftime("%H:%M:%S")
        }
        daily_orders.append(order_info)

        # Mijozga javob
        await update.message.reply_text(
            f"✅ **Buyurtmangiz qabul qilindi!**\n\n"
            f"💰 Jami summa: **{total_price:,.0f} so'm**\n\n"
            f"Tez orada siz bilan bog'lanamiz!",
            parse_mode="Markdown"
        )

        # Adminga xabar
        admin_message = (
            f"🛒 **YANGI BUYURTMA!**\n\n"
            f"👤 **Mijoz:** {user.full_name} (@{user.username or 'yoq'})\n"
            f"📞 **Tel:** {phone}\n"
            f"📍 **Manzil:** {address}\n\n"
            f"📦 **Buyurtma tarkibi:**\n{item_details}\n"
            f"💵 **Jami summa:** {total_price:,.0f} so'm\n"
            f"⏰ **Vaqt:** {order_info['time']}"
        )
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_message, parse_mode="Markdown")

    except Exception as e:
        logging.error(f"Xatolik: {e}")
        await update.message.reply_text("Buyurtma ma'lumotlarini qayta ishlashda xatolik yuz berdi.")

# 2. Istalgan vaqtda kunlik statistikani ko'rish (/statistika)
async def daily_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    if not daily_orders:
        await update.message.reply_text("Bugun hali hech qanday buyurtma tushmadi.")
        return

    total_sales = sum(order["total_price"] for order in daily_orders)
    total_count = len(daily_orders)

    report_text = (
        f"📊 **BUGUNGI KUNLIK SAVDO HISOBOTI**\n"
        f"📅 Sana: {datetime.now().strftime('%Y-%m-%d')}\n\n"
        f"🔢 Jami buyurtmalar: **{total_count} ta**\n"
        f"💰 Jami savdo summasi: **{total_sales:,.0f} so'm**\n"
        f"📈 O'rtacha chek: **{(total_sales / total_count):,.0f} so'm**"
    )
    await update.message.reply_text(report_text, parse_mode="Markdown")

# 3. Kun yakunidagi avtomatik hisobot funksiyasi
async def auto_daily_report(context: ContextTypes.DEFAULT_TYPE):
    if not daily_orders:
        await context.bot.send_message(chat_id=ADMIN_ID, text="🌙 Bugun hech qanday savdo bo'lmadi.")
        return

    total_sales = sum(order["total_price"] for order in daily_orders)
    total_count = len(daily_orders)

    report_text = (
        f"🌙 **KUN YAKUNI HISOBOTI**\n"
        f"📅 Sana: {datetime.now().strftime('%Y-%m-%d')}\n\n"
        f"📦 Jami buyurtmalar soni: **{total_count} ta**\n"
        f"💵 Umumiy tushum: **{total_sales:,.0f} so'm**\n"
    )
    await context.bot.send_message(chat_id=ADMIN_ID, text=report_text, parse_mode="Markdown")
    daily_orders.clear()

if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("statistika", daily_report))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, web_app_data_handler))

    # Avtomatik hisobotni tekshirish (agar job_queue tayyor bo'lsa)
    if app.job_queue:
        tz = ZoneInfo("Asia/Tashkent")
        app.job_queue.run_daily(auto_daily_report, time=time(hour=23, minute=59, tzinfo=tz))

    print("Bot muvaffaqiyatli ishga tushdi...")
    app.run_polling()