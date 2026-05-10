import os
import time
import threading

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

last_signal = None


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
    global last_signal

    if len(rsi_values) < 2 or rsi_values[-1] is None or rsi_values[-2] is None:
        return

    previous_rsi = rsi_values[-2]
    current_rsi = rsi_values[-1]
    current_price = closes[-1]
    current_time = times[-1]

    print(f"{current_time} | {SYMBOL} | Price={current_price} | RSI={current_rsi:.2f}")

    if previous_rsi <= OVERBOUGHT and current_rsi > OVERBOUGHT:
        signal = f"SELL_{current_time}"
        if last_signal != signal:
            send_telegram(
                f"[SELL] RSI crossed above {OVERBOUGHT}\n"
                f"{SYMBOL}\n"
                f"Price: {current_price}\n"
                f"RSI: {current_rsi:.2f}"
            )
            last_signal = signal

    elif previous_rsi >= OVERSOLD and current_rsi < OVERSOLD:
        signal = f"BUY_{current_time}"
        if last_signal != signal:
            send_telegram(
                f"[BUY] RSI crossed below {OVERSOLD}\n"
                f"{SYMBOL}\n"
                f"Price: {current_price}\n"
                f"RSI: {current_rsi:.2f}"
            )
            last_signal = signal


def main():
    send_telegram(f"[OK] RSI bot started for {SYMBOL} on {INTERVAL}")

    while True:
        try:
            closes, times = fetch_closes()
            rsi_values = calculate_rsi_series(closes)
            check_signal(closes, times, rsi_values)
        except Exception as e:
            print(f"Error: {e}")

        time.sleep(CHECK_SECONDS)


if __name__ == "__main__":
    start_web_server()
    main()
