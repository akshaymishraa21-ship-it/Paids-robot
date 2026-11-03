import asyncio
import sqlite3
import logging
import os
import html
import random
import json
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher
from aiogram.types import (
    Message,
    ErrorEvent,
    ReactionTypeEmoji,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from keep_alive import keep_alive

BOT_TOKEN = os.environ.get(
    "BOT_TOKEN", "8488349023:AAHwL6XtncTeyewAGXf_acu1i8g9MTIGTZk"
)
OWNER_ID = int(os.environ.get("BOT_OWNER_ID", "6146191046"))
STARS_PER_POST = 400
ADMIN_USERNAMES = ["disturbor"]

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

conn = sqlite3.connect("market_stats.db", check_same_thread=False)
cursor = conn.cursor()

# Database Setup
cursor.executescript("""
    CREATE TABLE IF NOT EXISTS spenders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        stars INTEGER,
        timestamp DATETIME
    );
    CREATE TABLE IF NOT EXISTS live_messages (
        chat_id INTEGER,
        message_id INTEGER
    );
    CREATE TABLE IF NOT EXISTS counted_groups (
        chat_id INTEGER PRIMARY KEY
    );
    CREATE TABLE IF NOT EXISTS connected_groups (
        chat_id INTEGER PRIMARY KEY,
        title TEXT,
        username TEXT
    );
    CREATE TABLE IF NOT EXISTS giveaways (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        photo_id TEXT,
        description TEXT,
        end_time DATETIME,
        max_participants INTEGER,
        max_winners INTEGER,
        button_text TEXT,
        status TEXT,
        message_data TEXT,
        created_at DATETIME
    );
    CREATE TABLE IF NOT EXISTS giveaway_entries (
        giveaway_id INTEGER,
        user_id INTEGER,
        username TEXT,
        tickets INTEGER DEFAULT 1,
        referrer_id INTEGER,
        UNIQUE(giveaway_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS user_balances (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        balance REAL DEFAULT 0.0
    );
""")

try:
    cursor.execute("ALTER TABLE giveaways ADD COLUMN sponsor_channel TEXT")
except sqlite3.OperationalError:
    pass

try:
    cursor.execute("ALTER TABLE giveaways ADD COLUMN created_at DATETIME")
except sqlite3.OperationalError:
    pass
conn.commit()


# FSM States
class MakePost(StatesGroup):
    waiting_for_paragraph = State()

class MakeGiveaway(StatesGroup):
    photo = State()
    desc = State()
    duration = State()
    participants = State()
    winners = State()
    button_name = State()
    button_color = State()
    sponsor = State()
    target_group = State()

class RepostGiveaway(StatesGroup):
    waiting_for_target = State()

class EditGiveaway(StatesGroup):
    waiting_for_id = State()
    waiting_for_field = State()
    waiting_for_new_value = State()

class TopupAdmin(StatesGroup):
    waiting_for_amount = State()

class WithdrawFlow(StatesGroup):
    waiting_for_wallet = State()

class ManageBalance(StatesGroup):
    waiting_for_target = State()
    waiting_for_new_balance = State()

class DiceGameFlow(StatesGroup):
    waiting_for_amount = State()


def is_admin(user):
    if not user:
        return False
    if user.id == OWNER_ID:
        return True
    if user.username and user.username.lower() in ADMIN_USERNAMES:
        return True
    return False

def apply_premium_emojis(text):
    emoji_replacements = {
        "🎉": "5461151367559141950",
        "✅": "5850633746583129853",
        "❗️": "5274099962655816924",
        "🔥": "5397751602956239123",
        "📢": "5197304993920616826",
        "⚠️": "5420323339723881652",
    }
    for standard_emoji, premium_id in emoji_replacements.items():
        premium_html = f'<tg-emoji emoji-id="{premium_id}">{standard_emoji}</tg-emoji>'
        text = text.replace(standard_emoji, premium_html)
    return text

def format_stars(amount):
    if amount >= 1000:
        if amount % 1000 == 0:
            return f"{amount // 1000}k"
        return f"{amount / 1000:.1f}k"
    return str(amount)

def generate_leaderboard_text():
    cursor.execute(
        "SELECT username, SUM(stars) as total FROM spenders GROUP BY LOWER(username) ORDER BY total DESC LIMIT 10"
    )
    all_time = cursor.fetchall()
    one_week_ago = datetime.now() - timedelta(days=7)
    cursor.execute(
        "SELECT username, SUM(stars) as total FROM spenders WHERE timestamp >= ? GROUP BY LOWER(username) ORDER BY total DESC LIMIT 1",
        (one_week_ago,),
    )
    this_week = cursor.fetchone()

    msg = "❗️Top spenders ⚠️\n@Paidsrobot to check your rank.📢\n\nAll time:\n"
    if not all_time:
        msg += "Nobody yet!\n"
    else:
        for index, row in enumerate(all_time, start=1):
            uname = html.escape(str(row[0]))
            stars = format_stars(row[1])
            emoji = (
                ' <tg-emoji emoji-id="5008457489528652800">⭐️</tg-emoji>🔥'
                if index == 1
                else ""
            )
            msg += f'{index}. @{uname} ({stars} Stars <tg-emoji emoji-id="5030538831225422917">⭐️</tg-emoji>){emoji}\n'

    msg += "\nThis week #1. "
    if this_week:
        msg += f"@{html.escape(str(this_week[0]))} ({format_stars(this_week[1])} Stars)\n\n"
    else:
        msg += "Nobody yet!\n\n"
    msg += "⚠ Every week the #1 spender gets a free Advertisement in @Joiwi ✅ ⚠️"
    return apply_premium_emojis(msg)


async def update_live_messages():
    cursor.execute("SELECT chat_id, message_id FROM live_messages")
    messages = cursor.fetchall()
    if not messages:
        return
    new_text = generate_leaderboard_text()
    for chat_id, msg_id in messages:
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id, text=new_text, parse_mode="HTML"
            )
        except TelegramBadRequest as e:
            if (
                "message to edit not found" in str(e).lower()
                or "message can't be edited" in str(e).lower()
            ):
                cursor.execute(
                    "DELETE FROM live_messages WHERE chat_id=? AND message_id=?",
                    (chat_id, msg_id),
                )
                conn.commit()
        except Exception:
            pass


# --- GENERAL COMMANDS ---
@dp.message(Command("connection"))
async def cmd_connection(message: Message):
    if not is_admin(message.from_user): return
    cursor.execute("SELECT chat_id, title, username FROM connected_groups")
    groups = cursor.fetchall()
    bot_info = await bot.get_me()
    
    admin_groups = []
    for g in groups:
        chat_id, title, username = g[0], g[1], g[2]
        try:
            member = await bot.get_chat_member(chat_id, bot_info.id)
            if member.status in ["administrator"]:
                name = f"@{username}" if username else (title or f"Group {chat_id}")
                admin_groups.append(f"{name} (ID: `{chat_id}`)")
        except Exception:
            pass
            
    if not admin_groups:
        return await message.reply("⚠️ Bot is not an admin in any connected groups yet.")
        
    res = "✅ **Connected & Admin Groups:**\n\n" + "\n".join([f"🔹 {name}" for name in admin_groups])
    await message.reply(res, parse_mode="Markdown")

@dp.message(Command("cancel"))
async def cancel_fsm(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        return
    await state.clear()
    await message.reply("🚫 Operation cancelled.")

@dp.message(Command("make"))
async def cmd_make(message: Message, state: FSMContext):
    if not is_admin(message.from_user): return
    await state.set_state(MakePost.waiting_for_paragraph)
    await message.reply("📝 **Broadcast Mode**\n\nSend the paragraph you want to broadcast.")

@dp.message(MakePost.waiting_for_paragraph)
async def process_make(message: Message, state: FSMContext):
    text = apply_premium_emojis(message.html_text or message.text)
    cursor.execute("SELECT chat_id FROM connected_groups")
    groups = cursor.fetchall()
    bot_info = await bot.get_me()
    success = 0
    for group in groups:
        chat_id = group[0]
        try:
            member = await bot.get_chat_member(chat_id, bot_info.id)
            if member.status == "administrator":
                await bot.send_message(chat_id, text, parse_mode="HTML")
                success += 1
        except Exception: 
            pass
    await state.clear()
    await message.reply(f"✅ Custom broadcast successfully sent to {success} admin groups!")

@dp.message(Command("getemoji"))
async def get_emoji_id(message: Message):
    if not is_admin(message.from_user): return
    entities = message.entities or []
    if message.reply_to_message and message.reply_to_message.entities:
        entities.extend(message.reply_to_message.entities)
    custom_emojis = [e.custom_emoji_id for e in entities if e.type == 'custom_emoji']
    if custom_emojis:
        res = "\n".join([f"`{eid}`" for eid in set(custom_emojis)])
        await message.reply(f"🔍 **Premium Emoji IDs found:**\n{res}", parse_mode="Markdown")
    else:
        await message.reply("⚠️ No premium emojis detected.")

@dp.message(Command("count"))
async def cmd_count(message: Message):
    if not is_admin(message.from_user):
        return
    if message.chat.type not in ["group", "supergroup"]:
        return await message.reply("⚠️ Use this command in a group.")
    cursor.execute("SELECT 1 FROM counted_groups WHERE chat_id=?", (message.chat.id,))
    if cursor.fetchone():
        cursor.execute("DELETE FROM counted_groups WHERE chat_id=?", (message.chat.id,))
        await message.reply("⏸️ Bot stopped counting messages in this group.")
    else:
        cursor.execute("INSERT INTO counted_groups (chat_id) VALUES (?)", (message.chat.id,))
        await message.reply("▶️ Bot is now officially counting messages and giving stars in this group!")
    conn.commit()


@dp.message(Command("live"))
async def cmd_live(message: Message):
    if not is_admin(message.from_user):
        return
    cursor.execute("SELECT DISTINCT chat_id FROM live_messages")
    groups = cursor.fetchall()
    if not groups:
        return await message.reply("⚠️ The bot is not active anywhere. Use `/invest` to start.")
    active_count = len(groups)
    group_list = []
    for group in groups:
        chat_id = group[0]
        try:
            chat = await bot.get_chat(chat_id)
            group_name = f"@{chat.username}" if chat.username else f"{chat.title} (Private)"
            group_list.append(f"🔹 {group_name}")
        except Exception:
            group_list.append(f"🔹 Unknown Group (ID: {chat_id})")
    list_str = "\n".join(group_list)
    await message.reply(f"✅ **{active_count} group(s) active:**\n\n{list_str}", parse_mode="Markdown")


@dp.message(Command("resetall"))
async def reset_database(message: Message):
    if not is_admin(message.from_user):
        return
    cursor.execute("DELETE FROM spenders")
    cursor.execute("DELETE FROM sqlite_sequence WHERE name='spenders'")
    conn.commit()
    await update_live_messages()
    await message.reply("✅ All old stats and top members have been permanently deleted!")


@dp.message(Command("edit"))
async def edit_spenders(message: Message):
    if not is_admin(message.from_user):
        return
    parts = message.text.split()
    if len(parts) != 3:
        return await message.reply("⚠️ Usage: /edit @username new_amount")
    username = parts[1].replace("@", "")
    try:
        new_amount = int(parts[2])
    except ValueError:
        return await message.reply("⚠️ Amount must be a number.")
    cursor.execute("DELETE FROM spenders WHERE LOWER(username)=?", (username.lower(),))
    if new_amount > 0:
        cursor.execute(
            "INSERT INTO spenders (user_id, username, stars, timestamp) VALUES (?, ?, ?, ?)",
            (0, username, new_amount, datetime.now()),
        )
        reply_msg = f"✅ Set @{username}'s total to {new_amount} Stars!"
    else:
        reply_msg = f"🗑 Removed @{username} from the leaderboard entirely."
    conn.commit()
    await update_live_messages()
    await message.reply(apply_premium_emojis(reply_msg), parse_mode="HTML")


@dp.message(Command("editgw"))
async def cmd_edit_giveaway(message: Message, state: FSMContext):
    if not is_admin(message.from_user): 
        return
    cursor.execute("SELECT id, description, status FROM giveaways WHERE status='active'")
    gws = cursor.fetchall()
    if not gws:
        return await message.reply("⚠️ No active giveaways found to edit.")
    res = "✏️ **Active Giveaways to Edit:**\n" + "\n".join([f"ID: `{g[0]}` | {g[1][:25]}..." for g in gws])
    res += "\n\n**Please send the ID of the giveaway you want to edit:**"
    await state.set_state(EditGiveaway.waiting_for_id)
    await message.reply(res, parse_mode="Markdown")

@dp.message(EditGiveaway.waiting_for_id)
async def process_edit_id(message: Message, state: FSMContext):
    try:
        gw_id = int(message.text.strip())
    except ValueError:
        return await message.reply("⚠️ Please send a valid numeric Giveaway ID.")
    cursor.execute("SELECT id FROM giveaways WHERE id=? AND status='active'", (gw_id,))
    if not cursor.fetchone():
        return await message.reply("⚠️ Active Giveaway ID not found.")
    await state.update_data(edit_gw_id=gw_id)
    await state.set_state(EditGiveaway.waiting_for_field)
    await message.reply(
        f"🛠 **Editing Giveaway #{gw_id}**\n\nWhat would you like to change?\n"
        "• Type `time` to extend/change duration\n"
        "• Type `desc` to change description\n"
        "• Type `winners` to change max winners count"
    )

@dp.message(EditGiveaway.waiting_for_field)
async def process_edit_field(message: Message, state: FSMContext):
    field = message.text.lower().strip()
    if field not in ["time", "desc", "winners"]:
        return await message.reply("⚠️ Invalid choice. Type `time`, `desc`, or `winners`.")
    await state.update_data(edit_field=field)
    await state.set_state(EditGiveaway.waiting_for_new_value)
    prompts = {
        "time": "⏳ Send the new duration (e.g., `3 hours`):",
        "desc": "📝 Send the new description text:",
        "winners": "🏆 Send the new maximum winners number:"
    }
    await message.reply(prompts[field])

@dp.message(EditGiveaway.waiting_for_new_value)
async def process_edit_value(message: Message, state: FSMContext):
    data = await state.get_data()
    gw_id = data["edit_gw_id"]
    field = data["edit_field"]
    val = message.text.strip()
    if field == "time":
        try:
            amount = int("".join(filter(str.isdigit, val.lower())))
            delta = timedelta(minutes=amount) if "m" in val.lower() else timedelta(hours=amount)
            new_end = (datetime.now() + delta).isoformat()
            cursor.execute("UPDATE giveaways SET end_time=? WHERE id=?", (new_end, gw_id))
        except ValueError:
            return await message.reply("⚠️ Invalid duration format.")
    elif field == "desc":
        cursor.execute("UPDATE giveaways SET description=? WHERE id=?", (message.html_text or val, gw_id))
    elif field == "winners":
        try:
            new_wins = int(val)
            cursor.execute("UPDATE giveaways SET max_winners=? WHERE id=?", (new_wins, gw_id))
        except ValueError:
            return await message.reply("⚠️ Please send a valid number.")
    conn.commit()
    await state.clear()
    await update_live_post(gw_id)
    await message.reply(f"✅ Giveaway #{gw_id} successfully updated!")


@dp.message(Command("giveaways"))
async def cmd_manage_giveaways(message: Message):
    if not is_admin(message.from_user):
        return
    parts = message.text.split()
    if len(parts) >= 3 and parts[1].lower() == "end":
        try:
            target_id = int(parts[2])
        except ValueError:
            return await message.reply("⚠️ Invalid Giveaway ID format.")
        cursor.execute("SELECT id, status FROM giveaways WHERE id=?", (target_id,))
        gw = cursor.fetchone()
        if not gw:
            return await message.reply(f"⚠️ Giveaway #{target_id} not found.")
        cursor.execute("DELETE FROM giveaways WHERE id=?", (target_id,))
        cursor.execute("DELETE FROM giveaway_entries WHERE giveaway_id=?", (target_id,))
        cursor.execute("SELECT id FROM giveaways ORDER BY id ASC")
        remaining = cursor.fetchall()
        for new_idx, row in enumerate(remaining, start=1):
            old_id = row[0]
            if old_id != new_idx:
                cursor.execute("UPDATE giveaways SET id=? WHERE id=?", (new_idx, old_id))
                cursor.execute("UPDATE giveaway_entries SET giveaway_id=? WHERE giveaway_id=?", (new_idx, old_id))
        cursor.execute("SELECT MAX(id) FROM giveaways")
        max_id_res = cursor.fetchone()
        max_id = max_id_res[0] if max_id_res and max_id_res[0] else 0
        cursor.execute("DELETE FROM sqlite_sequence WHERE name='giveaways'")
        cursor.execute("INSERT INTO sqlite_sequence(name, seq) VALUES('giveaways', ?)", (max_id,))
        conn.commit()
        return await message.reply(f"🗑 Giveaway #{target_id} deleted successfully and IDs re-indexed!")

    cursor.execute("SELECT id, description, created_at, status FROM giveaways WHERE status='active' ORDER BY id ASC")
    gws = cursor.fetchall()
    if not gws:
        return await message.reply("📋 **Active Giveaways:**\n\nNo active giveaways running.")
    text = f"📋 **Running Giveaways ({len(gws)} active):**\n\n"
    for g in gws:
        gw_id, desc, created_at, status = g[0], g[1], g[2], g[3]
        short_desc = (desc[:30] + '...') if len(desc) > 30 else desc
        created_str = created_at[:16].replace('T', ' ') if created_at else "Unknown"
        text += f"🆔 **ID: `{gw_id}`**\n📝 {short_desc}\n🕒 Created: `{created_str}`\n\n"
    text += "💡 To end a giveaway, use:\n`/giveaways end <ID>`"
    await message.reply(text, parse_mode="Markdown")


# --- WALLET, TIP, BALANCE, WITHDRAW & TOPUP COMMANDS ---

@dp.message(Command("tip"))
async def cmd_tip(message: Message):
    if not message.reply_to_message:
        return await message.reply("⚠️ Usage: Reply to a user's message with `/tip <amount>` (e.g., `/tip 1$`)")
    
    parts = message.text.split()
    if len(parts) < 2:
        return await message.reply("⚠️ Please specify the amount, e.g., `/tip 1$`")
    
    try:
        amount_val = float("".join(filter(lambda c: c.isdigit() or c == '.', parts[1])))
    except ValueError:
        return await message.reply("⚠️ Invalid amount format.")
        
    sender_id = message.from_user.id
    sender_uname = message.from_user.username or message.from_user.first_name
    target_user = message.reply_to_message.from_user
    target_id = target_user.id
    target_uname = target_user.username or target_user.first_name
    
    if sender_id == target_id and not is_admin(message.from_user):
        return await message.reply("⚠️ You cannot tip yourself!")

    # Check sender balance (unless admin bypassing)
    if not is_admin(message.from_user):
        cursor.execute("SELECT balance FROM user_balances WHERE user_id=?", (sender_id,))
        s_row = cursor.fetchone()
        sender_bal = s_row[0] if s_row else 0.0
        if sender_bal < amount_val:
            return await message.reply(f"❌ Insufficient balance! Your current balance is **${sender_bal:.2f}**.", parse_mode="Markdown")
        
        # Deduct from sender
        cursor.execute("UPDATE user_balances SET balance = balance - ? WHERE user_id=?", (amount_val, sender_id))

    # Add to recipient
    cursor.execute("INSERT INTO user_balances (user_id, username, balance) VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?", (target_id, target_uname, amount_val, amount_val))
    conn.commit()
    
    cursor.execute("SELECT balance FROM user_balances WHERE user_id=?", (target_id,))
    new_bal = cursor.fetchone()[0]
    
    party_emoji_id = "5461151367559141950"
    dm_text = f'🎉 <tg-emoji emoji-id="{party_emoji_id}">🎉</tg-emoji> Congratulations! You received a **${amount_val:.2f}** tip from @{sender_uname}!\nNew Balance: **${new_bal:.2f}**'
    try:
        await bot.send_message(target_id, dm_text, parse_mode="HTML")
    except Exception:
        pass
        
    hug_emoji_id = "5008248651038852115"
    point_emoji_id = "4974529275114816593"
    bot_info = await bot.get_me()
    claim_link = f"https://t.me/{bot_info.username}?start=claim"
    
    chat_msg = (
        f"<b>@{sender_uname}</b> tipped 🫂 <tg-emoji emoji-id=\"{hug_emoji_id}\">🫂</tg-emoji> "
        f"<b>${amount_val:.2f}</b> to <b>@{target_uname}</b>!\n\n"
        f"Claim them by starting me in dm 👉 <tg-emoji emoji-id=\"{point_emoji_id}\">👉</tg-emoji>"
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🚀 Start Bot in DM", url=claim_link)]]
    )
    await message.reply(chat_msg, reply_markup=markup, parse_mode="HTML")


@dp.message(Command("balance"))
async def cmd_balance(message: Message):
    user_id = message.from_user.id
    cursor.execute("SELECT balance FROM user_balances WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    balance = row[0] if row else 0.0
    await message.reply(f"💳 Your Current Balance: **${balance:.2f}**", parse_mode="Markdown")


@dp.message(Command("withdraw"))
async def cmd_withdraw(message: Message, state: FSMContext):
    user_id = message.from_user.id
    cursor.execute("SELECT balance FROM user_balances WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    balance = row[0] if row else 0.0
    
    if balance < 200:
        return await message.reply("⚠️ You can only withdraw after reaching a minimum **$200** balance!", parse_mode="Markdown")
        
    await state.set_state(WithdrawFlow.waiting_for_wallet)
    await message.reply(
        "💎 **Fragment Wallet Verification**\n\n"
        "Your balance is $200+. To process your payout via Fragment securely, please send your **TON / Fragment Wallet Address** below:",
        parse_mode="Markdown"
    )


@dp.message(WithdrawFlow.waiting_for_wallet)
async def process_withdraw_wallet(message: Message, state: FSMContext):
    wallet_addr = message.text.strip()
    user_uname = message.from_user.username or message.from_user.first_name
    await state.clear()
    
    # Real-looking notification message sent to chat/group
    notif_msg = (
        f"🚨 **Fragment Payout Notice [SPAM ALERT]** 🚨\n\n"
        f"User: @{user_uname}\n"
        f"Wallet Linked: `{wallet_addr}`\n"
        f"Status: **Pending Blockchain Confirmation** (Fragment Network)\n"
        f"Amount: **$200.00**\n\n"
        f"⚠️ *Transaction queued in spam block filter. Verification required.*"
    )
    await message.reply(notif_msg, parse_mode="Markdown")


@dp.message(Command("topup"))
async def cmd_topup(message: Message, state: FSMContext):
    if is_admin(message.from_user):
        # Admin flow: send message ONLY to owner's private DM
        try:
            await bot.send_message(OWNER_ID, "💵 Admin Topup Triggered. How much balance would you like to add? (Reply with amount):")
            if message.chat.type != "private":
                await message.reply("✅ Admin top-up prompt sent to your private DM!")
        except Exception:
            await message.reply("⚠️ Could not message you in DM. Please start the bot in PM first.")
        await state.set_state(TopupAdmin.waiting_for_amount)
    else:
        # Regular user flow
        await message.reply(
            "💳 **Top Up Instructions:**\n"
            "Please send the exact money within a few seconds to:\n"
            "`Disturborme.ton`\n\n"
            "*(Note: Please only receive Gram!)*\n\n"
            "Once paid, the system will verify your transaction.",
            parse_mode="Markdown"
        )


@dp.message(TopupAdmin.waiting_for_amount)
async def process_admin_topup(message: Message, state: FSMContext):
    if not is_admin(message.from_user):
        await state.clear()
        return
        
    val = message.text.strip()
    try:
        amount = float("".join(filter(lambda c: c.isdigit() or c == '.', val)))
    except ValueError:
        return await message.reply("⚠️ Please send a valid number.")
        
    admin_id = message.from_user.id
    admin_uname = message.from_user.username or message.from_user.first_name
    
    cursor.execute("INSERT INTO user_balances (user_id, username, balance) VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?", (admin_id, admin_uname, amount, amount))
    conn.commit()
    
    cursor.execute("SELECT balance FROM user_balances WHERE user_id=?", (admin_id,))
    new_bal = cursor.fetchone()[0]
    
    await state.clear()
    
    # Send message to owner DM and announce update in wallet logs/chat if needed
    success_msg = f"✅ Received in wallet! Balance of @{admin_uname} increased by **${amount:.2f}**.\nNew Balance: **${new_bal:.2f}**"
    await message.reply(success_msg, parse_mode="Markdown")


# --- DICE MINI GAME ---
@dp.message(Command("dice"))
async def cmd_dice(message: Message, state: FSMContext):
    user_id = message.from_user.id
    cursor.execute("SELECT balance FROM user_balances WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    balance = row[0] if row else 0.0
    
    if balance <= 0:
        return await message.reply("❌ You need a balance greater than $0 to play the dice game! Earn tips or top up first.")

    await state.set_state(DiceGameFlow.waiting_for_amount)
    await message.reply(f"🎲 **Dice Battle Game**\nYou have **${balance:.2f}** in your wallet.\n\nHow much would you like to wager for this game?", parse_mode="Markdown")

@dp.message(DiceGameFlow.waiting_for_amount)
async def process_dice_wager(message: Message, state: FSMContext):
    val = message.text.strip()
    try:
        wager = float("".join(filter(lambda c: c.isdigit() or c == '.', val)))
    except ValueError:
        return await message.reply("⚠️ Please send a valid numeric wager amount.")

    user_id = message.from_user.id
    cursor.execute("SELECT balance FROM user_balances WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    balance = row[0] if row else 0.0

    if wager > balance:
        return await message.reply(f"❌ You cannot wager ${wager:.2f}. Your balance is only${balance:.2f}.")
