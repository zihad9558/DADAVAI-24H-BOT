import os
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

TOKEN = os.getenv("BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 DADAVAI 24H BOT\n\n"
        "Welcome!\n"
        "Forex + OTC Signal System\n\n"
        "Commands:\n"
        "/signal - View signal status\n"
        "/help - Help"
    )

async def signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📊 DADAVAI 24H BOT\n\n"
        "Signal engine is starting...\n"
        "No live trading signal yet.\n"
        "Please wait for verified market data."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "DADAVAI 24H BOT\n"
        "Use /start to begin.\n"
        "Use /signal to check signal status."
    )

def main():
    if not TOKEN:
        raise ValueError("BOT_TOKEN is missing!")

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("signal", signal))
    app.add_handler(CommandHandler("help", help_command))
    app.run_polling()

if __name__ == "__main__":
    main()
