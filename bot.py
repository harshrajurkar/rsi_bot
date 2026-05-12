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

last_alerted_candle = {
    "overbought": None,
    "oversold": None,
}
last_error_notice = None


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


def notify_error(message: str):
    global last_error_notice

    if message == last_error_notice:
        return

    try:
        send_telegram(f"[ERROR] RSI bot\n{message}")
        last_error_notice = message
    except Exception as telegram_error:
        print(f"Telegram error notice failed: {telegram_error}")


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
    if not rsi_values or rsi_values[-1] is None:
        return

    current_rsi = rsi_values[-1]
    current_price = closes[-1]
    current_time = times[-1]

    print(f"{current_time} | {SYMBOL} | Price={current_price} | RSI={current_rsi:.2f}")

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
    send_telegram(
        f"[OK] RSI bot started\n"
        f"{SYMBOL} on {INTERVAL}\n"
        f"SELL RSI >= {OVERBOUGHT}\n"
        f"BUY RSI <= {OVERSOLD}\n"
        f"Check every {CHECK_SECONDS} seconds"
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
                sleep_seconds = CLOSED_CHECK_SECONDS
        except Exception as e:
            print(f"Error: {e}")
            notify_error(str(e))
            sleep_seconds = CHECK_SECONDS

        time.sleep(sleep_seconds)


if __name__ == "__main__":
    start_web_server()
    main()
