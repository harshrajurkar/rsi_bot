import os
import time
import threading
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

from flask import Flask
import requests
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

API_KEY = os.getenv("TWELVE_DATA_API_KEY")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SYMBOL = os.getenv("SYMBOL", "XAU/USD")
INTERVAL = os.getenv("INTERVAL", "5min")

RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
OVERBOUGHT = float(os.getenv("OVERBOUGHT", "70"))
OVERSOLD = float(os.getenv("OVERSOLD", "30"))
CHECK_SECONDS = int(os.getenv("CHECK_SECONDS", "60"))
CLOSED_CHECK_SECONDS = int(os.getenv("CLOSED_CHECK_SECONDS", "3600"))
MARKET_BOUNDARY_TIME = dt_time(3, 32)
IST = ZoneInfo("Asia/Kolkata")
bot_started_at = datetime.now(IST)

last_alerted_candle = {
    "overbought": None,
    "oversold": None,
}
last_error_notice = None
last_update_id = None
startup_rsi_sent = False
latest_status = {
    "state": "starting",
    "market": "unknown",
    "candle_time": None,
    "price": None,
    "rsi": None,
    "signal": "none",
    "checked_at": None,
    "error": None,
}


@app.route("/")
def home():
    return "Bot running"


def run_web():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, use_reloader=False)


def start_web_server():
    threading.Thread(target=run_web, daemon=True).start()


def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message}
    response = requests.post(url, json=payload, timeout=10)
    response.raise_for_status()


def telegram_request(method, payload=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    response = requests.post(url, json=payload or {}, timeout=35)
    response.raise_for_status()
    return response.json()


def notify_error(message: str):
    global last_error_notice

    if message == last_error_notice:
        return

    try:
        send_telegram(f"[ERROR] RSI bot\n{message}")
        last_error_notice = message
    except Exception as telegram_error:
        print(f"Telegram error notice failed: {telegram_error}")


def format_rsi_status(prefix="[STATUS]"):
    uptime_seconds = int((datetime.now(IST) - bot_started_at).total_seconds())
    checked_at = latest_status["checked_at"] or "not checked yet"
    candle_time = latest_status["candle_time"] or "not available"
    price = latest_status["price"]
    rsi = latest_status["rsi"]
    price_text = "not available" if price is None else f"{price}"
    rsi_text = "not available" if rsi is None else f"{rsi:.2f}"
    error = latest_status["error"] or "none"

    return (
        f"{prefix}\n"
        f"State: {latest_status['state']}\n"
        f"Uptime: {uptime_seconds}s\n"
        f"Market: {latest_status['market']}\n"
        f"Symbol: {SYMBOL}\n"
        f"Timeframe: {INTERVAL}\n"
        f"SELL: RSI >= {OVERBOUGHT}\n"
        f"BUY: RSI <= {OVERSOLD}\n"
        f"Check every: {CHECK_SECONDS}s\n"
        f"Last candle: {candle_time}\n"
        f"Price: {price_text}\n"
        f"RSI: {rsi_text}\n"
        f"Last signal zone: {latest_status['signal']}\n"
        f"Last check: {checked_at}\n"
        f"Last error: {error}"
    )


def handle_command(text):
    command = text.split()[0].lower()

    if command in ("/start", "/help"):
        return (
            "RSI alert bot commands:\n"
            "/status - show bot, market, price, and RSI status\n"
            "/help - show commands"
        )

    if command == "/status":
        return format_rsi_status()

    return "Unknown command. Send /help."


def process_update(update):
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    text = (message.get("text") or "").strip()

    if not text:
        return

    if str(chat.get("id")) != str(CHAT_ID):
        return

    try:
        send_telegram(handle_command(text))
    except Exception as error:
        print(f"Telegram command failed: {error}")


def poll_telegram_commands():
    global last_update_id

    try:
        response = telegram_request("getUpdates", {"timeout": 0})
        updates = response.get("result", [])
        if updates:
            last_update_id = updates[-1]["update_id"]
    except Exception as error:
        print(f"Telegram polling init error: {error}")

    while True:
        try:
            payload = {"timeout": 30}
            if last_update_id is not None:
                payload["offset"] = last_update_id + 1

            response = telegram_request("getUpdates", payload)
            for update in response.get("result", []):
                last_update_id = update["update_id"]
                process_update(update)
        except Exception as error:
            print(f"Telegram polling error: {error}")
            time.sleep(10)


def start_command_listener():
    threading.Thread(target=poll_telegram_commands, daemon=True).start()


def market_is_open():
    now = datetime.now(IST)
    weekday = now.weekday()
    current_time = now.time()

    if weekday == 5 and current_time >= MARKET_BOUNDARY_TIME:
        return False
    if weekday == 6:
        return False
    if weekday == 0 and current_time < MARKET_BOUNDARY_TIME:
        return False

    return True


def fetch_closes():
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "outputsize": max(100, RSI_PERIOD + 5),
        "apikey": API_KEY,
    }

    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    data = response.json()

    if "values" not in data:
        raise Exception(f"API error: {data}")

    values = sorted(data["values"], key=lambda x: x["datetime"])
    closes = [float(candle["close"]) for candle in values]
    times = [candle["datetime"] for candle in values]
    return closes, times


def calculate_rsi_series(closes):
    if len(closes) < RSI_PERIOD + 1:
        return []

    gains = []
    losses = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains[:RSI_PERIOD]) / RSI_PERIOD
    avg_loss = sum(losses[:RSI_PERIOD]) / RSI_PERIOD

    rsi_values = [None] * RSI_PERIOD
    if avg_loss == 0:
        rsi_values.append(100.0)
    else:
        rs = avg_gain / avg_loss
        rsi_values.append(100.0 - (100.0 / (1.0 + rs)))

    for i in range(RSI_PERIOD, len(gains)):
        avg_gain = ((avg_gain * (RSI_PERIOD - 1)) + gains[i]) / RSI_PERIOD
        avg_loss = ((avg_loss * (RSI_PERIOD - 1)) + losses[i]) / RSI_PERIOD
        if avg_loss == 0:
            rsi_values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(100.0 - (100.0 / (1.0 + rs)))

    return rsi_values


def check_signal(closes, times, rsi_values):
    global startup_rsi_sent

    if not rsi_values or rsi_values[-1] is None:
        return

    current_rsi = rsi_values[-1]
    current_price = closes[-1]
    current_time = times[-1]
    signal = "normal"

    if current_rsi >= OVERBOUGHT:
        signal = "overbought"
    elif current_rsi <= OVERSOLD:
        signal = "oversold"

    latest_status.update(
        {
            "state": "running",
            "market": "open",
            "candle_time": current_time,
            "price": current_price,
            "rsi": current_rsi,
            "signal": signal,
            "checked_at": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S %Z"),
            "error": None,
        }
    )

    print(f"{current_time} | {SYMBOL} | Price={current_price} | RSI={current_rsi:.2f}")

    if not startup_rsi_sent:
        send_telegram(format_rsi_status("[CURRENT RSI]"))
        startup_rsi_sent = True

    if current_rsi >= OVERBOUGHT and last_alerted_candle["overbought"] != current_time:
        send_telegram(
            f"[SELL] RSI at/above {OVERBOUGHT}\n"
            f"{SYMBOL}\n"
            f"Time: {current_time}\n"
            f"Price: {current_price}\n"
            f"RSI: {current_rsi:.2f}"
        )
        last_alerted_candle["overbought"] = current_time

    if current_rsi <= OVERSOLD and last_alerted_candle["oversold"] != current_time:
        send_telegram(
            f"[BUY] RSI at/below {OVERSOLD}\n"
            f"{SYMBOL}\n"
            f"Time: {current_time}\n"
            f"Price: {current_price}\n"
            f"RSI: {current_rsi:.2f}"
        )
        last_alerted_candle["oversold"] = current_time


def main():
    start_command_listener()
    send_telegram(
        f"[STARTED] RSI alert bot\n"
        f"Symbol: {SYMBOL}\n"
        f"Timeframe: {INTERVAL}\n"
        f"SELL: RSI >= {OVERBOUGHT}\n"
        f"BUY: RSI <= {OVERSOLD}\n"
        f"Check every: {CHECK_SECONDS}s\n"
        f"Started: {bot_started_at.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
        f"Command: /status"
    )

    while True:
        try:
            if market_is_open():
                closes, times = fetch_closes()
                rsi_values = calculate_rsi_series(closes)
                check_signal(closes, times, rsi_values)
                sleep_seconds = CHECK_SECONDS
            else:
                print("Market closed in IST schedule. Sleeping until next check...")
                latest_status.update(
                    {
                        "state": "running",
                        "market": "closed",
                        "checked_at": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S %Z"),
                        "error": None,
                    }
                )
                sleep_seconds = CLOSED_CHECK_SECONDS
        except Exception as e:
            print(f"Error: {e}")
            latest_status.update(
                {
                    "state": "error",
                    "checked_at": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S %Z"),
                    "error": str(e),
                }
            )
            notify_error(str(e))
            sleep_seconds = CHECK_SECONDS

        time.sleep(sleep_seconds)


if __name__ == "__main__":
    start_web_server()
    main()
