from telegram import Update, ReplyKeyboardRemove, MenuButtonWebApp, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = "8983485871:AAGjZ4uZteoh-VSfZZU4kgd79wdelfAQniw"
WEB_APP_URL = "https://jamshid0217.github.io/street-dog-app/"  # Web App havolangiz

async def post_init(application):
    # Pastki chap burchakdagi ko'k "Open App" tugmasini o'rnatamiz
    await application.bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text="Open App",
            web_app=WebAppInfo(url=WEB_APP_URL)
        )
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ReplyKeyboardRemove() ekran ostidagi barcha oddiy tugmalarni (Menyu, Savat...) o'chirib tashlaydi
    await update.message.reply_text(
        "Xush kelibsiz! Buyurtma berish uchun pastdagi 'Open App' tugmasini bosing.",
        reply_markup=ReplyKeyboardRemove()
    )

if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))

    print("Bot ishga tushdi...")
    app.run_polling()