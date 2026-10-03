import logging
from datetime import datetime
from telegram import Update, MenuButtonWebApp, WebAppInfo, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

BOT_TOKEN = "8983485871:AAFzezqjvtLgNDQ-LfIxSsMMttbdZMiFK0M"
WEB_APP_URL = "https://jamshid0217.github.io/street-dog-app/"
ADMIN_ID = 6069854654  

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

async def post_init(application):
    # Chat chap pastida faqat "Open App" tugmasi turadi
    await application.bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text="Open App",
            web_app=WebAppInfo(url=WEB_APP_URL)
        )
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_nickname = f"@{user.username}" if user.username else user.full_name
    
    # Ekranda hech qanday tugma bo'lmaydi, chat toza turadi
    await update.message.reply_text(
        f"Xush kelibsiz, **{user_nickname}**!\n\n"
        "Buyurtma berish uchun chap pastdagi **'Open App'** tugmasini bosing.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )

if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))

    print("Bot muvaffaqiyatli ishga tushdi...")
    app.run_polling()