import os
import threading
import time

import requests
from dotenv import load_dotenv
from flask import Flask

load_dotenv()

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("CONTROL_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
RENDER_API_KEY = os.getenv("RENDER_API_KEY")
RENDER_SERVICE_ID = os.getenv("RENDER_SERVICE_ID")
POLL_SECONDS = int(os.getenv("CONTROL_POLL_SECONDS", "3"))

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
RENDER_API = "https://api.render.com/v1"

last_update_id = None


@app.route("/")
def home():
    return "Control bot running"


def run_web():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, use_reloader=False)


def start_web_server():
    threading.Thread(target=run_web, daemon=True).start()


def telegram_request(method, payload=None):
    response = requests.post(f"{TELEGRAM_API}/{method}", json=payload or {}, timeout=15)
    response.raise_for_status()
    return response.json()


def send_telegram(message):
    telegram_request(
        "sendMessage",
        {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
        },
    )


def render_headers():
    return {
        "Authorization": f"Bearer {RENDER_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def render_request(method, path, payload=None):
    response = requests.request(
        method,
        f"{RENDER_API}{path}",
        headers=render_headers(),
        json=payload,
        timeout=30,
    )

    if response.status_code >= 400:
        raise Exception(f"Render API {response.status_code}: {response.text}")

    if response.text:
        return response.json()
    return {}


def get_service_status():
    data = render_request("GET", f"/services/{RENDER_SERVICE_ID}")
    service = data.get("service", data)
    name = service.get("name", "unknown")
    service_type = service.get("type", "unknown")
    suspended = service.get("suspended")
    auto_deploy = service.get("autoDeploy")
    branch = service.get("branch", "unknown")

    if suspended is True:
        state = "suspended"
    elif suspended is False:
        state = "running"
    else:
        state = "unknown"

    return (
        f"[STATUS]\n"
        f"Service: {name}\n"
        f"Type: {service_type}\n"
        f"State: {state}\n"
        f"Branch: {branch}\n"
        f"Auto deploy: {auto_deploy}"
    )


def handle_command(text):
    command = text.split()[0].lower()

    if command in ("/help", "/start"):
        return (
            "Render control commands:\n"
            "/status - show RSI bot status\n"
            "/stop - suspend RSI bot\n"
            "/resume - resume RSI bot\n"
            "/deploy - deploy latest commit\n"
            "/restart - restart current service\n"
            "/clearcache - clear build cache and deploy"
        )

    if command == "/status":
        return get_service_status()

    if command == "/stop":
        render_request("POST", f"/services/{RENDER_SERVICE_ID}/suspend")
        return "[OK] RSI bot suspend requested."

    if command in ("/resume", "/startbot"):
        render_request("POST", f"/services/{RENDER_SERVICE_ID}/resume")
        return "[OK] RSI bot resume requested."

    if command == "/deploy":
        render_request(
            "POST",
            f"/services/{RENDER_SERVICE_ID}/deploys",
            {"clearCache": "do_not_clear"},
        )
        return "[OK] Deploy latest commit requested."

    if command == "/clearcache":
        render_request(
            "POST",
            f"/services/{RENDER_SERVICE_ID}/deploys",
            {"clearCache": "clear"},
        )
        return "[OK] Clear cache deploy requested."

    if command == "/restart":
        render_request(
            "POST",
            f"/services/{RENDER_SERVICE_ID}/deploys",
            {"clearCache": "do_not_clear"},
        )
        return "[OK] Restart/deploy requested."

    return "Unknown command. Send /help."


def process_update(update):
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    text = (message.get("text") or "").strip()

    if not text:
        return

    if str(chat.get("id")) != str(TELEGRAM_CHAT_ID):
        return

    try:
        send_telegram(handle_command(text))
    except Exception as error:
        send_telegram(f"[ERROR]\n{error}")


def poll_telegram():
    global last_update_id

    send_telegram("[OK] Render control bot started. Send /help.")

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
            print(f"Control bot error: {error}")
            time.sleep(10)

        time.sleep(POLL_SECONDS)


def main():
    missing = [
        name
        for name, value in {
            "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
            "RENDER_API_KEY": RENDER_API_KEY,
            "RENDER_SERVICE_ID": RENDER_SERVICE_ID,
        }.items()
        if not value
    ]

    if not TELEGRAM_BOT_TOKEN:
        missing.append("CONTROL_TELEGRAM_BOT_TOKEN")

    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")

    start_web_server()
    poll_telegram()


if __name__ == "__main__":
    main()
