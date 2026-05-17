#!/usr/bin/env python3
"""
╔══════════════════════════════════════╗
║     HTML Decoder Telegram Bot        ║
║  Developer : BD ALAMIN               ║
║  Owner     : @BDALAMINHACKER         ║
║  Admin     : @AFRIN_SUPPORT_ADMIN    ║
╚══════════════════════════════════════╝
"""

import asyncio
import os
import pickle
import logging
import tempfile
from datetime import datetime
from threading import Thread

from dotenv import load_dotenv
load_dotenv()

from flask import Flask
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode
from playwright.async_api import async_playwright

# ════════════════════════════════════════
#              CONFIGURATION
# ════════════════════════════════════════
BOT_TOKEN      = os.environ.get("BOT_TOKEN", "")
OWNER_ID       = int(os.environ.get("OWNER_ID", "0"))
OWNER_USERNAME = os.environ.get("OWNER_USERNAME", "BDALAMINHACKER")
DB_FILE        = "database.pkl"

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN not found! Set BOT_TOKEN in Environment Variables.")
if not OWNER_ID:
    raise ValueError("❌ OWNER_ID not found! Set OWNER_ID in Environment Variables.")

# ════════════════════════════════════════
#                 LOGGING
# ════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ════════════════════════════════════════
#          FLASK — KEEP ALIVE SERVER
# ════════════════════════════════════════
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "✅ Bot is running!"

def run_flask():
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

# ════════════════════════════════════════
#               DATABASE
# ════════════════════════════════════════
def _default_db() -> dict:
    return {
        "users":    {},
        "admins":   [],
        "channels": [],
        "settings": {"bot_active": True},
        "stats":    {"total_decodes": 0},
    }

def load_db() -> dict:
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "rb") as f:
                data = pickle.load(f)
            for k, v in _default_db().items():
                data.setdefault(k, v)
            return data
        except Exception as e:
            logger.error(f"DB load error: {e}")
    return _default_db()

def save_db(db: dict) -> None:
    try:
        with open(DB_FILE, "wb") as f:
            pickle.dump(db, f)
    except Exception as e:
        logger.error(f"DB save error: {e}")

db = load_db()

# ════════════════════════════════════════
#               HELPERS
# ════════════════════════════════════════
def is_owner(uid: int)  -> bool: return uid == OWNER_ID
def is_admin(uid: int)  -> bool: return uid == OWNER_ID or uid in db["admins"]
def is_banned(uid: int) -> bool: return db["users"].get(uid, {}).get("banned", False)
def bot_active()        -> bool: return db["settings"].get("bot_active", True)

def register_user(user) -> None:
    if user.id not in db["users"]:
        db["users"][user.id] = {
            "username":     user.username or "",
            "name":         user.full_name or "",
            "joined":       datetime.now().isoformat(),
            "banned":       False,
            "private_acked": [],   # list of private channel IDs user has acknowledged
        }
        save_db(db)
    else:
        # Migrate old records that don't have private_acked
        if "private_acked" not in db["users"][user.id]:
            db["users"][user.id]["private_acked"] = []
            save_db(db)

async def check_channels(bot, uid: int):
    """
    Returns (all_joined: bool, missing_channels: list).

    Public channels  → verify via get_chat_member (bot must be admin).
    Private channels → try get_chat_member first (works if bot is admin).
                       If bot can't verify, check if user already clicked
                       JOINED (stored in db as private_acked list).
    """
    if not db["channels"]:
        return True, []

    # IDs of private channels this user has already acknowledged
    acked = db["users"].get(uid, {}).get("private_acked", [])

    missing = []
    for ch in db["channels"]:
        ch_type = ch.get("type", "public")
        try:
            m = await bot.get_chat_member(ch["id"], uid)
            if m.status in ("left", "kicked"):
                missing.append(ch)
            # status member / administrator / creator / restricted → joined ✅
        except Exception:
            if ch_type == "private" and ch["id"] in acked:
                # Bot can't verify but user has already acknowledged this channel
                pass   # treat as joined
            else:
                missing.append(ch)

    return len(missing) == 0, missing

def channels_kb(missing: list) -> InlineKeyboardMarkup:
    """Build inline keyboard with join buttons for each missing channel."""
    rows = []
    for ch in missing:
        link    = ch.get("link", "")
        name    = ch.get("name", "Channel")
        ch_type = ch.get("type", "public")
        label   = f"{'🔒' if ch_type == 'private' else '📢'} {name}"
        rows.append([InlineKeyboardButton(label, url=link)])
    rows.append([InlineKeyboardButton("✅ 𝗝𝗢𝗜𝗡𝗘𝗗", callback_data="check_joined")])
    return InlineKeyboardMarkup(rows)

def main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["🌐 𝗛𝗧𝗠𝗟 𝗗𝗘𝗖𝗢𝗗𝗘", "🧑‍💻 𝗗𝗘𝗩𝗘𝗟𝗢𝗣𝗘𝗥 𝗜𝗡𝗙𝗢"],
         ["📊 𝗕𝗢𝗧 𝗦𝗧𝗔𝗧𝗨𝗦"]],
        resize_keyboard=True,
    )

async def send_welcome(bot, chat_id: int, first_name: str) -> None:
    txt = (
        f"🌟 Welcome, <b>{first_name}</b>!\n\n"
        "🤖 I am an <b>HTML Decoder Bot</b>.\n"
        "Send me your Encrypted HTML file,\n"
        "and I will Decode it on my Server.\n\n"
        "Please select an option from the menu below 👇"
    )
    await bot.send_message(chat_id, txt, parse_mode=ParseMode.HTML, reply_markup=main_kb())

# ════════════════════════════════════════
#           BROWSER RENDERER
# ════════════════════════════════════════
async def render_html(path: str) -> str:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        page = await browser.new_page()
        await page.goto(f"file://{os.path.abspath(path)}", wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)
        html = await page.evaluate("() => document.documentElement.outerHTML")
        await browser.close()
    return html

# ════════════════════════════════════════
#           BAN MESSAGE
# ════════════════════════════════════════
def ban_text(uid: int) -> str:
    return (
        "⚠️ You have been banned from this bot and cannot use it.\n\n"
        f"𝗜𝗗 : <code>{uid}</code>\n\n"
        "🔔 Please contact the Owner with this Telegram User ID to request an Unban.\n\n"
        f"👑 OWNER : @{OWNER_USERNAME} ✅"
    )

# ════════════════════════════════════════
#             /start COMMAND
# ════════════════════════════════════════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    register_user(user)

    if not bot_active() and not is_admin(user.id):
        await update.message.reply_text("🔴 The bot is currently offline.")
        return

    if is_banned(user.id):
        await update.message.reply_html(ban_text(user.id))
        return

    joined, missing = await check_channels(context.bot, user.id)
    if not joined:
        await update.message.reply_text(
            "📢 Please join the channels below,\nthen press the ✅ <b>JOINED</b> button:",
            parse_mode=ParseMode.HTML,
            reply_markup=channels_kb(missing),
        )
        return

    await send_welcome(context.bot, user.id, user.first_name)

# ════════════════════════════════════════
#         CALLBACK QUERY HANDLER
# ════════════════════════════════════════
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q    = update.callback_query
    user = q.from_user
    data = q.data
    await q.answer()

    if data == "check_joined":
        # Before re-checking, acknowledge all private channels the user
        # was shown — they clicked JOINED so we trust them for private ones.
        user_record = db["users"].setdefault(user.id, {
            "username": user.username or "", "name": user.full_name or "",
            "joined": datetime.now().isoformat(), "banned": False, "private_acked": []
        })
        if "private_acked" not in user_record:
            user_record["private_acked"] = []

        for ch in db["channels"]:
            if ch.get("type") == "private" and ch["id"] not in user_record["private_acked"]:
                user_record["private_acked"].append(ch["id"])
        save_db(db)

        joined, missing = await check_channels(context.bot, user.id)
        if not joined:
            await q.answer("❌ You have not joined all channels yet!", show_alert=True)
            try: await q.message.edit_reply_markup(channels_kb(missing))
            except Exception: pass
            return

        if is_banned(user.id):
            try: await q.message.edit_text(ban_text(user.id), parse_mode=ParseMode.HTML)
            except Exception: pass
            return

        try: await q.message.delete()
        except Exception: pass
        await send_welcome(context.bot, user.id, user.first_name)
        return

    if data.startswith("admin_"):
        if not is_admin(user.id):
            await q.answer("❌ Access denied!", show_alert=True)
            return
        await _admin_cb(update, context, data)

# ════════════════════════════════════════
#        TEXT MESSAGE HANDLER
# ════════════════════════════════════════
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = update.message.text or ""

    if text == "#000000":
        if is_admin(user.id):
            await _show_admin_panel(update, context)
        else:
            await update.message.reply_text("❌ Access denied.")
        return

    action = context.user_data.get("admin_action")
    if action and is_admin(user.id):
        await _process_admin_input(update, context, action, text)
        return

    if not bot_active() and not is_admin(user.id):
        await update.message.reply_text("🔴 The bot is currently offline.")
        return

    if is_banned(user.id):
        return

    if context.user_data.get("waiting_for_html"):
        await update.message.reply_text("⚠️ Please upload a .html file.")
        return

    if "𝗛𝗧𝗠𝗟 𝗗𝗘𝗖𝗢𝗗𝗘" in text:
        joined, missing = await check_channels(context.bot, user.id)
        if not joined:
            await update.message.reply_text("📢 Please join all channels first:", reply_markup=channels_kb(missing))
            return
        await _decode_start(update, context)

    elif "𝗗𝗘𝗩𝗘𝗟𝗢𝗣𝗘𝗥 𝗜𝗡𝗙𝗢" in text:
        joined, missing = await check_channels(context.bot, user.id)
        if not joined:
            await update.message.reply_text("📢 Please join all channels first:", reply_markup=channels_kb(missing))
            return
        await _dev_info(update, context)

    elif "𝗕𝗢𝗧 𝗦𝗧𝗔𝗧𝗨𝗦" in text:
        await _bot_status(update, context)

    else:
        await update.message.reply_text(
            "🤔 I didn't understand that. Please select an option from the menu:",
            reply_markup=main_kb(),
        )

# ════════════════════════════════════════
#        DOCUMENT (FILE) HANDLER
# ════════════════════════════════════════
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user

    if not bot_active() and not is_admin(user.id):
        return
    if is_banned(user.id):
        return

    if not context.user_data.get("waiting_for_html"):
        await update.message.reply_text(
            "⚠️ Please press the '🌐 𝗛𝗧𝗠𝗟 𝗗𝗘𝗖𝗢𝗗𝗘' button first.",
            reply_markup=main_kb(),
        )
        return

    doc   = update.message.document
    fname = doc.file_name or "file.html"

    if not (fname.lower().endswith(".html") or fname.lower().endswith(".htm")):
        await update.message.reply_text("⚠️ Only .html or .htm files are supported.")
        return

    context.user_data["waiting_for_html"] = False
    await _do_decode(update, context, doc, fname)

# ════════════════════════════════════════
#       HTML DECODE — STEP 1: READY
# ════════════════════════════════════════
async def _decode_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("⏳")

    frames = [
        "⠋ Preparing system...",
        "⠙ Preparing system...",
        "⠹ Preparing system...",
        "⠸ Preparing system...",
        "⠼ Preparing system...",
        "⠴ Preparing system...",
        "⠦ Preparing system...",
        "⠧ Preparing system...",
        "⠇ Preparing system...",
        "⠏ Preparing system...",
        "✅ Ready!",
    ]
    for f in frames:
        await asyncio.sleep(0.5)
        try: await msg.edit_text(f)
        except Exception: pass

    await asyncio.sleep(1)
    try: await msg.delete()
    except Exception: pass

    context.user_data["waiting_for_html"] = True
    await update.message.reply_html(
        "📂 Please send your Encrypted HTML file ⬇️\n\n"
        "💡 Only <b>.html</b> or <b>.htm</b> files are supported."
    )

# ════════════════════════════════════════
#       HTML DECODE — STEP 2: PROCESS
# ════════════════════════════════════════
async def _do_decode(update: Update, context: ContextTypes.DEFAULT_TYPE, doc, fname: str) -> None:
    msg = await update.message.reply_text("📡 Establishing connection...")

    dl_steps = [
        "⬇️  Downloading file...",
        "🔒 Running security check...",
        "📋 Inspecting file...",
    ]
    for s in dl_steps:
        await asyncio.sleep(0.9)
        try: await msg.edit_text(s)
        except Exception: pass

    try:
        file_obj = await context.bot.get_file(doc.file_id)

        with tempfile.TemporaryDirectory() as tmp:
            in_path  = os.path.join(tmp, fname)
            await file_obj.download_to_drive(in_path)

            base     = fname.rsplit(".", 1)
            out_name = f"{base[0]}_decoded.{base[1]}" if len(base) == 2 else f"{fname}_decoded"
            out_path = os.path.join(tmp, out_name)

            decode_steps = [
                "🖥️ ▱▱▱▱▱▱▱  Starting server...",
                "🖥️ ▰▱▱▱▱▱▱  Starting server...",
                "🖥️ ▰▰▱▱▱▱▱  Loading server...",
                "🖥️ ▰▰▰▱▱▱▱  Server running...",
                "🔍 ▰▰▰▰▱▱▱  Scanning HTML...",
                "✨ ▰▰▰▰▰▱▱  Decoding...",
                "🔧 ▰▰▰▰▰▰▱  Building output...",
                "💾 ▰▰▰▰▰▰▰  Saving file...",
            ]

            task = asyncio.create_task(render_html(in_path))
            i    = 0
            while not task.done():
                try: await msg.edit_text(decode_steps[i % len(decode_steps)])
                except Exception: pass
                i += 1
                await asyncio.sleep(1.5)

            decoded_html = await task

            with open(out_path, "w", encoding="utf-8") as f:
                f.write(decoded_html)

            db["stats"]["total_decodes"] = db["stats"].get("total_decodes", 0) + 1
            save_db(db)

            try: await msg.edit_text("✅ Decode complete! Sending file...")
            except Exception: pass
            await asyncio.sleep(1)

            with open(out_path, "rb") as f:
                await update.message.reply_document(
                    document=f,
                    filename=out_name,
                    caption=(
                        "✅ <b>HTML Decoded Successfully!</b>\n\n"
                        f"📁 File : <code>{out_name}</code>\n"
                        "🌐 Decoded Using Our Server."
                    ),
                    parse_mode=ParseMode.HTML,
                )
            try: await msg.delete()
            except Exception: pass

    except Exception as e:
        logger.error(f"Decode error: {e}")
        try: await msg.edit_text("❌ An error occurred during decoding. Please try again later.")
        except Exception: pass

# ════════════════════════════════════════
#             DEVELOPER INFO
# ════════════════════════════════════════
async def _dev_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    txt = (
        "🤖 𝙽𝚊𝚖𝚎 : 𝙱𝙳 𝙰𝙻𝙰𝙼𝙸𝙽\n\n"
        "🏕️ 𝙳𝚛𝚎𝚊𝚖 : 𝙷𝚊𝚌𝚔𝚒𝚗𝚐 ♥︎ 𝚃𝚛𝚊𝚍𝚒𝚗𝚐 ♥︎ 𝙼𝚘𝚗𝚎𝚢.\n\n"
        "💥 𝚆𝚘𝚛𝚔 : 𝙽𝚘𝚝 𝙰𝚗𝚢𝚝𝚑𝚒𝚗𝚐.\n\n"
        "🎉 𝚃𝚑𝚊𝚗𝚔𝚜 𝙵𝚘𝚛 𝚂𝚎𝚎𝚒𝚗𝚐 𝙼𝚢 𝙸𝚗𝚏𝚘, "
        "𝙸𝚏 𝚈𝚘𝚞 𝙽𝚎𝚎𝚍 𝙷𝚎𝚕𝚙 𝙲𝚕𝚒𝚌𝚔 𝚝𝚑𝚎 𝙱𝚞𝚝𝚝𝚘𝚗 𝙱𝚎𝚕𝚘𝚠."
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("𝗖𝗢𝗡𝗧𝗔𝗖𝗧 𝗠𝗘", url=f"https://t.me/{OWNER_USERNAME}")
    ]])
    await update.message.reply_text(txt, reply_markup=kb)

# ════════════════════════════════════════
#              BOT STATUS
# ════════════════════════════════════════
async def _bot_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    txt = (
        f"👥 Total Users : <b>{len(db['users'])}</b>\n\n"
        f"🚀 Total HTML Decodes : <b>{db['stats'].get('total_decodes', 0)}</b>\n\n"
        "⚠️ I love my users, that's why I offer this for free 🫣."
    )
    await update.message.reply_html(txt)

# ════════════════════════════════════════
#            ADMIN PANEL UI
# ════════════════════════════════════════
def _panel_text() -> str:
    pub_count  = sum(1 for c in db["channels"] if c.get("type", "public") == "public")
    priv_count = sum(1 for c in db["channels"] if c.get("type", "public") == "private")
    return (
        "👑 <b>Admin Panel</b>\n\n"
        f"🤖 Bot Status  : {'✅ Online' if bot_active() else '🔴 Offline'}\n"
        f"👥 Total Users : <b>{len(db['users'])}</b>\n"
        f"📢 Public Channels  : <b>{pub_count}</b>\n"
        f"🔒 Private Channels : <b>{priv_count}</b>\n"
        f"👮 Admins      : <b>{len(db['admins'])}</b>\n"
        f"🚀 Total Decodes : <b>{db['stats'].get('total_decodes', 0)}</b>"
    )

def _panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Channels",   callback_data="admin_channels"),
         InlineKeyboardButton("👮 Admins",     callback_data="admin_admins")],
        [InlineKeyboardButton("🚫 Ban",        callback_data="admin_ban"),
         InlineKeyboardButton("✅ Unban",       callback_data="admin_unban")],
        [InlineKeyboardButton("📣 Broadcast",  callback_data="admin_broadcast"),
         InlineKeyboardButton("📦 DB Export",  callback_data="admin_export")],
        [InlineKeyboardButton(
            "🔴 Turn Bot OFF" if bot_active() else "🟢 Turn Bot ON",
            callback_data="admin_toggle_bot",
        )],
    ])

async def _show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(_panel_text(), reply_markup=_panel_kb())

# ════════════════════════════════════════
#          ADMIN CALLBACK ROUTER
# ════════════════════════════════════════
async def _admin_cb(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    q = update.callback_query

    async def edit(txt, kb=None):
        try:
            await q.message.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            pass

    # ── Back ──────────────────────────────
    if data == "admin_back":
        await edit(_panel_text(), _panel_kb())

    # ── Toggle Bot ────────────────────────
    elif data == "admin_toggle_bot":
        db["settings"]["bot_active"] = not bot_active()
        save_db(db)
        s = "✅ Online" if bot_active() else "🔴 Offline"
        await q.answer(f"Bot is now {s}!", show_alert=True)
        await edit(_panel_text(), _panel_kb())

    # ── DB Export ─────────────────────────
    elif data == "admin_export":
        try:
            with open(DB_FILE, "rb") as f:
                await context.bot.send_document(
                    q.from_user.id, f, filename="database.pkl",
                    caption="📦 Database exported successfully!",
                )
            await q.answer("✅ Database sent!", show_alert=True)
        except Exception as e:
            await q.answer(f"❌ {e}", show_alert=True)

    # ── Channel List ──────────────────────
    elif data == "admin_channels":
        txt = "📢 <b>Channel List:</b>\n\n"
        for i, ch in enumerate(db["channels"], 1):
            ch_type = ch.get("type", "public")
            icon    = "📢" if ch_type == "public" else "🔒"
            label   = "Public" if ch_type == "public" else "Private"
            txt += f"{i}. {icon} <b>{ch.get('name','?')}</b>  [{label}]\n"
        if not db["channels"]:
            txt += "No channels have been added yet."
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Public",  callback_data="admin_add_public_channel"),
             InlineKeyboardButton("➕ Add Private", callback_data="admin_add_private_channel")],
            [InlineKeyboardButton("➖ Remove",      callback_data="admin_remove_channel")],
            [InlineKeyboardButton("🔙 Back",        callback_data="admin_back")],
        ])
        await edit(txt, kb)

    # ── Add Public Channel ────────────────
    elif data == "admin_add_public_channel":
        context.user_data["admin_action"] = "add_public_channel"
        await edit(
            "📢 <b>Add Public Channel</b>\n\n"
            "Send the channel info in this format:\n\n"
            "<code>CHANNEL_ID | CHANNEL_LINK</code>\n\n"
            "Example:\n"
            "<code>-1001234567890 | https://t.me/mychannel</code>\n\n"
            "Or just send the @username:\n"
            "<code>@mychannel</code>\n\n"
            "⚠️ Make sure the bot is an <b>Admin</b> of the channel."
        )

    # ── Add Private Channel ───────────────
    elif data == "admin_add_private_channel":
        context.user_data["admin_action"] = "add_private_channel"
        await edit(
            "🔒 <b>Add Private Channel</b>\n\n"
            "Send the channel info in this format:\n\n"
            "<code>CHANNEL_ID | CHANNEL_INVITATION_LINK</code>\n\n"
            "Example:\n"
            "<code>-1001234567890 | https://t.me/+aBcDeFgHiJkL</code>\n\n"
            "📌 The invitation link must start with <b>https://t.me/+</b>"
        )

    # ── Remove Channel ────────────────────
    elif data == "admin_remove_channel":
        if not db["channels"]:
            await q.answer("No channels to remove!", show_alert=True); return
        rows = []
        for i, ch in enumerate(db["channels"]):
            ch_type = ch.get("type", "public")
            icon    = "📢" if ch_type == "public" else "🔒"
            rows.append([InlineKeyboardButton(
                f"❌ {icon} {ch.get('name','?')}",
                callback_data=f"admin_del_ch_{i}"
            )])
        rows.append([InlineKeyboardButton("🔙 Back", callback_data="admin_channels")])
        await edit("Which channel do you want to remove?", InlineKeyboardMarkup(rows))

    elif data.startswith("admin_del_ch_"):
        idx = int(data.split("_")[-1])
        if 0 <= idx < len(db["channels"]):
            removed = db["channels"].pop(idx)
            save_db(db)
            await q.answer(f"✅ {removed.get('name','?')} removed!", show_alert=True)
            await _admin_cb(update, context, "admin_channels")

    # ── Admin List ────────────────────────
    elif data == "admin_admins":
        txt = "👮 <b>Admin List:</b>\n\n"
        for aid in db["admins"]:
            ud   = db["users"].get(aid, {})
            txt += f"• {ud.get('name','Unknown')}  |  <code>{aid}</code>\n"
        if not db["admins"]:
            txt += "No admins have been added."
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Admin",    callback_data="admin_add_admin"),
             InlineKeyboardButton("➖ Remove Admin", callback_data="admin_remove_admin")],
            [InlineKeyboardButton("🔙 Back",         callback_data="admin_back")],
        ])
        await edit(txt, kb)

    elif data == "admin_add_admin":
        context.user_data["admin_action"] = "add_admin"
        await edit("👮 Send the Telegram User ID of the new admin:")

    elif data == "admin_remove_admin":
        if not db["admins"]:
            await q.answer("No admins to remove!", show_alert=True); return
        rows = [[InlineKeyboardButton(
                    f"❌ {db['users'].get(aid,{}).get('name','Unknown')} ({aid})",
                    callback_data=f"admin_del_adm_{aid}")]
                for aid in db["admins"]]
        rows.append([InlineKeyboardButton("🔙 Back", callback_data="admin_admins")])
        await edit("Which admin do you want to remove?", InlineKeyboardMarkup(rows))

    elif data.startswith("admin_del_adm_"):
        aid = int(data.split("_")[-1])
        if aid in db["admins"]:
            db["admins"].remove(aid)
            save_db(db)
            await q.answer("✅ Admin removed!", show_alert=True)
            await _admin_cb(update, context, "admin_admins")

    # ── Ban / Unban ───────────────────────
    elif data == "admin_ban":
        context.user_data["admin_action"] = "ban_user"
        await edit("🚫 Send the Telegram User ID to ban:")

    elif data == "admin_unban":
        context.user_data["admin_action"] = "unban_user"
        await edit("✅ Send the Telegram User ID to unban:")

    # ── Broadcast ─────────────────────────
    elif data == "admin_broadcast":
        context.user_data["admin_action"] = "broadcast"
        await edit(
            "📣 <b>Send Broadcast Message</b>\n\n"
            "Just type your message to send without buttons.\n\n"
            "To add buttons, use this format:\n"
            "<code>Your message here\n"
            "Button Text|https://link.com\n"
            "Button Text 2|https://link2.com</code>"
        )

# ════════════════════════════════════════
#        ADMIN TEXT INPUT PROCESSOR
# ════════════════════════════════════════
async def _process_admin_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    action: str,
    text: str,
) -> None:
    context.user_data.pop("admin_action", None)

    # ── Add Public Channel ────────────────────────────────────────────────────
    if action == "add_public_channel":
        raw = text.strip()

        # Format 1: @username
        if raw.startswith("@"):
            try:
                chat = await context.bot.get_chat(raw)
                link = f"https://t.me/{chat.username}" if chat.username else \
                       f"https://t.me/c/{str(chat.id).lstrip('-100')}"
                ch = {
                    "id":       chat.id,
                    "name":     chat.title or str(chat.id),
                    "type":     "public",
                    "link":     link,
                    "username": f"@{chat.username}" if chat.username else "",
                }
                if chat.id in [c["id"] for c in db["channels"]]:
                    await update.message.reply_text("⚠️ This channel is already in the list!")
                else:
                    db["channels"].append(ch)
                    save_db(db)
                    await update.message.reply_html(
                        f"✅ Public channel <b>{chat.title}</b> added!\n\n"
                        f"📋 <b>ID</b> : <code>{chat.id}</code>\n"
                        f"🔗 <b>Link</b> : {link}"
                    )
            except Exception:
                await update.message.reply_html(
                    "❌ Channel not found.\n<i>Make sure the bot is an Admin of the channel.</i>"
                )

        # Format 2: CHANNEL_ID | CHANNEL_LINK
        elif "|" in raw:
            parts = [p.strip() for p in raw.split("|", 1)]
            if len(parts) != 2:
                await update.message.reply_text(
                    "❌ Invalid format.\nUse: <code>CHANNEL_ID | CHANNEL_LINK</code>",
                    parse_mode=ParseMode.HTML,
                )
                return
            try:
                ch_id   = int(parts[0])
                ch_link = parts[1]
                if not ch_link.startswith("https://t.me/"):
                    await update.message.reply_text("❌ Link must start with https://t.me/")
                    return
                # Try to fetch name via bot API
                name = str(ch_id)
                try:
                    chat = await context.bot.get_chat(ch_id)
                    name = chat.title or name
                except Exception:
                    pass
                ch = {
                    "id":       ch_id,
                    "name":     name,
                    "type":     "public",
                    "link":     ch_link,
                    "username": "",
                }
                if ch_id in [c["id"] for c in db["channels"]]:
                    await update.message.reply_text("⚠️ This channel is already in the list!")
                else:
                    db["channels"].append(ch)
                    save_db(db)
                    await update.message.reply_html(
                        f"✅ Public channel <b>{name}</b> added!\n\n"
                        f"📋 <b>ID</b> : <code>{ch_id}</code>\n"
                        f"🔗 <b>Link</b> : {ch_link}"
                    )
            except ValueError:
                await update.message.reply_text("❌ Invalid Channel ID. Must be a number.")
        else:
            await update.message.reply_html(
                "❌ Invalid format.\n\n"
                "Use <code>@username</code>  or  "
                "<code>CHANNEL_ID | CHANNEL_LINK</code>"
            )

    # ── Add Private Channel ───────────────────────────────────────────────────
    elif action == "add_private_channel":
        raw = text.strip()
        if "|" not in raw:
            await update.message.reply_html(
                "❌ Invalid format.\n\n"
                "Use: <code>CHANNEL_ID | CHANNEL_INVITATION_LINK</code>\n\n"
                "Example: <code>-1001234567890 | https://t.me/+aBcDeFgHiJkL</code>"
            )
            return
        parts = [p.strip() for p in raw.split("|", 1)]
        try:
            ch_id      = int(parts[0])
            invite_url = parts[1]
            if not (invite_url.startswith("https://t.me/+") or invite_url.startswith("https://t.me/joinchat/")):
                await update.message.reply_text(
                    "❌ The invitation link must start with:\n"
                    "https://t.me/+  or  https://t.me/joinchat/"
                )
                return
            # Try to get channel name (bot may or may not be a member)
            name = str(ch_id)
            try:
                chat = await context.bot.get_chat(ch_id)
                name = chat.title or name
            except Exception:
                pass
            ch = {
                "id":       ch_id,
                "name":     name,
                "type":     "private",
                "link":     invite_url,
                "username": "",
            }
            if ch_id in [c["id"] for c in db["channels"]]:
                await update.message.reply_text("⚠️ This channel is already in the list!")
            else:
                db["channels"].append(ch)
                save_db(db)
                await update.message.reply_html(
                    f"✅ Private channel <b>{name}</b> added!\n\n"
                    f"📋 <b>ID</b>     : <code>{ch_id}</code>\n"
                    f"🔗 <b>Invite</b> : {invite_url}"
                )
        except ValueError:
            await update.message.reply_text("❌ Invalid Channel ID. Must be a number.")

    # ── Add Admin ─────────────────────────────────────────────────────────────
    elif action == "add_admin":
        try:
            aid = int(text.strip())
            if aid == OWNER_ID:
                await update.message.reply_text("⚠️ The Owner already has the highest authority!")
            elif aid in db["admins"]:
                await update.message.reply_text("⚠️ This user is already an admin!")
            else:
                db["admins"].append(aid)
                save_db(db)
                await update.message.reply_html(f"✅ ID <code>{aid}</code> has been made an admin!")
        except ValueError:
            await update.message.reply_text("❌ Please send a valid Telegram User ID (numbers only).")

    # ── Ban User ──────────────────────────────────────────────────────────────
    elif action == "ban_user":
        try:
            bid = int(text.strip())
            if bid == OWNER_ID:
                await update.message.reply_text("❌ The Owner cannot be banned!")
            else:
                if bid not in db["users"]:
                    db["users"][bid] = {"username":"","name":"Unknown","joined":"","banned":True}
                else:
                    db["users"][bid]["banned"] = True
                save_db(db)
                await update.message.reply_html(f"✅ ID <code>{bid}</code> has been banned!")
        except ValueError:
            await update.message.reply_text("❌ Please send a valid Telegram User ID.")

    # ── Unban User ────────────────────────────────────────────────────────────
    elif action == "unban_user":
        try:
            uid = int(text.strip())
            if uid in db["users"]:
                db["users"][uid]["banned"] = False
                save_db(db)
                await update.message.reply_html(f"✅ ID <code>{uid}</code> has been unbanned!")
            else:
                await update.message.reply_text("⚠️ This ID is not in the database.")
        except ValueError:
            await update.message.reply_text("❌ Please send a valid Telegram User ID.")

    # ── Broadcast ─────────────────────────────────────────────────────────────
    elif action == "broadcast":
        lines     = text.strip().split("\n")
        msg_lines = []
        btn_rows  = []

        for line in lines:
            if "|" in line:
                parts   = line.split("|", 1)
                btn_txt = parts[0].strip()
                btn_url = parts[1].strip()
                if btn_url.startswith("http"):
                    btn_rows.append([InlineKeyboardButton(btn_txt, url=btn_url)])
                    continue
            msg_lines.append(line)

        broadcast_text = "\n".join(msg_lines).strip()
        markup = InlineKeyboardMarkup(btn_rows) if btn_rows else None

        total   = len(db["users"])
        success = failed = 0
        prog    = await update.message.reply_text(f"📣 Starting broadcast...\n0/{total}")

        for uid in list(db["users"].keys()):
            try:
                await context.bot.send_message(uid, broadcast_text, reply_markup=markup)
                success += 1
            except Exception:
                failed += 1
            if (success + failed) % 20 == 0 or (success + failed) == total:
                try:
                    await prog.edit_text(
                        f"📣 Broadcasting...\n"
                        f"✅ Success: {success}  ❌ Failed: {failed}\n"
                        f"Total: {success+failed}/{total}"
                    )
                except Exception:
                    pass

        try:
            await prog.edit_text(
                f"📣 Broadcast Complete!\n✅ Success: {success}\n❌ Failed: {failed}\n📊 Total: {total}"
            )
        except Exception:
            pass

# ════════════════════════════════════════
#                 MAIN
# ════════════════════════════════════════
def main() -> None:
    # Start Flask keep-alive server in a separate thread
    Thread(target=run_flask, daemon=True).start()
    logger.info("✅ Flask keep-alive server started.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("✅ Bot polling started.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
