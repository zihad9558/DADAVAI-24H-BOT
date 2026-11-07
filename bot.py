import os
import asyncio
import urllib.parse
import urllib.request
import json

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# =========================
# CONFIGURATION
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_KEY = os.getenv("TWELVE_DATA_KEY")

TZ = ZoneInfo("Asia/Dhaka")

PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY"]

CHATS = set()
PENDING = []
RESULTS = []

LAST_SIGNAL = {}
SENT_PER_MINUTE = {}

MAX_SIGNALS_PER_MINUTE = 2


# =========================
# MARKET DATA
# =========================

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
        headers={"User-Agent": "Mozilla/5.0"}
    )

    with urllib.request.urlopen(
        request, timeout=15
    ) as response:

        data = json.loads(response.read())

    if "values" not in data:
        raise ValueError(
            data.get("message", "Market data unavailable")
        )

    candles = []

    for item in data["values"]:

        candles.append({
            "dt": datetime.strptime(
                item["datetime"],
                "%Y-%m-%d %H:%M:%S"
            ),
            "open": float(item["open"]),
            "high": float(item["high"]),
            "low": float(item["low"]),
            "close": float(item["close"])
        })

    candles.sort(key=lambda x: x["dt"])

    return candles


# =========================
# INDICATORS
# =========================

def ema(values, period):

    if len(values) < period:
        return None

    result = sum(values[:period]) / period

    multiplier = 2 / (period + 1)

    for price in values[period:]:

        result = (
            price * multiplier
            + result * (1 - multiplier)
        )

    return result


def calculate_rsi(values, period=14):

    if len(values) < period + 1:
        return None

    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    gains = [
        max(change, 0)
        for change in changes[-period:]
    ]

    losses = [
        max(-change, 0)
        for change in changes[-period:]
    ]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


def calculate_macd_histogram(values):

    if len(values) < 35:
        return None

    macd_values = []

    for i in range(26, len(values) + 1):

        subset = values[:i]

        macd_line = (
            ema(subset, 12)
            - ema(subset, 26)
        )

        macd_values.append(macd_line)

    signal = ema(macd_values, 9)

    return macd_values[-1] - signal


# =========================
# SIGNAL ENGINE
# =========================

def analyze(candles):

    if len(candles) < 60:
        return None

    closes = [c["close"] for c in candles]

    fast = ema(closes, 9)
    medium = ema(closes, 21)
    slow = ema(closes, 50)

    rsi = calculate_rsi(closes)
    macd = calculate_macd_histogram(closes)

    if None in (fast, medium, slow, rsi, macd):
        return None

    last = candles[-1]
    previous = candles[-2]

    buy = 0
    sell = 0

    # 1. EMA 9 / 21
    if fast > medium:
        buy += 1
    elif fast < medium:
        sell += 1

    # 2. EMA 21 / 50
    if medium > slow:
        buy += 1
    elif medium < slow:
        sell += 1

    # 3. RSI
    if 50 < rsi < 70:
        buy += 1
    elif 30 < rsi < 50:
        sell += 1

    # 4. MACD
    if macd > 0:
        buy += 1
    elif macd < 0:
        sell += 1

    # 5. Price vs EMA 9
    if last["close"] > fast:
        buy += 1
    elif last["close"] < fast:
        sell += 1

    # 6. Candle direction
    if last["close"] > last["open"]:
        buy += 1
    elif last["close"] < last["open"]:
        sell += 1

    # 7. Price structure
    if (
        last["high"] > previous["high"]
        and last["low"] > previous["low"]
    ):
        buy += 1

    elif (
        last["high"] < previous["high"]
        and last["low"] < previous["low"]
    ):
        sell += 1

    if buy >= 5 and buy > sell:

        return {
            "direction": "BUY",
            "score": buy,
            "rsi": rsi,
            "macd": macd
        }

    if sell >= 5 and sell > buy:

        return {
            "direction": "SELL",
            "score": sell,
            "rsi": rsi,
            "macd": macd
        }

    return None


# =========================
# TELEGRAM MESSAGES
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    chat_id = update.effective_chat.id

    CHATS.add(chat_id)

    await update.message.reply_text(
        "💎 DADAVAI 24H BOT V5\n\n"
        "✅ Live Forex Signal Engine\n"
        "✅ M1 BUY / SELL\n"
        "✅ Automatic WIN / LOSS tracking\n\n"
        "Commands:\n"
        "/signal - Current signal\n"
        "/stats - Results\n"
        "/help - Help"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "💎 DADAVAI BOT HELP\n\n"
        "/start - Activate bot\n"
        "/signal - Check signals\n"
        "/stats - WIN/LOSS statistics\n"
        "/help - Help"
    )


async def send_signal(app, symbol, signal, entry):

    direction = signal["direction"]

    message = (
        "💎 DADAVAI 24H BOT V5\n"
        "━━━━━━━━━━━━━━\n"
        f"📊 PAIR: {symbol}\n"
        "⏱ TIMEFRAME: M1\n"
        f"📈 SIGNAL: {'🟢' if direction == 'BUY' else '🔴'} {direction}\n"
        f"🎯 CONFLUENCE: {signal['score']}/7\n"
        f"📊 RSI: {signal['rsi']:.2f}\n"
        f"📉 MACD HIST: {signal['macd']:.6f}\n"
        f"💰 ENTRY PRICE: {entry['open']}\n"
        f"🕒 ENTRY: {entry['dt'].strftime('%Y-%m-%d %H:%M:%S')} (UTC+6)\n"
        "⏳ EXPIRY: 1 MINUTE\n"
        "━━━━━━━━━━━━━━\n"
        "📌 Result will be checked automatically.\n"
        "⚠️ Confluence is not a guaranteed win probability."
    )

    for chat_id in list(CHATS):

        try:
            await app.bot.send_message(
                chat_id=chat_id,
                text=message
            )

        except Exception as error:
            print("Telegram send error:", error)


# =========================
# RESULT TRACKER
# =========================

async def check_results(app, candles, symbol):

    global PENDING

    remaining = []

    for item in PENDING:

        if item["symbol"] != symbol:
            remaining.append(item)
            continue

        completed = next(
            (
                c for c in candles[:-1]
                if c["dt"] == item["entry_dt"]
            ),
            None
        )

        if completed is None:
            remaining.append(item)
            continue

        entry_price = item["entry_price"]
        exit_price = completed["close"]

        if item["direction"] == "BUY":

            if exit_price > entry_price:
                result = "WIN"
            elif exit_price < entry_price:
                result = "LOSS"
            else:
                result = "NEUTRAL"

        else:

            if exit_price < entry_price:
                result = "WIN"
            elif exit_price > entry_price:
                result = "LOSS"
            else:
                result = "NEUTRAL"

        RESULTS.append(result)

        wins = RESULTS.count("WIN")
        losses = RESULTS.count("LOSS")
        neutrals = RESULTS.count("NEUTRAL")

        total = wins + losses

        accuracy = (
            wins / total * 100
            if total else 0
        )

        message = (
            "💎 DADAVAI RESULT\n"
            "━━━━━━━━━━━━━━\n"
            f"📊 PAIR: {symbol}\n"
            f"📈 SIGNAL: {item['direction']}\n"
            f"💰 ENTRY: {entry_price}\n"
            f"💰 EXIT: {exit_price}\n"
            f"🏆 RESULT: {result}\n"
            "━━━━━━━━━━━━━━\n"
            f"🟢 WIN: {wins}\n"
            f"🔴 LOSS: {losses}\n"
            f"⚪ NEUTRAL: {neutrals}\n"
            f"🎯 ACCURACY: {accuracy:.2f}%\n"
            "━━━━━━━━━━━━━━\n"
            "Price-based result, not broker settlement."
        )

        for chat_id in list(CHATS):

            try:
                await app.bot.send_message(
                    chat_id=chat_id,
                    text=message
                )

            except Exception as error:
                print("Result send error:", error)

    PENDING = remaining


# =========================
# MANUAL SIGNAL
# =========================

async def signal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🔍 Checking live Forex data..."
    )

    for symbol in PAIRS:

        try:

            candles = await asyncio.to_thread(
                fetch_market_data,
                symbol
            )

            completed = candles[:-1]

            signal = analyze(completed)

            if signal:

                entry = candles[-1]

                await send_signal(
                    context.application,
                    symbol,
                    signal,
                    entry
                )

                return

        except Exception as error:
            print(symbol, error)

    await update.message.reply_text(
        "⏳ No verified signal right now.\n"
        "Please wait for the next setup."
    )


# =========================
# STATISTICS
# =========================

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    wins = RESULTS.count("WIN")
    losses = RESULTS.count("LOSS")
    neutrals = RESULTS.count("NEUTRAL")

    total = wins + losses

    accuracy = (
        wins / total * 100
        if total else 0
    )

    await update.message.reply_text(
        "💎 DADAVAI PERFORMANCE\n"
        "━━━━━━━━━━━━━━\n"
        f"🟢 WIN: {wins}\n"
        f"🔴 LOSS: {losses}\n"
        f"⚪ NEUTRAL: {neutrals}\n"
        f"📊 TOTAL: {len(RESULTS)}\n"
        f"🎯 ACCURACY: {accuracy:.2f}%\n"
        "━━━━━━━━━━━━━━\n"
        "Statistics reset when the bot restarts."
    )


# =========================
# AUTOMATIC SCANNER
# =========================

async def automatic_scan(app):

    while True:

        now = datetime.now(TZ)

        delay = 60 - now.second + 2

        await asyncio.sleep(delay)

        minute_key = datetime.now(TZ).strftime(
            "%Y-%m-%d %H:%M"
        )

        SENT_PER_MINUTE[minute_key] = 0

        for symbol in PAIRS:

            try:

                candles = await asyncio.to_thread(
                    fetch_market_data,
                    symbol
                )

                if len(candles) < 60:
                    continue

                # First evaluate previous signals
                await check_results(
                    app,
                    candles,
                    symbol
                )

                completed = candles[:-1]
                entry = candles[-1]

                signal = analyze(completed)

                if not signal:
                    continue

                signal_key = (
                    symbol,
                    completed[-1]["dt"]
                )

                if LAST_SIGNAL.get(symbol) == signal_key:
                    continue

                if SENT_PER_MINUTE[minute_key] >= MAX_SIGNALS_PER_MINUTE:
                    continue

                LAST_SIGNAL[symbol] = signal_key

                PENDING.append({
                    "symbol": symbol,
                    "direction": signal["direction"],
                    "entry_price": entry["open"],
                    "entry_dt": entry["dt"]
                })

                SENT_PER_MINUTE[minute_key] += 1

                await send_signal(
                    app,
                    symbol,
                    signal,
                    entry
                )

            except Exception as error:

                print(
                    "Scanner error:",
                    symbol,
                    error
                )


# =========================
# STARTUP
# =========================

async def post_init(app):

    print("DADAVAI V5 STARTED")

    app.create_task(
        automatic_scan(app)
    )


def main():

    if not BOT_TOKEN or not API_KEY:

        raise RuntimeError(
            "BOT_TOKEN or TWELVE_DATA_KEY missing"
        )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler("signal", signal_command)
    )

    app.add_handler(
        CommandHandler("stats", stats_command)
    )

    app.add_handler(
        CommandHandler("help", help_command)
    )

    print("DADAVAI V5 BOT READY")

    app.run_polling()


if __name__ == "__main__":
    main()
