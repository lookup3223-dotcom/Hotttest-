#!/usr/bin/env python3

import asyncio
import html
import json
import logging
import os
import re
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# =========================================================
# YOUR DETAILS / ENV
# =========================================================

# Railway ke liye token env var me rakho:
# BOT_TOKEN=your_new_bot_token
BOT_TOKEN = os.getenv("BOT_TOKEN", "PASTE_NEW_BOT_TOKEN_HERE")

# OWNER_ID ko env se override kar sakte ho; default same rahega.
OWNER_ID = int(os.getenv("OWNER_ID", "8662263918"))

# Optional config file path for Railway persistent volume.
CONFIG_FILE = os.getenv("CONFIG_FILE", "bot_config.json")

# =========================================================

MAX_START_MEDIA = 30
MAX_PRODUCT_MEDIA = 30
BUTTON_COUNT = 12

# ONLY /start output is auto-deleted.
AUTO_DELETE_SECONDS = 600  # 10 minutes


logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)


# =========================================================
# CONFIG
# =========================================================

def make_button_data(i: int):
    return {
        "title": f"Product {i}",
        "media": [],
        "caption": f"Details for Product {i} have not been set yet.",
        "delivery_type": "text",
        "delivery_file_id": None,
        "delivery_text": (
            "🎉 Your payment has been approved!\n"
            "Thank you for your purchase."
        ),
        "qr_file_id": None,
        "upi_id": "",
    }


def default_config():
    return {
        "start_message": (
            "👋 Welcome!\n\n"
            "Choose a product from the buttons below "
            "to see details and buy."
        ),
        "start_media": [],
        "payment_text": (
            "💳 Payment Instructions\n\n"
            "1️⃣ Scan the QR code above and complete the payment.\n"
            "2️⃣ Take a screenshot of the successful payment.\n"
            "3️⃣ Send that screenshot here in this chat.\n\n"
            "✅ As soon as an admin verifies your payment, "
            "your product will be delivered automatically."
        ),
        "qr_file_id": None,
        "admins": [],
        "users": [],
        "buttons": {
            str(i): make_button_data(i)
            for i in range(1, BUTTON_COUNT + 1)
        },
        "custom_cmds": {},
        "pending": {},
        "online_post": {
            "file_id": None,
            "caption": "🔥 New Update",
        },
        "copy_forward_restricted": True,
    }


def normalize_buttons(buttons):
    normalized = {}

    for i in range(1, BUTTON_COUNT + 1):
        key = str(i)
        base = make_button_data(i)

        old = buttons.get(key, {}) if isinstance(buttons, dict) else {}
        base.update(old)

        media_list = old.get("media") if isinstance(old, dict) else None

        if not isinstance(media_list, list):
            media_list = []

        # Old format migration.
        if not media_list and old.get("media_file_id"):
            media_list = [{
                "type": old.get("media_type") or "photo",
                "file_id": old.get("media_file_id"),
            }]

        base["media"] = media_list[:MAX_PRODUCT_MEDIA]
        base.pop("media_type", None)
        base.pop("media_file_id", None)

        normalized[key] = base

    return normalized


def load_config():
    if not os.path.exists(CONFIG_FILE):
        return default_config()

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        base = default_config()
        base.update(cfg)

        # Old start media migration.
        if not base.get("start_media") and cfg.get("start_media_file_id"):
            base["start_media"] = [{
                "type": cfg.get("start_media_type") or "photo",
                "file_id": cfg["start_media_file_id"],
            }]

        base["buttons"] = normalize_buttons(cfg.get("buttons", {}))

        if not isinstance(base.get("users"), list):
            base["users"] = []

        if not isinstance(base.get("admins"), list):
            base["admins"] = []

        if not isinstance(base.get("pending"), dict):
            base["pending"] = {}

        if not isinstance(base.get("custom_cmds"), dict):
            base["custom_cmds"] = {}

        if not isinstance(base.get("online_post"), dict):
            base["online_post"] = {
                "file_id": None,
                "caption": "🔥 New Update",
            }

        if not base["online_post"].get("caption"):
            base["online_post"]["caption"] = "🔥 New Update"

        if not isinstance(base.get("copy_forward_restricted"), bool):
            base["copy_forward_restricted"] = True

        return base

    except Exception as e:
        logging.warning(f"Config load failed: {e}")
        return default_config()


CFG = load_config()


def save_config():
    try:
        temp_file = CONFIG_FILE + ".tmp"

        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(
                CFG,
                f,
                ensure_ascii=False,
                indent=2,
            )

        os.replace(temp_file, CONFIG_FILE)

    except Exception as e:
        logging.error(f"Config save failed: {e}")


# =========================================================
# HELPERS
# =========================================================

def B(text: str) -> str:
    return f"<b>{html.escape(str(text))}</b>"


def is_admin(uid: int) -> bool:
    return uid == OWNER_ID or uid in CFG["admins"]


def all_admins():
    ids = [OWNER_ID]

    for admin_id in CFG["admins"]:
        if admin_id != OWNER_ID:
            ids.append(admin_id)

    return ids


def get_button(n: int | str):
    return CFG["buttons"][str(n)]


def build_button_label(i: int, title: str) -> str:
    return title


def get_button_style(i: int):
    if i % 3 == 1:
        return "success"
    elif i % 3 == 2:
        return "primary"
    return "danger"


def main_keyboard():
    rows = []

    for i in range(1, BUTTON_COUNT + 1):
        rows.append([
            InlineKeyboardButton(
                build_button_label(i, get_button(i)["title"]),
                callback_data=f"prod_{i}",
                style=get_button_style(i),
            )
        ])

    return InlineKeyboardMarkup(rows)


def sanitize_html_text(value: str) -> str:
    if not value:
        return ""

    text = str(value).strip()
    text = re.sub(r"<(?!/?(?:b|i|u|s|code|pre|a)(?:\s+href=\"[^\"]*\")?\s*/?>)", "&lt;", text)
    return text


def parse_media_entry(file_id: str, media_type: str):
    media_type = (media_type or "photo").lower().strip()
    if media_type not in {"photo", "video"}:
        media_type = "photo"
    return {
        "type": media_type,
        "file_id": file_id,
    }


async def safe_delete(chat_id: int, message_id: int, bot):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def schedule_delete(chat_id: int, message_id: int, bot):
    await asyncio.sleep(AUTO_DELETE_SECONDS)
    await safe_delete(chat_id, message_id, bot)


def remember_user(uid: int):
    if uid not in CFG["users"]:
        CFG["users"].append(uid)
        save_config()


async def send_start_content(chat_id: int, bot):
    start_media = CFG.get("start_media") or []
    text = CFG.get("start_message") or "Welcome"

    sent_message = None

    if start_media:
        media_group = []
        for idx, item in enumerate(start_media[:MAX_START_MEDIA]):
            file_id = item.get("file_id")
            media_type = item.get("type", "photo")
            if not file_id:
                continue

            caption = text if idx == 0 else None

            if media_type == "video":
                media_group.append(InputMediaVideo(media=file_id, caption=caption, parse_mode="HTML"))
            else:
                media_group.append(InputMediaPhoto(media=file_id, caption=caption, parse_mode="HTML"))

        if media_group:
            msgs = await bot.send_media_group(chat_id=chat_id, media=media_group)
            sent_message = msgs[-1]
            await bot.send_message(
                chat_id=chat_id,
                text=B("Choose a product below:"),
                parse_mode="HTML",
                reply_markup=main_keyboard(),
            )
    else:
        sent_message = await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=main_keyboard(),
        )

    if sent_message:
        asyncio.create_task(schedule_delete(chat_id, sent_message.message_id, bot))


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    remember_user(update.effective_user.id)
    await send_start_content(update.effective_chat.id, context.bot)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "<b>Available commands</b>\n\n"
        "/start - Show product menu\n"
        "/help - Show help\n"
    )

    if is_admin(update.effective_user.id):
        txt += (
            "\n<b>Admin commands</b>\n"
            "/setstart - Set welcome text\n"
            "/setpayment - Set payment instructions\n"
            "/setqr - Save replied photo/document as main QR\n"
            "/addadmin USER_ID - Add admin\n"
            "/removeadmin USER_ID - Remove admin\n"
            "/admins - List admins\n"
            "/settitle N title - Set button title\n"
            "/setcaption N text - Set product caption\n"
            "/addmedia N - Save replied photo/video to product\n"
            "/clearmedia N - Clear product media\n"
            "/setdeliverytext N text - Set delivery text\n"
            "/setdeliveryfile N - Save replied file as delivery\n"
            "/setproductqr N - Save replied QR for product\n"
            "/setupi N upi_id - Set UPI ID for product\n"
            "/users - Count users\n"
            "/broadcast - Reply to any message to broadcast it\n"
        )

    await update.message.reply_text(txt, parse_mode="HTML")


async def product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    m = re.match(r"prod_(\d+)$", query.data or "")
    if not m:
        return

    idx = int(m.group(1))
    item = get_button(idx)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Buy Now", callback_data=f"buy_{idx}", style="success")],
        [InlineKeyboardButton("Back", callback_data="back_main", style="primary")],
    ])

    media = item.get("media") or []
    caption = item.get("caption") or ""

    if media:
        media_group = []
        for i, media_item in enumerate(media[:MAX_PRODUCT_MEDIA]):
            file_id = media_item.get("file_id")
            media_type = media_item.get("type", "photo")
            if not file_id:
                continue

            cap = caption if i == 0 else None
            if media_type == "video":
                media_group.append(InputMediaVideo(media=file_id, caption=cap, parse_mode="HTML"))
            else:
                media_group.append(InputMediaPhoto(media=file_id, caption=cap, parse_mode="HTML"))

        if media_group:
            await context.bot.send_media_group(chat_id=query.message.chat_id, media=media_group)
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=B("Choose an option:"),
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            return

    await query.message.reply_text(
        caption,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def back_main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        CFG.get("start_message") or "Welcome",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


def payment_keyboard(product_no: int):
    rows = []
    item = get_button(product_no)
    upi_id = (item.get("upi_id") or "").strip()

    if upi_id:
        rows.append([
            InlineKeyboardButton(
                "Pay via UPI",
                url=f"upi://pay?pa={upi_id}",
            )
        ])

    rows.append([
        InlineKeyboardButton("Back", callback_data=f"prod_{product_no}", style="primary")
    ])
    return InlineKeyboardMarkup(rows)


async def buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    m = re.match(r"buy_(\d+)$", query.data or "")
    if not m:
        return

    product_no = int(m.group(1))
    item = get_button(product_no)

    qr_file_id = item.get("qr_file_id") or CFG.get("qr_file_id")
    payment_text = CFG.get("payment_text") or "Send payment screenshot after payment."

    if qr_file_id:
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=qr_file_id,
            caption=payment_text,
            parse_mode="HTML",
            reply_markup=payment_keyboard(product_no),
        )
    else:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=payment_text,
            parse_mode="HTML",
            reply_markup=payment_keyboard(product_no),
        )

    pending = CFG["pending"]
    pending[str(query.from_user.id)] = {
        "product": product_no,
        "time": datetime.utcnow().isoformat(),
    }
    save_config()


async def approve_user(context: ContextTypes.DEFAULT_TYPE, buyer_id: int, product_no: int):
    item = get_button(product_no)
    delivery_type = item.get("delivery_type") or "text"
    delivery_file_id = item.get("delivery_file_id")
    delivery_text = item.get("delivery_text") or "Payment approved."

    if delivery_type == "file" and delivery_file_id:
        await context.bot.send_document(
            chat_id=buyer_id,
            document=delivery_file_id,
            caption=delivery_text,
            parse_mode="HTML",
            protect_content=CFG.get("copy_forward_restricted", True),
        )
    else:
        await context.bot.send_message(
            chat_id=buyer_id,
            text=delivery_text,
            parse_mode="HTML",
            protect_content=CFG.get("copy_forward_restricted", True),
        )


async def reject_user(context: ContextTypes.DEFAULT_TYPE, buyer_id: int):
    await context.bot.send_message(
        chat_id=buyer_id,
        text=B("Payment was not approved. Please contact support/admin."),
        parse_mode="HTML",
    )


def approval_keyboard(user_id: int):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Approve", callback_data=f"approve_{user_id}", style="success"),
            InlineKeyboardButton("Reject", callback_data=f"reject_{user_id}", style="danger"),
        ]
    ])


async def forward_payment_to_admins(update: Update, context: ContextTypes.DEFAULT_TYPE, caption_text: str):
    msg = update.message

    for admin_id in all_admins():
        try:
            if msg.photo:
                file_id = msg.photo[-1].file_id
                await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=file_id,
                    caption=caption_text,
                    parse_mode="HTML",
                    reply_markup=approval_keyboard(update.effective_user.id),
                )
            elif msg.document:
                await context.bot.send_document(
                    chat_id=admin_id,
                    document=msg.document.file_id,
                    caption=caption_text,
                    parse_mode="HTML",
                    reply_markup=approval_keyboard(update.effective_user.id),
                )
        except Exception as e:
            logging.warning(f"Admin forward failed to {admin_id}: {e}")


async def handle_payment_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    remember_user(uid)

    pending = CFG.get("pending", {})
    entry = pending.get(str(uid))
    if not entry:
        return

    product_no = int(entry.get("product", 1))
    item = get_button(product_no)
    product_title = item.get("title") or f"Product {product_no}"

    caption_text = (
        f"<b>Payment proof received</b>\n"
        f"User ID: <code>{uid}</code>\n"
        f"Product: {html.escape(product_title)}"
    )

    await forward_payment_to_admins(update, context, caption_text)
    await update.message.reply_text(
        B("Screenshot received. Admin will verify it shortly."),
        parse_mode="HTML",
    )


async def approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        await query.answer("Not allowed", show_alert=True)
        return

    data = query.data or ""
    m = re.match(r"(approve|reject)_(\d+)$", data)
    if not m:
        return

    action = m.group(1)
    buyer_id = int(m.group(2))
    entry = CFG.get("pending", {}).get(str(buyer_id))

    if not entry:
        await query.edit_message_caption(
            caption=B("This request is already processed or expired."),
            parse_mode="HTML",
        )
        return

    product_no = int(entry.get("product", 1))

    if action == "approve":
        await approve_user(context, buyer_id, product_no)
        status_text = B(f"Approved user {buyer_id} for product {product_no}.")
    else:
        await reject_user(context, buyer_id)
        status_text = B(f"Rejected user {buyer_id}.")

    CFG["pending"].pop(str(buyer_id), None)
    save_config()

    try:
        if query.message.photo or query.message.video or query.message.document:
            await query.edit_message_caption(caption=status_text, parse_mode="HTML")
        else:
            await query.edit_message_text(status_text, parse_mode="HTML")
    except Exception:
        pass


async def setstart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text(B("Usage: /setstart your text"), parse_mode="HTML")
        return
    CFG["start_message"] = text
    save_config()
    await update.message.reply_text(B("Start message updated."), parse_mode="HTML")


async def setpayment_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text(B("Usage: /setpayment your text"), parse_mode="HTML")
        return
    CFG["payment_text"] = text
    save_config()
    await update.message.reply_text(B("Payment text updated."), parse_mode="HTML")


async def setqr_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    reply = update.message.reply_to_message
    if not reply or not (reply.photo or reply.document):
        await update.message.reply_text(B("Reply to a QR photo/document."), parse_mode="HTML")
        return

    if reply.photo:
        CFG["qr_file_id"] = reply.photo[-1].file_id
    else:
        CFG["qr_file_id"] = reply.document.file_id

    save_config()
    await update.message.reply_text(B("Main QR saved."), parse_mode="HTML")


async def settitle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) < 2 or not context.args[0].isdigit():
        await update.message.reply_text(B("Usage: /settitle N Title"), parse_mode="HTML")
        return

    idx = int(context.args[0])
    title = " ".join(context.args[1:]).strip()
    if not (1 <= idx <= BUTTON_COUNT) or not title:
        await update.message.reply_text(B("Invalid button number or empty title."), parse_mode="HTML")
        return

    get_button(idx)["title"] = title
    save_config()
    await update.message.reply_text(B(f"Button {idx} title updated."), parse_mode="HTML")


async def setcaption_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) < 2 or not context.args[0].isdigit():
        await update.message.reply_text(B("Usage: /setcaption N text"), parse_mode="HTML")
        return

    idx = int(context.args[0])
    text = " ".join(context.args[1:]).strip()
    if not (1 <= idx <= BUTTON_COUNT) or not text:
        await update.message.reply_text(B("Invalid button number or empty caption."), parse_mode="HTML")
        return

    get_button(idx)["caption"] = text
    save_config()
    await update.message.reply_text(B(f"Button {idx} caption updated."), parse_mode="HTML")


async def addmedia_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text(B("Usage: /addmedia N (reply to photo/video)"), parse_mode="HTML")
        return

    idx = int(context.args[0])
    reply = update.message.reply_to_message
    if not (1 <= idx <= BUTTON_COUNT) or not reply:
        await update.message.reply_text(B("Invalid request."), parse_mode="HTML")
        return

    entry = None
    if reply.photo:
        entry = parse_media_entry(reply.photo[-1].file_id, "photo")
    elif reply.video:
        entry = parse_media_entry(reply.video.file_id, "video")

    if not entry:
        await update.message.reply_text(B("Reply to a photo or video."), parse_mode="HTML")
        return

    media = get_button(idx).setdefault("media", [])
    media.append(entry)
    get_button(idx)["media"] = media[:MAX_PRODUCT_MEDIA]
    save_config()
    await update.message.reply_text(B(f"Media added to button {idx}."), parse_mode="HTML")


async def clearmedia_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text(B("Usage: /clearmedia N"), parse_mode="HTML")
        return

    idx = int(context.args[0])
    if not (1 <= idx <= BUTTON_COUNT):
        await update.message.reply_text(B("Invalid button number."), parse_mode="HTML")
        return

    get_button(idx)["media"] = []
    save_config()
    await update.message.reply_text(B(f"Button {idx} media cleared."), parse_mode="HTML")


async def setdeliverytext_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) < 2 or not context.args[0].isdigit():
        await update.message.reply_text(B("Usage: /setdeliverytext N text"), parse_mode="HTML")
        return

    idx = int(context.args[0])
    text = " ".join(context.args[1:]).strip()
    if not (1 <= idx <= BUTTON_COUNT) or not text:
        await update.message.reply_text(B("Invalid button number or empty text."), parse_mode="HTML")
        return

    item = get_button(idx)
    item["delivery_type"] = "text"
    item["delivery_text"] = text
    save_config()
    await update.message.reply_text(B(f"Delivery text set for button {idx}."), parse_mode="HTML")


async def setdeliveryfile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text(B("Usage: /setdeliveryfile N (reply to file)"), parse_mode="HTML")
        return

    idx = int(context.args[0])
    reply = update.message.reply_to_message
    if not (1 <= idx <= BUTTON_COUNT) or not reply or not reply.document:
        await update.message.reply_text(B("Reply to a document/file."), parse_mode="HTML")
        return

    item = get_button(idx)
    item["delivery_type"] = "file"
    item["delivery_file_id"] = reply.document.file_id
    if reply.caption:
        item["delivery_text"] = reply.caption
    save_config()
    await update.message.reply_text(B(f"Delivery file set for button {idx}."), parse_mode="HTML")


async def setproductqr_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text(B("Usage: /setproductqr N (reply to QR)"), parse_mode="HTML")
        return

    idx = int(context.args[0])
    reply = update.message.reply_to_message
    if not (1 <= idx <= BUTTON_COUNT) or not reply or not (reply.photo or reply.document):
        await update.message.reply_text(B("Reply to a QR photo/document."), parse_mode="HTML")
        return

    item = get_button(idx)
    item["qr_file_id"] = reply.photo[-1].file_id if reply.photo else reply.document.file_id
    save_config()
    await update.message.reply_text(B(f"QR set for button {idx}."), parse_mode="HTML")


async def setupi_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) < 2 or not context.args[0].isdigit():
        await update.message.reply_text(B("Usage: /setupi N upi_id"), parse_mode="HTML")
        return

    idx = int(context.args[0])
    upi_id = " ".join(context.args[1:]).strip()
    if not (1 <= idx <= BUTTON_COUNT) or not upi_id:
        await update.message.reply_text(B("Invalid button number or empty UPI ID."), parse_mode="HTML")
        return

    get_button(idx)["upi_id"] = upi_id
    save_config()
    await update.message.reply_text(B(f"UPI ID set for button {idx}."), parse_mode="HTML")


async def addadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text(B("Usage: /addadmin USER_ID"), parse_mode="HTML")
        return

    uid = int(context.args[0])
    if uid in CFG["admins"] or uid == OWNER_ID:
        await update.message.reply_text(B("This user is already an admin."), parse_mode="HTML")
        return

    CFG["admins"].append(uid)
    save_config()
    await update.message.reply_text(B(f"Admin added: {uid}"), parse_mode="HTML")


async def removeadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text(B("Usage: /removeadmin USER_ID"), parse_mode="HTML")
        return

    uid = int(context.args[0])
    if uid not in CFG["admins"]:
        await update.message.reply_text(B("This user is not an admin."), parse_mode="HTML")
        return

    CFG["admins"].remove(uid)
    save_config()
    await update.message.reply_text(B(f"Admin removed: {uid}"), parse_mode="HTML")


async def admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    lines = [f"👑 Owner: {OWNER_ID}"]

    for admin_id in CFG["admins"]:
        lines.append(f"🛡 Admin: {admin_id}")

    await update.message.reply_text("\n".join(lines))


async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text(B(f"Total users: {len(CFG['users'])}"), parse_mode="HTML")


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    reply = update.message.reply_to_message
    if not reply:
        await update.message.reply_text(B("Reply to a message to broadcast it."), parse_mode="HTML")
        return

    sent = 0
    failed = 0

    for uid in CFG.get("users", []):
        try:
            await reply.copy(chat_id=uid)
            sent += 1
        except Exception:
            failed += 1

    await update.message.reply_text(
        B(f"Broadcast done. Sent: {sent}, Failed: {failed}"),
        parse_mode="HTML",
    )


async def custom_command_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    if not text.startswith("/"):
        return

    cmd = text.split()[0][1:].split("@")[0]
    data = CFG.get("custom_cmds", {}).get(cmd)
    if not data:
        return

    file_id = data.get("file_id")
    caption = data.get("caption") or ""
    media_type = data.get("type") or "photo"

    if file_id:
        if media_type == "video":
            await context.bot.send_video(
                chat_id=update.effective_chat.id,
                video=file_id,
                caption=caption,
                parse_mode="HTML",
            )
        else:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=file_id,
                caption=caption,
                parse_mode="HTML",
            )
    else:
        await update.message.reply_text(caption, parse_mode="HTML")


async def unknown_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if text.startswith("/"):
        await custom_command_router(update, context)
        return


def main():
    if BOT_TOKEN == "PASTE_NEW_BOT_TOKEN_HERE":
        raise RuntimeError(
            "BOT_TOKEN set karo. Exposed token use mat karo; naya token env var me dalo."
        )

    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("setstart", setstart_cmd))
    app.add_handler(CommandHandler("setpayment", setpayment_cmd))
    app.add_handler(CommandHandler("setqr", setqr_cmd))
    app.add_handler(CommandHandler("settitle", settitle_cmd))
    app.add_handler(CommandHandler("setcaption", setcaption_cmd))
    app.add_handler(CommandHandler("addmedia", addmedia_cmd))
    app.add_handler(CommandHandler("clearmedia", clearmedia_cmd))
    app.add_handler(CommandHandler("setdeliverytext", setdeliverytext_cmd))
    app.add_handler(CommandHandler("setdeliveryfile", setdeliveryfile_cmd))
    app.add_handler(CommandHandler("setproductqr", setproductqr_cmd))
    app.add_handler(CommandHandler("setupi", setupi_cmd))
    app.add_handler(CommandHandler("addadmin", addadmin_cmd))
    app.add_handler(CommandHandler("removeadmin", removeadmin_cmd))
    app.add_handler(CommandHandler("admins", admins_cmd))
    app.add_handler(CommandHandler("users", users_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))

    app.add_handler(CallbackQueryHandler(product_callback, pattern=r"^prod_\d+$"))
    app.add_handler(CallbackQueryHandler(buy_callback, pattern=r"^buy_\d+$"))
    app.add_handler(CallbackQueryHandler(back_main_callback, pattern=r"^back_main$"))
    app.add_handler(CallbackQueryHandler(approval_callback, pattern=r"^(approve|reject)_\d+$"))

    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_payment_proof))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown_text_handler))
    app.add_handler(MessageHandler(filters.COMMAND, custom_command_router))

    print("=" * 55)
    print("Bot is running on Railway via long polling")
    print(f"Owner ID: {OWNER_ID}")
    print("=" * 55)

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
