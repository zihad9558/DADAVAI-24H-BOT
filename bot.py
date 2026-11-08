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
# DADAVAI 24H BOT V5.3 CONFIG
# =====================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_KEY = os.getenv("TWELVE_DATA_KEY")

TZ = ZoneInfo("Asia/Dhaka")

PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY"]

MAX_SIGNALS_PER_MINUTE = 2
EXPIRY_MINUTES = 1

CHATS = set()
PENDING = []
RESULTS = []

LAST_SIGNAL = {}
LAST_SCAN_SLOT = None
SCANNER_TASK = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

log = logging.getLogger(__name__)


# =====================================
# HELPER FUNCTIONS
# =====================================

def now_dhaka():
    return datetime.now(TZ)


def make_signal_id(symbol, signal_time):
    pair = symbol.replace("/", "")
    timestamp = signal_time.strftime("%Y%m%d%H%M%S")
    return f"DAD-{pair}-{timestamp}"


def price_digits(symbol):
    return 3 if "JPY" in symbol else 5


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
        headers={"User-Agent": "DADAVAI-V5.3"}
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.loads(response.read().decode())

    if "values" not in data:
        raise ValueError(
            data.get("message", "Market data unavailable")
        )

    candles = []

    for item in data["values"]:

        candle_time = datetime.strptime(
            item["datetime"],
            "%Y-%m-%d %H:%M:%S"
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
        result = (
            price * multiplier
            + result * (1 - multiplier)
        )

    return result


def ema_series(values, period):

    if len(values) < period:
        return [None] * len(values)

    output = [None] * (period - 1)

    result = sum(values[:period]) / period
    output.append(result)

    multiplier = 2 / (period + 1)

    for price in values[period:]:
        result = (
            price * multiplier
            + result * (1 - multiplier)
        )
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
        result[i] = calculate_rsi(
            values[:i + 1],
            period
        )

    return result


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

    closes = [
        candle["close"]
        for candle in candles
    ]

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


# =====================================
# CHART GENERATOR
# =====================================

def make_chart(candles, symbol, signal, entry=None):

    candles = candles[-60:]

    if len(candles) < 20:
        raise ValueError("Not enough candles for chart")

    closes = [
        candle["close"]
        for candle in candles
    ]

    dates = [
        mdates.date2num(candle["dt"])
        for candle in candles
    ]

    ema9 = ema_series(closes, 9)
    ema21 = ema_series(closes, 21)
    rsis = rsi_series(closes)

    fig, (ax, rax) = plt.subplots(
        2,
        1,
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

        bottom = min(
            candle["open"],
            candle["close"]
        )

        height = abs(
            candle["close"] - candle["open"]
        )

        if height == 0:
            height = 0.000001

        ax.add_patch(
            Rectangle(
                (dates[i] - width / 2, bottom),
                width,
                height,
                facecolor=color,
                edgecolor=color
            )
        )

    ax.plot(
        dates,
        ema9,
        label="EMA 9",
        linewidth=1.3
    )

    ax.plot(
        dates,
        ema21,
        label="EMA 21",
        linewidth=1.3
    )

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
    ax.xaxis.set_major_formatter(
        mdates.DateFormatter("%H:%M")
    )

    rax.plot(
        dates,
        rsis,
        label="RSI 14",
        linewidth=1.3
    )

    rax.axhline(70, linestyle="--", linewidth=0.8)
    rax.axhline(30, linestyle="--", linewidth=0.8)
    rax.axhline(50, linestyle=":", linewidth=0.8)

    rax.set_ylim(0, 100)
    rax.set_ylabel("RSI")
    rax.grid(alpha=0.2)
    rax.legend(loc="upper left")

    rax.xaxis_date()
    rax.xaxis.set_major_formatter(
        mdates.DateFormatter("%H:%M")
    )

    fig.autofmt_xdate()
    fig.tight_layout()

    image = io.BytesIO()

    fig.savefig(
        image,
        format="png",
        dpi=130
    )

    plt.close(fig)

    image.seek(0)
    image.name = "dadavai_chart.png"

    return image


# =====================================
# SEND SIGNAL
# =====================================

async def send_signal_chart(
    app,
    symbol,
    candles,
    signal,
    entry=None,
    recipients=None,
    signal_id=None,
    signal_time=None
):

    targets = (
        list(recipients)
        if recipients is not None
        else list(CHATS)
    )

    if not targets:
        return False

    if signal_time is None:
        signal_time = now_dhaka()

    if signal_id is None:
        signal_id = make_signal_id(
            symbol,
            signal_time
        )

    try:

        photo = await asyncio.to_thread(
            make_chart,
            candles,
            symbol,
            signal,
            entry
        )

    except Exception:

        log.exception("Chart generation failed")
        return False

    direction = signal["direction"]

    emoji = (
        "🟢"
        if direction == "BUY"
        else "🔴"
    )

    caption = (
        "💎 DADAVAI 24H BOT V5.3\n"
        "━━━━━━━━━━━━━━\n"
        f"🆔 SIGNAL ID: {signal_id}\n"
        f"📊 PAIR: {symbol}\n"
        "⏱ TIMEFRAME: M1\n"
        f"📈 SIGNAL: {emoji} {direction}\n"
        f"🎯 CONFLUENCE: {signal['score']}/7\n"
        f"📊 RSI: {signal['rsi']:.2f}\n"
        f"🕒 SIGNAL TIME: {signal_time.strftime('%Y-%m-%d %H:%M:%S')} UTC+6\n"
    )

    if entry:

        digits = price_digits(symbol)

        expiry_time = (
            entry["dt"]
            + timedelta(minutes=EXPIRY_MINUTES)
        )

        caption += (
            f"💰 ENTRY PRICE: {entry['open']:.{digits}f}\n"
            f"🚀 ENTRY TIME: {entry['dt'].strftime('%H:%M:%S')} UTC+6\n"
            f"⏳ EXPIRY TIME: {expiry_time.strftime('%H:%M:%S')} UTC+6\n"
        )

    caption += (
        "━━━━━━━━━━━━━━\n"
        "⚠️ Price-based signals are not guaranteed."
    )

    sent = False

    for chat_id in targets:

        try:

            photo.seek(0)

            await app.bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=caption
            )

            sent = True

        except Exception as error:

            log.warning(
                "Photo send error: %s",
                error
            )

    return sent


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

        entry_price = item["entry_price"]
        exit_price = target["close"]

        if exit_price == entry_price:

            result = "NEUTRAL"

        elif item["direction"] == "BUY":

            result = (
                "WIN"
                if exit_price > entry_price
                else "LOSS"
            )

        else:

            result = (
                "WIN"
                if exit_price < entry_price
                else "LOSS"
            )

        result_data = {
            "signal_id": item["signal_id"],
            "symbol": symbol,
            "direction": item["direction"],
            "entry": entry_price,
            "exit": exit_price,
            "result": result,
            "time": now_dhaka()
        }

        RESULTS.append(result_data)

        wins = sum(
            1 for r in RESULTS
            if r["result"] == "WIN"
        )

        losses = sum(
            1 for r in RESULTS
            if r["result"] == "LOSS"
        )

        neutrals = sum(
            1 for r in RESULTS
            if r["result"] == "NEUTRAL"
        )

        total = wins + losses

        accuracy = (
            wins / total * 100
            if total
            else 0
        )

        digits = price_digits(symbol)

        message = (
            "💎 DADAVAI RESULT V5.3\n"
            "━━━━━━━━━━━━━━\n"
            f"🆔 SIGNAL ID: {item['signal_id']}\n"
            f"📊 PAIR: {symbol}\n"
            f"📈 SIGNAL: {item['direction']}\n"
            f"💰 ENTRY: {entry_price:.{digits}f}\n"
            f"💰 EXIT: {exit_price:.{digits}f}\n"
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

                log.warning(
                    "Result send error: %s",
                    error
                )

    PENDING = remaining


# =====================================
# AUTOMATIC SCANNER
# =====================================

async def scan_once(app):

    global LAST_SCAN_SLOT

    now = now_dhaka()

    slot = now.strftime("%Y-%m-%d %H:%M")

    if LAST_SCAN_SLOT == slot:
        return

    LAST_SCAN_SLOT = slot

    candidates = []

    for symbol in PAIRS:

        try:

            candles = await asyncio.to_thread(
                fetch_market_data,
                symbol
            )

            completed = [
                candle for candle in candles
                if candle["dt"] + timedelta(minutes=1) <= now
            ]

            await evaluate_results(
                app,
                symbol,
                completed
            )

            if len(completed) < 60:
                continue

            signal = analyze(completed)

            if signal is None:
                continue

            last_closed = completed[-1]

            signal_key = (
                symbol,
                last_closed["dt"]
            )

            if LAST_SIGNAL.get(symbol) == signal_key:
                continue

            entry_time = (
                last_closed["dt"]
                + timedelta(minutes=1)
            )

            entry = next(
                (
                    candle for candle in candles
                    if candle["dt"] == entry_time
                ),
                None
            )

            if entry is None:
                continue

            entry_age = (
                now - entry["dt"]
            ).total_seconds()

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

            log.warning(
                "Market error %s: %s",
                symbol,
                error
            )

    candidates.sort(
        key=lambda item: item["signal"]["score"],
        reverse=True
    )

    for item in candidates[:MAX_SIGNALS_PER_MINUTE]:

        symbol = item["symbol"]
        signal = item["signal"]
        entry = item["entry"]

        signal_time = now_dhaka()

        signal_id = make_signal_id(
            symbol,
            signal_time
        )

        sent = await send_signal_chart(
            app,
            symbol,
            item["candles"],
            signal,
            entry,
            signal_id=signal_id,
            signal_time=signal_time
        )

        LAST_SIGNAL[symbol] = item["signal_key"]

        if sent:

            PENDING.append({
                "signal_id": signal_id,
                "symbol": symbol,
                "direction": signal["direction"],
                "entry_price": entry["open"],
                "entry_dt": entry["dt"],
                "signal_time": signal_time,
                "chats": list(CHATS)
            })

        log.info(
            "SIGNAL SENT: %s %s",
            symbol,
            signal["direction"]
        )


async def automatic_scan(app):

    log.info("DADAVAI V5.3 SCANNER STARTED")

    while True:

        now = now_dhaka()

        seconds = 60 - now.second + 2

        await asyncio.sleep(seconds)

        try:

            await scan_once(app)

        except asyncio.CancelledError:

            raise

        except Exception:

            log.exception(
                "Automatic scanner error"
            )

            await asyncio.sleep(5)


# =====================================
# TELEGRAM COMMANDS
# =====================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    chat_id = update.effective_chat.id

    CHATS.add(chat_id)

    await update.message.reply_text(
        "💎 DADAVAI 24H BOT V5.3\n\n"
        "✅ Live Forex M1 Engine\n"
        "✅ Automatic BUY / SELL\n"
        "✅ Candlestick Chart Images\n"
        "✅ Entry Price & Signal Time\n"
        "✅ 1 Minute Expiry Display\n"
        "✅ Automatic Price-based Results\n"
        "✅ Improved Performance Statistics\n"
        "✅ Maximum 2 signals per minute\n\n"
        "/signal - Check market\n"
        "/stats - Performance\n"
        "/help - Help"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "💎 DADAVAI BOT V5.3 HELP\n\n"
        "/start - Activate bot\n"
        "/signal - Check current market with chart\n"
        "/stats - WIN/LOSS statistics\n"
        "/help - Help\n\n"
        "Signals are based on Forex market data.\n"
        "Results are price-based, not broker settlement."
    )


async def signal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🔍 Checking live Forex data..."
    )

    found = []

    for symbol in PAIRS:

        try:

            candles = await asyncio.to_thread(
                fetch_market_data,
                symbol
            )

            now = now_dhaka()

            completed = [
                candle for candle in candles
                if candle["dt"] + timedelta(minutes=1) <= now
            ]

            signal = analyze(completed)

            if signal:

                found.append((
                    signal["score"],
                    symbol,
                    signal,
                    completed
                ))

        except Exception as error:

            log.warning(
                "Manual scan error %s: %s",
                symbol,
                error
            )

    found.sort(
        key=lambda item: item[0],
        reverse=True
    )

    if not found:

        await update.message.reply_text(
            "⏳ No qualified signal right now.\n"
            "Please wait for the next setup."
        )

        return

    for _, symbol, signal, candles in found[:2]:

        signal_time = now_dhaka()

        signal_id = make_signal_id(
            symbol,
            signal_time
        )

        await send_signal_chart(
            context.application,
            symbol,
            candles,
            signal,
            recipients=[update.effective_chat.id],
            signal_id=signal_id,
            signal_time=signal_time
        )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    wins = sum(
        1 for r in RESULTS
        if r["result"] == "WIN"
    )

    losses = sum(
        1 for r in RESULTS
        if r["result"] == "LOSS"
    )

    neutrals = sum(
        1 for r in RESULTS
        if r["result"] == "NEUTRAL"
    )

    total = wins + losses

    accuracy = (
        wins / total * 100
        if total
        else 0
    )

    pending_count = len(PENDING)

    await update.message.reply_text(
        "💎 DADAVAI PERFORMANCE V5.3\n"
        "━━━━━━━━━━━━━━\n"
        f"🟢 WIN: {wins}\n"
        f"🔴 LOSS: {losses}\n"
        f"⚪ NEUTRAL: {neutrals}\n"
        f"📊 COMPLETED: {len(RESULTS)}\n"
        f"⏳ PENDING: {pending_count}\n"
        f"🎯 ACCURACY: {accuracy:.2f}%\n"
        "━━━━━━━━━━━━━━\n"
        "Accuracy excludes NEUTRAL results.\n"
        "Statistics reset when bot restarts."
    )


# =====================================
# APPLICATION LIFECYCLE
# =====================================

async def post_init(app):

    global SCANNER_TASK

    print("DADAVAI V5.3 STARTED")

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

    print("DADAVAI V5.3 STOPPED")


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

    print("DADAVAI V5.3 BOT READY")

    app.run_polling()


if __name__ == "__main__":
    main()
