import os
import json
import asyncio
import logging
import urllib.parse
import urllib.request
import io

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# =====================================
# CONFIG
# =====================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_KEY = os.getenv("TWELVE_DATA_KEY")

TZ = ZoneInfo("Asia/Dhaka")

PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY"]

CHATS = set()
PENDING = []
RESULTS = []

LAST_SIGNAL = {}
LAST_SCAN_SLOT = None
SCANNER_TASK = None

MAX_SIGNALS_PER_MINUTE = 2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger(__name__)


# =====================================
# MARKET DATA
# =====================================

def fetch_market_data(symbol):

    params = urllib.parse.urlencode({
        "symbol": symbol,
        "interval": "1min",
        "outputsize": 100,
        "timezone": "Asia/Dhaka",
        "apikey": API_KEY
    })

    url = "https://api.twelvedata.com/time_series?" + params

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "DADAVAI-V5.2"}
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.loads(response.read().decode())

    if "values" not in data:
        raise ValueError(data.get("message", "Market data unavailable"))

    candles = []

    for item in data["values"]:
        candle_time = datetime.strptime(
            item["datetime"], "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=TZ)

        candles.append({
            "dt": candle_time,
            "open": float(item["open"]),
            "high": float(item["high"]),
            "low": float(item["low"]),
            "close": float(item["close"])
        })

    candles.sort(key=lambda candle: candle["dt"])
    return candles


# =====================================
# INDICATORS
# =====================================

def ema(values, period):

    if len(values) < period:
        return None

    result = sum(values[:period]) / period
    multiplier = 2 / (period + 1)

    for price in values[period:]:
        result = price * multiplier + result * (1 - multiplier)

    return result


def ema_series(values, period):

    if len(values) < period:
        return [None] * len(values)

    output = [None] * (period - 1)
    result = sum(values[:period]) / period
    output.append(result)

    multiplier = 2 / (period + 1)

    for price in values[period:]:
        result = price * multiplier + result * (1 - multiplier)
        output.append(result)

    return output


def calculate_rsi(values, period=14):

    if len(values) < period + 1:
        return None

    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    recent = changes[-period:]

    gains = [max(change, 0) for change in recent]
    losses = [max(-change, 0) for change in recent]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def rsi_series(values, period=14):

    result = [None] * len(values)

    for i in range(period, len(values)):
        result[i] = calculate_rsi(values[:i + 1], period)

    return result


def calculate_macd_histogram(values):

    if len(values) < 35:
        return None

    macd_values = []

    for i in range(26, len(values) + 1):
        subset = values[:i]
        macd_line = ema(subset, 12) - ema(subset, 26)
        macd_values.append(macd_line)

    signal_line = ema(macd_values, 9)

    if signal_line is None:
        return None

    return macd_values[-1] - signal_line


# =====================================
# SIGNAL ENGINE
# =====================================

def analyze(candles):

    if len(candles) < 60:
        return None

    closes = [candle["close"] for candle in candles]

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

    if fast > medium:
        buy += 1
    elif fast < medium:
        sell += 1

    if medium > slow:
        buy += 1
    elif medium < slow:
        sell += 1

    if 50 < rsi < 70:
        buy += 1
    elif 30 < rsi < 50:
        sell += 1

    if macd > 0:
        buy += 1
    elif macd < 0:
        sell += 1

    if last["close"] > fast:
        buy += 1
    elif last["close"] < fast:
        sell += 1

    if last["close"] > last["open"]:
        buy += 1
    elif last["close"] < last["open"]:
        sell += 1

    if last["high"] > previous["high"] and last["low"] > previous["low"]:
        buy += 1
    elif last["high"] < previous["high"] and last["low"] < previous["low"]:
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


# =====================================
# CHART GENERATOR
# =====================================

def make_chart(candles, symbol, signal, entry=None):

    candles = candles[-60:]

    if len(candles) < 20:
        raise ValueError("Not enough candles for chart")

    closes = [c["close"] for c in candles]
    dates = [mdates.date2num(c["dt"]) for c in candles]

    ema9 = ema_series(closes, 9)
    ema21 = ema_series(closes, 21)
    rsis = rsi_series(closes)

    fig, (ax, rax) = plt.subplots(
        2, 1,
        figsize=(12, 8),
        gridspec_kw={"height_ratios": [3, 1]}
    )

    fig.suptitle(
        f"DADAVAI 24H | {symbol} | M1 | {signal['direction']}",
        fontsize=15,
        fontweight="bold"
    )

    width = 0.00045

    for i, candle in enumerate(candles):

        color = (
            "#00c853"
            if candle["close"] >= candle["open"]
            else "#ff1744"
        )

        ax.plot(
            [dates[i], dates[i]],
            [candle["low"], candle["high"]],
            color=color,
            linewidth=1
        )

        bottom = min(candle["open"], candle["close"])
        height = abs(candle["close"] - candle["open"])

        if height == 0:
            height = 0.000001

        ax.add_patch(Rectangle(
            (dates[i] - width / 2, bottom),
            width,
            height,
            facecolor=color,
            edgecolor=color
        ))

    ax.plot(dates, ema9, label="EMA 9", linewidth=1.3)
    ax.plot(dates, ema21, label="EMA 21", linewidth=1.3)

    if entry:
        ax.axhline(
            entry["open"],
            linestyle="--",
            linewidth=1,
            label="Entry price"
        )

    marker_index = len(candles) - 1
    marker_price = candles[-1]["close"]

    if signal["direction"] == "BUY":
        ax.scatter(
            dates[marker_index],
            marker_price,
            marker="^",
            s=180,
            color="green",
            label="BUY"
        )
    else:
        ax.scatter(
            dates[marker_index],
            marker_price,
            marker="v",
            s=180,
            color="red",
            label="SELL"
        )

    ax.set_ylabel("Price")
    ax.legend(loc="upper left")
    ax.grid(alpha=0.2)
    ax.xaxis_date()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

    rax.plot(dates, rsis, label="RSI 14", linewidth=1.3)
    rax.axhline(70, linestyle="--", linewidth=0.8)
    rax.axhline(30, linestyle="--", linewidth=0.8)
    rax.axhline(50, linestyle=":", linewidth=0.8)
    rax.set_ylim(0, 100)
    rax.set_ylabel("RSI")
    rax.grid(alpha=0.2)
    rax.legend(loc="upper left")
    rax.xaxis_date()
    rax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

    fig.autofmt_xdate()
    fig.tight_layout()

    image = io.BytesIO()
    fig.savefig(image, format="png", dpi=130)
    plt.close(fig)

    image.seek(0)
    image.name = "dadavai_chart.png"

    return image


async def send_signal_chart(
    app, symbol, candles, signal, entry=None, recipients=None
):

    targets = list(recipients) if recipients is not None else list(CHATS)

    try:
        photo = await asyncio.to_thread(
            make_chart, candles, symbol, signal, entry
        )
    except Exception:
        log.exception("Chart generation failed")
        return

    direction = signal["direction"]
    emoji = "🟢" if direction == "BUY" else "🔴"

    caption = (
        f"💎 DADAVAI 24H BOT\n"
        f"📊 PAIR: {symbol}\n"
        f"⏱ TIMEFRAME: M1\n"
        f"📈 SIGNAL: {emoji} {direction}\n"
        f"🎯 CONFLUENCE: {signal['score']}/7\n"
        f"📊 RSI: {signal['rsi']:.2f}\n"
    )

    if entry:
        caption += (
            f"💰 ENTRY: {entry['open']}\n"
            f"🕒 TIME: {entry['dt'].strftime('%Y-%m-%d %H:%M:%S')} UTC+6\n"
            "⏳ EXPIRY: 1 MINUTE\n"
        )

    caption += (
        "⚠️ Chart uses available market candles. "
        "Signals are not guaranteed."
    )

    for chat_id in targets:
        try:
            photo.seek(0)
            await app.bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=caption
            )
        except Exception as error:
            log.warning("Photo send error: %s", error)


# =====================================
# RESULT TRACKER
# =====================================

async def evaluate_results(app, symbol, completed):

    global PENDING

    remaining = []

    for item in PENDING:

        if item["symbol"] != symbol:
            remaining.append(item)
            continue

        target = next(
            (
                candle for candle in completed
                if candle["dt"] == item["entry_dt"]
            ),
            None
        )

        if target is None:
            remaining.append(item)
            continue

        entry = item["entry_price"]
        exit_price = target["close"]

        if exit_price == entry:
            result = "NEUTRAL"
        elif item["direction"] == "BUY":
            result = "WIN" if exit_price > entry else "LOSS"
        else:
            result = "WIN" if exit_price < entry else "LOSS"

        RESULTS.append(result)

        wins = RESULTS.count("WIN")
        losses = RESULTS.count("LOSS")
        neutrals = RESULTS.count("NEUTRAL")

        total = wins + losses
        accuracy = wins / total * 100 if total else 0

        message = (
            "💎 DADAVAI RESULT 💎\n"
            "━━━━━━━━━━━━━━\n"
            f"📊 PAIR: {symbol}\n"
            f"📈 SIGNAL: {item['direction']}\n"
            f"💰 ENTRY: {entry}\n"
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

        for chat_id in item["chats"]:
            try:
                await app.bot.send_message(
                    chat_id=chat_id,
                    text=message
                )
            except Exception as error:
                log.warning("Result send error: %s", error)

    PENDING = remaining


# =====================================
# AUTOMATIC SCANNER
# =====================================

async def scan_once(app):

    global LAST_SCAN_SLOT

    now = datetime.now(TZ)
    slot = now.strftime("%Y-%m-%d %H:%M")

    if LAST_SCAN_SLOT == slot:
        return

    LAST_SCAN_SLOT = slot
    candidates = []

    for symbol in PAIRS:

        try:
            candles = await asyncio.to_thread(
                fetch_market_data, symbol
            )

            completed = [
                candle for candle in candles
                if candle["dt"] + timedelta(minutes=1) <= now
            ]

            await evaluate_results(app, symbol, completed)

            if len(completed) < 60:
                continue

            signal = analyze(completed)

            if signal is None:
                continue

            last_closed = completed[-1]
            signal_key = (symbol, last_closed["dt"])

            if LAST_SIGNAL.get(symbol) == signal_key:
                continue

            entry_time = last_closed["dt"] + timedelta(minutes=1)

            entry = next(
                (
                    candle for candle in candles
                    if candle["dt"] == entry_time
                ),
                None
            )

            if entry is None:
                continue

            entry_age = (now - entry["dt"]).total_seconds()

            if entry_age < 0 or entry_age > 45:
                continue

            candidates.append({
                "symbol": symbol,
                "signal": signal,
                "entry": entry,
                "candles": completed,
                "signal_key": signal_key
            })

        except Exception as error:
            log.warning("Market error %s: %s", symbol, error)

    candidates.sort(
        key=lambda item: item["signal"]["score"],
        reverse=True
    )

    for item in candidates[:MAX_SIGNALS_PER_MINUTE]:

        symbol = item["symbol"]
        signal = item["signal"]
        entry = item["entry"]

        LAST_SIGNAL[symbol] = item["signal_key"]

        PENDING.append({
            "symbol": symbol,
            "direction": signal["direction"],
            "entry_price": entry["open"],
            "entry_dt": entry["dt"],
            "chats": list(CHATS)
        })

        await send_signal_chart(
            app,
            symbol,
            item["candles"],
            signal,
            entry
        )

        log.info("SIGNAL SENT: %s %s", symbol, signal["direction"])


async def automatic_scan(app):

    log.info("DADAVAI V5.2 SCANNER STARTED")

    while True:

        now = datetime.now(TZ)
        seconds = 60 - now.second + 2

        await asyncio.sleep(seconds)

        try:
            await scan_once(app)

        except asyncio.CancelledError:
            raise

        except Exception:
            log.exception("Automatic scanner error")
            await asyncio.sleep(5)


# =====================================
# TELEGRAM COMMANDS
# =====================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    chat_id = update.effective_chat.id
    CHATS.add(chat_id)

    await update.message.reply_text(
        "💎 DADAVAI 24H BOT V5.2\n\n"
        "✅ Live Forex M1 Engine\n"
        "✅ Automatic BUY / SELL\n"
        "✅ Signal chart images\n"
        "✅ Automatic WIN / LOSS\n"
        "✅ Maximum 2 signals per minute\n\n"
        "/signal - Check market\n"
        "/stats - Performance\n"
        "/help - Help"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "💎 DADAVAI BOT HELP\n\n"
        "/start - Activate bot\n"
        "/signal - Check current market with chart\n"
        "/stats - WIN/LOSS statistics\n"
        "/help - Help"
    )


async def signal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🔍 Checking live Forex data..."
    )

    found = []

    for symbol in PAIRS:

        try:
            candles = await asyncio.to_thread(
                fetch_market_data, symbol
            )

            now = datetime.now(TZ)

            completed = [
                candle for candle in candles
                if candle["dt"] + timedelta(minutes=1) <= now
            ]

            signal = analyze(completed)

            if signal:
                found.append((signal["score"], symbol, signal, completed))

        except Exception as error:
            log.warning("Manual scan error %s: %s", symbol, error)

    found.sort(key=lambda item: item[0], reverse=True)

    if not found:
        await update.message.reply_text(
            "⏳ No qualified signal right now.\n"
            "Please wait for the next setup."
        )
        return

    for _, symbol, signal, candles in found[:2]:

        await send_signal_chart(
            context.application,
            symbol,
            candles,
            signal,
            recipients=[update.effective_chat.id]
        )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    wins = RESULTS.count("WIN")
    losses = RESULTS.count("LOSS")
    neutrals = RESULTS.count("NEUTRAL")

    total = wins + losses
    accuracy = wins / total * 100 if total else 0

    await update.message.reply_text(
        "💎 DADAVAI PERFORMANCE\n"
        "━━━━━━━━━━━━━━\n"
        f"🟢 WIN: {wins}\n"
        f"🔴 LOSS: {losses}\n"
        f"⚪ NEUTRAL: {neutrals}\n"
        f"📊 TOTAL: {len(RESULTS)}\n"
        f"🎯 ACCURACY: {accuracy:.2f}%\n"
        "━━━━━━━━━━━━━━\n"
        "Statistics reset when bot restarts."
    )


# =====================================
# APPLICATION LIFECYCLE
# =====================================

async def post_init(app):

    global SCANNER_TASK

    print("DADAVAI V5.2 STARTED")

    SCANNER_TASK = asyncio.create_task(
        automatic_scan(app)
    )


async def post_shutdown(app):

    global SCANNER_TASK

    if SCANNER_TASK is not None:
        SCANNER_TASK.cancel()

        try:
            await SCANNER_TASK
        except asyncio.CancelledError:
            pass

    print("DADAVAI V5.2 STOPPED")


# =====================================
# MAIN
# =====================================

def main():

    if not BOT_TOKEN or not API_KEY:
        raise RuntimeError(
            "BOT_TOKEN or TWELVE_DATA_KEY missing"
        )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("signal", signal_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("help", help_command))

    print("DADAVAI V5.2 BOT READY")

    app.run_polling()


if __name__ == "__main__":
    main()
