import os
import logging
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
NOVA_POSHTA_API_KEY = os.getenv("NOVA_POSHTA_API_KEY")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")

NP_API_URL = "https://api.novaposhta.ua/v2.0/json/"

BTN_ALL = "Усі ТТН"
BTN_ACTIVE = "Не доставлені"
BTN_SEARCH = "Пошук по ТТН"
BTN_CANCEL = "Скасувати"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [[BTN_ALL, BTN_ACTIVE], [BTN_SEARCH, BTN_CANCEL]],
    resize_keyboard=True
)

app = FastAPI()
telegram_app = Application.builder().token(TELEGRAM_BOT_TOKEN).updater(None).build()


def np_request(model_name: str, called_method: str, method_properties: dict | None = None) -> dict:
    payload = {
        "apiKey": NOVA_POSHTA_API_KEY,
        "modelName": model_name,
        "calledMethod": called_method,
        "methodProperties": method_properties or {}
    }

    response = requests.post(NP_API_URL, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def get_documents_list(days: int = 7) -> list[dict]:
    date_to = datetime.now()
    date_from = date_to - timedelta(days=days)

    response = np_request(
        model_name="InternetDocument",
        called_method="getDocumentList",
        method_properties={
            "DateTimeFrom": date_from.strftime("%d.%m.%Y"),
            "DateTimeTo": date_to.strftime("%d.%m.%Y"),
            "Page": "1",
            "GetFullList": "1"
        }
    )

    if not response.get("success"):
        errors = response.get("errors") or ["Помилка запиту до Нової Пошти"]
        raise ValueError(", ".join(map(str, errors)))

    return response.get("data", []) or []


def get_ttn_status(ttn: str) -> dict:
    response = np_request(
        model_name="TrackingDocument",
        called_method="getStatusDocuments",
        method_properties={
            "Documents": [{"DocumentNumber": ttn}]
        }
    )

    if not response.get("success"):
        errors = response.get("errors") or ["Помилка запиту до Нової Пошти"]
        raise ValueError(", ".join(map(str, errors)))

    data = response.get("data", []) or []
    if not data:
        raise ValueError("Інформацію по цій ТТН не знайдено.")

    return data[0]


def extract_ttn_and_status(doc: dict) -> tuple[str, str]:
    ttn = doc.get("IntDocNumber") or doc.get("Number") or doc.get("DocumentNumber") or ""
    status = doc.get("StateName") or doc.get("Status") or "Статус невідомий"
    return str(ttn).strip(), str(status).strip()


def is_delivered_status(status: str) -> bool:
    s = status.lower()
    return (
        "отримано" in s
        or "вручено" in s
        or "доставлено" in s
    )


def split_text(text: str, chunk_size: int = 3500) -> list[str]:
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    current = []

    for line in text.splitlines():
        candidate = "\n".join(current + [line])
        if len(candidate) > chunk_size and current:
            chunks.append("\n".join(current))
            current = [line]
        else:
            current.append(line)

    if current:
        chunks.append("\n".join(current))

    return chunks


async def send_long_message(update: Update, text: str) -> None:
    for chunk in split_text(text):
        await update.message.reply_text(chunk)


def format_documents_list(docs: list[dict], title: str) -> str:
    if not docs:
        return f"{title}\n\nНічого не знайдено."

    lines = [title, ""]
    for doc in docs:
        ttn, status = extract_ttn_and_status(doc)
        lines.append(f"{ttn} — {status}")

    return "\n".join(lines)


def format_ttn_info(ttn: str, data: dict) -> str:
    return (
        f"ТТН: {ttn}\n"
        f"Статус: {data.get('Status') or '—'}\n"
        f"Місто відправника: {data.get('CitySender') or '—'}\n"
        f"Місто отримувача: {data.get('CityRecipient') or '—'}\n"
        f"Відділення отримувача: {data.get('WarehouseRecipient') or '—'}\n"
        f"Дата отримання: {data.get('RecipientDateTime') or '—'}\n"
        f"Тип платника: {data.get('PayerType') or '—'}\n"
        f"Післяплата: {data.get('AfterpaymentOnGoodsCost') or '—'}\n"
        f"Кількість місць: {data.get('SeatsAmount') or '—'}\n"
        f"Оголошена вартість: {data.get('AnnouncedPrice') or '—'}\n"
        f"Вага: {data.get('DocumentWeight') or '—'}"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["awaiting_ttn"] = False
    await update.message.reply_text(
        "Бот готовий. Обери дію:",
        reply_markup=MAIN_KEYBOARD
    )


async def handle_all_ttns(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["awaiting_ttn"] = False

    try:
        docs = get_documents_list(days=7)
        text = format_documents_list(docs, "Усі ТТН за останні 7 днів:")
        await send_long_message(update, text)
    except Exception as e:
        await update.message.reply_text(f"Помилка: {e}")


async def handle_active_ttns(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["awaiting_ttn"] = False

    try:
        docs = get_documents_list(days=7)
        active_docs = []

        for doc in docs:
            _, status = extract_ttn_and_status(doc)
            if not is_delivered_status(status):
                active_docs.append(doc)

        text = format_documents_list(active_docs, "Не доставлені ТТН за останні 7 днів:")
        await send_long_message(update, text)
    except Exception as e:
        await update.message.reply_text(f"Помилка: {e}")


async def handle_search_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["awaiting_ttn"] = True
    await update.message.reply_text("Відправ номер ТТН одним повідомленням.")


async def handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["awaiting_ttn"] = False
    await update.message.reply_text("Скасовано.", reply_markup=MAIN_KEYBOARD)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    text = (update.message.text or "").strip()

    if text == BTN_ALL:
        await handle_all_ttns(update, context)
        return

    if text == BTN_ACTIVE:
        await handle_active_ttns(update, context)
        return

    if text == BTN_SEARCH:
        await handle_search_button(update, context)
        return

    if text == BTN_CANCEL:
        await handle_cancel(update, context)
        return

    if context.user_data.get("awaiting_ttn"):
        ttn = "".join(ch for ch in text if ch.isdigit())
        if not ttn:
            await update.message.reply_text("Надішли коректний номер ТТН.")
            return

        try:
            data = get_ttn_status(ttn)
            message = format_ttn_info(ttn, data)
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"Помилка: {e}")
        finally:
            context.user_data["awaiting_ttn"] = False
        return

    await update.message.reply_text("Обери кнопку з меню.", reply_markup=MAIN_KEYBOARD)


@app.on_event("startup")
async def on_startup() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Не знайдено TELEGRAM_BOT_TOKEN")
    if not NOVA_POSHTA_API_KEY:
        raise RuntimeError("Не знайдено NOVA_POSHTA_API_KEY")

    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    await telegram_app.initialize()
    await telegram_app.start()

    if RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL.rstrip('/')}/webhook"
        await telegram_app.bot.set_webhook(url=webhook_url)
        logging.info("Webhook встановлено: %s", webhook_url)
    else:
        logging.info("RENDER_EXTERNAL_URL не задано. Webhook локально не встановлюємо.")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    try:
        if RENDER_EXTERNAL_URL:
            await telegram_app.bot.delete_webhook()
        await telegram_app.stop()
        await telegram_app.shutdown()
    except Exception as e:
        logging.warning("Помилка під час shutdown: %s", e)


@app.get("/")
async def root():
    return {"status": "ok"}


@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}
