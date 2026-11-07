import os
import json
import asyncio
import urllib.parse
import urllib.request
# OTC DATA SOURCE CHECKER
OTC_PAIRS = ["USD/BRL-OTC"]

def check_otc_source():
    return {
        "pair": "USD/BRL-OTC",
        "provider": "Twelve Data",
        "status": "UNAVAILABLE",
        "reason": "Twelve Data does not provide verified Quotex OTC prices."
    }

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


def fetch_market_data(symbol):
    params = urllib.parse.urlencode({
        "symbol": symbol,
        "interval": "1min",
        "outputsize": 100,
        "apikey": API_KEY
    })

    url = (
        "https://api.twelvedata.com/time_series?"
        + params
    )

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "DadavaiBot/1.0"}
    )

    with urllib.request.urlopen(
        request, timeout=20
    ) as response:
        data = json.loads(response.read().decode())

    if "values" not in data:
        raise ValueError(
            data.get("message", "Market data unavailable")
        )

    # Twelve Data returns newest candle first.
    rows = list(reversed(data["values"]))

    # Ignore the newest candle because it may be incomplete.
    rows = rows[:-1]

    if len(rows) < 60:
        raise ValueError("Not enough candle data")

    closes = [float(row["close"]) for row in rows]

    return rows[-1]["datetime"], closes


def ema(values, period):
    if len(values) < period:
        raise ValueError("Not enough EMA data")

    multiplier = 2 / (period + 1)
    result = [values[0]]

    for value in values[1:]:
        result.append(
            (value * multiplier)
            + (result[-1] * (1 - multiplier))
        )

    return result


def calculate_rsi(values, period=14):
    changes = [
        values[i] - values[i - 1]
        for i in range(len(values) - period, len(values))
    ]

    gains = [max(change, 0) for change in changes]
    losses = [max(-change, 0) for change in changes]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100 if avg_gain > 0 else 50

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


def analyze_market(closes):
    ema9 = ema(closes, 9)[-1]
    ema21 = ema(closes, 21)[-1]

    rsi = calculate_rsi(closes)

    macd_line = [
        fast - slow
        for fast, slow in zip(
            ema(closes, 12),
            ema(closes, 26)
        )
    ]

    macd_signal = ema(macd_line, 9)[-1]
    macd = macd_line[-1]

    if (
        ema9 > ema21
        and macd > macd_signal
        and 50 < rsi < 70
    ):
        return "BUY", rsi

    if (
        ema9 < ema21
        and macd < macd_signal
        and 30 < rsi < 50
    ):
        return "SELL", rsi

    return "WAIT", rsi


async def get_signal(symbol):
    candle_time, closes = await asyncio.to_thread(
        fetch_market_data, symbol
    )

    direction, rsi = analyze_market(closes)

    return candle_time, direction, rsi


def signal_message(symbol, candle_time, direction, rsi):
    emoji = {
        "BUY": "🟢",
        "SELL": "🔴",
        "WAIT": "⚪"
    }.get(direction, "⚪")

    return (
        "🤖 DADAVAI 24H BOT\n\n"
        f"💱 Pair: {symbol}\n"
        "⏱ Timeframe: M1\n"
        f"📊 Signal: {emoji} {direction}\n"
        f"📈 RSI: {rsi:.2f}\n"
        f"🕒 Candle: {candle_time}\n\n"
        "⚠️ Signals are not guaranteed. "
        "Verify before trading."
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    CHATS.add(chat_id)

    await update.message.reply_text(
        "🤖 DADAVAI 24H BOT\n\n"
        "Welcome!\n"
        "Forex M1 Signal Engine is active.\n\n"
        "/signal - Check market signals\n"
        "/help - Help"
    )


async def signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📊 Checking live Forex market data..."
    )

    for symbol in PAIRS:
        try:
            candle_time, direction, rsi = await get_signal(symbol)

            message = signal_message(
                symbol, candle_time, direction, rsi
            )

            await update.message.reply_text(message)

        except Exception as error:
            await update.message.reply_text(
                f"⚠️ {symbol}: Data unavailable.\n"
                f"Reason: {str(error)[:180]}"
            )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 DADAVAI 24H BOT HELP\n\n"
        "/start - Start bot\n"
        "/signal - Get M1 Forex signals\n"
        "/help - Help\n\n"
        "Pairs:\n"
        "EUR/USD\n"
        "GBP/USD\n"
        "USD/JPY"
    )


async def automatic_scan(app):
    while True:
        for symbol in PAIRS:
            try:
                candle_time, direction, rsi = await get_signal(symbol)

                if direction == "WAIT":
                    continue

                # Send only once for each completed candle.
                if LAST_SIGNAL.get(symbol) == candle_time:
                    continue

                LAST_SIGNAL[symbol] = candle_time

                message = signal_message(
                    symbol, candle_time, direction, rsi
                )

                for chat_id in list(CHATS):
                    try:
                        await app.bot.send_message(
                            chat_id=chat_id,
                            text=message
                        )
                    except Exception as error:
                        print("Telegram send error:", error)

            except Exception as error:
                print(f"{symbol} scan error: {error}")

        await asyncio.sleep(60)


async def post_init(app):
    app.create_task(automatic_scan(app))


def main():
    print("DADAVAI NEW BOT VERSION 2 STARTED", flush=True)
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")

    if not API_KEY:
        raise RuntimeError("TWELVE_DATA_KEY is missing")

    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("signal", signal))
    app.add_handler(CommandHandler("help", help_command))

    print("DADAVAI 24H BOT started!")

    app.run_polling()


if __name__ == "__main__":
    main()
