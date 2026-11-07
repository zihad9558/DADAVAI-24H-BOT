import os
import json
import asyncio
import urllib.parse
import urllib.request

from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

TOKEN = os.getenv("BOT_TOKEN")
API_KEY = os.getenv("TWELVE_DATA_KEY")

DHAKA = ZoneInfo("Asia/Dhaka")

# Normal Forex pairs only
PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY"]

# Quotex OTC is not available from Twelve Data
OTC_PAIRS = ["USD/BRL-OTC"]

CHATS = set()
LAST_SIGNAL = {}
SIGNAL_COUNTS = {}


def fetch_market_data(symbol):
    params = urllib.parse.urlencode({
        "symbol": symbol,
        "interval": "1min",
        "outputsize": 100,
        "timezone": "Asia/Dhaka",
        "apikey": API_KEY
    })

    url = (
        "https://api.twelvedata.com/time_series?"
        + params
    )

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "DadavaiBot/2.0"}
    )

    with urllib.request.urlopen(
        request, timeout=20
    ) as response:
        data = json.loads(
            response.read().decode()
        )

    if "values" not in data:
        raise ValueError(
            data.get("message", "Market data unavailable")
        )

    rows = list(reversed(data["values"]))

    # Ignore possibly incomplete latest candle
    rows = rows[:-1]

    if len(rows) < 60:
        raise ValueError("Not enough candle data")

    closes = [
        float(row["close"])
        for row in rows
    ]

    return rows[-1]["datetime"], closes


def ema(values, period):
    if len(values) < period:
        raise ValueError("Not enough EMA data")

    multiplier = 2 / (period + 1)
    result = [values[0]]

    for value in values[1:]:
        result.append(
            value * multiplier
            + result[-1] * (1 - multiplier)
        )

    return result


def calculate_rsi(values, period=14):
    changes = [
        values[i] - values[i - 1]
        for i in range(len(values) - period, len(values))
    ]

    gains = [max(x, 0) for x in changes]
    losses = [max(-x, 0) for x in changes]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100 if avg_gain > 0 else 50

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


def analyze_market(closes):
    ema9 = ema(closes, 9)[-1]
    ema21 = ema(closes, 21)[-1]
    ema50 = ema(closes, 50)[-1]

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
        ema9 > ema21 > ema50
        and macd > macd_signal
        and 50 < rsi < 70
    ):
        return "BUY", rsi

    if (
        ema9 < ema21 < ema50
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
    emoji = "🟢" if direction == "BUY" else "🔴"

    return (
        "💎 DADAVAI 24H BOT\n"
        "━━━━━━━━━━━━━━\n"
        f"📊 PAIR: {symbol}\n"
        "⏱ TIMEFRAME: M1\n"
        f"📈 SIGNAL: {emoji} {direction}\n"
        f"📊 RSI: {rsi:.2f}\n"
        f"🕒 CANDLE: {candle_time} (UTC+6)\n"
        "━━━━━━━━━━━━━━\n"
        "⚠️ Signals are not guaranteed."
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    CHATS.add(update.effective_chat.id)

    await update.message.reply_text(
        "🤖 DADAVAI 24H BOT\n\n"
        "Signal Engine is active.\n\n"
        "/signal - Live Forex signals\n"
        "/otc - OTC data status\n"
        "/help - Help"
    )


async def signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📊 Checking Forex market data..."
    )

    for symbol in PAIRS:
        try:
            candle_time, direction, rsi = await get_signal(symbol)

            await update.message.reply_text(
                signal_message(
                    symbol, candle_time, direction, rsi
                )
            )

        except Exception as error:
            await update.message.reply_text(
                f"⚠️ {symbol}: Data unavailable.\n"
                f"{str(error)[:150]}"
            )


async def otc_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💎 DADAVAI OTC DATA CHECK\n"
        "━━━━━━━━━━━━━━\n"
        "📊 PAIR: USD/BRL-OTC\n"
        "🔌 PROVIDER: Twelve Data\n"
        "📡 STATUS: UNAVAILABLE\n\n"
        "Twelve Data does not provide "
        "verified Quotex OTC prices.\n\n"
        "⚠️ No unverified OTC signals will be sent."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 DADAVAI 24H BOT HELP\n\n"
        "/start - Start bot\n"
        "/signal - Forex signals\n"
        "/otc - OTC data status\n"
        "/help - Help\n\n"
        "Forex pairs:\n"
        "EUR/USD\n"
        "GBP/USD\n"
        "USD/JPY\n\n"
        "OTC pair:\n"
        "USD/BRL-OTC (data unavailable)"
    )


async def automatic_scan(app):
    while True:
        for symbol in PAIRS:
            try:
                candle_time, direction, rsi = await get_signal(symbol)

                if direction == "WAIT":
                    continue

                if LAST_SIGNAL.get(symbol) == candle_time:
                    continue

                count = SIGNAL_COUNTS.get(candle_time, 0)

                if count >= 2:
                    continue

                LAST_SIGNAL[symbol] = candle_time
                SIGNAL_COUNTS[candle_time] = count + 1

                # Keep recent candle counts only
                if len(SIGNAL_COUNTS) > 10:
                    oldest = next(iter(SIGNAL_COUNTS))
                    del SIGNAL_COUNTS[oldest]

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
    print("DADAVAI NEW BOT VERSION 3 STARTED", flush=True)

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
    app.add_handler(CommandHandler("otc", otc_command))
    app.add_handler(CommandHandler("help", help_command))

    print("DADAVAI 24H BOT started!", flush=True)

    app.run_polling()


if __name__ == "__main__":
    main()
