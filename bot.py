import asyncio
import io
import json
import logging
import uuid
import os
from datetime import datetime, timedelta
import qrcode
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from config import TELEGRAM_BOT_TOKEN, ADMIN_USER_IDS, PANEL_URL, PANEL_API_PATH, PANEL_USERNAME, PANEL_PASSWORD, PANEL_API_TOKEN
from panel_api import PanelAPI
import db as _db

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

panel = PanelAPI(
    base_url=PANEL_URL,
    api_path=PANEL_API_PATH,
    username=PANEL_USERNAME,
    password=PANEL_PASSWORD,
    api_token=PANEL_API_TOKEN,
)

# ==================== I18N ====================
TRANSLATIONS_FILE = os.path.join(os.path.dirname(__file__), "translations.json")

# In-memory cache, populated from SQLite at startup and kept in sync on every change
USER_LANG: dict[int, str] = {}

def load_translations() -> dict:
    with open(TRANSLATIONS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

translations = load_translations()

def t(user_id: int, key: str, **kwargs) -> str:
    lang = USER_LANG.get(user_id, "en")
    text = translations.get(lang, translations["en"]).get(key, key)
    if kwargs:
        text = text.format(**kwargs)
    return text

def set_lang(user_id: int, lang: str):
    USER_LANG[user_id] = lang
    _db.set_user_lang(user_id, lang)

def toggle_lang(user_id: int) -> str:
    current = USER_LANG.get(user_id, "en")
    new_lang = "fa" if current == "en" else "en"
    set_lang(user_id, new_lang)
    return new_lang

# ==================== PLANS & ORDERS ====================
PLANS_FILE = os.path.join(os.path.dirname(__file__), "plans.json")

def load_plans() -> list:
    try:
        with open(PLANS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

# In-memory order state: {user_id: {"plan_id": ..., "state": "waiting_screenshot"}}
pending_orders: dict = {}

# In-memory client creation state: {user_id: {"selected_inbounds": [id, ...], "step": "select_inbounds"|"confirm"}}
client_creation_state: dict = {}

# In-memory approval state: {admin_id: {"order_id": ..., "selected_inbounds": [...], "inbounds": [...]}}
approval_state: dict = {}


def format_bytes(b: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024:
            return f"{b:.2f} {unit}"
        b /= 1024
    return f"{b:.2f} PB"


def format_ts(ts: int) -> str:
    if not ts:
        return "Never"
    from datetime import datetime
    return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M")


def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if ADMIN_USER_IDS and user_id not in ADMIN_USER_IDS:
            msg = update.message or update.callback_query.message
            if msg:
                await msg.reply_text("Access denied.")
            return
        return await func(update, context)
    return wrapper


# ==================== LANGUAGE ====================
async def lang_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    new_lang = toggle_lang(user_id)
    is_admin = user_id in ADMIN_USER_IDS
    if is_admin:
        keyboard = [
            [InlineKeyboardButton(t(user_id, "server_status"), callback_data="server_status")],
            [InlineKeyboardButton(t(user_id, "inbounds"), callback_data="inbounds_menu"), InlineKeyboardButton(t(user_id, "clients"), callback_data="clients_menu")],
            [InlineKeyboardButton(t(user_id, "nodes"), callback_data="nodes_menu"), InlineKeyboardButton(t(user_id, "hosts"), callback_data="hosts_menu")],
            [InlineKeyboardButton(t(user_id, "xray_management"), callback_data="xray_menu"), InlineKeyboardButton(t(user_id, "settings"), callback_data="settings_menu")],
            [InlineKeyboardButton(t(user_id, "get_links"), callback_data="get_links"), InlineKeyboardButton(t(user_id, "online_clients"), callback_data="online_clients")],
            [InlineKeyboardButton(t(user_id, "pending_orders_btn"), callback_data="pending_orders")],
            [InlineKeyboardButton(t(user_id, "switch_lang"), callback_data="switch_lang")],
        ]
        await update.message.reply_text(f"✅ {t(user_id, 'lang_set')}\n\n{t(user_id, 'admin_welcome')}", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        keyboard = [
            [InlineKeyboardButton(t(user_id, "buy_vpn"), callback_data="buy_menu")],
            [InlineKeyboardButton(t(user_id, "test_account_btn"), callback_data="test_account"),
             InlineKeyboardButton(t(user_id, "my_plans_btn"), callback_data="my_plans")],
            [InlineKeyboardButton(t(user_id, "support"), callback_data="support")],
            [InlineKeyboardButton(t(user_id, "switch_lang"), callback_data="switch_lang")],
        ]
        await update.message.reply_text(f"✅ {t(user_id, 'lang_set')}\n\n{t(user_id, 'welcome')}", reply_markup=InlineKeyboardMarkup(keyboard))


# ==================== MAIN MENU ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_admin = user_id in ADMIN_USER_IDS

    if is_admin:
        keyboard = [
            [InlineKeyboardButton(t(user_id, "server_status"), callback_data="server_status")],
            [InlineKeyboardButton(t(user_id, "inbounds"), callback_data="inbounds_menu"), InlineKeyboardButton(t(user_id, "clients"), callback_data="clients_menu")],
            [InlineKeyboardButton(t(user_id, "nodes"), callback_data="nodes_menu"), InlineKeyboardButton(t(user_id, "hosts"), callback_data="hosts_menu")],
            [InlineKeyboardButton(t(user_id, "xray_management"), callback_data="xray_menu"), InlineKeyboardButton(t(user_id, "settings"), callback_data="settings_menu")],
            [InlineKeyboardButton(t(user_id, "get_links"), callback_data="get_links"), InlineKeyboardButton(t(user_id, "online_clients"), callback_data="online_clients")],
            [InlineKeyboardButton(t(user_id, "pending_orders_btn"), callback_data="pending_orders")],
            [InlineKeyboardButton(t(user_id, "switch_lang"), callback_data="switch_lang")],
        ]
        await update.message.reply_text(t(user_id, "admin_welcome"), reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        keyboard = [
            [InlineKeyboardButton(t(user_id, "buy_vpn"), callback_data="buy_menu")],
            [InlineKeyboardButton(t(user_id, "test_account_btn"), callback_data="test_account"),
             InlineKeyboardButton(t(user_id, "my_plans_btn"), callback_data="my_plans")],
            [InlineKeyboardButton(t(user_id, "support"), callback_data="support")],
            [InlineKeyboardButton(t(user_id, "switch_lang"), callback_data="switch_lang")],
        ]
        welcome_msg = t(user_id, 'welcome')
        await update.message.reply_text(welcome_msg, reply_markup=InlineKeyboardMarkup(keyboard))


# ==================== PURCHASE SYSTEM ====================
async def buy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    plans = load_plans()
    if not plans:
        await update.message.reply_text(t(user_id, "no_plans"))
        return
    msg = f"<b>{t(user_id, 'plans_title')}</b>\n\n"
    keyboard = []
    for plan in plans:
        text = f"{plan['name']} - {plan['price_display']}"
        keyboard.append([InlineKeyboardButton(text, callback_data=f"plan_{plan['id']}")])
    keyboard.append([InlineKeyboardButton(t(user_id, "switch_lang"), callback_data="switch_lang")])
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def plan_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, plan_id: str):
    query = update.callback_query
    user_id = query.from_user.id
    plans = load_plans()
    plan = next((p for p in plans if p["id"] == plan_id), None)
    if not plan:
        await query.edit_message_text(t(user_id, "plan_not_found"))
        return
    traffic = t(user_id, "unlimited") if plan['total_gb'] == 0 else f"{plan['total_gb']}GB"
    msg = (
        f"<b>{plan['name']}</b>\n\n"
        f"{plan['description']}\n\n"
        f"{t(user_id, 'price')}: <b>{plan['price_display']}</b>\n"
        f"{t(user_id, 'traffic')}: {traffic}\n"
        f"{t(user_id, 'duration')}: {plan['expiry_days']} {t(user_id, 'days')}\n"
        f"{t(user_id, 'protocols')}: {', '.join(plan['protocols'])}\n\n"
        f"{t(user_id, 'buy_now')}"
    )
    keyboard = [
        [InlineKeyboardButton(t(user_id, "buy_now"), callback_data=f"buy_now_{plan_id}")],
        [InlineKeyboardButton(t(user_id, "back"), callback_data="buy_menu")],
    ]
    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def buy_now(update: Update, context: ContextTypes.DEFAULT_TYPE, plan_id: str):
    query = update.callback_query
    user_id = query.from_user.id
    plans = load_plans()
    plan = next((p for p in plans if p["id"] == plan_id), None)
    if not plan:
        await query.edit_message_text(t(user_id, "plan_not_found"))
        return

    pending_orders[user_id] = {"plan_id": plan_id, "state": "waiting_screenshot"}

    msg = (
        f"<b>{t(user_id, 'payment_title', plan=plan['name'])}</b>\n\n"
        f"{t(user_id, 'amount')}: <b>{plan['price_display']}</b>\n\n"
        f"{t(user_id, 'transfer_to')}\n"
        f"<code>{t(user_id, 'card_number')}: 1234-5678-9012-3456</code>\n"
        f"{t(user_id, 'bank')}: Sample Bank\n"
        f"{t(user_id, 'name')}: Your Name\n\n"
        f"{t(user_id, 'after_payment')}"
    )
    keyboard = [[InlineKeyboardButton(t(user_id, "cancel"), callback_data="cancel_order")]]
    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    if user_id in pending_orders:
        del pending_orders[user_id]
    await query.edit_message_text(t(user_id, "order_cancelled"))


async def handle_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    print(f"[Bot] Photo/image received from user {user_id}")

    if user_id not in pending_orders or pending_orders[user_id]["state"] != "waiting_screenshot":
        print(f"[Bot] User {user_id} not in waiting_screenshot state, ignoring")
        return

    print(f"[Bot] User {user_id} is waiting for screenshot, processing...")

    plan_id = pending_orders[user_id]["plan_id"]
    plans = load_plans()
    plan = next((p for p in plans if p["id"] == plan_id), None)
    if not plan:
        await update.message.reply_text(t(user_id, "plan_not_found"))
        del pending_orders[user_id]
        return

    order_id = str(uuid.uuid4())[:8]
    orders = _db.get_all_orders()

    # Get file_id from photo or document
    file_id = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        print(f"[Bot] Got photo file_id: {file_id}")
    elif update.message.document and update.message.document.mime_type and update.message.document.mime_type.startswith("image/"):
        file_id = update.message.document.file_id
        print(f"[Bot] Got document image file_id: {file_id}")

    new_order = {
        "user_id": user_id,
        "username": update.message.from_user.username or "N/A",
        "first_name": update.message.from_user.first_name or "N/A",
        "plan_id": plan_id,
        "plan_name": plan["name"],
        "price": plan["price"],
        "price_display": plan["price_display"],
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "screenshot_file_id": file_id,
        "admin_messages": {},
    }
    _db.save_order(order_id, new_order)
    del pending_orders[user_id]

    await update.message.reply_text(
        f"{t(user_id, 'screenshot_received')}\n\n"
        f"{t(user_id, 'order_id')}: <code>{order_id}</code>\n"
        f"{t(user_id, 'plan')}: {plan['name']}\n"
        f"{t(user_id, 'amount')}: {plan['price_display']}\n\n"
        f"{t(user_id, 'being_reviewed')}",
        parse_mode="HTML"
    )

    for admin_id in ADMIN_USER_IDS:
        try:
            keyboard = [
                [
                    InlineKeyboardButton(t(admin_id, "approve"), callback_data=f"approve_order_{order_id}"),
                    InlineKeyboardButton(t(admin_id, "reject"), callback_data=f"reject_order_{order_id}"),
                ]
            ]
            caption = (
                f"<b>{t(admin_id, 'new_order', order_id=order_id)}</b>\n\n"
                f"{t(admin_id, 'user')}: {update.message.from_user.first_name} (@{update.message.from_user.username or 'N/A'})\n"
                f"{t(admin_id, 'user_id')}: <code>{user_id}</code>\n"
                f"{t(admin_id, 'plan')}: {plan['name']}\n"
                f"{t(admin_id, 'amount')}: {plan['price_display']}\n"
                f"{t(admin_id, 'time')}: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )

            if file_id:
                print(f"[Bot] Forwarding screenshot to admin {admin_id}")
                sent_msg = await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=file_id,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
                _db.update_order_fields(order_id, admin_messages={
                    **(_db.get_order(order_id) or {}).get("admin_messages", {}),
                    str(admin_id): {"message_id": sent_msg.message_id, "is_photo": True},
                })
                print(f"[Bot] Screenshot forwarded to admin {admin_id}, msg_id={sent_msg.message_id}")
            else:
                sent_msg = await context.bot.send_message(
                    chat_id=admin_id,
                    text=caption,
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
                _db.update_order_fields(order_id, admin_messages={
                    **(_db.get_order(order_id) or {}).get("admin_messages", {}),
                    str(admin_id): {"message_id": sent_msg.message_id, "is_photo": False},
                })
                print(f"[Bot] Order {order_id} notified to admin {admin_id}, msg_id={sent_msg.message_id}")
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")
            print(f"[Bot] ERROR forwarding to admin {admin_id}: {e}")


async def update_all_admin_messages(context, order_id: str, new_text: str, new_keyboard=None):
    """Update all admin messages for an order (handles both photo and text messages)."""
    order = _db.get_order(order_id)
    if not order:
        return
    admin_messages = order.get("admin_messages", {})
    for admin_id_str, msg_info in admin_messages.items():
        try:
            admin_id = int(admin_id_str)
            message_id = msg_info["message_id"]
            is_photo = msg_info.get("is_photo", False)
            if is_photo:
                await context.bot.edit_message_caption(
                    chat_id=admin_id,
                    message_id=message_id,
                    caption=new_text,
                    parse_mode="HTML",
                    reply_markup=new_keyboard,
                )
            else:
                await context.bot.edit_message_text(
                    chat_id=admin_id,
                    message_id=message_id,
                    text=new_text,
                    parse_mode="HTML",
                    reply_markup=new_keyboard,
                )
            print(f"[Bot] Updated admin {admin_id_str} message {message_id}")
        except Exception as e:
            print(f"[Bot] Failed to update admin {admin_id_str} message {message_id}: {e}")
            logger.error(f"Failed to update admin {admin_id_str}: {e}")


async def approve_order(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: str):
    query = update.callback_query
    admin_id = query.from_user.id
    try:
        order = _db.get_order(order_id)
        if not order:
            await query.answer(t(admin_id, "order_not_found"))
            return
        if order["status"] != "pending":
            await query.answer(t(admin_id, "order_already_processed"), show_alert=True)
            return

        # Test orders don't have a real plan — skip plan validation
        if order.get("order_type") != "test":
            plans = load_plans()
            plan = next((p for p in plans if p["id"] == order["plan_id"]), None)
            if not plan:
                await query.answer(t(admin_id, "plan_not_found"))
                return

        await query.answer()

        # Try to edit message - works for text messages, fails for photo messages
        try:
            await query.edit_message_text(t(admin_id, "loading"))
        except Exception:
            # Photo message - can't edit text, just send new message
            await context.bot.send_message(chat_id=admin_id, text=t(admin_id, "loading"))

        # Fetch inbounds for selection
        ib_data = await panel.inbounds_list()
        if not ib_data.get("success"):
            await query.edit_message_text(f"{t(admin_id, 'error')}: {ib_data.get('msg', 'Unknown')}")
            return

        inbounds = ib_data.get("obj", [])
        if not inbounds:
            await query.edit_message_text(t(admin_id, "no_inbounds"))
            return

        # Store approval state
        approval_state[admin_id] = {
            "order_id": order_id,
            "selected_inbounds": [],
            "inbounds": [{"id": ib["id"], "remark": ib.get("remark", "N/A"), "protocol": ib.get("protocol", "?"), "port": ib.get("port", "?")} for ib in inbounds],
        }

        await _show_approval_inbound_selection(update, context, admin_id)

    except Exception as e:
        logger.error(f"approve_order error: {e}", exc_info=True)
        try:
            await query.answer(f"Error: {str(e)[:100]}", show_alert=True)
        except:
            pass


async def _show_approval_inbound_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id: int):
    query = update.callback_query
    state = approval_state[admin_id]
    inbounds = state["inbounds"]
    selected = state["selected_inbounds"]

    keyboard = []
    for ib in inbounds:
        check = "✅" if ib["id"] in selected else "⬜"
        text = f"{check} {ib['remark']} ({ib['protocol']}:{ib['port']})"
        keyboard.append([InlineKeyboardButton(text, callback_data=f"approve_toggle_{ib['id']}")])

    if len(selected) == len(inbounds):
        keyboard.append([InlineKeyboardButton(f"⬜ {t(admin_id, 'all_inbounds')}", callback_data="approve_toggle_all")])
    else:
        keyboard.append([InlineKeyboardButton(f"✅ {t(admin_id, 'all_inbounds')}", callback_data="approve_toggle_all")])

    if selected:
        keyboard.append([InlineKeyboardButton(f"✅ {t(admin_id, 'approve')} ({len(selected)})", callback_data="approve_confirm")])
    keyboard.append([InlineKeyboardButton(t(admin_id, "cancel"), callback_data="approve_cancel")])

    order_id = state["order_id"]
    order = _db.get_order(order_id) or {}

    if order.get("order_type") == "test":
        plan_name = f"🧪 {t(admin_id, 'test_account_btn')} — {TEST_ACCOUNT_DAYS}d / {TEST_ACCOUNT_GB}GB"
    else:
        plans = load_plans()
        plan = next((p for p in plans if p["id"] == order.get("plan_id")), None)
        plan_name = plan["name"] if plan else "N/A"

    msg = (
        f"<b>{t(admin_id, 'approve')} - Order #{order_id}</b>\n"
        f"Plan: {plan_name}\n\n"
        f"{t(admin_id, 'select_inbounds')}\n"
        f"{t(admin_id, 'selected')}: {len(selected)}/{len(inbounds)}"
    )

    # Try edit, if fails (photo message) send new message
    try:
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception:
        sent = await context.bot.send_message(chat_id=admin_id, text=msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        state["message_id"] = sent.message_id


async def approve_toggle_inbound(update: Update, context: ContextTypes.DEFAULT_TYPE, inbound_id: int):
    query = update.callback_query
    admin_id = query.from_user.id
    state = approval_state.get(admin_id)
    if not state:
        await query.answer("Session expired. Start over.", show_alert=True)
        return

    if inbound_id in state["selected_inbounds"]:
        state["selected_inbounds"].remove(inbound_id)
    else:
        state["selected_inbounds"].append(inbound_id)

    await query.answer()
    # If we have a stored message_id (from photo message), edit that message
    if "message_id" in state:
        try:
            await context.bot.edit_message_text(
                chat_id=admin_id,
                message_id=state["message_id"],
                text="",  # will be replaced by _show_approval_inbound_selection
                parse_mode="HTML",
            )
        except:
            pass
    await _show_approval_inbound_selection(update, context, admin_id)


async def approve_toggle_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    admin_id = query.from_user.id
    state = approval_state.get(admin_id)
    if not state:
        await query.answer("Session expired. Start over.", show_alert=True)
        return

    all_ids = [ib["id"] for ib in state["inbounds"]]
    if len(state["selected_inbounds"]) == len(all_ids):
        state["selected_inbounds"] = []
    else:
        state["selected_inbounds"] = all_ids[:]

    await query.answer()
    await _show_approval_inbound_selection(update, context, admin_id)


async def approve_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    admin_id = query.from_user.id
    state = approval_state.get(admin_id)
    if not state:
        await query.answer("Session expired. Start over.", show_alert=True)
        return

    if not state["selected_inbounds"]:
        await query.answer(t(admin_id, "no_inbounds_selected"), show_alert=True)
        return

    await query.answer()

    # Try to edit, if fails (photo message) send new message
    try:
        await query.edit_message_text(t(admin_id, "loading"))
    except Exception:
        await context.bot.send_message(chat_id=admin_id, text=t(admin_id, "loading"))

    order_id = state["order_id"]
    selected_inbounds = state["selected_inbounds"]
    del approval_state[admin_id]

    try:
        order = _db.get_order(order_id)
        if not order or order["status"] != "pending":
            await context.bot.send_message(chat_id=admin_id, text=t(admin_id, "order_already_processed"))
            return

        is_test = order.get("order_type") == "test"

        # Resolve plan parameters — test orders don't use plans.json
        if is_test:
            expiry_days = TEST_ACCOUNT_DAYS
            total_bytes = TEST_ACCOUNT_GB * 1024 * 1024 * 1024
            plan_name = t(admin_id, "test_account_btn")
            traffic_label = f"{TEST_ACCOUNT_GB}GB"
        else:
            plans = load_plans()
            plan = next((p for p in plans if p["id"] == order["plan_id"]), None)
            if not plan:
                await context.bot.send_message(chat_id=admin_id, text=t(admin_id, "plan_not_found"))
                return
            expiry_days = plan["expiry_days"]
            total_bytes = plan["total_gb"] * 1024 * 1024 * 1024 if plan["total_gb"] > 0 else 0
            plan_name = plan["name"]
            traffic_label = t(admin_id, "unlimited") if plan["total_gb"] == 0 else f"{plan['total_gb']}GB"

        email = f"tg_{order['user_id']}_{order_id}"
        expiry = int((datetime.now() + timedelta(days=expiry_days)).timestamp() * 1000)
        expiry_str = (datetime.now() + timedelta(days=expiry_days)).strftime("%Y-%m-%d %H:%M")

        client_payload = {
            "client": {
                "email": email,
                "enable": True,
                "expiryTime": expiry,
                "totalGB": total_bytes,
                "tgId": 0,
                "limitIp": 0,
            },
            "inboundIds": selected_inbounds,
        }

        result = await panel.client_add(client_payload)
        if not result.get("success"):
            await context.bot.send_message(chat_id=admin_id, text=f"{t(admin_id, 'error')}: {result.get('msg', 'Unknown')}")
            return

        _db.update_order_fields(order_id,
            status="approved",
            email=email,
            approved_by=admin_id,
            approved_at=datetime.now().isoformat(),
        )

        # Get subId
        sub_link = ""
        sub_id = ""
        client_result = await panel.client_get(email)
        if client_result.get("success") and client_result.get("obj"):
            inner_client = client_result["obj"].get("client", client_result["obj"])
            sub_id = inner_client.get("subId", "")
        if not sub_id:
            list_result = await panel.clients_list()
            if list_result.get("success") and list_result.get("obj"):
                for cl in list_result["obj"]:
                    if cl.get("email") == email:
                        sub_id = cl.get("subId", "")
                        break
        if sub_id:
            sub_link = f"https://time.consub.online:2096/QwKSHCxv7daW6W4uqS/sub/{sub_id}"
            # Persist sub_link so My Plans can show it
            _db.update_order_fields(order_id, sub_link=sub_link)

        admin_name = update.effective_user.first_name or str(admin_id)

        # Update all admin messages
        approved_text = (
            f"✅ <b>{t(admin_id, 'order_approved', order_id=order_id)}</b>\n\n"
            f"{t(admin_id, 'client_created')}: <code>{email}</code>\n"
            f"{t(admin_id, 'plan')}: {plan_name}\n"
            f"{t(admin_id, 'duration')}: {expiry_str}\n"
            f"{t(admin_id, 'traffic')}: {traffic_label}\n"
            f"Inbounds: {len(selected_inbounds)}"
        )
        if sub_link:
            approved_text += f"\n\n<b>Sub Link:</b>\n<code>{sub_link}</code>"
        approved_text += f"\n\nApproved by: {admin_name}"

        await update_all_admin_messages(context, order_id, approved_text)

        # Also send a direct success message to the admin who approved
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=approved_text,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Failed to send success to admin {admin_id}: {e}")

        # Notify customer
        if is_test:
            # Test account: QR code photo with full details in the caption (single message)
            if sub_link:
                try:
                    await _send_test_account_to_user(context, order["user_id"], order_id, sub_link, expiry_str)
                except Exception as e:
                    logger.error(f"Failed to send test account to user {order['user_id']}: {e}")
            else:
                try:
                    await context.bot.send_message(
                        chat_id=order["user_id"],
                        text=(
                            f"🧪 <b>{t(order['user_id'], 'test_account_ready')}</b>\n\n"
                            f"{t(order['user_id'], 'order_id')}: <code>{order_id}</code>\n"
                            f"{t(order['user_id'], 'duration')}: {expiry_days} {t(order['user_id'], 'days')} ({expiry_str})\n"
                            f"{t(order['user_id'], 'traffic')}: {TEST_ACCOUNT_GB}GB\n\n"
                            f"{t(order['user_id'], 'use_in_client')}"
                        ),
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.error(f"Failed to notify test user {order['user_id']}: {e}")
        else:
            # Regular order: text message + separate QR photo
            customer_text = (
                f"✅ <b>{t(order['user_id'], 'order_approved_title')}</b>\n\n"
                f"{t(order['user_id'], 'order_id')}: <code>{order_id}</code>\n"
                f"{t(order['user_id'], 'plan')}: {plan_name}\n"
                f"{t(order['user_id'], 'duration')}: {expiry_str}\n"
                f"{t(order['user_id'], 'traffic')}: {traffic_label}"
            )
            if sub_link:
                customer_text += f"\n\n<b>🔗 {t(order['user_id'], 'get_links')}:</b>\n<code>{sub_link}</code>"
            customer_text += f"\n\n{t(order['user_id'], 'use_in_client')}"
            customer_text += _build_app_recommendations(order["user_id"])

            try:
                await context.bot.send_message(chat_id=order["user_id"], text=customer_text, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Failed to notify user {order['user_id']}: {e}")

            if sub_link:
                try:
                    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
                    qr.add_data(sub_link)
                    qr.make(fit=True)
                    img = qr.make_image(fill_color="black", back_color="white")
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    buf.seek(0)
                    await context.bot.send_photo(
                        chat_id=order["user_id"],
                        photo=buf,
                        caption=f"📷 {t(order['user_id'], 'qr_code_caption')}{_build_app_recommendations(order['user_id'])}",
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.error(f"Failed to send QR code to user {order['user_id']}: {e}")

    except Exception as e:
        logger.error(f"approve_confirm error: {e}", exc_info=True)
        try:
            await context.bot.send_message(chat_id=admin_id, text=f"Error: {str(e)[:200]}")
        except:
            pass


async def approve_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    admin_id = query.from_user.id
    if admin_id in approval_state:
        del approval_state[admin_id]
    await query.answer()
    try:
        await query.edit_message_text(t(admin_id, "order_cancelled"))
    except Exception:
        await context.bot.send_message(chat_id=admin_id, text=t(admin_id, "order_cancelled"))


async def reject_order(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: str):
    query = update.callback_query
    admin_id = query.from_user.id
    try:
        order = _db.get_order(order_id)
        if not order:
            await query.answer(t(admin_id, "order_not_found"))
            return
        if order["status"] != "pending":
            await query.answer(t(admin_id, "order_already_processed"), show_alert=True)
            return

        await query.answer()

        _db.update_order_fields(order_id,
            status="rejected",
            rejected_by=admin_id,
            rejected_at=datetime.now().isoformat(),
        )

        admin_name = update.effective_user.first_name or str(admin_id)

        rejected_text = (
            f"❌ <b>{t(admin_id, 'order_rejected', order_id=order_id)}</b>\n\n"
            f"{t(admin_id, 'plan')}: {order['plan_name']}\n"
            f"{t(admin_id, 'amount')}: {order['price_display']}\n\n"
            f"Rejected by: {admin_name}"
        )
        await update_all_admin_messages(context, order_id, rejected_text)

        # Notify customer
        try:
            await context.bot.send_message(
                chat_id=order["user_id"],
                text=(
                    f"❌ <b>{t(order['user_id'], 'order_rejected_title')}</b>\n\n"
                    f"{t(order['user_id'], 'order_id')}: <code>{order_id}</code>\n"
                    f"{t(order['user_id'], 'plan')}: {order['plan_name']}\n\n"
                    f"{t(order['user_id'], 'contact_support')}"
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Failed to notify user {order['user_id']}: {e}")

    except Exception as e:
        logger.error(f"reject_order error: {e}", exc_info=True)
        try:
            await query.answer(f"Error: {str(e)[:100]}", show_alert=True)
        except:
            pass


# ==================== TEST ACCOUNT ====================
TEST_ACCOUNT_DAYS = 1
TEST_ACCOUNT_GB = 1

# Inbound filter for test accounts: skip those with no remark or remark containing "tcp" (case-insensitive)
def _is_test_inbound(ib: dict) -> bool:
    remark = (ib.get("remark") or "").strip()
    if not remark:
        return False
    if "tcp" in remark.lower():
        return False
    return True


def _build_app_recommendations(user_id: int) -> str:
    return (
        f"\n\n📱 <b>{t(user_id, 'recommended_apps')}</b>\n"
        f"• Android: <b>v2rayNG</b> / <b>v2Box</b>\n"
        f"• Windows: <b>v2rayN</b> / <b>Happ</b>"
    )


async def request_test_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User pressed 'Get Test Account' — auto-provision immediately, no admin approval."""
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    # One test account per user, tracked by numeric Telegram ID
    if _db.has_used_test_account(user_id):
        await query.edit_message_text(
            t(user_id, "test_already_requested"),
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(t(user_id, "back"), callback_data="main_menu")]]
            ),
        )
        return

    # Show "creating…" feedback immediately
    await query.edit_message_text(t(user_id, "loading"))

    try:
        # Fetch inbounds and filter
        ib_data = await panel.inbounds_list()
        if not ib_data.get("success"):
            await context.bot.send_message(
                chat_id=user_id,
                text=f"{t(user_id, 'error')}: {ib_data.get('msg', 'Unknown')}",
            )
            return

        all_inbounds = ib_data.get("obj", [])
        selected_inbounds = [ib["id"] for ib in all_inbounds if _is_test_inbound(ib)]

        if not selected_inbounds:
            await context.bot.send_message(
                chat_id=user_id,
                text=t(user_id, "no_inbounds"),
            )
            return

        order_id = "test_" + str(uuid.uuid4())[:8]
        email = f"tg_{user_id}_{order_id}"
        expiry = int((datetime.now() + timedelta(days=TEST_ACCOUNT_DAYS)).timestamp() * 1000)
        expiry_str = (datetime.now() + timedelta(days=TEST_ACCOUNT_DAYS)).strftime("%Y-%m-%d %H:%M")
        total_bytes = TEST_ACCOUNT_GB * 1024 * 1024 * 1024

        client_payload = {
            "client": {
                "email": email,
                "enable": True,
                "expiryTime": expiry,
                "totalGB": total_bytes,
                "tgId": 0,
                "limitIp": 0,
            },
            "inboundIds": selected_inbounds,
        }

        result = await panel.client_add(client_payload)
        if not result.get("success"):
            await context.bot.send_message(
                chat_id=user_id,
                text=f"{t(user_id, 'error')}: {result.get('msg', 'Unknown')}",
            )
            return

        # Mark user as having used their test — do this right after successful creation
        _db.mark_test_account_used(user_id)

        # Resolve sub link
        sub_link = ""
        sub_id = ""
        client_result = await panel.client_get(email)
        if client_result.get("success") and client_result.get("obj"):
            inner = client_result["obj"].get("client", client_result["obj"])
            sub_id = inner.get("subId", "")
        if not sub_id:
            list_result = await panel.clients_list()
            if list_result.get("success") and list_result.get("obj"):
                for cl in list_result["obj"]:
                    if cl.get("email") == email:
                        sub_id = cl.get("subId", "")
                        break
        if sub_id:
            sub_link = f"https://time.consub.online:2096/QwKSHCxv7daW6W4uqS/sub/{sub_id}"

        # Save order record
        new_order = {
            "user_id": user_id,
            "username": query.from_user.username or "N/A",
            "first_name": query.from_user.first_name or "N/A",
            "plan_id": "__test__",
            "plan_name": t(user_id, "test_account_btn"),
            "price": 0,
            "price_display": t(user_id, "free"),
            "order_type": "test",
            "status": "approved",
            "email": email,
            "sub_link": sub_link,
            "screenshot_file_id": None,
            "admin_messages": {},
            "approved_at": datetime.now().isoformat(),
            "created_at": datetime.now().isoformat(),
        }
        _db.save_order(order_id, new_order)

        # Send QR + details to user
        await _send_test_account_to_user(context, user_id, order_id, sub_link, expiry_str)

        # Notify admins — info only, no buttons
        admin_info = (
            f"🧪 <b>Test account created</b>\n\n"
            f"{t(ADMIN_USER_IDS[0] if ADMIN_USER_IDS else user_id, 'user')}: "
            f"{query.from_user.first_name} (@{query.from_user.username or 'N/A'})\n"
            f"ID: <code>{user_id}</code>\n"
            f"Email: <code>{email}</code>\n"
            f"Inbounds: {len(selected_inbounds)}\n"
            f"Expires: {expiry_str}"
        )
        if sub_link:
            admin_info += f"\n\n<code>{sub_link}</code>"
        for admin_id in ADMIN_USER_IDS:
            try:
                await context.bot.send_message(chat_id=admin_id, text=admin_info, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id} of test account: {e}")

    except Exception as e:
        logger.error(f"request_test_account error: {e}", exc_info=True)
        try:
            await context.bot.send_message(chat_id=user_id, text=f"{t(user_id, 'error')}: {str(e)[:200]}")
        except Exception:
            pass


async def _send_test_account_to_user(context, user_id: int, order_id: str, sub_link: str, expiry_str: str):
    """Send QR code photo with the sub link + app recommendations — all in one message."""
    caption = (
        f"🧪 <b>{t(user_id, 'test_account_ready')}</b>\n\n"
        f"{t(user_id, 'order_id')}: <code>{order_id}</code>\n"
        f"{t(user_id, 'duration')}: {TEST_ACCOUNT_DAYS} {t(user_id, 'days')} ({expiry_str})\n"
        f"{t(user_id, 'traffic')}: {TEST_ACCOUNT_GB}GB\n\n"
        f"🔗 <b>{t(user_id, 'get_links')}:</b>\n<code>{sub_link}</code>\n\n"
        f"📷 {t(user_id, 'qr_code_caption')}"
        f"{_build_app_recommendations(user_id)}"
    )
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
    qr.add_data(sub_link)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    await context.bot.send_photo(
        chat_id=user_id,
        photo=buf,
        caption=caption,
        parse_mode="HTML",
    )


async def pending_orders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    pending = _db.get_pending_orders()
    if not pending:
        await query.edit_message_text(t(user_id, "no_pending"))
        return
    msg = f"<b>{t(user_id, 'pending_orders')}</b>\n\n"
    keyboard = []
    for oid, order in list(pending.items())[:10]:
        text = f"#{oid} - {order['plan_name']} - {order['price_display']}"
        keyboard.append([InlineKeyboardButton(text, callback_data=f"view_order_{oid}")])
    keyboard.append([InlineKeyboardButton(t(user_id, "back"), callback_data="main_menu")])
    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def view_order(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: str):
    query = update.callback_query
    user_id = query.from_user.id
    order = _db.get_order(order_id)
    if not order:
        await query.edit_message_text(t(user_id, "order_not_found"))
        return
    status_key = order["status"]
    msg = (
        f"<b>{t(user_id, 'order_details', order_id=order_id)}</b>\n\n"
        f"{t(user_id, 'user')}: {order['first_name']} (@{order['username']})\n"
        f"{t(user_id, 'user_id')}: <code>{order['user_id']}</code>\n"
        f"{t(user_id, 'plan')}: {order['plan_name']}\n"
        f"{t(user_id, 'amount')}: {order['price_display']}\n"
        f"{t(user_id, 'status')}: {t(user_id, status_key)}\n"
        f"{t(user_id, 'created')}: {order['created_at']}"
    )
    keyboard = []
    if order["status"] == "pending":
        keyboard = [
            [
                InlineKeyboardButton(t(user_id, "approve"), callback_data=f"approve_order_{order_id}"),
                InlineKeyboardButton(t(user_id, "reject"), callback_data=f"reject_order_{order_id}"),
            ],
        ]
    keyboard.append([InlineKeyboardButton(t(user_id, "back"), callback_data="pending_orders")])
    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


# ==================== MY PLANS ====================
async def my_plans_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show a user all their approved orders (plans + sub links)."""
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    orders = _db.get_user_approved_orders(user_id)
    if not orders:
        await query.edit_message_text(
            t(user_id, "my_plans_empty"),
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(t(user_id, "back"), callback_data="main_menu")]]
            ),
        )
        return

    msg = f"<b>{t(user_id, 'my_plans_title')}</b>\n\n"
    keyboard = []
    for order in orders[:15]:
        icon = "🧪" if order.get("order_type") == "test" else "📦"
        label = f"{icon} {order['plan_name']} — #{order['order_id']}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"my_plan_{order['order_id']}")])
    keyboard.append([InlineKeyboardButton(t(user_id, "back"), callback_data="main_menu")])
    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def my_plan_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: str):
    """Show details + sub link for one of the user's approved plans."""
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    order = _db.get_order(order_id)
    if not order or order.get("user_id") != user_id:
        await query.edit_message_text(
            t(user_id, "order_not_found"),
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(t(user_id, "back"), callback_data="my_plans")]]
            ),
        )
        return

    sub_link = order.get("sub_link") or ""
    approved_at = order.get("approved_at", "")[:16] if order.get("approved_at") else "—"
    icon = "🧪" if order.get("order_type") == "test" else "📦"

    msg = (
        f"{icon} <b>{order['plan_name']}</b>\n\n"
        f"{t(user_id, 'order_id')}: <code>{order_id}</code>\n"
        f"{t(user_id, 'status')}: ✅ {t(user_id, 'approved')}\n"
        f"{t(user_id, 'approved')}: {approved_at}\n"
    )
    if order.get("price_display") and order.get("order_type") != "test":
        msg += f"{t(user_id, 'amount')}: {order['price_display']}\n"

    keyboard = []
    if sub_link:
        msg += f"\n🔗 <b>{t(user_id, 'get_links')}:</b>\n<code>{sub_link}</code>"
        keyboard.append([InlineKeyboardButton(t(user_id, "show_qr"), callback_data=f"my_plan_qr_{order_id}")])

    msg += f"\n\n{t(user_id, 'use_in_client')}"
    keyboard.append([InlineKeyboardButton(t(user_id, "back"), callback_data="my_plans")])

    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def my_plan_qr(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: str):
    """Send QR code for a plan the user already has."""
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    order = _db.get_order(order_id)
    if not order or order.get("user_id") != user_id or not order.get("sub_link"):
        await query.answer(t(user_id, "order_not_found"), show_alert=True)
        return

    sub_link = order["sub_link"]
    try:
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
        qr.add_data(sub_link)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        await context.bot.send_photo(
            chat_id=user_id,
            photo=buf,
            caption=f"📷 {t(user_id, 'qr_code_caption')}\n\n<code>{sub_link}</code>",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"my_plan_qr error for user {user_id}: {e}")


# ==================== LANGUAGE ====================
async def switch_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    new_lang = toggle_lang(user_id)
    await query.answer()
    is_admin = user_id in ADMIN_USER_IDS
    if is_admin:
        keyboard = [
            [InlineKeyboardButton(t(user_id, "server_status"), callback_data="server_status")],
            [InlineKeyboardButton(t(user_id, "inbounds"), callback_data="inbounds_menu"), InlineKeyboardButton(t(user_id, "clients"), callback_data="clients_menu")],
            [InlineKeyboardButton(t(user_id, "nodes"), callback_data="nodes_menu"), InlineKeyboardButton(t(user_id, "hosts"), callback_data="hosts_menu")],
            [InlineKeyboardButton(t(user_id, "xray_management"), callback_data="xray_menu"), InlineKeyboardButton(t(user_id, "settings"), callback_data="settings_menu")],
            [InlineKeyboardButton(t(user_id, "get_links"), callback_data="get_links"), InlineKeyboardButton(t(user_id, "online_clients"), callback_data="online_clients")],
            [InlineKeyboardButton(t(user_id, "pending_orders_btn"), callback_data="pending_orders")],
            [InlineKeyboardButton(t(user_id, "switch_lang"), callback_data="switch_lang")],
        ]
        await query.edit_message_text(f"✅ {t(user_id, 'lang_set')}\n\n{t(user_id, 'admin_welcome')}", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        keyboard = [
            [InlineKeyboardButton(t(user_id, "buy_vpn"), callback_data="buy_menu")],
            [InlineKeyboardButton(t(user_id, "test_account_btn"), callback_data="test_account"),
             InlineKeyboardButton(t(user_id, "my_plans_btn"), callback_data="my_plans")],
            [InlineKeyboardButton(t(user_id, "support"), callback_data="support")],
            [InlineKeyboardButton(t(user_id, "switch_lang"), callback_data="switch_lang")],
        ]
        await query.edit_message_text(f"✅ {t(user_id, 'lang_set')}\n\n{t(user_id, 'welcome')}", reply_markup=InlineKeyboardMarkup(keyboard))


# ==================== SERVER ====================
@admin_only
async def server_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Loading server status...")
    data = await panel.server_status()
    if not data.get("success"):
        await update.message.reply_text(f"Error: {data.get('msg', 'Unknown')}")
        return
    obj = data.get("obj", {})
    msg = (
        f"<b>Server Status</b>\n\n"
        f"CPU: {obj.get('cpu', 'N/A')}%\n"
        f"Memory: {format_bytes(obj.get('mem', {}).get('current', 0))} / {format_bytes(obj.get('mem', {}).get('total', 0))}\n"
        f"Swap: {format_bytes(obj.get('swap', {}).get('current', 0))} / {format_bytes(obj.get('swap', {}).get('total', 0))}\n"
        f"Disk: {format_bytes(obj.get('disk', {}).get('current', 0))} / {format_bytes(obj.get('disk', {}).get('total', 0))}\n"
        f"Uptime: {obj.get('uptime', 'N/A')}s\n"
        f"Xray: {'Running' if obj.get('xray') else 'Stopped'}\n"
        f"Load: {obj.get('load', 'N/A')}"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


# ==================== INBOUNDS ====================
@admin_only
async def inbounds_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Loading inbounds...")
    data = await panel.inbounds_list()
    if not data.get("success"):
        await update.message.reply_text(f"Error: {data.get('msg', 'Unknown')}")
        return
    inbounds = data.get("obj", [])
    if not inbounds:
        await update.message.reply_text("No inbounds found.")
        return
    keyboard = []
    for ib in inbounds[:20]:
        status = "ON" if ib.get("enable") else "OFF"
        text = f"[{status}] {ib.get('remark', 'N/A')} ({ib.get('protocol', '?')}:{ib.get('port', '?')})"
        keyboard.append([InlineKeyboardButton(text, callback_data=f"inbound_{ib['id']}")])
    keyboard.append([InlineKeyboardButton("Add Inbound", callback_data="inbound_add")])
    keyboard.append([InlineKeyboardButton("Back", callback_data="main_menu")])
    await update.message.reply_text("Inbounds:", reply_markup=InlineKeyboardMarkup(keyboard))


@admin_only
async def inbound_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, inbound_id: int):
    query = update.callback_query
    await query.edit_message_text("Loading inbound...")
    data = await panel.inbound_get(inbound_id)
    if not data.get("success"):
        await query.edit_message_text(f"Error: {data.get('msg', 'Unknown')}")
        return
    ib = data.get("obj", {})
    clients = ib.get("settings", {}).get("clients", []) if ib.get("settings") else []
    status = "Enabled" if ib.get("enable") else "Disabled"
    msg = (
        f"<b>Inbound: {ib.get('remark', 'N/A')}</b>\n\n"
        f"ID: {ib.get('id')}\n"
        f"Protocol: {ib.get('protocol')}\n"
        f"Port: {ib.get('port')}\n"
        f"Tag: {ib.get('tag')}\n"
        f"Status: {status}\n"
        f"Clients: {len(clients)}\n"
        f"Up: {format_bytes(ib.get('up', 0))}\n"
        f"Down: {format_bytes(ib.get('down', 0))}"
    )
    keyboard = [
        [
            InlineKeyboardButton("Enable" if not ib.get("enable") else "Disable", callback_data=f"inbound_toggle_{inbound_id}"),
            InlineKeyboardButton("Reset Traffic", callback_data=f"inbound_reset_{inbound_id}"),
        ],
        [
            InlineKeyboardButton("Get Links", callback_data=f"inbound_links_{inbound_id}"),
            InlineKeyboardButton("Delete", callback_data=f"inbound_del_{inbound_id}"),
        ],
        [InlineKeyboardButton("Back to Inbounds", callback_data="inbounds_menu")],
    ]
    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


@admin_only
async def inbound_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE, inbound_id: int, enable: bool):
    data = await panel.inbound_set_enable(inbound_id, enable)
    status = "enabled" if enable else "disabled"
    if data.get("success"):
        await update.callback_query.answer(f"Inbound {status}")
    else:
        await update.callback_query.answer(f"Error: {data.get('msg', 'Unknown')}")


@admin_only
async def inbound_reset(update: Update, context: ContextTypes.DEFAULT_TYPE, inbound_id: int):
    data = await panel.inbound_reset_traffic(inbound_id)
    if data.get("success"):
        await update.callback_query.answer("Traffic reset!")
    else:
        await update.callback_query.answer(f"Error: {data.get('msg', 'Unknown')}")


@admin_only
async def inbound_delete(update: Update, context: ContextTypes.DEFAULT_TYPE, inbound_id: int):
    data = await panel.inbound_delete(inbound_id)
    if data.get("success"):
        await update.callback_query.answer("Deleted!")
        await update.callback_query.edit_message_text("Inbound deleted.")
    else:
        await update.callback_query.answer(f"Error: {data.get('msg', 'Unknown')}")


@admin_only
async def inbound_links(update: Update, context: ContextTypes.DEFAULT_TYPE, inbound_id: int):
    query = update.callback_query
    await query.edit_message_text("Loading links...")
    ib_data = await panel.inbound_get(inbound_id)
    if not ib_data.get("success"):
        await query.edit_message_text("Error fetching inbound")
        return
    ib = ib_data.get("obj", {})
    clients = ib.get("settings", {}).get("clients", []) if ib.get("settings") else []
    if not clients:
        await query.edit_message_text("No clients in this inbound.")
        return
    links_data = await panel.client_links(clients[0].get("email", ""))
    if links_data.get("success"):
        links = links_data.get("obj", [])
        msg = f"<b>Links for {ib.get('remark', 'N/A')}</b>\n\n"
        for i, link in enumerate(links[:10], 1):
            msg += f"{i}. <code>{link[:150]}</code>\n"
        await query.edit_message_text(msg, parse_mode="HTML")
    else:
        await query.edit_message_text("Error fetching links")


# ==================== CLIENTS ====================
@admin_only
async def clients_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Loading clients...")
    data = await panel.clients_list()
    if not data.get("success"):
        await update.message.reply_text(f"Error: {data.get('msg', 'Unknown')}")
        return
    clients = data.get("obj", [])
    if not clients:
        await update.message.reply_text("No clients found.")
        return
    keyboard = []
    for cl in clients[:20]:
        status = "ON" if cl.get("enable") else "OFF"
        email = cl.get("email", "N/A")
        text = f"[{status}] {email}"
        keyboard.append([InlineKeyboardButton(text, callback_data=f"client_{email}")])
    keyboard.append([InlineKeyboardButton("Add Client", callback_data="client_add")])
    keyboard.append([InlineKeyboardButton("Back", callback_data="main_menu")])
    await update.message.reply_text("Clients:", reply_markup=InlineKeyboardMarkup(keyboard))


@admin_only
async def client_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, email: str):
    query = update.callback_query
    await query.edit_message_text("Loading client...")
    data = await panel.client_get(email)
    if not data.get("success"):
        await query.edit_message_text(f"Error: {data.get('msg', 'Unknown')}")
        return
    cl = data.get("obj", {})
    inbound_ids = cl.get("inboundIds", [])
    status = "Enabled" if cl.get("enable") else "Disabled"
    msg = (
        f"<b>Client: {email}</b>\n\n"
        f"Status: {status}\n"
        f"Up: {format_bytes(cl.get('up', 0))}\n"
        f"Down: {format_bytes(cl.get('down', 0))}\n"
        f"Total: {format_bytes(cl.get('totalGB', 0) * 1024 * 1024 * 1024) if cl.get('totalGB') else 'Unlimited'}\n"
        f"Expiry: {format_ts(cl.get('expiryTime', 0))}\n"
        f"Inbounds: {inbound_ids}\n"
        f"Sub ID: {cl.get('subId', 'N/A')}"
    )
    keyboard = [
        [
            InlineKeyboardButton("Enable" if not cl.get("enable") else "Disable", callback_data=f"client_toggle_{email}"),
            InlineKeyboardButton("Reset Traffic", callback_data=f"client_reset_{email}"),
        ],
        [
            InlineKeyboardButton("Get Links", callback_data=f"client_links_{email}"),
            InlineKeyboardButton("Delete", callback_data=f"client_del_{email}"),
        ],
        [InlineKeyboardButton("Back to Clients", callback_data="clients_menu")],
    ]
    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


@admin_only
async def client_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE, email: str, enable: bool):
    if enable:
        data = await panel.clients_bulk_enable([email])
    else:
        data = await panel.clients_bulk_disable([email])
    status = "enabled" if enable else "disabled"
    if data.get("success"):
        await update.callback_query.answer(f"Client {status}")
    else:
        await update.callback_query.answer(f"Error: {data.get('msg', 'Unknown')}")


@admin_only
async def client_reset(update: Update, context: ContextTypes.DEFAULT_TYPE, email: str):
    data = await panel.client_reset_traffic(email)
    if data.get("success"):
        await update.callback_query.answer("Traffic reset!")
    else:
        await update.callback_query.answer(f"Error: {data.get('msg', 'Unknown')}")


@admin_only
async def client_delete(update: Update, context: ContextTypes.DEFAULT_TYPE, email: str):
    data = await panel.client_delete(email)
    if data.get("success"):
        await update.callback_query.answer("Deleted!")
        await update.callback_query.edit_message_text(f"Client {email} deleted.")
    else:
        await update.callback_query.answer(f"Error: {data.get('msg', 'Unknown')}")


@admin_only
async def client_links(update: Update, context: ContextTypes.DEFAULT_TYPE, email: str):
    query = update.callback_query
    await query.edit_message_text("Loading links...")
    data = await panel.client_links(email)
    if not data.get("success"):
        await query.edit_message_text(f"Error: {data.get('msg', 'Unknown')}")
        return
    links = data.get("obj", [])
    if not links:
        await query.edit_message_text(f"No links for {email}.")
        return
    msg = f"<b>Links for {email}</b>\n\n"
    for i, link in enumerate(links[:10], 1):
        msg += f"{i}. <code>{link[:150]}</code>\n"
    if len(links) > 10:
        msg += f"\n... and {len(links) - 10} more"
    await query.edit_message_text(msg, parse_mode="HTML")


# ==================== CLIENT CREATION FLOW ====================
async def client_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    await query.edit_message_text(t(user_id, "loading"))

    data = await panel.inbounds_list()
    if not data.get("success"):
        await query.edit_message_text(f"{t(user_id, 'error')}: {data.get('msg', 'Unknown')}")
        return

    inbounds = data.get("obj", [])
    if not inbounds:
        await query.edit_message_text(t(user_id, "no_inbounds"))
        return

    # Initialize state
    client_creation_state[user_id] = {
        "step": "select_inbounds",
        "selected_inbounds": [],
        "inbounds": [{"id": ib["id"], "remark": ib.get("remark", "N/A"), "protocol": ib.get("protocol", "?"), "port": ib.get("port", "?")} for ib in inbounds],
    }

    await _show_inbound_selection(update, context, user_id)


async def _show_inbound_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    query = update.callback_query
    state = client_creation_state[user_id]
    inbounds = state["inbounds"]
    selected = state["selected_inbounds"]

    keyboard = []
    for ib in inbounds:
        check = "✅" if ib["id"] in selected else "⬜"
        text = f"{check} {ib['remark']} ({ib['protocol']}:{ib['port']})"
        keyboard.append([InlineKeyboardButton(text, callback_data=f"toggle_inbound_{ib['id']}")])

    # Select all / Deselect all
    if len(selected) == len(inbounds):
        keyboard.append([InlineKeyboardButton(f"⬜ {t(user_id, 'all_inbounds')}", callback_data="toggle_all_inbounds")])
    else:
        keyboard.append([InlineKeyboardButton(f"✅ {t(user_id, 'all_inbounds')}", callback_data="toggle_all_inbounds")])

    # Create button
    if selected:
        keyboard.append([InlineKeyboardButton(f"➕ {t(user_id, 'create_client')} ({len(selected)})", callback_data="confirm_create_client")])
    keyboard.append([InlineKeyboardButton(t(user_id, "cancel"), callback_data="cancel_create_client")])

    msg = f"<b>{t(user_id, 'select_inbounds')}</b>\n\n"
    msg += f"{t(user_id, 'selected')}: {len(selected)}/{len(inbounds)}"

    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def toggle_inbound(update: Update, context: ContextTypes.DEFAULT_TYPE, inbound_id: int):
    query = update.callback_query
    user_id = query.from_user.id
    state = client_creation_state.get(user_id)
    if not state:
        await query.answer("Session expired. Start over.", show_alert=True)
        return

    if inbound_id in state["selected_inbounds"]:
        state["selected_inbounds"].remove(inbound_id)
    else:
        state["selected_inbounds"].append(inbound_id)

    await query.answer()
    await _show_inbound_selection(update, context, user_id)


async def toggle_all_inbounds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    state = client_creation_state.get(user_id)
    if not state:
        await query.answer("Session expired. Start over.", show_alert=True)
        return

    all_ids = [ib["id"] for ib in state["inbounds"]]
    if len(state["selected_inbounds"]) == len(all_ids):
        state["selected_inbounds"] = []
    else:
        state["selected_inbounds"] = all_ids[:]

    await query.answer()
    await _show_inbound_selection(update, context, user_id)


async def confirm_create_client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    state = client_creation_state.get(user_id)
    if not state:
        await query.answer("Session expired. Start over.", show_alert=True)
        return

    if not state["selected_inbounds"]:
        await query.answer(t(user_id, "no_inbounds_selected"), show_alert=True)
        return

    # Generate random email and remark
    import random
    import string
    random_str = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    email = f"tg_{user_id}_{random_str}"
    remark = f"User {user_id} - {random_str}"

    state["email"] = email
    state["remark"] = remark
    state["step"] = "confirm"

    selected_names = []
    for ib in state["inbounds"]:
        if ib["id"] in state["selected_inbounds"]:
            selected_names.append(f"  • {ib['remark']} ({ib['protocol']}:{ib['port']})")

    msg = (
        f"<b>{t(user_id, 'confirm_create')}</b>\n\n"
        f"<b>{t(user_id, 'client_email')}:</b> <code>{email}</code>\n"
        f"<b>{t(user_id, 'client_remark')}:</b> {remark}\n"
        f"<b>{t(user_id, 'inbounds_label')}:</b>\n"
        + "\n".join(selected_names)
    )

    keyboard = [
        [InlineKeyboardButton(f"✅ {t(user_id, 'create_client')}", callback_data="do_create_client")],
        [InlineKeyboardButton(t(user_id, "cancel"), callback_data="cancel_create_client")],
    ]
    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def do_create_client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    state = client_creation_state.get(user_id)
    if not state or state.get("step") != "confirm":
        await query.answer("Session expired. Start over.", show_alert=True)
        return

    await query.answer()
    await query.edit_message_text(t(user_id, "loading"))

    email = state["email"]
    inbound_ids = state["selected_inbounds"]

    client_payload = {
        "client": {
            "email": email,
            "enable": True,
            "tgId": 0,
            "limitIp": 0,
        },
        "inboundIds": inbound_ids,
    }

    result = await panel.client_add(client_payload)

    if result.get("success"):
        # Get subId for subscription link
        sub_link = ""
        sub_id = ""
        client_result = await panel.client_get(email)
        if client_result.get("success") and client_result.get("obj"):
            inner_client = client_result["obj"].get("client", client_result["obj"])
            sub_id = inner_client.get("subId", "")

        # Fallback: get subId from clients list
        if not sub_id:
            list_result = await panel.clients_list()
            if list_result.get("success") and list_result.get("obj"):
                for cl in list_result["obj"]:
                    if cl.get("email") == email:
                        sub_id = cl.get("subId", "")
                        break

        if sub_id:
            sub_link = f"https://time.consub.online:2096/QwKSHCxv7daW6W4uqS/sub/{sub_id}"

        msg = (
            f"✅ <b>{t(user_id, 'client_created_success')}</b>\n\n"
            f"<b>{t(user_id, 'client_email')}:</b> <code>{email}</code>\n"
            f"<b>{t(user_id, 'inbounds_label')}:</b> {len(inbound_ids)}"
        )
        if sub_link:
            msg += f"\n\n<b>🔗 Subscription Link:</b>\n<code>{sub_link}</code>"

        keyboard = [[InlineKeyboardButton(t(user_id, "back"), callback_data="clients_menu")]]
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await query.edit_message_text(
            f"{t(user_id, 'error')}: {result.get('msg', 'Unknown')}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(user_id, "back"), callback_data="clients_menu")]])
        )

    del client_creation_state[user_id]


async def cancel_create_client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    if user_id in client_creation_state:
        del client_creation_state[user_id]
    await query.answer()
    await query.edit_message_text(t(user_id, "order_cancelled"))


@admin_only
async def get_all_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text("Loading all links...")
    data = await panel.inbound_all_links()
    if not data.get("success"):
        await query.edit_message_text(f"Error: {data.get('msg', 'Unknown')}")
        return
    links = data.get("obj", [])
    if not links:
        await query.edit_message_text("No links found.")
        return
    msg = f"<b>All Links ({len(links)})</b>\n\n"
    for i, link in enumerate(links[:20], 1):
        msg += f"{i}. <code>{link[:150]}</code>\n"
    if len(links) > 20:
        msg += f"\n... and {len(links) - 20} more"
    keyboard = [[InlineKeyboardButton("Back", callback_data="main_menu")]]
    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


@admin_only
async def online_clients(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text("Checking online clients...")
    data = await panel.client_online()
    if not data.get("success"):
        await query.edit_message_text(f"Error: {data.get('msg', 'Unknown')}")
        return
    online = data.get("obj", [])
    if not online:
        await query.edit_message_text("No clients online.")
        return
    msg = f"<b>Online Clients ({len(online)})</b>\n\n"
    for email in online[:30]:
        msg += f"  {email}\n"
    if len(online) > 30:
        msg += f"\n... and {len(online) - 30} more"
    keyboard = [[InlineKeyboardButton("Back", callback_data="main_menu")]]
    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


# ==================== NODES ====================
@admin_only
async def nodes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Loading nodes...")
    data = await panel.nodes_list()
    if not data.get("success"):
        await update.message.reply_text(f"Error: {data.get('msg', 'Unknown')}")
        return
    nodes = data.get("obj", [])
    if not nodes:
        await update.message.reply_text("No nodes found.")
        return
    keyboard = []
    for node in nodes[:10]:
        status = "Online" if node.get("online") else "Offline"
        text = f"[{status}] {node.get('remark', 'N/A')}"
        keyboard.append([InlineKeyboardButton(text, callback_data=f"node_{node['id']}")])
    keyboard.append([InlineKeyboardButton("Add Node", callback_data="node_add")])
    keyboard.append([InlineKeyboardButton("Back", callback_data="main_menu")])
    await update.message.reply_text("Nodes:", reply_markup=InlineKeyboardMarkup(keyboard))


@admin_only
async def node_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, node_id: int):
    query = update.callback_query
    await query.edit_message_text("Loading node...")
    data = await panel.node_get(node_id)
    if not data.get("success"):
        await query.edit_message_text(f"Error: {data.get('msg', 'Unknown')}")
        return
    node = data.get("obj", {})
    status = "Online" if node.get("online") else "Offline"
    msg = (
        f"<b>Node: {node.get('remark', 'N/A')}</b>\n\n"
        f"ID: {node.get('id')}\n"
        f"Address: {node.get('address', 'N/A')}\n"
        f"Status: {status}\n"
        f"Last Checkin: {format_ts(node.get('lastCheckin', 0))}"
    )
    keyboard = [
        [
            InlineKeyboardButton("Enable" if not node.get("enable") else "Disable", callback_data=f"node_toggle_{node_id}"),
            InlineKeyboardButton("Delete", callback_data=f"node_del_{node_id}"),
        ],
        [InlineKeyboardButton("Back to Nodes", callback_data="nodes_menu")],
    ]
    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


@admin_only
async def node_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE, node_id: int, enable: bool):
    data = await panel.node_set_enable(node_id, enable)
    status = "enabled" if enable else "disabled"
    if data.get("success"):
        await update.callback_query.answer(f"Node {status}")
    else:
        await update.callback_query.answer(f"Error: {data.get('msg', 'Unknown')}")


@admin_only
async def node_delete(update: Update, context: ContextTypes.DEFAULT_TYPE, node_id: int):
    data = await panel.node_delete(node_id)
    if data.get("success"):
        await update.callback_query.answer("Deleted!")
        await update.callback_query.edit_message_text("Node deleted.")
    else:
        await update.callback_query.answer(f"Error: {data.get('msg', 'Unknown')}")


# ==================== HOSTS ====================
@admin_only
async def hosts_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.edit_message_text("Loading hosts...")
    else:
        await update.message.reply_text("Loading hosts...")
    data = await panel.hosts_list()
    if not data.get("success"):
        target = query.message if query else update.message
        await target.reply_text(f"Error: {data.get('msg', 'Unknown')}")
        return
    hosts = data.get("obj", [])
    if not hosts:
        target = query.message if query else update.message
        await target.reply_text("No hosts found.")
        return
    keyboard = []
    for host in hosts[:20]:
        status = "ON" if host.get("enable") else "OFF"
        text = f"[{status}] {host.get('remark', 'N/A')} (Inbound: {host.get('inboundId', '?')})"
        keyboard.append([InlineKeyboardButton(text, callback_data=f"host_{host['id']}")])
    keyboard.append([InlineKeyboardButton("Back", callback_data="main_menu")])
    target = query.message if query else update.message
    await target.reply_text("Hosts:", reply_markup=InlineKeyboardMarkup(keyboard))


# ==================== XRAY ====================
@admin_only
async def xray_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Get Config", callback_data="xray_config")],
        [InlineKeyboardButton("Outbound Traffic", callback_data="xray_traffic")],
        [InlineKeyboardButton("Restart Xray", callback_data="xray_restart")],
        [InlineKeyboardButton("Stop Xray", callback_data="xray_stop")],
        [InlineKeyboardButton("Xray Logs", callback_data="xray_logs")],
        [InlineKeyboardButton("Back", callback_data="main_menu")],
    ]
    await update.message.reply_text("Xray Management:", reply_markup=InlineKeyboardMarkup(keyboard))


@admin_only
async def xray_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text("Loading config...")
    data = await panel.xray_config()
    if not data.get("success"):
        await query.edit_message_text(f"Error: {data.get('msg', 'Unknown')}")
        return
    config = data.get("obj", "")
    if isinstance(config, str) and len(config) > 3000:
        config = config[:3000] + "\n... (truncated)"
    keyboard = [[InlineKeyboardButton("Back", callback_data="xray_menu")]]
    await query.edit_message_text(f"<b>Xray Config:</b>\n\n<code>{config}</code>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


@admin_only
async def xray_traffic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text("Loading traffic...")
    data = await panel.xray_outbound_traffic()
    if not data.get("success"):
        await query.edit_message_text(f"Error: {data.get('msg', 'Unknown')}")
        return
    obj = data.get("obj", {})
    msg = "<b>Outbound Traffic</b>\n\n"
    if isinstance(obj, dict):
        for tag, stats in obj.items():
            msg += f"<b>{tag}</b>\n  Up: {format_bytes(stats.get('up', 0))}\n  Down: {format_bytes(stats.get('down', 0))}\n\n"
    keyboard = [[InlineKeyboardButton("Back", callback_data="xray_menu")]]
    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


@admin_only
async def xray_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text("Restarting Xray...")
    data = await panel.restart_xray()
    if data.get("success"):
        await query.edit_message_text("Xray restarted successfully.")
    else:
        await query.edit_message_text(f"Error: {data.get('msg', 'Unknown')}")


@admin_only
async def xray_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text("Stopping Xray...")
    data = await panel.stop_xray()
    if data.get("success"):
        await query.edit_message_text("Xray stopped successfully.")
    else:
        await query.edit_message_text(f"Error: {data.get('msg', 'Unknown')}")


@admin_only
async def xray_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text("Loading logs...")
    data = await panel.xray_logs(30)
    if not data.get("success"):
        await query.edit_message_text(f"Error: {data.get('msg', 'Unknown')}")
        return
    logs = data.get("obj", "")
    if isinstance(logs, str) and len(logs) > 3000:
        logs = logs[-3000:]
    keyboard = [[InlineKeyboardButton("Back", callback_data="xray_menu")]]
    await query.edit_message_text(f"<b>Xray Logs:</b>\n\n<code>{logs}</code>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


# ==================== SETTINGS ====================
@admin_only
async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("View Settings", callback_data="settings_view")],
        [InlineKeyboardButton("Panel Logs", callback_data="panel_logs")],
        [InlineKeyboardButton("Back", callback_data="main_menu")],
    ]
    await update.message.reply_text("Settings:", reply_markup=InlineKeyboardMarkup(keyboard))


@admin_only
async def settings_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text("Loading settings...")
    data = await panel.settings_all()
    if not data.get("success"):
        await query.edit_message_text(f"Error: {data.get('msg', 'Unknown')}")
        return
    settings = data.get("obj", {})
    msg = "<b>Panel Settings</b>\n\n"
    if isinstance(settings, dict):
        for key, val in settings.items():
            if isinstance(val, (str, int, float, bool)):
                msg += f"<b>{key}:</b> <code>{val}</code>\n"
    if len(msg) > 3000:
        msg = msg[:3000] + "\n... (truncated)"
    keyboard = [[InlineKeyboardButton("Back", callback_data="settings_menu")]]
    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


@admin_only
async def panel_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text("Loading logs...")
    data = await panel.panel_logs(30)
    if not data.get("success"):
        await query.edit_message_text(f"Error: {data.get('msg', 'Unknown')}")
        return
    logs = data.get("obj", "")
    if isinstance(logs, str) and len(logs) > 3000:
        logs = logs[-3000:]
    keyboard = [[InlineKeyboardButton("Back", callback_data="settings_menu")]]
    await query.edit_message_text(f"<b>Panel Logs:</b>\n\n<code>{logs}</code>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


# ==================== CALLBACK ROUTER ====================
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    # Don't answer here - let each handler answer as needed

    try:
        if data == "main_menu":
            user_id = query.from_user.id
            is_admin = user_id in ADMIN_USER_IDS

            if is_admin:
                keyboard = [
                    [InlineKeyboardButton(t(user_id, "server_status"), callback_data="server_status")],
                    [InlineKeyboardButton(t(user_id, "inbounds"), callback_data="inbounds_menu"), InlineKeyboardButton(t(user_id, "clients"), callback_data="clients_menu")],
                    [InlineKeyboardButton(t(user_id, "nodes"), callback_data="nodes_menu"), InlineKeyboardButton(t(user_id, "hosts"), callback_data="hosts_menu")],
                    [InlineKeyboardButton(t(user_id, "xray_management"), callback_data="xray_menu"), InlineKeyboardButton(t(user_id, "settings"), callback_data="settings_menu")],
                    [InlineKeyboardButton(t(user_id, "get_links"), callback_data="get_links"), InlineKeyboardButton(t(user_id, "online_clients"), callback_data="online_clients")],
                    [InlineKeyboardButton(t(user_id, "pending_orders_btn"), callback_data="pending_orders")],
                    [InlineKeyboardButton(t(user_id, "switch_lang"), callback_data="switch_lang")],
                ]
                await query.edit_message_text(t(user_id, "admin_welcome"), reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                keyboard = [
                    [InlineKeyboardButton(t(user_id, "buy_vpn"), callback_data="buy_menu")],
                    [InlineKeyboardButton(t(user_id, "test_account_btn"), callback_data="test_account"),
                     InlineKeyboardButton(t(user_id, "my_plans_btn"), callback_data="my_plans")],
                    [InlineKeyboardButton(t(user_id, "support"), callback_data="support")],
                    [InlineKeyboardButton(t(user_id, "switch_lang"), callback_data="switch_lang")],
                ]
                await query.edit_message_text(t(user_id, "welcome"), reply_markup=InlineKeyboardMarkup(keyboard))

        elif data == "server_status":
            user_id = query.from_user.id
            await query.edit_message_text(t(user_id, "loading"))
            status = await panel.server_status()
            if not status.get("success"):
                await query.edit_message_text(f"{t(user_id, 'error')}: {status.get('msg', 'Unknown')}")
                return
            obj = status.get("obj", {})
            xray_status = t(user_id, "running") if obj.get('xray') else t(user_id, "stopped")
            msg = (
                f"<b>{t(user_id, 'server_status')}</b>\n\n"
                f"{t(user_id, 'cpu')}: {obj.get('cpu', 'N/A')}%\n"
                f"{t(user_id, 'memory')}: {format_bytes(obj.get('mem', {}).get('current', 0))} / {format_bytes(obj.get('mem', {}).get('total', 0))}\n"
                f"{t(user_id, 'swap')}: {format_bytes(obj.get('swap', {}).get('current', 0))} / {format_bytes(obj.get('swap', {}).get('total', 0))}\n"
                f"{t(user_id, 'disk')}: {format_bytes(obj.get('disk', {}).get('current', 0))} / {format_bytes(obj.get('disk', {}).get('total', 0))}\n"
                f"{t(user_id, 'uptime')}: {obj.get('uptime', 'N/A')}s\n"
                f"{t(user_id, 'xray')}: {xray_status}\n"
                f"{t(user_id, 'load')}: {obj.get('load', 'N/A')}"
            )
            keyboard = [[InlineKeyboardButton(t(user_id, "back"), callback_data="main_menu")]]
            await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

        elif data == "inbounds_menu":
            user_id = query.from_user.id
            await query.edit_message_text(t(user_id, "loading"))
            ib_data = await panel.inbounds_list()
            if not ib_data.get("success"):
                await query.edit_message_text(f"{t(user_id, 'error')}: {ib_data.get('msg', 'Unknown')}")
                return
            inbounds = ib_data.get("obj", [])
            if not inbounds:
                await query.edit_message_text(t(user_id, "no_inbounds"))
                return
            keyboard = []
            for ib in inbounds[:20]:
                status = "ON" if ib.get("enable") else "OFF"
                text = f"[{status}] {ib.get('remark', 'N/A')} ({ib.get('protocol', '?')}:{ib.get('port', '?')})"
                keyboard.append([InlineKeyboardButton(text, callback_data=f"inbound_{ib['id']}")])
            keyboard.append([InlineKeyboardButton(t(user_id, "add_inbound"), callback_data="inbound_add")])
            keyboard.append([InlineKeyboardButton(t(user_id, "back"), callback_data="main_menu")])
            await query.edit_message_text(t(user_id, "inbounds"), reply_markup=InlineKeyboardMarkup(keyboard))

        elif data.startswith("inbound_") and data.split("_")[1].isdigit():
            inbound_id = int(data.split("_")[1])
            await inbound_detail(update, context, inbound_id)

        elif data.startswith("inbound_toggle_"):
            inbound_id = int(data.replace("inbound_toggle_", ""))
            inbound_data = await panel.inbound_get(inbound_id)
            if inbound_data.get("success"):
                current_enable = inbound_data.get("obj", {}).get("enable", False)
                await inbound_toggle(update, context, inbound_id, not current_enable)
                await inbound_detail(update, context, inbound_id)

        elif data.startswith("inbound_reset_"):
            inbound_id = int(data.replace("inbound_reset_", ""))
            await inbound_reset(update, context, inbound_id)

        elif data.startswith("inbound_del_"):
            inbound_id = int(data.replace("inbound_del_", ""))
            await inbound_delete(update, context, inbound_id)

        elif data.startswith("inbound_links_"):
            inbound_id = int(data.replace("inbound_links_", ""))
            await inbound_links(update, context, inbound_id)

        elif data == "clients_menu":
            user_id = query.from_user.id
            await query.edit_message_text(t(user_id, "loading"))
            cl_data = await panel.clients_list()
            if not cl_data.get("success"):
                await query.edit_message_text(f"{t(user_id, 'error')}: {cl_data.get('msg', 'Unknown')}")
                return
            clients = cl_data.get("obj", [])
            if not clients:
                await query.edit_message_text(t(user_id, "no_clients"))
                return
            keyboard = []
            for cl in clients[:20]:
                status = "ON" if cl.get("enable") else "OFF"
                email = cl.get("email", "N/A")
                text = f"[{status}] {email}"
                keyboard.append([InlineKeyboardButton(text, callback_data=f"client_{email}")])
            keyboard.append([InlineKeyboardButton(t(user_id, "add_client"), callback_data="client_add")])
            keyboard.append([InlineKeyboardButton(t(user_id, "back"), callback_data="main_menu")])
            await query.edit_message_text(t(user_id, "clients"), reply_markup=InlineKeyboardMarkup(keyboard))

        elif data.startswith("client_") and not data.startswith("client_add"):
            email = data.replace("client_", "")
            await client_detail(update, context, email)

        elif data.startswith("client_toggle_"):
            email = data.replace("client_toggle_", "")
            cl_data = await panel.client_get(email)
            if cl_data.get("success"):
                current_enable = cl_data.get("obj", {}).get("enable", False)
                await client_toggle(update, context, email, not current_enable)
                await client_detail(update, context, email)

        elif data.startswith("client_reset_"):
            email = data.replace("client_reset_", "")
            await client_reset(update, context, email)

        elif data.startswith("client_del_"):
            email = data.replace("client_del_", "")
            await client_delete(update, context, email)

        elif data.startswith("client_links_"):
            email = data.replace("client_links_", "")
            await client_links(update, context, email)

        elif data == "client_add":
            await client_add_start(update, context)

        elif data.startswith("toggle_inbound_"):
            inbound_id = int(data.replace("toggle_inbound_", ""))
            await toggle_inbound(update, context, inbound_id)

        elif data == "toggle_all_inbounds":
            await toggle_all_inbounds(update, context)

        elif data == "confirm_create_client":
            await confirm_create_client(update, context)

        elif data == "do_create_client":
            await do_create_client(update, context)

        elif data == "cancel_create_client":
            await cancel_create_client(update, context)

        elif data == "nodes_menu":
            user_id = query.from_user.id
            await query.edit_message_text(t(user_id, "loading"))
            node_data = await panel.nodes_list()
            if not node_data.get("success"):
                await query.edit_message_text(f"{t(user_id, 'error')}: {node_data.get('msg', 'Unknown')}")
                return
            nodes = node_data.get("obj", [])
            if not nodes:
                await query.edit_message_text(t(user_id, "no_nodes"))
                return
            keyboard = []
            for node in nodes[:10]:
                status = t(user_id, "online") if node.get("online") else t(user_id, "offline")
                text = f"[{status}] {node.get('remark', 'N/A')}"
                keyboard.append([InlineKeyboardButton(text, callback_data=f"node_{node['id']}")])
            keyboard.append([InlineKeyboardButton(t(user_id, "add_node"), callback_data="node_add")])
            keyboard.append([InlineKeyboardButton(t(user_id, "back"), callback_data="main_menu")])
            await query.edit_message_text(t(user_id, "nodes"), reply_markup=InlineKeyboardMarkup(keyboard))

        elif data.startswith("node_") and data.split("_")[1].isdigit():
            node_id = int(data.split("_")[1])
            await node_detail(update, context, node_id)

        elif data.startswith("node_toggle_"):
            node_id = int(data.replace("node_toggle_", ""))
            node_data = await panel.node_get(node_id)
            if node_data.get("success"):
                current_enable = node_data.get("obj", {}).get("enable", False)
                await node_toggle(update, context, node_id, not current_enable)
                await node_detail(update, context, node_id)

        elif data.startswith("node_del_"):
            node_id = int(data.replace("node_del_", ""))
            await node_delete(update, context, node_id)

        elif data == "hosts_menu":
            await hosts_cmd(update, context)

        elif data == "xray_menu":
            user_id = query.from_user.id
            keyboard = [
                [InlineKeyboardButton(t(user_id, "get_config"), callback_data="xray_config")],
                [InlineKeyboardButton(t(user_id, "outbound_traffic"), callback_data="xray_traffic")],
                [InlineKeyboardButton(t(user_id, "restart_xray"), callback_data="xray_restart")],
                [InlineKeyboardButton(t(user_id, "stop_xray"), callback_data="xray_stop")],
                [InlineKeyboardButton(t(user_id, "xray_logs"), callback_data="xray_logs")],
                [InlineKeyboardButton(t(user_id, "back"), callback_data="main_menu")],
            ]
            await query.edit_message_text(t(user_id, "xray_management"), reply_markup=InlineKeyboardMarkup(keyboard))

        elif data == "xray_config":
            await xray_config(update, context)
        elif data == "xray_traffic":
            await xray_traffic(update, context)
        elif data == "xray_restart":
            await xray_restart(update, context)
        elif data == "xray_stop":
            await xray_stop(update, context)
        elif data == "xray_logs":
            await xray_logs(update, context)

        elif data == "settings_menu":
            user_id = query.from_user.id
            keyboard = [
                [InlineKeyboardButton(t(user_id, "view_settings"), callback_data="settings_view")],
                [InlineKeyboardButton(t(user_id, "panel_logs"), callback_data="panel_logs")],
                [InlineKeyboardButton(t(user_id, "back"), callback_data="main_menu")],
            ]
            await query.edit_message_text(t(user_id, "settings"), reply_markup=InlineKeyboardMarkup(keyboard))

        elif data == "settings_view":
            await settings_view(update, context)
        elif data == "panel_logs":
            await panel_logs(update, context)

        elif data == "get_links":
            await get_all_links(update, context)
        elif data == "online_clients":
            await online_clients(update, context)

        elif data == "buy_menu":
            user_id = query.from_user.id
            plans = load_plans()
            if not plans:
                await query.edit_message_text(t(user_id, "no_plans"))
                return
            msg = f"<b>{t(user_id, 'plans_title')}</b>\n\n"
            keyboard = []
            for plan in plans:
                text = f"{plan['name']} - {plan['price_display']}"
                keyboard.append([InlineKeyboardButton(text, callback_data=f"plan_{plan['id']}")])
            keyboard.append([InlineKeyboardButton(t(user_id, "back"), callback_data="main_menu")])
            await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

        elif data == "test_account":
            await request_test_account(update, context)

        elif data == "my_plans":
            await my_plans_cmd(update, context)

        elif data.startswith("my_plan_qr_"):
            order_id = data.replace("my_plan_qr_", "")
            await my_plan_qr(update, context, order_id)

        elif data.startswith("my_plan_"):
            order_id = data.replace("my_plan_", "")
            await my_plan_detail(update, context, order_id)

        elif data.startswith("plan_") and not data.startswith("plan_add"):
            plan_id = data.replace("plan_", "")
            await plan_detail(update, context, plan_id)

        elif data.startswith("buy_now_"):
            plan_id = data.replace("buy_now_", "")
            await buy_now(update, context, plan_id)

        elif data == "cancel_order":
            await cancel_order(update, context)

        elif data.startswith("approve_order_"):
            order_id = data.replace("approve_order_", "")
            print(f"[Bot] Approve button pressed for order {order_id} by admin {query.from_user.id}")
            await approve_order(update, context, order_id)

        elif data.startswith("approve_toggle_") and data.replace("approve_toggle_", "").isdigit():
            inbound_id = int(data.replace("approve_toggle_", ""))
            await approve_toggle_inbound(update, context, inbound_id)

        elif data == "approve_toggle_all":
            await approve_toggle_all(update, context)

        elif data == "approve_confirm":
            await approve_confirm(update, context)

        elif data == "approve_cancel":
            await approve_cancel(update, context)

        elif data.startswith("reject_order_"):
            order_id = data.replace("reject_order_", "")
            print(f"[Bot] Reject button pressed for order {order_id} by admin {query.from_user.id}")
            await reject_order(update, context, order_id)

        elif data == "pending_orders":
            await pending_orders_cmd(update, context)

        elif data.startswith("view_order_"):
            order_id = data.replace("view_order_", "")
            await view_order(update, context, order_id)

        elif data == "switch_lang":
            await switch_lang(update, context)

        elif data == "support":
            user_id = query.from_user.id
            await query.edit_message_text(
                f"<b>{t(user_id, 'support')}</b>\n\n"
                f"{t(user_id, 'support_text')}\n\n"
                f"{t(user_id, 'support_username')}",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(user_id, "back"), callback_data="main_menu")]])
            )

        else:
            await query.edit_message_text("Unknown command.")

    except Exception as e:
        logger.error(f"Callback error: {e}", exc_info=True)
        print(f"[Bot] CALLBACK ERROR: {e}")
        try:
            await query.answer(f"Error: {str(e)[:100]}", show_alert=True)
        except:
            pass


# ==================== HELP ====================
@admin_only
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    msg = (
        f"<b>{t(user_id, 'help_title')}</b>\n\n"
        f"<b>{t(user_id, 'admin_commands')}</b>\n"
        f"{t(user_id, 'help_start')}\n"
        f"{t(user_id, 'help_status')}\n"
        f"{t(user_id, 'help_inbounds')}\n"
        f"{t(user_id, 'help_clients')}\n"
        f"{t(user_id, 'help_nodes')}\n"
        f"{t(user_id, 'help_hosts')}\n"
        f"{t(user_id, 'help_links')}\n"
        f"{t(user_id, 'help_online')}\n"
        f"{t(user_id, 'help_xray')}\n"
        f"{t(user_id, 'help_settings')}\n"
        f"{t(user_id, 'help_help')}\n\n"
        f"<b>{t(user_id, 'user_commands')}</b>\n"
        f"{t(user_id, 'help_buy')}"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


# ==================== ERROR HANDLER ====================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)


# ==================== STARTUP ====================
async def post_init(application: Application) -> None:
    # Initialise SQLite and load persisted user languages into memory
    _db.init_db()
    USER_LANG.update(_db.load_all_langs())
    print(f"[Bot] Loaded language preferences for {len(USER_LANG)} user(s) from DB.")

    print("[Bot] Logging in to panel...")
    success = await panel.login()
    if success:
        print("[Bot] Panel login successful!")
    else:
        print("[Bot] WARNING: Panel login failed. Using API token mode.")


# ==================== MAIN ====================
def main():
    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set in .env")
        return

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("status", server_status_cmd))
    app.add_handler(CommandHandler("inbounds", inbounds_cmd))
    app.add_handler(CommandHandler("clients", clients_cmd))
    app.add_handler(CommandHandler("nodes", nodes_cmd))
    app.add_handler(CommandHandler("hosts", hosts_cmd))
    app.add_handler(CommandHandler("links", get_all_links))
    app.add_handler(CommandHandler("online", online_clients))
    app.add_handler(CommandHandler("xray", xray_cmd))
    app.add_handler(CommandHandler("settings", settings_cmd))
    app.add_handler(CommandHandler("buy", buy_cmd))
    app.add_handler(CommandHandler("lang", lang_cmd))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_screenshot))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_error_handler(error_handler)

    print("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
