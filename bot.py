import os
import json
import asyncio
import urllib.parse
import urllib.request
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

TOKEN = os.getenv("BOT_TOKEN")
API_KEY = os.getenv("TWELVE_DATA_KEY")

PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY"]
CHATS = set()
LAST_SIGNAL = {}

def get_market_data(symbol):
    params = urllib.parse.urlencode({
        "symbol": symbol,
        "interval": "1min",
        "outputsize": 70,
        "apikey": API_KEY,
        "timezone": "UTC"
    })

    url = "https://api.twelvedata.com/time_series?" + params
    request = urllib.request.Request(
        url, headers={"User-Agent": "DADAVAI-BOT/1.0"}
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.loads(response.read().decode())

    if data.get("status") != "ok" or "values" not in data:
        raise ValueError(data.get("message", "Market data unavailable"))

    candles = data["values"]

    if len(candles) < 60:
        raise ValueError("Not enough candles")

    # Newest candle may still be forming
    return list(reversed(candles))[1:]

def ema(values, period):
    multiplier = 2 / (period + 1)
    result = values[0]

    for value in values[1:]:
        result = value * multiplier + result * (1 - multiplier)

    return result

def rsi(values, period=14):
    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    gains = [max(x, 0) for x in changes[-period:]]
    losses = [max(-x, 0) for x in changes[-period:]]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100 if avg_gain > 0 else 50

    return 100 - (100 / (1 + avg_gain / avg_loss))

def analyze(symbol):
    candles = get_market_data(symbol)
    closes = [float(c["close"]) for c in candles]

    fast = ema(closes[-50:], 9)
    slow = ema(closes[-50:], 21)
    momentum = rsi(closes)

    macd_now = (
        ema(closes[-50:], 12) -
        ema(closes[-50:], 26)
    )

    macd_prev = (
        ema(closes[-51:-1], 12) -
        ema(closes[-51:-1], 26)
    )

    if fast > slow and momentum > 50 and macd_now > macd_prev and macd_now > 0:
        direction = "CALL 🟢"
    elif fast < slow and momentum < 50 and macd_now < macd_prev and macd_now < 0:
        direction = "PUT 🔴"
    else:
        direction = "NO TRADE ⚪"

    return (
        f"📊 {symbol}\n"
        f"⏱ Timeframe: M1\n"
        f"📈 EMA 9: {fast:.5f}\n"
        f"📉 EMA 21: {slow:.5f}\n"
        f"RSI: {momentum:.2f}\n"
        f"MACD: {macd_now:.6f}\n\n"
        f"Signal: {direction}\n"
        f"⚠️ Demo analysis only"
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    CHATS.add(update.effective_chat.id)

    await update.message.reply_text(
        "🤖 DADAVAI 24H BOT\n"
        "Automatic M1 Analysis Started!\n\n"
        "/signal - Analyze Forex pairs\n"
        "/help - Help"
    )

async def signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not API_KEY:
        await update.message.reply_text("API key missing.")
        return

    await update.message.reply_text("🔎 Fetching market data...")

    for symbol in PAIRS:
        try:
            result = await asyncio.to_thread(analyze, symbol)
            await update.message.reply_text(result)
        except Exception as error:
            await update.message.reply_text(
                f"⚠️ {symbol}: Data unavailable.\n"
                f"{str(error)[:150]}"
            )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "DADAVAI 24H BOT\n"
        "/start - Start bot\n"
        "/signal - M1 market analysis\n"
        "Forex demo analysis only."
    )

async def automatic_scan(app):
    while True:
        await asyncio.sleep(60)

        for symbol in PAIRS:
            try:
                result = await asyncio.to_thread(analyze, symbol)

                if "CALL 🟢" not in result and "PUT 🔴" not in result:
                    continue

                key = (symbol, result.split("Signal: ")[1].split("\n")[0])

                if LAST_SIGNAL.get(symbol) == key[1]:
                    continue

                LAST_SIGNAL[symbol] = key[1]

                for chat_id in list(CHATS):
                    await app.bot.send_message(
                        chat_id=chat_id,
                        text="🤖 DADAVAI 24H BOT\n" + result
                    )

            except Exception:
                continue

async def post_init(app):
    app.create_task(automatic_scan(app))

def main():
    if not TOKEN:
        raise ValueError("BOT_TOKEN missing")
    if not API_KEY:
        raise ValueError("TWELVE_DATA_KEY missing")

    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("signal", signal))
    app.add_handler(CommandHandler("help", help_command))

    app.run_polling()

if __name__ == "__main__":
    main()
